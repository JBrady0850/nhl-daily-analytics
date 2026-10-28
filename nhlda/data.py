# =============================================================================
# NHL DAILY ANALYTICS -- data layer (model V34.7)
# All network access, caching, and source normalization. Free public sources
# only; no API keys.
#
# Sources
#   NHL Web API    api-web.nhle.com/v1
#   NHL Stats API  api.nhle.com/stats/rest/en
#   MoneyPuck      moneypuck.com/moneypuck/playerData/seasonSummary
# =============================================================================

import asyncio
import csv
import io
import json
import logging
import os
import datetime as dt
from collections import defaultdict

import aiohttp

NHL_BASE = "https://api-web.nhle.com/v1/"
STATS_BASE = "https://api.nhle.com/stats/rest/en/"
MP_BASE = "https://moneypuck.com/moneypuck/playerData/seasonSummary/"

CONCURRENCY_LIMIT = 8
GAME_TYPE_PRESEASON = 1
GAME_TYPE_REGULAR = 2
GAME_TYPE_PLAYOFFS = 3

# Off by default: production runs (including the scheduled task) only ever see
# regular-season and playoff games. Preseason dates predate SEASON_OPENS, so
# TeamGameLog's current-season window is empty for them regardless of this
# flag -- pillar inputs fall back to neutral defaults (0.5 shot share, 0.0
# goal diff) rather than real rolling-form data. Set NHL_INCLUDE_PRESEASON=1
# for an explicit, one-off diagnostic run only; never enable it standing.
INCLUDE_PRESEASON = os.environ.get("NHL_INCLUDE_PRESEASON") == "1"

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nhl.com",
    "Referer": "https://www.nhl.com/",
}

log = logging.getLogger("nhl.data")



def season_string(d: dt.date) -> str:
    """NHL season id. The 2026-27 season opens 2026-09-29."""
    return f"{d.year}{d.year + 1}" if d.month >= 9 else f"{d.year - 1}{d.year}"


def safe_float(v, default=0.0):
    try:
        f = float(v)
        return default if f != f else f
    except (TypeError, ValueError):
        return default


def safe_int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def parse_toi_seconds(toi) -> int:
    if not toi or ":" not in str(toi):
        return 0
    try:
        m, s = str(toi).split(":")[:2]
        return int(m) * 60 + int(s)
    except ValueError:
        return 0


class Fetcher:
    """Shared session with retry, a semaphore, and a per-run response cache.

    V32 refetched the same play-by-play document once per player on the ice,
    producing thousands of duplicate requests per slate.
    """

    def __init__(self, session, limit=CONCURRENCY_LIMIT):
        self.session = session
        self.sem = asyncio.Semaphore(limit)
        self.cache = {}
        self.stats = {"hits": 0, "misses": 0, "errors": 0}

    async def get(self, url, params=None, as_text=False, retries=3, headers=None):
        key = (url, json.dumps(params, sort_keys=True) if params else None, as_text)
        if key in self.cache:
            self.stats["hits"] += 1
            return self.cache[key]
        async with self.sem:
            if key in self.cache:
                self.stats["hits"] += 1
                return self.cache[key]
            req_headers = headers if headers is not None else BASE_HEADERS
            for attempt in range(retries):
                try:
                    async with self.session.get(url, headers=req_headers, params=params) as r:
                        if r.status == 200:
                            data = await (r.text() if as_text else r.json(content_type=None))
                            self.cache[key] = data
                            self.stats["misses"] += 1
                            return data
                        log.warning("HTTP %s (try %s) %s", r.status, attempt + 1, url)
                except Exception as exc:  # noqa: BLE001
                    log.warning("fetch error (try %s) %s: %s", attempt + 1, url, exc)
                await asyncio.sleep(1.2 * (attempt + 1))
        self.stats["errors"] += 1
        self.cache[key] = None
        return None


# =============================================================================
# SLATE
# V32 defect: it read /v1/schedule/{date}, which returns the whole game WEEK,
# and treated all 40-plus games as "today". /v1/score/{date} is a single day.
# =============================================================================
class SlateDetector:
    def __init__(self, target_date: str, include_preseason: bool = None):
        self.target_date = target_date
        self.matchups = []
        self.game_type = GAME_TYPE_REGULAR
        self.include_preseason = (INCLUDE_PRESEASON if include_preseason is None
                                   else include_preseason)

    async def detect(self, f: Fetcher):
        data = await f.get(f"{NHL_BASE}score/{self.target_date}")
        if not data:
            log.error("slate detection failed for %s", self.target_date)
            return self
        allowed = {GAME_TYPE_REGULAR, GAME_TYPE_PLAYOFFS}
        if self.include_preseason:
            allowed.add(GAME_TYPE_PRESEASON)
        for g in data.get("games", []):
            if g.get("gameDate") and g["gameDate"] != self.target_date:
                continue
            gtype = g.get("gameType", GAME_TYPE_REGULAR)
            if gtype not in allowed:
                continue
            away, home = g.get("awayTeam", {}), g.get("homeTeam", {})
            self.matchups.append({
                "game_id": g.get("id"),
                "game_type": gtype,
                "game_state": g.get("gameState", ""),
                "start_utc": g.get("startTimeUTC"),
                "venue": (g.get("venue") or {}).get("default", ""),
                "away_abbrev": (away.get("abbrev") or "").upper(),
                "home_abbrev": (home.get("abbrev") or "").upper(),
                "away_id": away.get("id"),
                "home_id": home.get("id"),
            })
            if gtype == GAME_TYPE_PLAYOFFS:
                self.game_type = GAME_TYPE_PLAYOFFS
        label = "PLAYOFFS" if self.game_type == GAME_TYPE_PLAYOFFS else "REGULAR"
        if self.include_preseason and self.matchups and all(
                m["game_type"] == GAME_TYPE_PRESEASON for m in self.matchups):
            label = "PRESEASON (neutral-default pillar inputs, not calibrated)"
        log.info("slate %s: %d games (%s)", self.target_date, len(self.matchups), label)
        return self


# =============================================================================
# TEAM GAME LOG  (NHL Stats API, one row per team-game)
# Supplies the true L5 window that V32 never had: it assigned l5_xgf and
# season_xgf from the same single season-summary value.
# =============================================================================
class TeamGameLog:
    def __init__(self, season_start: str, through: str, game_type=GAME_TYPE_REGULAR,
                 exclude_from: str = None):
        self.season_start = season_start
        self.through = through
        # Games on or after exclude_from are dropped. Without this the target
        # date's own result leaks into form and every team reports 0 rest days.
        self.exclude_from = exclude_from
        self.game_type = game_type
        self.by_team = defaultdict(list)
        self.abbrev_by_id = {}

    async def load(self, f: Fetcher):
        rows, offset = [], 0
        while True:
            params = {
                "isAggregate": "false", "isGame": "true",
                "sort": '[{"property":"gameDate","direction":"ASC"},{"property":"teamId","direction":"ASC"}]',
                "start": str(offset), "limit": "100",
                "factCayenneExp": "gamesPlayed>=1",
                "cayenneExp": (f'gameDate>="{self.season_start}" and gameDate<="{self.through}" '
                               f'and gameTypeId={self.game_type}'),
            }
            d = await f.get(STATS_BASE + "team/summary", params=params)
            if not d:
                break
            batch = d.get("data", [])
            rows.extend(batch)
            offset += len(batch)
            if not batch or offset >= d.get("total", 0):
                break

        by_game = defaultdict(list)
        for r in rows:
            by_game[r["gameId"]].append(r)
        for pair in by_game.values():
            if len(pair) == 2:
                a, b = pair
                self.abbrev_by_id[a["teamId"]] = b["opponentTeamAbbrev"]
                self.abbrev_by_id[b["teamId"]] = a["opponentTeamAbbrev"]

        for r in rows:
            ab = self.abbrev_by_id.get(r["teamId"])
            if not ab:
                continue
            if self.exclude_from and r["gameDate"] >= self.exclude_from:
                continue
            self.by_team[ab].append({
                "date": r["gameDate"], "game_id": r["gameId"],
                "home_road": r.get("homeRoad"), "opp": r.get("opponentTeamAbbrev"),
                "sf": safe_float(r.get("shotsForPerGame")), "sa": safe_float(r.get("shotsAgainstPerGame")),
                "gf": safe_int(r.get("goalsFor")), "ga": safe_int(r.get("goalsAgainst")),
                "pp": safe_float(r.get("powerPlayPct")), "pk": safe_float(r.get("penaltyKillPct")),
                "pts": safe_int(r.get("points")), "win": safe_int(r.get("wins")),
            })
        for ab in self.by_team:
            self.by_team[ab].sort(key=lambda x: (x["date"], x["game_id"]))
        log.info("team game log: %d teams, %d team-games", len(self.by_team), len(rows))
        return self

    def window(self, abbrev, n=5):
        h = self.by_team.get(abbrev.upper(), [])
        if not h:
            return None
        w = h[-n:] if n else h
        sf = sum(x["sf"] for x in w) or 1.0
        sa = sum(x["sa"] for x in w)
        gf = sum(x["gf"] for x in w)
        ga = sum(x["ga"] for x in w)
        k = len(w)
        return {
            "shot_share": sf / (sf + sa) if (sf + sa) else 0.5,
            "goal_share": gf / (gf + ga) if (gf + ga) else 0.5,
            "gf_pg": gf / k, "ga_pg": ga / k,
            "goal_diff_pg": (gf - ga) / k,
            "pp": sum(x["pp"] for x in w) / k, "pk": sum(x["pk"] for x in w) / k,
            "pt_pct": sum(x["pts"] for x in w) / (2.0 * k),
            "record": f"{sum(x['win'] for x in w)}-{k - sum(x['win'] for x in w)}",
            "n": k,
        }

    def venue_shot_share(self, abbrev, venue, n=None, k=8, fallback=0.5):
        """This team's shot share in games at ONE venue ('H' or 'R'), shrunk
        toward its own season-blended shot share by venue sample size (an
        empirical-Bayes shrinkage with an 8-game half-life). A team's true
        home/road split is real -- travel, crowd, last change -- but on
        20-40 games a season it is noisy, so an early-season 2-0 home start
        should not swing the score the way a 20-game home record would.
        Returns (shrunk_share, raw_games_at_venue)."""
        h = self.by_team.get(abbrev.upper(), [])
        pool = [x for x in h if x.get("home_road") == venue]
        ssn = self.window(abbrev, None)
        prior = ssn["shot_share"] if ssn else fallback
        if not pool:
            return prior, 0
        w = pool[-n:] if n else pool
        sf = sum(x["sf"] for x in w) or 0.0
        sa = sum(x["sa"] for x in w)
        raw = sf / (sf + sa) if (sf + sa) else prior
        weight = len(w) / (len(w) + k)
        return weight * raw + (1 - weight) * prior, len(w)

    def games_played(self, abbrev):
        return len(self.by_team.get(abbrev.upper(), []))

    def rest_days(self, abbrev, game_date: str, default=3):
        h = self.by_team.get(abbrev.upper(), [])
        if not h:
            return default
        try:
            gd = dt.date.fromisoformat(game_date)
            last = dt.date.fromisoformat(h[-1]["date"])
            return max(0, min(5, (gd - last).days))
        except ValueError:
            return default


# =============================================================================
# MONEYPUCK
# V32 defect: rows were keyed by playerId / team with no situation filter, and
# MoneyPuck ships five rows per entity ordered other, all, 5on5, 4on5, 5on4.
# The last row won, so every team's xGF% came off its POWER PLAY split (TOR
# read 0.88 instead of 0.46) and every skater's totals came off 5on4.
# =============================================================================
# V34.4.11: MoneyPuck publishes the new season's files after opening night
# with only the teams and players that have played. Taking that season
# wholesale (V34.0 to V34.4.10) left every team that had not played with no
# goalie, no skaters and no xGF, so its games could not be priced (DATA
# FAULT, 2026-09-30). Both seasons are now loaded. While the season is young
# (no team has MP_MIN_GP games in the current file), each row is chosen per
# team or player: the current season once it covers MP_MIN_GP games, the prior
# season before that, the current row if there is no prior one. After that the
# current season is used exactly as before V34.4.11.
MP_MIN_GP = 5     # V34.5.4 (2026-10-02): this season's rows from 5 games (was 10)


class MoneyPuckLoader:
    def __init__(self, season_year: int, game_type=GAME_TYPE_REGULAR, situation="all"):
        self.season_year = season_year
        self.situation = situation
        self.label = "playoffs" if game_type == GAME_TYPE_PLAYOFFS else "regular"
        self.skaters, self.goalies, self.teams = {}, {}, {}
        self.prior_skaters, self.prior_goalies, self.prior_teams = {}, {}, {}
        self.loaded_season = None
        self.prior_season = None
        self.early = True

    async def load(self, f: Fetcher):
        cur, prev = self.season_year, self.season_year - 1
        cb, pb = f"{MP_BASE}{cur}/{self.label}/", f"{MP_BASE}{prev}/{self.label}/"
        await asyncio.gather(
            self._csv(f, cb + "skaters.csv", self.skaters, "playerId"),
            self._csv(f, cb + "goalies.csv", self.goalies, "playerId"),
            self._csv(f, cb + "teams.csv", self.teams, "team"),
            self._csv(f, pb + "skaters.csv", self.prior_skaters, "playerId"),
            self._csv(f, pb + "goalies.csv", self.prior_goalies, "playerId"),
            self._csv(f, pb + "teams.csv", self.prior_teams, "team"),
        )
        if self.teams:
            self.loaded_season = cur
        elif self.prior_teams:
            self.loaded_season = prev
            log.warning("MoneyPuck %s not published; using %s", cur, prev)
        if self.prior_teams:
            self.prior_season = prev
        cur_max = max((safe_int(r.get("games_played")) for r in self.teams.values()), default=0)
        self.early = not self.teams or cur_max < MP_MIN_GP
        log.info("MoneyPuck %s (%s): skaters=%d goalies=%d teams=%d; prior %s: skaters=%d "
                 "goalies=%d teams=%d; current rows used from %d games",
                 cur, self.situation, len(self.skaters), len(self.goalies), len(self.teams),
                 prev, len(self.prior_skaters), len(self.prior_goalies), len(self.prior_teams),
                 MP_MIN_GP)
        return self

    async def _csv(self, f: Fetcher, url, target, key_col):
        raw = await f.get(url, as_text=True)
        if not raw or raw.lstrip().startswith("<"):
            return
        for row in csv.DictReader(io.StringIO(raw)):
            if (row.get("situation") or "").strip() != self.situation:
                continue
            key = (row.get(key_col) or "").strip().upper()
            if key:
                target[key] = row

    def _pick(self, current, prior, key):
        key = str(key).upper()
        row = current.get(key)
        if not self.early and current:
            return row or {}
        if row and safe_int(row.get("games_played")) >= MP_MIN_GP:
            return row
        return prior.get(key) or row or {}

    def skater(self, pid):
        return self._pick(self.skaters, self.prior_skaters, pid)

    def goalie(self, pid):
        return self._pick(self.goalies, self.prior_goalies, pid)

    def goalie_prior(self, pid):
        """Last season's row for a goalie, or {}."""
        return self.prior_goalies.get(str(pid).upper()) or {}

    def team(self, ab):
        return self._pick(self.teams, self.prior_teams, ab)

    def prior_team_form(self, ab):
        """Last season's shot share and goal differential per game for a team,
        on the same definitions as TeamGameLog.window(); None without a row."""
        r = self.prior_teams.get(str(ab).upper())
        if not r:
            return None
        sf, sa = safe_float(r.get("shotsOnGoalFor")), safe_float(r.get("shotsOnGoalAgainst"))
        gf, ga = safe_float(r.get("goalsFor")), safe_float(r.get("goalsAgainst"))
        gp = safe_int(r.get("games_played"))
        if not gp or not (sf + sa):
            return None
        return {"shot_share": sf / (sf + sa), "goal_diff_pg": (gf - ga) / gp, "games": gp}

    def rows_from_prior(self):
        """How many teams are being read from the prior season right now."""
        if not self.early and self.teams:
            return 0
        keys = set(self.teams) | set(self.prior_teams)
        return sum(1 for k in keys if self.team(k) is self.prior_teams.get(k))

    def team_xgf(self, ab):
        v = safe_float(self.team(ab).get("xGoalsPercentage"), 0.0)
        if v <= 0:
            return None
        return v / 100.0 if v > 1.0 else v


# --- goalie stat derivation --------------------------------------------------
# V32 asked goalies.csv for savedShotsOnGoal, goalsAgainst, shutouts,
# highDangerSavePercentage and goals_saved_above_expected. None of those
# columns exist, so every goalie metric in the engine resolved to 0.0.
# Real columns: ongoal, goals, xGoals, highDangerShots, highDangerGoals.
def goalie_stats(mp_row):
    ongoal = safe_float(mp_row.get("ongoal"))
    goals = safe_float(mp_row.get("goals"))
    xg = safe_float(mp_row.get("xGoals"))
    hds = safe_float(mp_row.get("highDangerShots"))
    hdg = safe_float(mp_row.get("highDangerGoals"))
    ice = safe_float(mp_row.get("icetime"))
    return {
        "games_played": safe_int(mp_row.get("games_played")),
        "toi_minutes": round(ice / 60.0, 1),
        "shots_against": ongoal,
        "goals_against": goals,
        "saves": max(0.0, ongoal - goals),
        "save_pct": round((ongoal - goals) / ongoal, 4) if ongoal > 0 else 0.0,
        "gsax": round(xg - goals, 2),
        "hdsv_pct": round((hds - hdg) / hds, 4) if hds > 0 else 0.0,
        "gaa": round(goals / (ice / 3600.0), 2) if ice > 0 else 0.0,
    }


# =============================================================================
# ROSTERS + STANDINGS
# =============================================================================
class RosterResolver:
    def __init__(self, abbrev, season):
        self.abbrev = abbrev.upper()
        self.season = season
        self.skaters, self.goalies = [], []

    async def resolve(self, f: Fetcher):
        d = await f.get(f"{NHL_BASE}roster/{self.abbrev}/{self.season}")
        if not d:
            log.warning("roster unavailable %s %s", self.abbrev, self.season)
            return self
        for key in ("forwards", "defensemen"):
            for p in d.get(key, []):
                self.skaters.append(self._norm(p, "skater"))
        for p in d.get("goalies", []):
            self.goalies.append(self._norm(p, "goalie"))
        return self

    def _norm(self, p, role):
        return {
            "player_id": str(p.get("id", "")),
            "name": f"{(p.get('firstName') or {}).get('default','')} "
                    f"{(p.get('lastName') or {}).get('default','')}".strip(),
            "position": p.get("positionCode", ""),
            "jersey": p.get("sweaterNumber"),
            "role": role,
            "team": self.abbrev,
            "shoots_catches": p.get("shootsCatches", ""),
            "birth_date": p.get("birthDate", ""),
        }


class StandingsResolver:
    """V32 defect: it sorted a conference by points and read index 7 as the
    wildcard cutline, so every team at or above the line got gap 0 and the full
    +5.0 motivation modifier. The API publishes wildcardSequence directly."""

    def __init__(self):
        self.rows = {}

    async def resolve(self, f: Fetcher):
        d = await f.get(f"{NHL_BASE}standings/now")
        if not d:
            return self
        conf = defaultdict(list)
        for t in d.get("standings", []):
            ab = (t.get("teamAbbrev") or {}).get("default", "").upper()
            if not ab:
                continue
            rec = {
                "abbrev": ab, "points": safe_int(t.get("points")),
                "games_played": safe_int(t.get("gamesPlayed")),
                "point_pct": safe_float(t.get("pointPctg")),
                "conference": t.get("conferenceName", ""),
                "division": t.get("divisionName", ""),
                "division_seq": safe_int(t.get("divisionSequence"), 99),
                "wildcard_seq": safe_int(t.get("wildcardSequence"), 99),
                "clinch": t.get("clinchIndicator", ""),
                "streak": f"{t.get('streakCode','')}{t.get('streakCount','')}",
                "record": f"{safe_int(t.get('wins'))}-{safe_int(t.get('losses'))}-{safe_int(t.get('otLosses'))}",
            }
            self.rows[ab] = rec
            conf[rec["conference"]].append(rec)

        # The cutline is the last team holding a playoff berth: division top-3
        # (wildcard_seq 0) plus the top two wildcards (wildcard_seq 1 and 2).
        for teams in conf.values():
            inside = [t for t in teams if t["wildcard_seq"] in (0, 1, 2)]
            cut = min((t["points"] for t in inside), default=0)
            for t in teams:
                t["wildcard_gap"] = (0 if t["wildcard_seq"] in (0, 1, 2)
                                     else max(0, cut - t["points"]))
        return self

    def get(self, ab):
        return self.rows.get(str(ab).upper(), {})

    def wildcard_gap(self, ab):
        return self.get(ab).get("wildcard_gap")

    def is_locked(self, ab):
        """A division leader is 'locked' only once a berth is clinched, not
        merely by sitting first in October."""
        r = self.get(ab)
        return r.get("division_seq") == 1 and r.get("clinch", "") in ("x", "y", "z", "p")


class PlayoffSeriesResolver:
    def __init__(self, season):
        self.season = season
        self.series = {}

    async def resolve(self, f: Fetcher):
        d = await f.get(f"{NHL_BASE}playoffs/carousel/{self.season}")
        if not d:
            return self
        for s in d.get("series", []):
            top = (s.get("topSeedTeam") or {}).get("abbrev", "").upper()
            bot = (s.get("bottomSeedTeam") or {}).get("abbrev", "").upper()
            tw, bw = safe_int(s.get("topSeedWins")), safe_int(s.get("bottomSeedWins"))
            for ab, w, ow, is_top in ((top, tw, bw, True), (bot, bw, tw, False)):
                if not ab:
                    continue
                self.series[ab] = {
                    "round": safe_int(s.get("playoffRound"), 1),
                    "series_letter": s.get("seriesLetter", ""),
                    "team_wins": w, "opponent_wins": ow, "games_played": w + ow,
                    "home_ice": is_top, "elimination_game": ow == 3,
                    "clinch_opportunity": w == 3, "series_tied": w == ow,
                    "opponent_abbrev": bot if is_top else top,
                }
        return self

    def get(self, ab):
        return self.series.get(str(ab).upper(), {})


# =============================================================================
# NEXT SLATE AND FINAL SCORES (V34.3)
# Two small readers on the same NHL web API the SlateDetector already uses.
# next_slate() feeds the no-games notice; fetch_finals() feeds the
# results ledger. Both are best-effort: a None return means "not available",
# never an exception that could take down the build.
# =============================================================================
async def next_slate(f: Fetcher, from_date: str, allowed=None, max_weeks=6):
    """First date strictly after from_date with at least one game whose
    gameType is in `allowed` (regular season and playoffs by default).
    Returns {"date", "game_count", "matchups": ["AWY @ HOM", ...]} or None."""
    allowed = allowed or {GAME_TYPE_REGULAR, GAME_TYPE_PLAYOFFS}
    cursor = from_date
    for _ in range(max_weeks):
        data = await f.get(f"{NHL_BASE}schedule/{cursor}")
        if not data:
            return None
        for day in data.get("gameWeek", []):
            if day.get("date", "") <= from_date:
                continue
            games = [g for g in day.get("games", []) if g.get("gameType") in allowed]
            if games:
                return {"date": day["date"], "game_count": len(games),
                        "matchups": [f"{(g.get('awayTeam') or {}).get('abbrev','?')} @ "
                                     f"{(g.get('homeTeam') or {}).get('abbrev','?')}" for g in games]}
        nxt = data.get("nextStartDate")
        if not nxt or nxt <= cursor:
            return None
        cursor = nxt
    return None


def _odds_to_prob(value, provider_format):
    """Implied probability (with the book's margin still in) from one price.
    American when the provider lists American odds, else decimal."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if provider_format == "american":
        if v == 0:
            return None
        return 100.0 / (v + 100.0) if v > 0 else -v / (-v + 100.0)
    return 1.0 / v if v > 1.0 else None


def _provider_format(value):
    """The NHL score feed mixes decimal books (1.83, 2.40) and American books
    (-125, +110). Anything with an absolute value of 100 or more, or a sign, is
    American; a positive number under 100 is decimal."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return "american" if abs(v) >= 100 or str(value).strip().startswith(("+", "-")) else "decimal"


async def fetch_market_odds(f: Fetcher, date: str):
    """V34.6 market benchmark. The NHL score feed for the date carries each
    team's moneyline from several sportsbooks. Per book the two implied
    probabilities are normalized to remove the margin; the consensus is the
    median across books. Keyed "AWY_vs_HOM": {"home_prob", "away_prob",
    "books", "captured_utc", "sources"}. None when the feed has no odds."""
    data = await f.get(f"{NHL_BASE}score/{date}")
    if not data:
        return None
    names = {p.get("partnerId", p.get("providerId")): p.get("name")
             for p in data.get("oddsPartners") or [] if isinstance(p, dict)}
    out = {}
    captured = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    for g in data.get("games", []):
        if g.get("gameDate") and g["gameDate"] != date:
            continue
        away, home = g.get("awayTeam") or {}, g.get("homeTeam") or {}
        a, h = (away.get("abbrev") or "").upper(), (home.get("abbrev") or "").upper()
        ao = {o.get("providerId"): o.get("value") for o in away.get("odds") or [] if isinstance(o, dict)}
        ho = {o.get("providerId"): o.get("value") for o in home.get("odds") or [] if isinstance(o, dict)}
        probs, books = [], []
        for pid in ho:
            if pid not in ao:
                continue
            fmt = _provider_format(ho[pid])
            ph, pa = _odds_to_prob(ho[pid], fmt), _odds_to_prob(ao[pid], fmt)
            if not ph or not pa or ph + pa <= 0:
                continue
            probs.append(ph / (ph + pa))
            books.append(names.get(pid) or str(pid))
        if not a or not h or not probs:
            continue
        probs.sort()
        mid = len(probs) // 2
        hp = probs[mid] if len(probs) % 2 else (probs[mid - 1] + probs[mid]) / 2.0
        out[f"{a}_vs_{h}"] = {"home_prob": round(hp, 4), "away_prob": round(1.0 - hp, 4),
                              "books": len(probs), "sources": books, "captured_utc": captured}
    return out or None


async def fetch_finals(f: Fetcher, date: str):
    """Final scores for one date keyed "AWY_vs_HOM". Only games the league has
    closed (gameState OFF or FINAL) are returned; an in-progress or postponed
    game is left out so the caller settles it on a later day."""
    data = await f.get(f"{NHL_BASE}score/{date}")
    if not data:
        return None
    out = {}
    for g in data.get("games", []):
        if g.get("gameDate") and g["gameDate"] != date:
            continue
        if g.get("gameState") not in ("OFF", "FINAL"):
            continue
        away, home = g.get("awayTeam") or {}, g.get("homeTeam") or {}
        a, h = (away.get("abbrev") or "").upper(), (home.get("abbrev") or "").upper()
        if not a or not h or away.get("score") is None or home.get("score") is None:
            continue
        out[f"{a}_vs_{h}"] = {
            "away": a, "home": h,
            "away_score": safe_int(away.get("score")), "home_score": safe_int(home.get("score")),
            "winner": h if safe_int(home.get("score")) > safe_int(away.get("score")) else a,
            "last_period": ((g.get("gameOutcome") or {}).get("lastPeriodType") or "REG"),
            "game_type": g.get("gameType"),
        }
    return out
