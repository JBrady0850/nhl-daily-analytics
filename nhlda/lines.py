"""Forward lines and defense pairs for every team, with NHL game-log stats.

Line combinations come from Daily Faceoff's team page when it answers. When it
does not (a block, a page change, a team with no page yet), the lines are rebuilt
from the team's most recent game: the NHL shift chart gives every player's
shifts, and players who shared the most ice time are grouped into four forward
lines and three defense pairs. Either way every player's numbers are rebuilt
from the NHL's own per-game logs (this season, no preseason).
"""
import asyncio
import datetime as dt
import json
import logging
import os
from collections import defaultdict

from . import intel as I
from .data import NHL_BASE, STATS_BASE, parse_toi_seconds

log = logging.getLogger("nhlda.lines")

SHIFT_TYPE = 517             # shift rows in the NHL shift chart (other codes are goal events)
POS_FORWARD = {"C": "C", "L": "LW", "R": "RW"}

# Public 1.13.3. On 2026-10-30 Daily Faceoff answered GitHub's server at the 9:00 AM
# update and refused it (HTTP 403) at 4:45 PM: every team's lines fell back to the
# shift-chart rebuild and every starting goalie to the rotation guess. Its last good
# answer is now kept in state/dfo_cache.json and used when it refuses: lines for up
# to LINES_CACHE_DAYS days, starting goalies only for the same date.
DFO_CACHE = "dfo_cache.json"
LINES_CACHE_DAYS = 3


def _utc_now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_days(then, now):
    try:
        a = dt.datetime.fromisoformat(then.replace("Z", "+00:00"))
        b = dt.datetime.fromisoformat(now.replace("Z", "+00:00"))
        return (b - a).total_seconds() / 86400
    except (AttributeError, ValueError):
        return 99


def _load_cache(state_dir):
    try:
        c = json.load(open(os.path.join(state_dir, DFO_CACHE), encoding="utf-8"))
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(state_dir, cache):
    path = os.path.join(state_dir, DFO_CACHE)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(cache, fh, separators=(",", ":"), sort_keys=True)
    os.replace(path + ".tmp", path)


async def dfo_lines_with_cache(f, teams, state_dir, now_utc=None):
    """Daily Faceoff's lines for each team, four teams at a time. A team it refuses
    gets its last good answer when that is under LINES_CACHE_DAYS days old.
    Returns ({team: DailyFaceoffLines}, {"refused": [...], "cached": {team: read_utc}})."""
    now = now_utc or _utc_now()
    cache = _load_cache(state_dir)
    saved = cache.setdefault("lines", {})
    got, refused, used = {}, [], {}
    for i in range(0, len(teams), 4):                     # gentle on the source
        chunk = teams[i:i + 4]
        res = await asyncio.gather(*[I.DailyFaceoffLines(t).resolve(f) for t in chunk])
        got.update({t: dl for t, dl in zip(chunk, res)})
    for t in teams:
        dl = got[t]
        if not dl.error and dl.combinations:
            saved[t] = {"read_utc": now, "url": dl.url, "combinations": dl.combinations}
            continue
        if dl.error == "fetch_failed":
            refused.append(t)
        old = saved.get(t)
        if old and _age_days(old.get("read_utc"), now) <= LINES_CACHE_DAYS:
            again = I.DailyFaceoffLines(t)
            again.url = old.get("url")
            again.from_combinations(old.get("combinations") or {})
            if not again.error:
                again.cached_utc = old.get("read_utc")
                got[t] = again
                used[t] = again.cached_utc
    _save_cache(state_dir, cache)
    return got, {"refused": refused, "cached": used}


async def dfo_goalies_with_cache(f, date, state_dir, now_utc=None):
    """Daily Faceoff's starting goalies for the date; when it refuses, its earlier
    answer for the same date (never another day's)."""
    now = now_utc or _utc_now()
    g = await I.DailyFaceoffGoalies().resolve(f, date)
    cache = _load_cache(state_dir)
    if g.loaded:
        cache["goalies"] = {"date": date, "read_utc": now, "by_team": g.by_team}
        _save_cache(state_dir, cache)
        return g
    g.refused = g.error == "fetch_failed"
    old = cache.get("goalies") or {}
    if old.get("date") == date and old.get("by_team"):
        g.by_team = dict(old["by_team"])
        g.loaded = True
        g.cached_utc = old.get("read_utc")
    return g


def dfo_note(goalies_refused, lines_refused, lines_cached, rebuilt, goalies_cached=None):
    """The Data note for a run Daily Faceoff did not answer, saying what was used
    instead; None when it answered."""
    if not goalies_refused and not lines_refused:
        return None
    stamp = lambda u: (u or "")[:16].replace("T", " ") + " UTC"
    used = []
    if goalies_refused:
        used.append(f"starting goalies come from its answer at {stamp(goalies_cached)}" if goalies_cached
                    else "starting goalies are the rotation guess")
    if lines_refused:
        if lines_cached:
            when = sorted(set(lines_cached.values()))
            used.append(f"line combinations come from its answer at {stamp(when[-1])}"
                        + (f" ({len(lines_cached)} teams)" if len(lines_cached) < 32 else ""))
        if rebuilt:
            used.append(f"line combinations for {rebuilt} team{'s' if rebuilt != 1 else ''} are rebuilt from "
                        "each team's last game")
    return "Daily Faceoff did not answer this update, so " + "; ".join(used) + "."


def center_middle(rows):
    """Left wing, center, right wing: the center between his wingers even when the
    NHL lists both wingers on one side."""
    order = {"LW": 0, "C": 1, "RW": 2}
    rows = sorted(rows, key=lambda r: order.get(r.get("pos"), 9))
    centers = [r for r in rows if r.get("pos") == "C"]
    if len(rows) != 3 or len(centers) != 1:
        return rows
    wings = [r for r in rows if r is not centers[0]]
    return [wings[0], centers[0], wings[1]]


def order_rebuilt_lines(lines):
    """Number rebuilt forward lines by the line's points per game this season (the
    shift chart cannot know the coach's numbering; ice time is no guide, since a
    top line that takes penalties plays less)."""
    out = sorted(lines, key=lambda ln: -((ln.get("totals") or {}).get("season") or {}).get("p_pg", 0))
    for n, ln in enumerate(out, start=1):
        ln["line"] = n
    return out


def _abs_seconds(period, mmss):
    return (int(period) - 1) * 1200 + parse_toi_seconds(mmss or "0:00")


def _overlap(a, b):
    """Total seconds two players were on the ice together."""
    i = j = 0
    tot = 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if hi > lo:
            tot += hi - lo
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return tot


def group_by_shared_ice(players, shifts, size, groups):
    """Greedy grouping: start each group with the unassigned pair that shared
    the most ice, add members by shared time with the group, order groups by
    their total ice time. players: ids; shifts: {id: [(start, end), ...]}."""
    toi = {p: sum(e - s for s, e in shifts.get(p, [])) for p in players}
    left = [p for p in sorted(players, key=lambda p: -toi[p]) if toi[p] > 0]
    pair = {}
    for x in range(len(left)):
        for y in range(x + 1, len(left)):
            a, b = left[x], left[y]
            pair[(a, b)] = pair[(b, a)] = _overlap(shifts[a], shifts[b])
    out = []
    while len(left) >= size and len(out) < groups:
        best = max(((a, b) for i, a in enumerate(left) for b in left[i + 1:]),
                   key=lambda ab: pair.get(ab, 0), default=None)
        if not best:
            break
        grp = list(best)
        while len(grp) < size:
            cand = [p for p in left if p not in grp]
            if not cand:
                break
            grp.append(max(cand, key=lambda p: sum(pair.get((p, g), 0) for g in grp)))
        for p in grp:
            left.remove(p)
        out.append(grp)
    out.sort(key=lambda g: -sum(toi[p] for p in g))
    return out


async def last_game(f, team, before_date):
    """The team's most recent finished regular-season or playoff game before the date."""
    data = await f.get(f"{NHL_BASE}club-schedule-season/{team}/now") or {}
    done = [g for g in data.get("games") or []
            if g.get("gameType") in (2, 3) and (g.get("gameDate") or "") < before_date
            and g.get("gameState") in ("OFF", "FINAL")]
    return max(done, key=lambda g: g.get("gameDate") or "") if done else None


async def lines_from_shifts(f, team, before_date):
    dl = I.DailyFaceoffLines(team)
    game = await last_game(f, team, before_date)
    if not game:
        dl.error = "no game played yet"
        return dl
    gid = game["id"]
    box, sc = await asyncio.gather(f.get(f"{NHL_BASE}gamecenter/{gid}/boxscore"),
                                   f.get(f"{STATS_BASE}shiftcharts", params={"cayenneExp": f"gameId={gid}"}))
    if not box or not sc or not sc.get("data"):
        dl.error = "shift chart unavailable"
        return dl
    side = "homeTeam" if (box.get("homeTeam") or {}).get("abbrev") == team else "awayTeam"
    pbg = (box.get("playerByGameStats") or {}).get(side) or {}
    pos, names = {}, {}
    for grp in ("forwards", "defense"):
        for p in pbg.get(grp) or []:
            pos[p["playerId"]] = p.get("position") or ("D" if grp == "defense" else "C")
            names[p["playerId"]] = (p.get("name") or {}).get("default", "")
    shifts = defaultdict(list)
    for s in sc["data"]:
        if s.get("typeCode") != SHIFT_TYPE or s.get("teamAbbrev") != team or s.get("playerId") not in pos:
            continue
        a, b = _abs_seconds(s.get("period", 1), s.get("startTime")), _abs_seconds(s.get("period", 1), s.get("endTime"))
        if b > a:
            shifts[s["playerId"]].append((a, b))
        full = f"{s.get('firstName', '')} {s.get('lastName', '')}".strip()
        if full:
            names[s["playerId"]] = full
    for v in shifts.values():
        v.sort()
    fwd = [p for p in pos if pos[p] != "D"]
    dmen = [p for p in pos if pos[p] == "D"]
    order = {"LW": 0, "C": 1, "RW": 2, "D": 3}

    def row(pid):
        r = I.DailyFaceoffLines._row({"name": names.get(pid, ""), "positionIdentifier": ""})
        r["pos"] = POS_FORWARD.get(pos[pid], "D")
        r["nhl_id_hint"] = pid
        return r

    for n, grp in enumerate(group_by_shared_ice(fwd, shifts, 3, 4), start=1):
        rows = center_middle([row(p) for p in grp])
        dl.lines.append({"line": n, "players": rows, "totals": {w: dl._totals(rows, w) for w in dl.WINDOWS}})
    for n, grp in enumerate(group_by_shared_ice(dmen, shifts, 2, 3), start=1):
        rows = [row(p) for p in grp]
        dl.pairs.append({"pair": n, "players": rows, "totals": {w: dl._totals(rows, w) for w in dl.WINDOWS}})
    if not dl.lines:
        dl.error = "no forward lines in the shift chart"
        return dl
    dl.source_name = f"Last game ({game.get('gameDate')}), NHL shift chart"
    dl.source_url = f"https://www.nhl.com/gamecenter/{gid}"
    dl.updated_at = game.get("startTimeUTC")
    dl._mark_leaders()
    return dl


async def all_team_lines(f, teams, target_date, season, state_dir, use_dfo=True):
    """Lines for every team: Daily Faceoff first, shift charts as the fallback,
    then NHL game-log stats for every player. Returns ({team: dict}, status)."""
    got, info = {}, {"refused": [], "cached": {}}
    if use_dfo:
        got, info = await dfo_lines_with_cache(f, teams, state_dir)
    fallback = [t for t in teams if t not in got or got[t].error]
    if fallback:
        res = await asyncio.gather(*[lines_from_shifts(f, t, target_date) for t in fallback])
        got.update({t: dl for t, dl in zip(fallback, res)})
    win = I.NhlPlayerWindows(season, target_date, os.path.join(state_dir, "gamelog_cache.json"),
                             os.path.join(state_dir, "player_master_list.json"))
    ids = {}
    for dl in got.values():                                   # shift-chart rows already know the id
        for grp in (dl.lines, dl.pairs):
            for ln in grp:
                for r in ln["players"]:
                    if r.get("nhl_id_hint"):
                        ids[(dl.abbrev, I._norm_name(r["name"]))] = r.pop("nhl_id_hint")
    orig = win.resolve_id

    async def resolve_id(f_, name, team):
        return ids.get((team, I._norm_name(name))) or await orig(f_, name, team)
    win.resolve_id = resolve_id
    stats = await win.apply(f, [dl for dl in got.values() if not dl.error], pairs=True)
    out = {}
    drop = ("dfo_last10", "dfo_season", "last10_playoff_games")
    for t in fallback:                                        # rebuilt lines: numbered by points
        if not got[t].error:
            got[t].lines = order_rebuilt_lines(got[t].lines)
            got[t]._mark_leaders()
    for t in teams:
        dl = got[t]
        for grp in (dl.lines, dl.pairs):
            for ln in grp:
                for r in ln["players"]:
                    for k in drop:
                        r.pop(k, None)
        d = dl.as_dict()
        d["source"] = ("daily_faceoff" if (dl.url and not dl.error and not (dl.source_name or "").endswith("shift chart"))
                       else ("shift_chart" if not dl.error else "none"))
        if getattr(dl, "cached_utc", None):
            d["cached_utc"] = dl.cached_utc          # Daily Faceoff's earlier answer, read then
        out[t] = d
    status = {"teams": len(teams),
              "daily_faceoff": sum(1 for d in out.values() if d["source"] == "daily_faceoff"),
              "shift_chart": sum(1 for d in out.values() if d["source"] == "shift_chart"),
              "missing": [t for t, d in out.items() if d["source"] == "none"],
              "players": stats.get("players"), "nhl_stats": stats.get("nhl"),
              "unresolved": len(stats.get("unresolved") or []),
              "refused": len(info["refused"]), "cached": len(info["cached"]),
              "cached_utc": max(info["cached"].values()) if info["cached"] else None}
    return out, status
