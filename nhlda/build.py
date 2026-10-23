"""Daily build: price the slate, settle results, run the learning cycle, build
lines for all 32 teams, and write the app's data file.

Outputs
  <out>/latest.json   the public data file (format nhl-daily-analytics/1)
  <out>/latest.js     the same data as `window.NDA_DATA = {...}` so the app also
                      works when opened straight from a downloaded folder
  <state>/...         results ledger, learning state, one compact file per
                      slate (used to settle results the next day), player list
Free public sources only (NHL web and stats APIs, MoneyPuck, Daily Faceoff,
Hockey-Reference). No keys, no accounts, nothing personal.
"""
import asyncio
import datetime as dt
import json
import logging
import os

import aiohttp

from . import core as C
from . import intel as I
from . import raa_core as R
from .data import (Fetcher, SlateDetector, TeamGameLog, MoneyPuckLoader, StandingsResolver,
                   PlayoffSeriesResolver, GAME_TYPE_PLAYOFFS, season_string, next_slate,
                   fetch_market_odds)
from .lines import all_team_lines

FORMAT = "nhl-daily-analytics/1"
APP_DATA_VERSION = 1
log = logging.getLogger("nhlda.build")

# Keys that must never reach the public file (checked by `python -m nhlda check`):
# anything about stakes, wagers, mail or chat commands. Hockey points (goals plus
# assists) are fine and appear throughout.
FORBIDDEN_KEYS = {"allocation", "balance", "base_net", "discord", "commands", "bet", "bets",
                  "playable", "reserve", "pool", "stake", "staked", "wager", "funded",
                  "email", "recipient", "gmail", "authorized", "points_staked"}
FORBIDDEN_TEXT = ("!bet", "@gmail.", "discord")


def _r(x, n=4):
    return round(x, n) if isinstance(x, (int, float)) else x


def _pillars(p):
    return {k: _r(p.get(k), 3) for k in ("p1_form", "p2_absence", "p3_motivation", "p4_gvi",
                                         "p5_friction", "adj_raa") if k in p}


def _goalie(g):
    keep = ("name", "player_id", "source", "confirmed", "last_start", "days_rest", "window_starts",
            "window_toi_min", "season_sv_pct", "gsax", "hdsv_pct", "gaa", "games_played")
    out = {k: g.get(k) for k in keep if k in g}
    intel = g.get("intel") or {}
    if intel:
        out["reported_by"] = intel.get("reported_by")
        out["source_url"] = intel.get("source_url")
    return out


def _absence(a):
    return {k: a.get(k) for k in ("player_id", "name", "position", "toi_share", "points_share", "source", "injury_note")
            if k in a}


async def team_names(f):
    d = await f.get("https://api-web.nhle.com/v1/standings/now") or {}
    out = {}
    for t in d.get("standings") or []:
        ab = (t.get("teamAbbrev") or {}).get("default")
        if ab:
            out[ab] = {"name": (t.get("teamName") or {}).get("default", ab),
                       "conference": t.get("conferenceName"), "division": t.get("divisionName"),
                       "record": f"{t.get('wins', 0)}-{t.get('losses', 0)}-{t.get('otLosses', 0)}",
                       "pts": t.get("points"), "games_played": t.get("gamesPlayed")}
    return out


async def build(target_date, state_dir, out_dir, teams=None, use_dfo=True):
    os.makedirs(os.path.join(state_dir, "slates"), exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)
    teams = teams or C.TEAMS_32
    d = dt.date.fromisoformat(target_date)
    season = season_string(d)
    season_open = C.SEASON_OPENS if season == "20262027" else f"{d.year if d.month >= 9 else d.year - 1}-09-15"
    started = dt.datetime.now(dt.timezone.utc)
    problems = []

    timeout = aiohttp.ClientTimeout(total=180, connect=30)
    async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(limit=12)) as session:
        f = Fetcher(session)
        try:
            results = await C.settle_results(f, state_dir, target_date)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"results: {exc}")
            results = {"season_to_date": C.season_to_date(C.read_ledger(state_dir))}
        try:
            learning = C.run_learning(state_dir)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"learning: {exc}")
            learning = {"error": str(exc)[:160]}
            C._apply_learning(C._baseline_settings())
        try:
            await C.refresh_master_list(f, season, state_dir)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"player list: {exc}")
        names = await team_names(f)
        try:
            upcoming = await next_slate(f, target_date)
        except Exception:  # noqa: BLE001
            upcoming = None

        slate = await SlateDetector(target_date).detect(f)
        games, top_sk, top_g, gate, intel_status = [], [], [], {}, {}
        if slate.matchups:
            gl, mp, st = await asyncio.gather(
                TeamGameLog(season_open, target_date, slate.game_type, exclude_from=target_date).load(f),
                MoneyPuckLoader(int(season[:4]), slate.game_type, situation="all").load(f),
                StandingsResolver().resolve(f))
            series = PlayoffSeriesResolver(season)
            if slate.game_type == GAME_TYPE_PLAYOFFS:
                await series.resolve(f)
            dfo = hr = None
            if use_dfo:
                try:
                    dfo = await I.DailyFaceoffGoalies().resolve(f, target_date)
                except Exception as exc:  # noqa: BLE001
                    problems.append(f"goalies: {exc}")
            try:
                hr = await I.HockeyReferenceInjuries().resolve(f, as_of=target_date)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"injuries: {exc}")
            inj_source = "hockey_reference" if hr and hr.loaded else "none"
            if inj_source == "none":
                # Hockey-Reference refuses GitHub's servers: fall back to the injured
                # lists on Daily Faceoff's team pages, and say so in the app
                alt = None
                if use_dfo:
                    slate_teams = sorted({t for m in slate.matchups for t in (m["away_abbrev"], m["home_abbrev"]) if t})
                    try:
                        alt = await I.DailyFaceoffInjuries().resolve(f, slate_teams)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Daily Faceoff injuries: %s", exc)
                if alt and alt.loaded:
                    hr, inj_source = alt, "daily_faceoff"
                    problems.append("Hockey-Reference's injury report could not be reached this run, so injuries "
                                    "come from the injured lists on Daily Faceoff's team pages.")
                else:
                    problems.append("No injury report could be reached this run, so missing players come only "
                                    "from each team's last lineup.")
            try:
                market = await fetch_market_odds(f, target_date) or {}
            except Exception:  # noqa: BLE001
                market = {}
            intel_status = {
                "starting_goalies": {"loaded": bool(dfo and dfo.loaded),
                                     "teams": len(dfo.by_team) if dfo else 0,
                                     "confirmed": sum(1 for v in dfo.by_team.values() if v.get("confirmed")) if dfo else 0},
                "injuries": {"loaded": bool(hr and hr.loaded), "source": inj_source,
                             "rows": sum(len(v) for v in hr.by_team.values()) if hr else 0},
                "market_games": len(market), "moneypuck_season": mp.loaded_season,
            }
            engines = [C.GameEngine(m, season, gl, mp, st, series, target_date, dfo=dfo, hr_injuries=hr,
                                    season_open=season_open) for m in slate.matchups]
            for i in range(0, len(engines), 3):
                await asyncio.gather(*[e.run(f) for e in engines[i:i + 3]])
            deltas = {f"{e.away}_vs_{e.home}": (abs((e.home_raa + R.HOME_ADVANTAGE) - e.away_raa)
                                                 if e.home_raa is not None and e.away_raa is not None else None)
                      for e in engines}
            gate = R.compute_delta_gate(deltas)
            for e in engines:
                key = f"{e.away}_vs_{e.home}"
                wp = e.payload["home_team"].get("win_probability", {}) or {}
                hwp = wp.get("home_win_prob")
                mk = market.get(key) or {}
                g = {"game_id": e.m.get("game_id"), "matchup": f"{e.away} @ {e.home}",
                     "away": e.away, "home": e.home, "start_utc": e.m.get("start_utc"),
                     "venue": e.m.get("venue"), "state": e.m.get("game_state"),
                     "home_win_prob": _r(hwp), "away_win_prob": _r(wp.get("away_win_prob")),
                     "tier": wp.get("confidence_tier"), "confidence_pct": wp.get("confidence_pct"),
                     "delta": _r(deltas[key], 2),
                     "gate_cleared": bool((gate.get("matchups", {}).get(key) or {}).get("authorized")),
                     "market": ({"home_prob": _r(mk.get("home_prob")), "away_prob": _r(mk.get("away_prob")),
                                 "books": mk.get("books")} if mk else None)}
                if hwp is not None:
                    g["projected"] = e.home if hwp >= 0.5 else e.away
                    g["win_prob"] = _r(hwp if hwp >= 0.5 else 1 - hwp)
                for side in ("home", "away"):
                    t = e.payload[f"{side}_team"]
                    g[side + "_side"] = {"raa": _r(t["raa_pillars"].get("adj_raa"), 2),
                                         "pillars": _pillars(t["raa_pillars"]),
                                         "form": t.get("form"), "goalie": _goalie(t.get("goalie") or {}),
                                         "absences": [_absence(a) for a in (t.get("absences") or [])[:5]]}
                games.append(g)
            games.sort(key=lambda g: -(g.get("win_prob") or 0))
            for k, g in enumerate(games, start=1):
                g["rank"] = k
            pool = [p for e in engines for side in ("home_team", "away_team") for p in e.payload[side]["players"][:10]]
            opp = {e.home: e.away for e in engines} | {e.away: e.home for e in engines}
            keep = ("player_id", "name", "team", "position", "avg_toi_min", "proj_goals", "proj_assists", "proj_points",
                    "proj_sog", "proj_blocks", "proj_hits", "dk_proj", "win_prob", "save_pct", "gsax")
            top_sk = [dict({k: p.get(k) for k in keep if k in p}, opp=opp.get(p.get("team")))
                      for p in sorted((p for p in pool if p["position"] != "G"), key=lambda p: -p["dk_proj"])[:30]]
            top_g = [dict({k: p.get(k) for k in keep if k in p}, opp=opp.get(p.get("team")))
                     for p in sorted((p for p in pool if p["position"] == "G"), key=lambda p: -p["dk_proj"])[:12]]
            # compact copy for settling tomorrow
            json.dump({"date": target_date, "games": [
                {"matchup": g["matchup"], "home_win_prob": g["home_win_prob"], "tier": g["tier"],
                 "gate_cleared": g["gate_cleared"], "market": g["market"],
                 "home_pillars": g["home_side"]["pillars"], "away_pillars": g["away_side"]["pillars"]}
                for g in games if g.get("home_win_prob") is not None]},
                open(os.path.join(state_dir, "slates", f"{target_date}.json"), "w", encoding="utf-8"), indent=1)

        try:
            lines, lines_status = await all_team_lines(f, teams, target_date, season, state_dir, use_dfo=use_dfo)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"lines: {exc}")
            lines, lines_status = {}, {"error": str(exc)[:160]}

        std = results.get("season_to_date") or {}
        last_rows = [{k: r.get(k) for k in ("date", "matchup", "projected", "win_prob", "tier", "actual",
                                            "away_score", "home_score", "last_period", "correct", "market_prob")}
                     for r in results.get("last_settled_rows") or []]
        out = {
            "format": FORMAT, "data_version": APP_DATA_VERSION,
            "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "date": target_date, "season": season, "model_version": C.MODEL_VERSION,
            "teams": names,
            "slate": {"game_count": len(games),
                      "game_type": "PLAYOFFS" if slate.game_type == GAME_TYPE_PLAYOFFS else "REGULAR",
                      "games": games,
                      "gate": {"floor": R.GATE_FLOOR, "dynamic_gate": gate.get("dynamic_gate"),
                               "cleared": sum(1 for g in games if g.get("gate_cleared"))},
                      "next": upcoming},
            "lines": lines,
            "results": {"last_date": results.get("last_settled_date"), "last_games": last_rows,
                        "season": std},
            "model": {"settings_in_use": learning.get("current"), "baseline": learning.get("baseline"),
                      "learning": {k: learning.get(k) for k in ("games", "next_at", "cycles", "accepted_cycles",
                                                                "forward", "min_forward", "forward_t", "last")},
                      "calibration": std.get("calibration")},
            "dfs": {"skaters": top_sk, "goalies": top_g},
            "sources": dict(intel_status, lines=lines_status, requests=f.stats,
                            seconds=round((dt.datetime.now(dt.timezone.utc) - started).total_seconds())),
            "problems": problems,
        }
    check_public(out)
    text = json.dumps(out, separators=(",", ":"), ensure_ascii=True)
    for name, body in (("latest.json", text), ("latest.js", "window.NDA_DATA=" + text + ";\n")):
        tmp = os.path.join(out_dir, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.replace(tmp, os.path.join(out_dir, name))
    return out


def check_public(obj, path="$"):
    """Raise if a forbidden key (stakes, wagers, mail, chat commands) is present."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in FORBIDDEN_KEYS:
                raise ValueError(f"forbidden key in public data: {path}.{k}")
            check_public(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            check_public(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        low = obj.lower()
        for t in FORBIDDEN_TEXT:
            if t in low:
                raise ValueError(f"forbidden text in public data at {path}: {t}")
