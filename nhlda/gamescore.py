"""Game Score breakdown: how much each player added in his team's latest game,
split into offense and defense, each with four parts.

Inputs (free, no keys): the NHL play-by-play and shift charts for the game, the
expected-goal value MoneyPuck.com gives each unblocked shot (its season shot file,
updated nightly), and MoneyPuck's season team and skater summaries for league
rates and player quality. Credit MoneyPuck.com wherever this is shown.

The model is this app's own, built on the public Game Score of Dom Luszczyszyn
(Hockey Graphs, 2016) and expressed in the same units:

  Production    offense  0.75 G + 0.70 A1 + 0.55 A2 + 0.075 SOG + 0.15 penalties drawn
                         + 0.01 faceoffs won - 0.01 faceoffs lost
                defense  0.05 blocked shots - 0.15 penalties taken
  Play driving  5-on-5, on the ice, against the league rate for his 5-on-5 time:
                offense  0.9 (xGF - L xG) + 0.15 (GF - L G)
                defense  0.9 (L xG - xGA) + 0.15 (L G - GA)
  Special teams offense  0.15 (power-play goals for on the ice - league PP rate x PP time)
                defense  0.15 (league PP rate x PK time - goals against on the ice short-handed)
  Usage         5-on-5 teammates and opponents, weighted by shared seconds, from their
                season on-ice rates (shrunk toward the league with one hour of league play):
                offense  -0.45 t5 [(teammates xGF/60 - L) + (opponents xGA/60 - L)]
                defense  -0.45 t5 [(L - teammates xGA/60) + (L - opponents xGF/60)]
                (t5 in hours: strong teammates or weak opponents cost a little credit)
  Goalie        goals saved above expected: the xG of unblocked shots he faced minus
                goals allowed (shootout excluded)

When the shift charts or the shot values are missing two days after a game, the
game is scored on production only and marked that way.
"""
import asyncio
import csv
import datetime as dt
import io
import json
import logging
import os
import zipfile
from collections import defaultdict

from .data import NHL_BASE, STATS_BASE, MP_BASE, BASE_HEADERS, safe_float

log = logging.getLogger("nhlda.gamescore")

MP_SHOTS_URL = "https://peter-tanner.com/moneypuck/downloads/shots_{year}.zip"
CACHE_NAME = "gamescore.json"
LOOKBACK_DAYS = 3        # finished games of today and the three days before
WAIT_DAYS = 2            # then score on production only if inputs are still missing
DEFAULT_RATES = {"xg": 2.5, "g": 2.4, "pp": 6.5}   # per 60, used only when MoneyPuck is unreachable
FINAL_STATES = {"FINAL", "OFF"}
W = {"g": 0.75, "a1": 0.70, "a2": 0.55, "sog": 0.075, "pd": 0.15, "fow": 0.01, "fol": 0.01,
     "blk": 0.05, "pt": 0.15, "xg": 0.9, "goal": 0.15, "st": 0.15, "use": 0.45}


def _secs(mmss):
    m, s = str(mmss or "0:0").split(":")
    return int(m) * 60 + int(s)


def _strength(code, home):
    """5v5, PP, PK, EV (other even), or EN (a net is empty) for one side, from the
    NHL situationCode: away goalie, away skaters, home skaters, home goalie."""
    code = str(code or "1551").rjust(4, "1")
    ag, ask, hsk, hg = (int(c) for c in code[:4])
    if ag == 0 or hg == 0:
        return "EN"
    us, them = (hsk, ask) if home else (ask, hsk)
    if us == them == 5:
        return "5v5"
    return "PP" if us > them else "PK" if us < them else "EV"


def league_rates(teams_csv):
    """League 5-on-5 xG and goals per 60 and power-play goals per 60 from MoneyPuck's
    season team summary."""
    acc = defaultdict(lambda: [0.0, 0.0, 0.0])
    for r in csv.DictReader(io.StringIO(teams_csv or "")):
        a = acc[(r.get("situation") or "").strip()]
        a[0] += safe_float(r.get("iceTime")); a[1] += safe_float(r.get("xGoalsFor")); a[2] += safe_float(r.get("goalsFor"))
    e, p = acc.get("5on5"), acc.get("5on4")
    if not e or e[0] < 36000 or not p or p[0] < 3600:
        return dict(DEFAULT_RATES)
    return {"xg": e[1] / e[0] * 3600, "g": e[2] / e[0] * 3600, "pp": p[2] / p[0] * 3600}


def player_quality(skaters_csv, rates):
    """Each skater's season 5-on-5 on-ice xGF/60 and xGA/60, shrunk toward the league
    rate with one hour of league play."""
    q, L = {}, rates["xg"]
    for r in csv.DictReader(io.StringIO(skaters_csv or "")):
        if (r.get("situation") or "").strip() != "5on5":
            continue
        hours = safe_float(r.get("icetime")) / 3600
        try:
            pid = int(r.get("playerId"))
        except (TypeError, ValueError):
            continue
        q[pid] = ((safe_float(r.get("OnIce_F_xGoals")) + L) / (hours + 1), (safe_float(r.get("OnIce_A_xGoals")) + L) / (hours + 1))
    return q


def shot_values(rows):
    """MoneyPuck shot rows for one game -> {(period, second of period, shooter): xG}."""
    out = {}
    for r in rows:
        try:
            per, t = int(float(r["period"])), int(float(r["time"]))
            out[(per, t - (per - 1) * 1200, int(float(r.get("shooterPlayerId") or 0)))] = float(r["xGoal"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _xg_of(xg, per, t, shooter):
    for dt_ in (0, -1, 1, -2, 2):
        v = xg.get((per, t + dt_, shooter))
        if v is not None:
            return v
    return None


def breakdown(pbp, shift_rows, shots, rates, quality):
    """Score every player of both teams for one finished game. shift_rows: the NHL
    shift chart rows (empty when not published); shots: {(period, sec, shooter): xG}
    (empty when not published). Returns {"inputs": "full"|"production", "teams": {...}}."""
    home, away = pbp["homeTeam"], pbp["awayTeam"]
    hid, aid = home["id"], away["id"]
    abbr = {hid: home["abbrev"], aid: away["abbrev"]}
    full = bool(shift_rows) and bool(shots)
    roster = {}
    for r in pbp.get("rosterSpots") or []:
        roster[r["playerId"]] = {"team": r["teamId"], "pos": r.get("positionCode") or "",
                                 "name": f"{(r.get('firstName') or {}).get('default', '')} {(r.get('lastName') or {}).get('default', '')}".strip()}
    L, Lg, Lpp = rates["xg"], rates["g"], rates["pp"]

    # who is on the ice each second: a player counts at second t of a shift that runs (start, end]
    onice = defaultdict(lambda: defaultdict(set))
    toi = defaultdict(int)
    for s in shift_rows or []:
        if s.get("typeCode") not in (517, None) or not s.get("startTime") or not s.get("endTime"):
            continue
        pid, per = s.get("playerId"), int(s.get("period") or 0)
        a, b = _secs(s["startTime"]), _secs(s["endTime"])
        if pid not in roster or roster[pid]["pos"] == "G":
            continue
        for t in range(a + 1, b + 1):
            onice[per][t].add(pid)
        toi[pid] += max(0, b - a)

    plays = [e for e in pbp.get("plays") or [] if (e.get("periodDescriptor") or {}).get("periodType") != "SO"]
    st = defaultdict(lambda: defaultdict(float))
    for e in plays:
        k, d = e.get("typeDescKey"), e.get("details") or {}
        per, t = e["periodDescriptor"]["number"], _secs(e.get("timeInPeriod"))
        code = e.get("situationCode") or "1551"
        if k in ("goal", "shot-on-goal", "missed-shot", "blocked-shot"):
            shooter = d.get("scoringPlayerId") or d.get("shootingPlayerId")
            if k == "blocked-shot":
                # the NHL credits a blocked shot to the blocking team; the shooter is the other side
                blocker = d.get("blockingPlayerId")
                if blocker in roster:
                    st[blocker]["blk"] += 1
                shoot_team = roster.get(shooter, {}).get("team") or (aid if d.get("eventOwnerTeamId") == hid else hid)
                x = 0.0
            else:
                shoot_team = d.get("eventOwnerTeamId") or roster.get(shooter, {}).get("team")
                x = _xg_of(shots, per, t, shooter) or 0.0
            if shooter in roster and k != "blocked-shot":
                st[shooter]["ixg"] += x
                if k in ("goal", "shot-on-goal"):
                    st[shooter]["sog"] += 1
            if k == "goal":
                if shooter in roster:
                    st[shooter]["g"] += 1
                if d.get("assist1PlayerId") in roster:
                    st[d["assist1PlayerId"]]["a1"] += 1
                if d.get("assist2PlayerId") in roster:
                    st[d["assist2PlayerId"]]["a2"] += 1
            for pid in onice[per].get(t, ()):
                team = roster[pid]["team"]
                ours = team == shoot_team
                stg = _strength(code, team == hid)
                if stg == "5v5":
                    st[pid]["xgf" if ours else "xga"] += x
                    st[pid]["cf" if ours else "ca"] += 1
                    if k == "goal":
                        st[pid]["gf" if ours else "ga"] += 1
                elif k == "goal" and stg == "PP" and ours:
                    st[pid]["ppgf"] += 1
                elif k == "goal" and stg == "PK" and not ours:
                    st[pid]["pkga"] += 1
            if k in ("goal", "shot-on-goal", "missed-shot") and d.get("goalieInNetId"):
                g = d["goalieInNetId"]
                st[g]["xga_g"] += x
                st[g]["ga_g" if k == "goal" else "sv_g" if k == "shot-on-goal" else "miss_g"] += 1
        elif k == "penalty":
            if d.get("committedByPlayerId") in roster:
                st[d["committedByPlayerId"]]["pt"] += 1
            if d.get("drawnByPlayerId") in roster:
                st[d["drawnByPlayerId"]]["pd"] += 1
        elif k == "faceoff":
            if d.get("winningPlayerId") in roster:
                st[d["winningPlayerId"]]["fow"] += 1
            if d.get("losingPlayerId") in roster:
                st[d["losingPlayerId"]]["fol"] += 1

    # strength each second (carried forward from the last event), time by strength,
    # and the teammates and opponents each skater shared the 5-on-5 ice with
    if onice:
        timeline = defaultdict(list)
        for e in plays:
            timeline[e["periodDescriptor"]["number"]].append((_secs(e.get("timeInPeriod")), e.get("situationCode") or "1551"))
        for per, secs in onice.items():
            evs = sorted(timeline.get(per, []))
            code, j = "1551", 0
            for t in sorted(secs):
                while j < len(evs) and evs[j][0] < t:
                    code = evs[j][1]
                    j += 1
                players = secs[t]
                for pid in players:
                    team = roster[pid]["team"]
                    stg = _strength(code, team == hid)
                    st[pid]["t_" + stg] += 1
                    if stg == "5v5" and quality is not None:
                        for q in players:
                            if q == pid:
                                continue
                            f_, a_ = quality.get(q, (L, L))
                            if roster[q]["team"] == team:
                                st[pid]["tm_f"] += f_; st[pid]["tm_a"] += a_; st[pid]["tm_n"] += 1
                            else:
                                st[pid]["op_f"] += f_; st[pid]["op_a"] += a_; st[pid]["op_n"] += 1

    teams = {}
    for tid, oid in ((hid, aid), (aid, hid)):
        sk, gk = [], []
        for pid, info in roster.items():
            if info["team"] != tid:
                continue
            s = st.get(pid, {})
            if info["pos"] == "G":
                faced = s.get("ga_g", 0) + s.get("sv_g", 0)
                if faced:
                    gk.append({"id": pid, "name": info["name"], "pos": "G", "shots": int(faced),
                               "ga": int(s.get("ga_g", 0)), "xga": round(s.get("xga_g", 0.0), 2) if full else None,
                               "gsax": round(s.get("xga_g", 0.0) - s.get("ga_g", 0), 2) if full else None})
                continue
            if full and not toi.get(pid):
                continue                      # dressed but did not play
            o_prod = (W["g"] * s.get("g", 0) + W["a1"] * s.get("a1", 0) + W["a2"] * s.get("a2", 0) + W["sog"] * s.get("sog", 0)
                      + W["pd"] * s.get("pd", 0) + W["fow"] * s.get("fow", 0) - W["fol"] * s.get("fol", 0))
            d_prod = W["blk"] * s.get("blk", 0) - W["pt"] * s.get("pt", 0)
            o_drv = d_drv = o_st = d_st = o_use = d_use = 0.0
            if full:
                t5, tpp, tpk = s.get("t_5v5", 0) / 3600, s.get("t_PP", 0) / 3600, s.get("t_PK", 0) / 3600
                o_drv = W["xg"] * (s.get("xgf", 0) - L * t5) + W["goal"] * (s.get("gf", 0) - Lg * t5)
                d_drv = W["xg"] * (L * t5 - s.get("xga", 0)) + W["goal"] * (Lg * t5 - s.get("ga", 0))
                o_st = W["st"] * (s.get("ppgf", 0) - Lpp * tpp)
                d_st = W["st"] * (Lpp * tpk - s.get("pkga", 0))
                if quality is not None:
                    tmf = s["tm_f"] / s["tm_n"] if s.get("tm_n") else L
                    tma = s["tm_a"] / s["tm_n"] if s.get("tm_n") else L
                    opf = s["op_f"] / s["op_n"] if s.get("op_n") else L
                    opa = s["op_a"] / s["op_n"] if s.get("op_n") else L
                    o_use = -W["use"] * t5 * ((tmf - L) + (opa - L))
                    d_use = -W["use"] * t5 * ((L - tma) + (L - opf))
            o = [round(v, 2) for v in (o_prod, o_drv, o_st, o_use)]
            dd = [round(v, 2) for v in (d_prod, d_drv, d_st, d_use)]
            sk.append({"id": pid, "name": info["name"], "pos": info["pos"],
                       "toi": round(toi.get(pid, 0) / 60, 1) if full else None,
                       "gs": round(sum(o) + sum(dd), 2), "o": o, "d": dd,
                       "box": {k: int(s.get(k, 0)) for k in ("g", "a1", "a2", "sog", "blk", "pd", "pt")}})
        sk.sort(key=lambda p: (p["pos"] == "D", -p["gs"], p["name"]))
        teams[abbr[tid]] = {"opp": abbr[oid], "home": tid == hid, "skaters": sk, "goalies": gk}
    return {"inputs": "full" if full else "production", "teams": teams}


def _team_result(pbp, ab):
    home, away = pbp["homeTeam"], pbp["awayTeam"]
    us, them = (home, away) if home["abbrev"] == ab else (away, home)
    last = ((pbp.get("gameOutcome") or {}).get("lastPeriodType") or "REG")
    won = (us.get("score") or 0) > (them.get("score") or 0)
    return {"for": us.get("score"), "against": them.get("score"), "last_period": last,
            "result": "W" if won else ("OTL" if last in ("OT", "SO") else "L")}


async def _shot_rows(session, year, want):
    """MoneyPuck's season shot file, filtered to the games in want (MoneyPuck game
    ids: the NHL id without the season). {} when it cannot be read."""
    try:
        async with session.get(MP_SHOTS_URL.format(year=year), headers={"User-Agent": BASE_HEADERS["User-Agent"]}) as r:
            if r.status != 200:
                log.warning("MoneyPuck shots: HTTP %s", r.status)
                return {}
            blob = await r.read()
        out = defaultdict(list)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            name = next(n for n in z.namelist() if n.endswith(".csv"))
            with z.open(name) as fh:
                for row in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8")):
                    try:
                        g = int(float(row["game_id"]))
                    except (KeyError, ValueError):
                        continue
                    if g in want:
                        out[g].append(row)
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("MoneyPuck shots: %s", exc)
        return {}


def _load_cache(state_dir):
    try:
        return json.load(open(os.path.join(state_dir, CACHE_NAME), encoding="utf-8"))
    except (OSError, ValueError):
        return {"games": {}}


def latest_by_team(cache):
    """Each team's most recent scored game from the cache."""
    best = {}
    for gid, g in (cache.get("games") or {}).items():
        for ab, entry in (g.get("teams") or {}).items():
            key = (g.get("date") or "", int(gid))
            if ab not in best or key > best[ab][0]:
                best[ab] = (key, dict(entry, game_id=int(gid), date=g.get("date"), inputs=g.get("inputs")))
    return {ab: v[1] for ab, v in sorted(best.items())}


async def update(f, session, state_dir, target_date, season_year, game_type_ok=(2, 3)):
    """Score every finished game of the last LOOKBACK_DAYS days not yet scored in full,
    keep each team's latest game in state/gamescore.json, and return the public block."""
    cache = _load_cache(state_dir)
    games = cache.setdefault("games", {})
    day = dt.date.fromisoformat(target_date)
    finals = []
    for k in range(LOOKBACK_DAYS, -1, -1):
        d = (day - dt.timedelta(days=k)).isoformat()
        data = await f.get(f"{NHL_BASE}score/{d}") or {}
        for g in data.get("games") or []:
            if g.get("gameDate") == d and g.get("gameState") in FINAL_STATES and g.get("gameType") in game_type_ok:
                finals.append((d, int(g["id"])))
    todo = [(d, gid) for d, gid in finals if not (games.get(str(gid)) or {}).get("complete")]
    if todo:
        shots = await _shot_rows(session, season_year, {gid % 1000000 for _, gid in todo})
        teams_csv, skaters_csv = await asyncio.gather(
            f.get(f"{MP_BASE}{season_year}/regular/teams.csv", as_text=True),
            f.get(f"{MP_BASE}{season_year}/regular/skaters.csv", as_text=True))
        rates = league_rates(teams_csv if teams_csv and not teams_csv.lstrip().startswith("<") else "")
        quality = player_quality(skaters_csv if skaters_csv and not skaters_csv.lstrip().startswith("<") else "", rates)
        for i in range(0, len(todo), 4):
            chunk = todo[i:i + 4]
            got = await asyncio.gather(*[asyncio.gather(
                f.get(f"{NHL_BASE}gamecenter/{gid}/play-by-play"),
                f.get(f"{STATS_BASE}shiftcharts", params={"cayenneExp": f"gameId={gid}"})) for _, gid in chunk])
            for (d, gid), (pbp, sh) in zip(chunk, got):
                if not pbp or pbp.get("gameState") not in FINAL_STATES:
                    continue
                rows = [r for r in (sh or {}).get("data") or [] if r.get("typeCode") == 517]
                xg = shot_values(shots.get(gid % 1000000, []))
                full = bool(rows) and len(xg) >= 10
                late = (day - dt.date.fromisoformat(d)).days >= WAIT_DAYS
                if not full and not late:
                    continue                       # wait for the shift charts or the shot values
                try:
                    res = breakdown(pbp, rows if full else [], xg if full else {}, rates, quality)
                except Exception as exc:  # noqa: BLE001
                    log.warning("game score %s: %s", gid, exc)
                    continue
                for ab, entry in res["teams"].items():
                    entry.update(_team_result(pbp, ab))
                games[str(gid)] = {"date": d, "inputs": res["inputs"], "complete": True, "teams": res["teams"],
                                   "rates": {k: round(v, 3) for k, v in rates.items()}}
    # keep only each team's latest game
    latest = latest_by_team(cache)
    keep = {str(v["game_id"]) for v in latest.values()}
    cache["games"] = {gid: g for gid, g in games.items() if gid in keep}
    with open(os.path.join(state_dir, CACHE_NAME), "w", encoding="utf-8") as fh:
        json.dump(cache, fh, separators=(",", ":"))
    return {"teams": latest,
            "credit": "NHL play-by-play and shift charts; expected goals and season rates from MoneyPuck.com"}
