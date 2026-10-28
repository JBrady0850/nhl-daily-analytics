#!/usr/bin/env python3
# =============================================================================
# NHL DAILY ANALYTICS -- engine core (public build of the RAA model V34.7)
#
# Prices every game on a slate with the five-pillar Relative Advantage
# Assessment (raa_core.py): form, availability, motivation, goaltending and
# rest. This public build keeps the predictions, goaltending, lines, results,
# learning and DFS projections; it carries no points, wagers, allocation,
# email or chat-command output.
# =============================================================================

import asyncio
import csv
import json
import logging
import math
import os
import re
import unicodedata
import datetime as dt
from collections import defaultdict

from . import raa_core as R
from . import intel as I
from .data import (
    NHL_BASE, GAME_TYPE_PLAYOFFS, GAME_TYPE_REGULAR, Fetcher, SlateDetector, TeamGameLog,
    MoneyPuckLoader, RosterResolver, StandingsResolver, PlayoffSeriesResolver,
    goalie_stats, season_string, safe_float, safe_int, parse_toi_seconds,
    next_slate, fetch_finals, fetch_market_odds,
)

MODEL_VERSION = "34.7"
SEASON_LENGTH_2627 = 84
SEASON_OPENS = "2026-09-29"
SEASON_ENDS = "2027-04-10"

# Start-time groups used only to order games (America/Chicago clock).
BLOCKS = [("CON_A", 10 * 60, 14 * 60 + 30), ("CON_B", 15 * 60, 17 * 60 + 45),
          ("CON_C", 18 * 60, 20 * 60 + 45), ("CON_D", 21 * 60, 26 * 60)]

EARLY_FORM_GAMES = 5            # until a team has played this many games its form is blended with
                                # last season (weight games/5 on this season), and its goalies' GVI
                                # windows are filled from last season's starts.
LEDGER_NAME = "results_ledger.csv"
LEDGER_LOOKBACK_DAYS = 10       # settle any unsettled slate inside this window
MASTER_LIST_NAME = "player_master_list.json"
GAMELOG_CACHE = "gamelog_cache.json"
GOALIE_POLICY = os.environ.get("NHL_GOALIE_POLICY", "probable")  # probable | strict

DK_WEIGHTS = {"goal": 6.0, "assist": 4.0, "sog": 0.9, "block": 0.5,
              "hit": 0.5, "pp_point": 0.5, "shg": 2.0}
DK_GOALIE = {"win": 6.0, "ga_penalty": -1.0, "save": 0.6, "shutout": 3.0}
DFS_POSITION_BASELINE = {"C": 6.5, "L": 6.0, "R": 6.0, "D": 5.0, "G": 7.5}

TEAMS_32 = ["ANA","BOS","BUF","CAR","CBJ","CGY","CHI","COL","DAL","DET","EDM","FLA",
            "LAK","MIN","MTL","NJD","NSH","NYI","NYR","OTT","PHI","PIT","SEA","SJS",
            "STL","TBL","TOR","UTA","VAN","VGK","WPG","WSH"]

log = logging.getLogger("nhlda.core")


def ct_from_utc(utc_str):
    """America/Chicago is UTC-5 during the NHL regular season until the first
    Sunday in November and after the second Sunday in March, UTC-6 between."""
    if not utc_str:
        return None
    try:
        u = dt.datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
    except ValueError:
        return None
    y = u.year
    dst_start = _nth_weekday(y, 3, 6, 2)      # 2nd Sunday March
    dst_end = _nth_weekday(y, 11, 6, 1)       # 1st Sunday November
    offset = -5 if dst_start <= u.date() < dst_end else -6
    return u + dt.timedelta(hours=offset)


def _nth_weekday(year, month, weekday, n):
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def block_for(ct):
    if not ct:
        return "CON_C"
    m = ct.hour * 60 + ct.minute
    if m < 10 * 60:
        m += 24 * 60
    for name, lo, hi in BLOCKS:
        if lo <= m <= hi:
            return name
    return "CON_D"


# =============================================================================
# ABSENCE DETECTION  (Pillar 2 input)
# V32 hardcoded absence_val=0.0, so Pillar 2 was inert on every team.
# A player on the active roster who did not dress in the team's most recent
# game is treated as absent, weighted by his share of team ice time and points.
# =============================================================================
async def detect_absences(f: Fetcher, abbrev, roster, mp, last_game_id, hr_injuries=None):
    dressed = None
    if last_game_id:
        box = await f.get(f"{NHL_BASE}gamecenter/{last_game_id}/boxscore")
        if box:
            dressed = set()
            for side in ("awayTeam", "homeTeam"):
                pbg = (box.get("playerByGameStats") or {}).get(side) or {}
                for grp in ("forwards", "defense", "goalies"):
                    for p in pbg.get(grp, []):
                        dressed.add(str(p.get("playerId", "")))

    if dressed is None and not hr_injuries:
        return [], 0.0

    pool = []
    for p in roster:
        row = mp.skater(p["player_id"]) or mp.goalie(p["player_id"])
        if not row:
            continue
        pool.append((p, safe_float(row.get("icetime")), safe_float(row.get("I_F_points"))))
    total_toi = sum(x[1] for x in pool) or 1.0
    total_pts = sum(x[2] for x in pool) or 1.0

    by_id = {}
    if dressed is not None:
        for p, toi, pts in pool:
            if p["player_id"] in dressed or toi <= 0:
                continue
            toi_share = toi / total_toi
            pts_share = pts / total_pts
            if toi_share < 0.015:            # fringe/depth, not a material absence
                continue
            by_id[p["player_id"]] = {"player_id": p["player_id"], "name": p["name"],
                            "position": p["position"], "toi_share": round(toi_share, 4),
                            "points_share": round(pts_share, 4), "source": "LINEUP_DIFF"}

    # V34.2: cross-check Hockey-Reference's injury table. This catches a
    # fresh injury the boxscore diff cannot see yet -- that diff only knows
    # about the last GAME played, not news since. A player the lineup diff
    # already flagged just gets the injury note attached; a player HR lists
    # who was still dressed last game (a new, in-progress injury) is added
    # fresh, at the same toi/points weighting used everywhere else.
    # The injury list may be Hockey-Reference's or, when that site cannot be
    # reached, Daily Faceoff's; its TAG names the source on each row.
    tag = getattr(hr_injuries, "TAG", "HR")
    if hr_injuries:
        for p, toi, pts in pool:
            hit = hr_injuries.match_player(abbrev, p["name"])
            if not hit:
                continue
            src = hit.get("tag") or tag
            if p["player_id"] in by_id:
                by_id[p["player_id"]]["injury_note"] = hit["injury_note"]
                by_id[p["player_id"]]["injury_type"] = hit["injury_type"]
                by_id[p["player_id"]]["source"] = f"LINEUP_DIFF+{src}_INJURY"
                continue
            if toi <= 0:
                continue
            toi_share = toi / total_toi
            pts_share = pts / total_pts
            if toi_share < 0.015:
                continue
            by_id[p["player_id"]] = {"player_id": p["player_id"], "name": p["name"],
                            "position": p["position"], "toi_share": round(toi_share, 4),
                            "points_share": round(pts_share, 4), "source": f"{src}_INJURY_REPORT",
                            "injury_note": hit["injury_note"], "injury_type": hit["injury_type"]}

    missing = sorted(by_id.values(), key=lambda m: -(m["toi_share"] + m["points_share"]))
    return missing, R.absence_impact(missing)


# =============================================================================
# GOALIE RESOLUTION  (Pillar 4 input)
# V32 picked whichever goalie had the most season games played, so
# starter_confirmed was True on every team on every slate.
# =============================================================================
def _fold_name(name):
    """A name for matching: no accents, single spaces, lower case."""
    s = unicodedata.normalize("NFKD", name or "")
    return re.sub(r"\s+", " ", "".join(c for c in s if not unicodedata.combining(c))).strip().lower()


def _prev_season(season):
    """'20262027' -> '20252026'."""
    try:
        y = int(str(season)[:4])
    except ValueError:
        return None
    return f"{y - 1}{y}"


async def resolve_goalie(f: Fetcher, abbrev, goalies, mp, season, game_type, game_date, dfo=None,
                         season_open=None, fill_prior=False):
    """Return (starter, history, days_since_start, confirmed, source, intel)."""
    candidates = []
    for g in goalies:
        row = mp.goalie(g["player_id"])
        st = goalie_stats(row) if row else {}
        logdata = await f.get(f"{NHL_BASE}player/{g['player_id']}/game-log/{season}/{game_type}")
        games = (logdata or {}).get("gameLog", []) or []
        # Strictly before the target date: the game log is returned newest-first
        # and includes games later than the slate being scored.
        games = [x for x in games if (x.get("gameDate") or "9999") < game_date]
        starts = [x for x in games if parse_toi_seconds(x.get("toi", "0:00")) > 1800]
        last_date = starts[0].get("gameDate") if starts else None
        candidates.append({"player": g, "stats": st, "games": games,
                           "starts": starts, "last_start": last_date})

    active = [c for c in candidates if c["starts"]]
    if not active:
        active = [c for c in candidates if c["stats"].get("games_played", 0) > 0]

    # V34.2: Daily Faceoff names a specific goalie for this team tonight,
    # sourced from a beat reporter's own report. If that goalie is on our
    # active list, it overrides the rotation guess -- this is exactly the
    # "direct to source" signal the rotation heuristic exists to approximate
    # when better information is not available. Falls through to rotation
    # inference on any mismatch: a call-up not yet on our roster snapshot, or
    # Daily Faceoff simply not having posted for this date.
    # Public 1.12.1: the named goalie is looked for among every goalie on the
    # roster, not only those who have played this season. A goalie traded in, or
    # back from a holdout or an injury, who starts his first game was missed and
    # the rotation guess shown instead (Hellebuyck's Devils debut, 2026-10-27).
    dfo_intel = dfo.get(abbrev) if dfo else None
    rotation = None
    source = None
    if dfo_intel and dfo_intel.get("goalie_name"):
        target = _fold_name(dfo_intel["goalie_name"])
        for c in candidates:
            if _fold_name(c["player"]["name"]) == target:
                rotation = c
                source = "DFO_CONFIRMED" if dfo_intel.get("confirmed") else "DFO_PROJECTED"
                break

    if rotation is None:
        if not active:
            return None, [], 99, False, "NONE", None
        # Probable starter: most recent start, tie-broken by season workload.
        active.sort(key=lambda c: (c["last_start"] or "0000-00-00",
                                   c["stats"].get("games_played", 0)), reverse=True)
        rotation = active[0]
        # A goalie who started the previous night rarely starts again on a B2B.
        if len(active) > 1 and rotation["last_start"]:
            try:
                gd = dt.date.fromisoformat(game_date)
                ls = dt.date.fromisoformat(rotation["last_start"])
                if (gd - ls).days <= 1:
                    rotation = active[1]
            except ValueError:
                pass
        source = "PROBABLE_ROTATION" if GOALIE_POLICY != "strict" else "UNCONFIRMED"

    # V34.4.11: early season (the team has fewer than EARLY_FORM_GAMES games).
    # With fewer than five starts this season the GVI window is filled from the
    # same goalie's most recent prior-season regular starts, so a team that has
    # not played yet is not left without a sample (or given the full no-sample
    # penalty) while its opponent is scored on one game. Later in the season, or
    # once the goalie has five starts, nothing changes.
    # A goalie Daily Faceoff names who has not started this season gets the same
    # fill at any point of the season: his own last starts are the only sample.
    window = list(rotation["starts"][:5])
    rotation["prior_starts_used"] = 0
    prev = _prev_season(season)
    named_first = source in ("DFO_CONFIRMED", "DFO_PROJECTED") and not rotation["starts"]
    if (fill_prior or named_first) and len(window) < 5 and game_type == GAME_TYPE_REGULAR and prev:
        pdata = await f.get(f"{NHL_BASE}player/{rotation['player']['player_id']}/game-log/"
                            f"{prev}/{GAME_TYPE_REGULAR}")
        pstarts = [x for x in ((pdata or {}).get("gameLog") or [])
                   if parse_toi_seconds(x.get("toi", "0:00")) > 1800]
        need = 5 - len(window)
        window += pstarts[:need]
        rotation["prior_starts_used"] = min(need, len(pstarts))

    hist = []
    for g in window:
        toi = parse_toi_seconds(g.get("toi", "0:00")) / 60.0
        sv = safe_float(g.get("savePctg"), 0.0)
        if toi > 0 and 0.0 < sv <= 1.0:
            hist.append([round(toi, 2), sv])

    days = 99
    if rotation["last_start"]:
        try:
            days = (dt.date.fromisoformat(game_date)
                    - dt.date.fromisoformat(rotation["last_start"])).days
        except ValueError:
            days = 3
    elif season_open:
        # V34.4.11: no start yet this season. The off-season is not a layoff;
        # count days since opening night, so rust applies only to a goalie who
        # has sat 10 or more days of this season.
        try:
            days = max(0, (dt.date.fromisoformat(game_date)
                           - dt.date.fromisoformat(season_open)).days)
        except ValueError:
            days = 99

    if source == "DFO_CONFIRMED":
        confirmed = True
    elif source == "DFO_PROJECTED":
        confirmed = GOALIE_POLICY != "strict"
    else:
        confirmed = GOALIE_POLICY != "strict"
    # the report goes with the goalie it is about, never with the rotation guess
    return rotation, hist, days, confirmed, source, (dfo_intel if source in ("DFO_CONFIRMED", "DFO_PROJECTED") else None)


# =============================================================================
# DFS PROJECTION
# V32 defects fixed here:
#   - p60 was I_F_primaryAssists + I_F_goals, which are SEASON TOTALS, then
#     divided by 60 as if they were a per-60 rate.
#   - secondary assists were dropped entirely.
#   - I_F_blockedShotAttempts is the player's OWN attempts that got blocked by
#     opponents, not shots he blocked. The block column is shotsBlockedByPlayer.
#   - every goalie column requested was absent from goalies.csv.
# =============================================================================
def project_skater(player, mp_row, l5_games, win_prob, game_type):
    gp = max(safe_float(mp_row.get("games_played"), 0.0), 1.0)
    ice = safe_float(mp_row.get("icetime"))
    toi_pg_min = (ice / gp) / 60.0 if ice > 0 else 14.0

    if l5_games:
        toi_l5 = sum(parse_toi_seconds(g.get("toi", "0:00")) for g in l5_games)
        if toi_l5 > 0:
            toi_pg_min = 0.6 * (toi_l5 / len(l5_games) / 60.0) + 0.4 * toi_pg_min

    goals = safe_float(mp_row.get("I_F_goals"))
    a1 = safe_float(mp_row.get("I_F_primaryAssists"))
    a2 = safe_float(mp_row.get("I_F_secondaryAssists"))
    sog = safe_float(mp_row.get("I_F_shotsOnGoal"))
    blocks = safe_float(mp_row.get("shotsBlockedByPlayer"))
    hits = safe_float(mp_row.get("I_F_hits"))
    pp_pts = safe_float(mp_row.get("I_F_points")) - safe_float(mp_row.get("I_F_points_5on5"), 0.0)

    hours = ice / 3600.0 if ice > 0 else gp * 0.25
    hours = max(hours, 0.05)
    g60, a160, a260 = goals / hours, a1 / hours, a2 / hours
    sog60, blk60, hit60 = sog / hours, blocks / hours, hits / hours

    uplift = 1.0 + ((win_prob - 0.5) * 0.30)
    share = toi_pg_min / 60.0

    p_g = g60 * share * uplift
    p_a = (a160 + a260) * share * uplift
    p_sog = sog60 * share * uplift
    p_blk = blk60 * share
    p_hit = hit60 * share
    p_pp = max(0.0, pp_pts / gp) * uplift

    dk = (p_g * DK_WEIGHTS["goal"] + p_a * DK_WEIGHTS["assist"]
          + p_sog * DK_WEIGHTS["sog"] + p_blk * DK_WEIGHTS["block"]
          + p_hit * DK_WEIGHTS["hit"] + p_pp * DK_WEIGHTS["pp_point"])
    if game_type == GAME_TYPE_PLAYOFFS:
        dk *= 0.88

    return {
        "player_id": player["player_id"], "name": player["name"],
        "team": player.get("team", ""), "position": player.get("position", "C"),
        "avg_toi_min": round(toi_pg_min, 2),
        "proj_goals": round(p_g, 3), "proj_assists": round(p_a, 3),
        "proj_points": round(p_g + p_a, 3), "proj_sog": round(p_sog, 2),
        "proj_blocks": round(p_blk, 2), "proj_hits": round(p_hit, 2),
        "proj_pp_points": round(p_pp, 3), "dk_proj": round(dk, 2),
        "win_uplift": round(uplift, 3), "salary_floor": DFS_POSITION_BASELINE.get(player.get("position"), 5.5),
    }


def project_goalie(player, mp_row, win_prob, game_type, prior_row=None):
    """A goalie with no game this season shows no season numbers (None, shown as a
    dash), and his projection uses last season's rates when there are any."""
    st = goalie_stats(mp_row) if mp_row else {}
    rate = st or (goalie_stats(prior_row) if prior_row else {})
    gp = max(rate.get("games_played", 0), 1)
    sv_pg = rate.get("saves", 0.0) / gp
    ga_pg = rate.get("goals_against", 0.0) / gp
    so_rate = 0.06 if rate.get("save_pct", 0) > 0.915 else 0.03
    dk = (win_prob * DK_GOALIE["win"] + sv_pg * DK_GOALIE["save"]
          + ga_pg * DK_GOALIE["ga_penalty"] + so_rate * DK_GOALIE["shutout"])
    if game_type == GAME_TYPE_PLAYOFFS:
        dk *= 0.92
    return {
        "player_id": player["player_id"], "name": player["name"],
        "team": player.get("team", ""), "position": "G",
        "win_prob": round(win_prob, 4), "sv_per_game": round(sv_pg, 2),
        "ga_per_game": round(ga_pg, 2), "save_pct": st.get("save_pct") if st else None,
        "gsax": st.get("gsax") if st else None, "hdsv_pct": st.get("hdsv_pct") if st else None,
        "dk_proj": round(dk, 2), "salary_floor": DFS_POSITION_BASELINE["G"],
    }


# =============================================================================
# GAME ENGINE
# =============================================================================
class GameEngine:
    def __init__(self, matchup, season, gamelog, mp, standings, series, target_date,
                 dfo=None, hr_injuries=None, season_open=None):
        self.m = matchup
        self.season_open = season_open
        self.season = season
        self.gl = gamelog
        self.mp = mp
        self.st = standings
        self.series = series
        self.date = target_date
        self.dfo = dfo                 # nhl_intel.DailyFaceoffGoalies, or None
        self.hr = hr_injuries          # nhl_intel.HockeyReferenceInjuries, or None
        self.home = matchup["home_abbrev"]
        self.away = matchup["away_abbrev"]
        self.game_type = matchup["game_type"]
        self.ct = ct_from_utc(matchup.get("start_utc"))
        self.block = block_for(self.ct)
        self.payload = {}
        self.home_raa = None
        self.away_raa = None
        self.injuries = {"home": [], "away": []}

    async def run(self, f: Fetcher):
        hr, ar = await asyncio.gather(
            RosterResolver(self.home, self.season).resolve(f),
            RosterResolver(self.away, self.season).resolve(f),
        )
        sides = {}
        for tag, ab, roster in (("home", self.home, hr), ("away", self.away, ar)):
            sides[tag] = await self._side(f, tag, ab, roster)

        hp, hrec = R.compute_raa(sides["home"]["inputs"])
        ap, arec = R.compute_raa(sides["away"]["inputs"])
        self.home_raa = hp["adj_raa"] if isinstance(hp["adj_raa"], float) else None
        self.away_raa = ap["adj_raa"] if isinstance(ap["adj_raa"], float) else None

        wp = (R.compute_win_probability(self.home_raa, self.away_raa)
              if self.home_raa is not None and self.away_raa is not None else {})
        h_wp = wp.get("home_win_prob", 0.5)
        a_wp = wp.get("away_win_prob", 0.5)

        players = {"home": [], "away": []}
        for tag, wprob in (("home", h_wp), ("away", a_wp)):
            s = sides[tag]
            for p in s["roster"].skaters:
                row = self.mp.skater(p["player_id"])
                if not row:
                    continue
                players[tag].append(project_skater(p, row, s["l5_logs"].get(p["player_id"], []),
                                                   wprob, self.game_type))
            if s["starter"]:
                gp_ = s["starter"]["player"]
                players[tag].append(project_goalie(gp_, self.mp.goalie(gp_["player_id"]),
                                                   wprob, self.game_type,
                                                   prior_row=self.mp.goalie_prior(gp_["player_id"])))
            players[tag].sort(key=lambda x: -x["dk_proj"])

        self.payload = {
            "metadata": {
                "game_id": self.m["game_id"], "game_type": self.game_type,
                "game_type_label": "PLAYOFFS" if self.game_type == GAME_TYPE_PLAYOFFS else "REGULAR",
                "date": self.date, "home": self.home, "away": self.away,
                "venue": self.m.get("venue", ""), "season": self.season,
                "ct_start": self.ct.strftime("%I:%M %p").lstrip("0") if self.ct else "",
                "block": self.block, "version": MODEL_VERSION,
                "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            },
            "series_overlay": ({"home": self.series.get(self.home),
                                "away": self.series.get(self.away)}
                               if self.game_type == GAME_TYPE_PLAYOFFS else {}),
            "standings_context": {"home": self.st.get(self.home), "away": self.st.get(self.away)},
            "home_team": {"abbrev": self.home, "raa_pillars": hp, "recovery": hrec,
                          "win_probability": wp, "form": sides["home"]["form"],
                          "goalie": sides["home"]["goalie_meta"],
                          "absences": sides["home"]["absences"],
                          "players": players["home"][:25]},
            "away_team": {"abbrev": self.away, "raa_pillars": ap, "recovery": arec,
                          "win_probability": ({"home_win_prob": a_wp, "away_win_prob": h_wp,
                                               "raw_delta": -wp.get("raw_delta", 0),
                                               "confidence_pct": wp.get("confidence_pct", 0),
                                               "confidence_tier": wp.get("confidence_tier", "LOW")}
                                              if wp else {}),
                          "form": sides["away"]["form"], "goalie": sides["away"]["goalie_meta"],
                          "absences": sides["away"]["absences"],
                          "players": players["away"][:25]},
        }
        self.injuries = {"home": sides["home"]["absences"], "away": sides["away"]["absences"]}
        return self

    async def _side(self, f: Fetcher, tag, ab, roster):
        l5 = self.gl.window(ab, 5) or {"shot_share": 0.5, "goal_share": 0.5,
                                       "goal_diff_pg": 0.0, "record": "0-0", "n": 0}
        ssn = self.gl.window(ab, None) or l5
        l3 = self.gl.window(ab, 3) or l5
        gp = self.gl.games_played(ab)
        rest = self.gl.rest_days(ab, self.date)
        opp = self.away if tag == "home" else self.home
        opp_rest = self.gl.rest_days(opp, self.date)

        # V34.4.11: early season. A one-game sample swung Pillar 1 to its +/-20
        # clamp (2026-10-01 test: CHI 17.5 after one loss). Until EARLY_FORM_GAMES
        # games, each form input is blended with last season's value, weight
        # gp/EARLY_FORM_GAMES on this season; a team with no prior row is blended
        # toward neutral. At EARLY_FORM_GAMES games and later nothing changes.
        early_w = min(1.0, gp / float(EARLY_FORM_GAMES))
        prior_form = self.mp.prior_team_form(ab) if early_w < 1.0 else None
        base_ss = prior_form["shot_share"] if prior_form else 0.5
        base_gd = prior_form["goal_diff_pg"] if prior_form else 0.0

        def _early(x, base):
            return x if early_w >= 1.0 else early_w * x + (1.0 - early_w) * base

        l5 = dict(l5, shot_share=_early(l5["shot_share"], base_ss))
        ssn = dict(ssn, shot_share=_early(ssn["shot_share"], base_ss),
                   goal_diff_pg=_early(ssn["goal_diff_pg"], base_gd))
        l3 = dict(l3, shot_share=_early(l3["shot_share"], base_ss))

        # V34.1: split-adjusted form. A home team's true form tonight is best
        # read off its home split, not a blend that includes its road games,
        # and vice versa for the away team. Shrunk toward the season rate by
        # sample size -- see TeamGameLog.venue_shot_share.
        venue = "H" if tag == "home" else "R"
        venue_share, venue_n = self.gl.venue_shot_share(ab, venue)
        venue_share = _early(venue_share, base_ss)

        hist = self.gl.by_team.get(ab, [])
        last_gid = hist[-1]["game_id"] if hist else None
        absences, absence_val = await detect_absences(f, ab, roster.skaters, self.mp, last_gid,
                                                       hr_injuries=self.hr)

        starter, ghist, gdays, gconf, gsrc, dfo_intel = await resolve_goalie(
            f, ab, roster.goalies, self.mp, self.season, self.game_type, self.date, dfo=self.dfo,
            season_open=self.season_open, fill_prior=early_w < 1.0)

        gmeta = {"source": gsrc, "confirmed": gconf}
        named = ((self.dfo or {}).get(ab) or {}).get("goalie_name")
        if named and not dfo_intel and starter and _fold_name(named) != _fold_name(starter["player"]["name"]):
            gmeta["dfo_named"] = named          # Daily Faceoff named a goalie not on our roster
        if dfo_intel:
            gmeta["intel"] = {"reported_by": dfo_intel.get("source_name"),
                              "source_url": dfo_intel.get("source_url"),
                              "detail": dfo_intel.get("detail"),
                              "reported_at": dfo_intel.get("reported_at")}
        if starter:
            grow = self.mp.goalie(starter["player"]["player_id"])
            gs = goalie_stats(grow) if grow else {}       # no game this season: no season numbers
            gmeta.update({
                "name": starter["player"]["name"], "player_id": starter["player"]["player_id"],
                "last_start": starter["last_start"], "days_rest": gdays,
                "window_starts": len(ghist),
                "window_prior_starts": starter.get("prior_starts_used", 0),
                "window_toi_min": round(sum(h[0] for h in ghist), 1),
                "season_sv_pct": gs.get("save_pct"), "gsax": gs.get("gsax"),
                "hdsv_pct": gs.get("hdsv_pct"), "gaa": gs.get("gaa"),
                "games_played": gs.get("games_played"),
            })

        # apex = top-quartile process team on the current slate
        is_apex = ssn["shot_share"] >= 0.520 and ssn["goal_diff_pg"] >= 0.25
        mp_xgf = self.mp.team_xgf(ab)

        l5_logs = {}
        for p in roster.skaters[:22]:
            d = await f.get(f"{NHL_BASE}player/{p['player_id']}/game-log/{self.season}/{self.game_type}")
            gl_ = [x for x in ((d or {}).get("gameLog") or [])
                   if (x.get("gameDate") or "9999") < self.date]
            l5_logs[p["player_id"]] = gl_[:5]

        inputs = {
            "l5_shot_share": l5["shot_share"], "ssn_shot_share": ssn["shot_share"],
            "ssn_goal_diff_pg": ssn["goal_diff_pg"],
            "l5_xgf": None, "ssn_xgf": mp_xgf,
            "venue_shot_share": venue_share, "l3_shot_share": l3["shot_share"],
            "absence_val": absence_val, "is_apex": is_apex,
            "wildcard_gap": self.st.wildcard_gap(ab),
            "is_division_leader_locked": self.st.is_locked(ab),
            "games_played": gp, "season_length": SEASON_LENGTH_2627,
            "goalie_history": ghist, "starter_confirmed": gconf,
            "days_since_last_start": gdays, "is_recall": False,
            "rest_days": rest, "opp_rest_days": opp_rest,
            "hdsv_pct": gmeta.get("hdsv_pct") or 0.0, "gsax": gmeta.get("gsax") or 0.0,
        }
        form = {"l5_shot_share": round(l5["shot_share"], 4), "l5_record": l5.get("record"),
                "ssn_shot_share": round(ssn["shot_share"], 4),
                "ssn_goal_diff_pg": round(ssn["goal_diff_pg"], 3),
                "ssn_xgf_pct": mp_xgf, "games_played": gp, "rest_days": rest,
                "is_apex": is_apex,
                "venue": "home" if venue == "H" else "road",
                "venue_shot_share": round(venue_share, 4), "venue_games": venue_n,
                "l3_shot_share": round(l3["shot_share"], 4), "l3_record": l3.get("record"),
                "l3_vs_l5_momentum": round(l3["shot_share"] - l5["shot_share"], 4),
                "early_season_weight": round(early_w, 2),
                "prior_season_form": ({"shot_share": round(prior_form["shot_share"], 4),
                                       "goal_diff_pg": round(prior_form["goal_diff_pg"], 3)}
                                      if prior_form else None)}
        return {"roster": roster, "inputs": inputs, "form": form,
                "goalie_meta": gmeta, "absences": absences,
                "starter": starter, "l5_logs": l5_logs}


# =============================================================================
# PROJECTED WINNER
# =============================================================================
def _win_side(e):
    """(winner, win_prob, confidence_tier) for one game from the logistic
    win-probability split, or None when the engine could not price it."""
    wp = e.payload["home_team"].get("win_probability", {}) or {}
    hwp = wp.get("home_win_prob")
    if hwp is None:
        return None
    if hwp >= 0.5:
        return e.home, float(hwp), wp.get("confidence_tier", "LOW")
    awp = wp.get("away_win_prob")
    return e.away, float(awp if awp is not None else 1.0 - hwp), wp.get("confidence_tier", "LOW")




# =============================================================================
# RESULTS LEDGER (public): one row per settled game, no stakes of any kind.
# The five pillar differences (home minus away, before the game) ride on each
# row so the learning cycle can refit from the ledger alone.
# =============================================================================
PILLAR_KEYS = ("p1_form", "p2_absence", "p3_motivation", "p4_gvi", "p5_friction")
LEDGER_FIELDS = ["date", "matchup", "projected", "win_prob", "tier", "gate_cleared", "actual",
                 "away_score", "home_score", "last_period", "correct", "brier", "market_prob",
                 "market_books", "settled_utc", "dp1", "dp2", "dp3", "dp4", "dp5"]


def _ledger_path(state_dir):
    return os.path.join(state_dir, LEDGER_NAME)


def read_ledger(state_dir):
    p = _ledger_path(state_dir)
    if not os.path.exists(p):
        return []
    with open(p, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _append_ledger(state_dir, rows):
    p = _ledger_path(state_dir)
    new = not os.path.exists(p)
    with open(p, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LEDGER_FIELDS, lineterminator="\n")
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in LEDGER_FIELDS})


def market_benchmark(ledger_rows):
    """The model against the consensus moneyline captured at run time, on the
    settled games that carry one: log loss for both, agreement with the market
    favorite, and the model's record when it disagreed."""
    rows = [r for r in ledger_rows if r.get("market_prob") not in ("", None)
            and r.get("win_prob") not in ("", None)]
    if not rows:
        return {"games": 0}
    ll_m = ll_k = 0.0
    agree = agree_correct = disagree = disagree_correct = 0
    for r in rows:
        p = min(max(safe_float(r["win_prob"]), 1e-6), 1 - 1e-6)
        q = min(max(safe_float(r["market_prob"]), 1e-6), 1 - 1e-6)
        c = r.get("correct") == "1"
        ll_m += -math.log(p if c else 1 - p)
        ll_k += -math.log(q if c else 1 - q)
        if q >= 0.5:
            agree += 1
            agree_correct += 1 if c else 0
        else:
            disagree += 1
            disagree_correct += 1 if c else 0
    n = len(rows)
    return {"games": n, "model_log_loss": round(ll_m / n, 4), "market_log_loss": round(ll_k / n, 4),
            "agree": agree, "agree_correct": agree_correct,
            "disagree": disagree, "disagree_correct": disagree_correct}


CALIBRATION_BINS = ((0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 1.01))


def calibration_bins(ledger_rows):
    """Hit rate by projected win probability, season to date."""
    out = []
    for lo, hi in CALIBRATION_BINS:
        rows = [r for r in ledger_rows if lo <= safe_float(r.get("win_prob")) < hi]
        n = len(rows)
        hit = sum(1 for r in rows if r.get("correct") == "1")
        out.append({"lo": lo, "hi": min(hi, 1.0), "games": n, "correct": hit,
                    "hit_rate": round(hit / n, 4) if n else None,
                    "mean_prob": round(sum(safe_float(r.get("win_prob")) for r in rows) / n, 4) if n else None})
    return out


def season_to_date(ledger_rows):
    """Accuracy and calibration over every settled row this season."""
    rows = [r for r in ledger_rows if r.get("date", "") >= SEASON_OPENS]
    n = len(rows)
    if n == 0:
        return {"games": 0}
    correct = sum(1 for r in rows if r.get("correct") == "1")
    gate = [r for r in rows if r.get("gate_cleared") == "1"]
    gate_correct = sum(1 for r in gate if r.get("correct") == "1")
    briers = [safe_float(r.get("brier")) for r in rows if r.get("brier") not in ("", None)]
    lls = []
    for r in rows:
        p = min(max(safe_float(r.get("win_prob")), 1e-6), 1 - 1e-6)
        lls.append(-math.log(p if r.get("correct") == "1" else 1 - p))
    by_tier = {}
    for t in ("HIGH", "MODERATE", "LOW"):
        tr = [r for r in rows if r.get("tier") == t]
        by_tier[t] = {"games": len(tr), "correct": sum(1 for r in tr if r.get("correct") == "1")}
    return {
        "games": n, "correct": correct, "accuracy": round(correct / n, 4),
        "gate_cleared": len(gate), "gate_correct": gate_correct,
        "gate_accuracy": round(gate_correct / len(gate), 4) if gate else None,
        "brier": round(sum(briers) / len(briers), 4) if briers else None,
        "log_loss": round(sum(lls) / len(lls), 4) if lls else None,
        "by_tier": by_tier, "market": market_benchmark(rows), "calibration": calibration_bins(rows),
        "first_date": min(r["date"] for r in rows), "last_date": max(r["date"] for r in rows),
    }


def _slate_path(state_dir, date):
    return os.path.join(state_dir, "slates", f"{date}.json")


async def settle_results(f: Fetcher, state_dir, target_date):
    """Settle every stored slate dated before target_date (inside the lookback
    window) that still has unsettled games. Never raises."""
    ledger = read_ledger(state_dir)
    done = {(r["date"], r["matchup"]) for r in ledger}
    today = dt.date.fromisoformat(target_date)
    new_rows = []
    sdir = os.path.join(state_dir, "slates")
    for name in sorted(os.listdir(sdir)) if os.path.isdir(sdir) else []:
        m = re.match(r"(\d{4}-\d{2}-\d{2})\.json$", name)
        if not m:
            continue
        d = dt.date.fromisoformat(m.group(1))
        if d >= today or (today - d).days > LEDGER_LOOKBACK_DAYS:
            continue
        try:
            slate = json.load(open(os.path.join(sdir, name), encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pending = [g for g in slate.get("games") or [] if (d.isoformat(), g.get("matchup")) not in done]
        if not pending:
            continue
        finals = await fetch_finals(f, d.isoformat())
        if not finals:
            continue
        for g in pending:
            away, home = g["matchup"].split(" @ ")
            fin = finals.get(f"{away}_vs_{home}")
            hwp = g.get("home_win_prob")
            if not fin or hwp is None:
                continue
            projected = home if hwp >= 0.5 else away
            prob = hwp if projected == home else 1 - hwp
            correct = 1 if fin["winner"] == projected else 0
            actual_home = 1.0 if fin["winner"] == home else 0.0
            mk = g.get("market") or {}
            mk_prob = mk.get("home_prob") if projected == home else mk.get("away_prob")
            hp, ap = g.get("home_pillars") or {}, g.get("away_pillars") or {}
            try:
                dps = [round(float(hp[k]) - float(ap[k]), 4) for k in PILLAR_KEYS]
            except (KeyError, TypeError, ValueError):
                dps = [""] * 5
            row = {"date": d.isoformat(), "matchup": g["matchup"], "projected": projected,
                   "win_prob": round(prob, 4), "tier": g.get("tier", ""),
                   "gate_cleared": 1 if g.get("gate_cleared") else 0, "actual": fin["winner"],
                   "away_score": fin["away_score"], "home_score": fin["home_score"],
                   "last_period": fin["last_period"], "correct": correct,
                   "brier": round((hwp - actual_home) ** 2, 4),
                   "market_prob": round(mk_prob, 4) if isinstance(mk_prob, (int, float)) else "",
                   "market_books": mk.get("books", ""),
                   "settled_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")}
            row.update({f"dp{i + 1}": v for i, v in enumerate(dps)})
            new_rows.append(row)
    if new_rows:
        _append_ledger(state_dir, new_rows)
        ledger = read_ledger(state_dir)
    by_date = defaultdict(list)
    for r in ledger:
        by_date[r["date"]].append(r)
    last_date = max(by_date) if by_date else None
    return {"newly_settled": len(new_rows), "last_settled_date": last_date,
            "last_settled_rows": by_date.get(last_date, []) if last_date else [],
            "season_to_date": season_to_date(ledger)}


# =============================================================================
# LEARNING: every LEARN_BATCH settled games the model refits its slope, home ice
# and pillar weights (raa_core.learning_cycle) and adopts the result only after
# it beats the settings in use on at least 40 unseen games by two standard
# errors of log loss.
# =============================================================================
LEARNING_NAME = "learning.json"


def _learning_path(state_dir):
    return os.path.join(state_dir, LEARNING_NAME)


def _learning_rows(state_dir):
    rows, skipped = [], 0
    for r in read_ledger(state_dir):
        if r.get("date", "") < SEASON_OPENS or not r.get("actual") or " @ " not in r.get("matchup", ""):
            skipped += 1
            continue
        try:
            d = [float(r[f"dp{i}"]) for i in range(1, 6)]
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        home = r["matchup"].split(" @ ")[1]
        rows.append((d, 1 if r["actual"] == home else 0))
    return rows, skipped


def _baseline_settings():
    b = R.LEARN_BASELINE
    return {"k": b["k"], "h": b["h"], "w": list(b["w"])}


def _load_learning(state_dir):
    try:
        st = json.load(open(_learning_path(state_dir), encoding="utf-8"))
        if st.get("current") and st.get("baseline"):
            st.setdefault("candidate", None)
            st.setdefault("candidate_from", 0)
            st.setdefault("forward", [])
            st.setdefault("history", [])
            return st
    except (OSError, ValueError):
        pass
    base = _baseline_settings()
    return {"baseline": base, "current": dict(base, w=list(base["w"])),
            "candidate": None, "candidate_from": 0, "forward": [],
            "next_at": R.LEARN_BATCH, "history": []}


def _save_learning(state_dir, st):
    tmp = _learning_path(state_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=1)
    os.replace(tmp, _learning_path(state_dir))


def _apply_learning(cur):
    R.set_learned(cur["k"], cur["h"], cur["w"])


def run_learning(state_dir):
    """Run a learning cycle when enough new games have settled, then put the
    settings in use for this run. Returns the learning block for the app."""
    st = _load_learning(state_dir)
    rows, skipped = _learning_rows(state_dir)
    n = len(rows)
    ran = False
    if n >= st.get("next_at", R.LEARN_BATCH):
        rep = R.learning_cycle(rows, st["current"], st["baseline"], st.get("candidate"),
                               st.get("candidate_from", 0), st.get("forward"))
        entry = {"cycle": len(st["history"]) + 1,
                 "utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M"),
                 "games": n, "new_games_scored": rep["new_games_scored"],
                 "accepted": rep["accepted"], "reason": rep["reason"], "forward": rep["forward"],
                 "before": rep["before"], "after": rep["current"], "tested": rep["tested"],
                 "candidate": rep["candidate"], "scores": rep["scores"]}
        st["current"] = rep["current"]
        st["candidate"] = rep["candidate"]
        st["candidate_from"] = n
        st["forward"] = rep["forward_record"]
        st["history"].append(entry)
        st["next_at"] = (n // R.LEARN_BATCH + 1) * R.LEARN_BATCH
        _save_learning(state_dir, st)
        ran = True
    _apply_learning(st["current"])
    hist = st["history"]
    last = hist[-1] if hist else None
    return {"games": n, "skipped": skipped, "next_at": st["next_at"],
            "cycles": len(hist), "accepted_cycles": sum(1 for h in hist if h.get("accepted")),
            "current": st["current"], "baseline": st["baseline"], "candidate": st.get("candidate"),
            "forward": R.forward_test(st.get("forward") or []),
            "min_forward": R.LEARN_MIN_FORWARD, "forward_t": R.LEARN_FORWARD_T,
            "last": ({k: last.get(k) for k in ("cycle", "utc", "games", "accepted", "reason")}
                     if last else None),
            "ran_now": ran}


# =============================================================================
# PLAYER MASTER LIST (name -> NHL id fallback for the lines tables)
# =============================================================================
async def refresh_master_list(f: Fetcher, season, state_dir):
    rosters = await asyncio.gather(*[RosterResolver(t, season).resolve(f) for t in TEAMS_32])
    players, seen = [], set()
    for r in rosters:
        for p in r.skaters + r.goalies:
            if not p["player_id"] or not p["name"] or p["player_id"] in seen:
                continue
            seen.add(p["player_id"])
            players.append({k: p.get(k) for k in ("team", "name", "player_id", "position", "role")})
    players.sort(key=lambda p: (p["team"], p["position"] or "", p["name"]))
    if len(players) > 300:                     # never overwrite a good list with a failed fetch
        json.dump({"season": season, "generated": dt.date.today().isoformat(),
                   "count": len(players), "players": players},
                  open(os.path.join(state_dir, MASTER_LIST_NAME), "w", encoding="utf-8"), indent=0)
    return {"count": len(players)}
