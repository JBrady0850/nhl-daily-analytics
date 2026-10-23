# =============================================================================
# NHL RAA - INTEL LAYER V34.5.5.0
#
# Direct-to-source goalie confirmation and injury status, added in place of a
# raw social-media poll. Two structured, low-maintenance sources:
#
#   Daily Faceoff  dailyfaceoff.com/starting-goalies/{date}
#     A Next.js page. The starter table is not in the static HTML -- it is
#     hydrated client-side -- but Next.js embeds the exact same data as plain
#     JSON in a <script id="__NEXT_DATA__"> tag on the initial response. No
#     browser automation is needed: a single HTTP GET returns the goalie name,
#     a "Confirmed" / no-tag confidence flag, and -- this is the direct-to-
#     source part -- the specific reporter's name and the URL of the original
#     X post the confirmation came from (homeNewsSourceName / homeNewsSourceUrl).
#     Daily Faceoff is the source of truth for starters in the model
#     spec (Section 7); this reads its own confirmation data instead of
#     asking a person to check the site by hand.
#
#   Hockey-Reference  hockey-reference.com/friv/injuries.cgi
#     A plain, static HTML table: player, team, injury date, type, and a free-
#     text note that itself usually cites a reporter or outlet. No auth, no
#     JS. Confirmed 2026-08-27 from a cloud sandbox IP with no rate limiting.
#     Confirmed 2026-09-19 from the production Windows machine's own network
#     that the same URL can answer HTTP 403 with the same minimal header set
#     (User-Agent + Accept only) while the identical request still succeeds
#     from the cloud sandbox. This looks like Sports-Reference's bot
#     protection keying off a thin, non-browser-like header set rather than
#     a hard IP or robots.txt block (robots.txt for this path was already
#     confirmed permissive). INTEL_HEADERS below adds the remaining headers
#     an ordinary browser sends on every request, tested 2026-09-19 against
#     the same 403 from the same Windows machine: no change, still 403. That
#     rules out a thin-header heuristic and points to something a header
#     change cannot fix, most likely a Cloudflare TLS/client fingerprint
#     check. Not pursued further -- the next step would be a different HTTP
#     client library, which starts to look like defeating an access control
#     rather than presenting honestly, and this source is best-effort by
#     design. HockeyReferenceInjuries degrades to hr=None exactly as
#     designed on every network tried so far; nothing downstream depends on
#     this source being present. Practical effect: expect this source to
#     work from the cloud scheduled task and to stay unavailable if the
#     engine ever runs from this Windows machine's own network.
#
# X/Twitter and the official NHL.com injury page were evaluated and rejected:
# both are disallowed by robots.txt for automated fetching (verified 2026-08-27),
# and the user does not want a paid-API poller layered on top when Daily
# Faceoff already carries direct reporter attribution for the goalie decision
# specifically. See V34_CHANGE_LOG.md V34.2.0.0 for the full evaluation.
# =============================================================================

import json
import logging
import re
import datetime as dt

from bs4 import BeautifulSoup

import asyncio
import os

log = logging.getLogger("nhl.intel")

DFO_BASE = "https://www.dailyfaceoff.com/starting-goalies/"
HR_INJURIES_URL = "https://www.hockey-reference.com/friv/injuries.cgi"
# ESPN's public NHL injury feed: JSON, no key, league-wide, and it answers GitHub's
# servers (Hockey-Reference refuses them). Undocumented, so it can change.
ESPN_INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries"

# A plain, honest, browser-complete header set. The shared Fetcher's
# BASE_HEADERS carries an Origin/Referer of nhl.com, which is correct for
# NHL's own APIs but would be a false trail on a third-party site.
INTEL_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

TEAM_FULL_NAMES = {
    "ANA": "Anaheim Ducks", "BOS": "Boston Bruins", "BUF": "Buffalo Sabres",
    "CAR": "Carolina Hurricanes", "CBJ": "Columbus Blue Jackets", "CGY": "Calgary Flames",
    "CHI": "Chicago Blackhawks", "COL": "Colorado Avalanche", "DAL": "Dallas Stars",
    "DET": "Detroit Red Wings", "EDM": "Edmonton Oilers", "FLA": "Florida Panthers",
    "LAK": "Los Angeles Kings", "MIN": "Minnesota Wild", "MTL": "Montreal Canadiens",
    "NJD": "New Jersey Devils", "NSH": "Nashville Predators", "NYI": "New York Islanders",
    "NYR": "New York Rangers", "OTT": "Ottawa Senators", "PHI": "Philadelphia Flyers",
    "PIT": "Pittsburgh Penguins", "SEA": "Seattle Kraken", "SJS": "San Jose Sharks",
    "STL": "St. Louis Blues", "TBL": "Tampa Bay Lightning", "TOR": "Toronto Maple Leafs",
    "UTA": "Utah Mammoth", "VAN": "Vancouver Canucks", "VGK": "Vegas Golden Knights",
    "WPG": "Winnipeg Jets", "WSH": "Washington Capitals",
}


def _norm(name):
    return re.sub(r"\s+", " ", (name or "").replace(".", "")).strip().lower()


_NAME_TO_ABBREV = {_norm(v): k for k, v in TEAM_FULL_NAMES.items()}


def team_abbrev_from_name(full_name):
    return _NAME_TO_ABBREV.get(_norm(full_name))


# =============================================================================
# DAILY FACEOFF - GOALIE CONFIRMATION
# =============================================================================
class DailyFaceoffGoalies:
    """One resolve() call per slate, not per game -- the page returns every
    game for the date in a single response."""

    def __init__(self):
        self.by_team = {}   # abbrev -> entry dict
        self.loaded = False
        self.error = None

    async def resolve(self, f, date: str):
        url = f"{DFO_BASE}{date}"
        raw = await f.get(url, as_text=True, headers=INTEL_HEADERS)
        if not raw:
            self.error = "fetch_failed"
            return self
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', raw, re.S)
        if not m:
            self.error = "no_next_data"
            log.warning("Daily Faceoff: __NEXT_DATA__ not found for %s (page layout may have changed)", date)
            return self
        try:
            payload = json.loads(m.group(1))
            games = payload["props"]["pageProps"].get("data", []) or []
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            self.error = f"parse_error: {exc}"
            log.warning("Daily Faceoff: could not parse __NEXT_DATA__ for %s: %s", date, exc)
            return self

        for g in games:
            for side in ("home", "away"):
                ab = team_abbrev_from_name(g.get(f"{side}TeamName"))
                if not ab:
                    continue
                strength = g.get(f"{side}NewsStrengthName")
                self.by_team[ab] = {
                    "goalie_name": g.get(f"{side}GoalieName"),
                    "confirmed": strength == "Confirmed",
                    "strength": strength,
                    "detail": g.get(f"{side}NewsDetails"),
                    "source_name": g.get(f"{side}NewsSourceName"),
                    "source_url": g.get(f"{side}NewsSourceUrl"),
                    "reported_at": g.get(f"{side}NewsCreatedAt"),
                    "season_sv_pct": g.get(f"{side}GoalieSavePercentage"),
                    "season_gaa": g.get(f"{side}GoalieGoalsAgainstAvg"),
                }
        self.loaded = True
        log.info("Daily Faceoff: %d team entries for %s%s", len(self.by_team), date,
                 "" if self.loaded and self.by_team else " (empty -- no games that day)")
        return self

    def get(self, abbrev):
        return self.by_team.get(str(abbrev).upper())


# =============================================================================
# DAILY FACEOFF - PROJECTED LINE COMBINATIONS (V34.4.4)
# dailyfaceoff.com/teams/<slug>/line-combinations is a Next.js page like the
# goalie page: __NEXT_DATA__ carries the combinations as JSON. robots.txt
# disallows only /api/ and /cms/ (checked 2026-09-29), so this page is allowed.
# Observed shape (2026-09-29, VGK): pageProps.combinations = {teamAbbreviation,
# sourceName, source (reporter post URL), updatedAt, players: [{name,
# groupIdentifier f1..f4 / d1..d3 / g / pp1.. / pk1.., positionIdentifier
# lw|c|rw|ld|rd|g1.., injuryStatus, gameTimeDecision, season{...},
# last10{gamesPlayed, goals, assists, points, shots, powerplayPoints,
# toiMinutes, toiSeconds (window totals), ...}}], lines: [...]}.
# =============================================================================
DFO_LINES_URL = "https://www.dailyfaceoff.com/teams/{slug}/line-combinations"


def team_slug(abbrev):
    """Daily Faceoff team slug from the full team name; verified 2026-09-29
    against the site's own 32-team list."""
    name = TEAM_FULL_NAMES.get(str(abbrev).upper())
    if not name:
        return None
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace(".", "")).strip("-")


def _i(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


class DailyFaceoffLines:
    """The four projected forward lines of one team with each player's stats
    in two windows (last 10 games and season to date), and which line leads
    in points and in shots per game in each window. V34.5.1: also the three
    defense pairs, each player's power-play unit (PP1, PP2), time on ice per
    game in whole seconds, and ties between leading lines marked as ties."""

    FORWARD_GROUPS = ("f1", "f2", "f3", "f4")
    DEFENSE_GROUPS = ("d1", "d2", "d3")
    PP_GROUPS = {"pp1": "PP1", "pp2": "PP2"}
    POS_ORDER = {"lw": 0, "c": 1, "rw": 2, "ld": 0, "rd": 1}
    WINDOWS = ("last10", "season")

    def __init__(self, abbrev):
        self.abbrev = str(abbrev).upper()
        self.url = None
        self.lines = []
        self.pairs = []
        self.error = None
        self.source_name = None
        self.source_url = None
        self.updated_at = None

    async def resolve(self, f):
        slug = team_slug(self.abbrev)
        if not slug:
            self.error = "unknown team"
            return self
        self.url = DFO_LINES_URL.format(slug=slug)
        raw = await f.get(self.url, as_text=True, headers=INTEL_HEADERS)
        if not raw:
            self.error = "fetch_failed"
            return self
        return self.parse(raw)

    def parse(self, raw):
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', raw, re.S)
        if not m:
            self.error = "no_next_data"
            log.warning("Daily Faceoff lines: __NEXT_DATA__ not found for %s", self.abbrev)
            return self
        try:
            c = json.loads(m.group(1))["props"]["pageProps"]["combinations"]
            players = c.get("players") or []
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            self.error = f"parse_error: {exc}"
            log.warning("Daily Faceoff lines: could not parse %s: %s", self.abbrev, exc)
            return self
        self.source_name = c.get("sourceName")
        self.source_url = c.get("source")
        self.updated_at = c.get("updatedAt")
        by_group, pp_unit = {}, {}
        for p in players:
            gid = p.get("groupIdentifier")
            if gid in self.FORWARD_GROUPS or gid in self.DEFENSE_GROUPS:
                by_group.setdefault(gid, []).append(p)
            if gid in self.PP_GROUPS:
                pp_unit.setdefault(p.get("playerId") or p.get("name"), self.PP_GROUPS[gid])
        for n, gid in enumerate(self.FORWARD_GROUPS, start=1):
            members = sorted(by_group.get(gid, []),
                             key=lambda p: self.POS_ORDER.get(p.get("positionIdentifier"), 9))
            rows = [self._row(p, pp_unit) for p in members]
            self.lines.append({"line": n, "players": rows,
                               "totals": {w: self._totals(rows, w) for w in self.WINDOWS}})
        for n, gid in enumerate(self.DEFENSE_GROUPS, start=1):
            members = sorted(by_group.get(gid, []),
                             key=lambda p: self.POS_ORDER.get(p.get("positionIdentifier"), 9))
            rows = [self._row(p, pp_unit) for p in members]
            if rows:
                self.pairs.append({"pair": n, "players": rows,
                                   "totals": {w: self._totals(rows, w) for w in self.WINDOWS}})
        if not any(ln["players"] for ln in self.lines):
            self.error = "no forward lines on page"
            return self
        self._mark_leaders()
        log.info("Daily Faceoff lines: %s loaded (%s, updated %s)", self.abbrev,
                 self.source_name, self.updated_at)
        return self

    @staticmethod
    def _stats(src):
        src = src or {}
        gp = _i(src.get("gamesPlayed"))
        toi_sec = _i(src.get("toiMinutes")) * 60 + _i(src.get("toiSeconds"))
        return {"gp": gp, "g": _i(src.get("goals")), "a": _i(src.get("assists")),
                "p": _i(src.get("points")), "sog": _i(src.get("shots")),
                "ppp": _i(src.get("powerplayPoints")),
                "toi_pg": round(toi_sec / 60.0 / gp, 1) if gp else None,
                "toi_sec_pg": int(toi_sec / gp + 0.5) if gp else None}

    @classmethod
    def _row(cls, p, pp_unit=None):
        return {"pos": (p.get("positionIdentifier") or "").upper(), "name": p.get("name") or "",
                "injury": p.get("injuryStatus"), "gtd": bool(p.get("gameTimeDecision")),
                "pp": (pp_unit or {}).get(p.get("playerId") or p.get("name"), ""),
                "last10": cls._stats(p.get("last10")), "season": cls._stats(p.get("season"))}

    @staticmethod
    def _totals(rows, window):
        st = [r[window] for r in rows]

        def per_game(key):
            return round(sum(x[key] / x["gp"] for x in st if x["gp"]), 2)
        return {"g": sum(x["g"] for x in st), "a": sum(x["a"] for x in st),
                "p": sum(x["p"] for x in st), "sog": sum(x["sog"] for x in st),
                "ppp": sum(x["ppp"] for x in st),
                # V34.5.5: penalty minutes come from the NHL game logs only (Daily
                # Faceoff publishes none); None when no player on the line has them.
                "pim": (sum(x.get("pim") or 0 for x in st)
                        if any(isinstance(x.get("pim"), int) for x in st) else None),
                "p_pg": per_game("p"), "sog_pg": per_game("sog")}

    def _mark_leaders(self):
        """Per window. Points leader: highest combined points per game (sum
        of each player's own rate, so a player with fewer games is not
        understated). Offense leader: highest combined shots on goal per
        game. V34.5.1: no tiebreak; every line level with the best rate is a
        leader and carries points_tied / shots_tied. No leader when the
        window is all zero."""
        filled = [ln for ln in self.lines if ln["players"]]
        for ln in self.lines:
            ln["leaders"] = {}
        for w in self.WINDOWS:
            top_p = max((ln["totals"][w]["p_pg"] for ln in filled), default=0)
            top_s = max((ln["totals"][w]["sog_pg"] for ln in filled), default=0)
            n_p = sum(1 for ln in filled if ln["totals"][w]["p_pg"] == top_p)
            n_s = sum(1 for ln in filled if ln["totals"][w]["sog_pg"] == top_s)
            for ln in self.lines:
                on_p = bool(ln["players"]) and top_p > 0 and ln["totals"][w]["p_pg"] == top_p
                on_s = bool(ln["players"]) and top_s > 0 and ln["totals"][w]["sog_pg"] == top_s
                ln["leaders"][w] = {"points": on_p, "shots": on_s,
                                    "points_tied": on_p and n_p > 1, "shots_tied": on_s and n_s > 1}

    @property
    def season_started(self):
        return any(r["season"]["gp"] > 0 for ln in self.lines for r in ln["players"])

    @property
    def season_games(self):
        """Most season games played by any listed skater: the team's games so far."""
        return max((r["season"]["gp"] for grp in (self.lines, self.pairs)
                    for ln in grp for r in ln["players"]), default=0)

    def as_dict(self):
        return {"team": self.abbrev, "url": self.url, "error": self.error,
                "source_name": self.source_name, "source_url": self.source_url,
                "updated_at": self.updated_at, "season_started": self.season_started,
                "season_games": self.season_games, "lines": self.lines, "pairs": self.pairs,
                "stats_source": getattr(self, "stats_source", "daily faceoff")}


# =============================================================================
# NHL GAME-LOG WINDOWS FOR THE LINES TABLES (V34.5.2.0, this season only V34.5.3.0)
#
# Design rule (2026-10-01): "Do not include pre-season in the stats." Daily Faceoff's
# "last 10" window counts every game a player dressed for: preseason games
# (every one of 96 players checked on 2026-10-01 had 2 to 4 in it), playoff
# games, older seasons and, for a call-up with no NHL games, AHL games; one
# player (Gritsyuk, 66 NHL games in 2025-26) came back all zeros. The lines
# and pairs keep Daily Faceoff's combinations and PP units, but every stat
# window is rebuilt here from the NHL's own per-player game logs:
#   last10  the player's last 10 NHL games THIS season (regular season, plus
#           this season's playoffs from April), fewer until he has played 10.
#   season  this season's regular-season games.
# Preseason is never fetched. V34.5.2 reached back into earlier seasons to
# fill 10 games, so every veteran still showed GP 10 in the first week (CAR on
# 2026-10-02: 1 game this season plus 9 playoff games from May);
# 2026-10-02: "todays run still shows 10 GP across the board". Since V34.5.3
# no window crosses into an earlier season (LOOKBACK_SEASONS = 1).
# A player whose NHL id cannot be found keeps Daily Faceoff's SEASON numbers
# (this season's regular season, never preseason) in both windows and is
# marked (source "dfo") so the app can say so.
# =============================================================================
NHL_API = "https://api-web.nhle.com/v1/"
LAST_N = 10
LOOKBACK_SEASONS = 1          # this season only (V34.5.3); 3 reached back into old seasons


def _norm_name(name):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]+", "", s.lower().replace("-", " ")).strip()


def _toi_sec(t):
    try:
        m, x = str(t or "0:00").split(":")
        return int(m) * 60 + int(x)
    except ValueError:
        return 0


class NhlPlayerWindows:
    def __init__(self, season, target_date, cache_path=None, master_path=None):
        self.season = str(season)                     # e.g. "20262027"
        self.target_date = str(target_date)
        self.cache_path = cache_path
        self.master_path = master_path
        self.cache = {}
        self.rosters = {}
        self.master = None
        self.stats = {"players": 0, "nhl": 0, "dfo_fallback": 0, "unresolved": []}
        if cache_path:
            try:
                with open(cache_path, encoding="utf-8") as fh:
                    self.cache = json.load(fh)
            except (OSError, ValueError):
                self.cache = {}

    def _seasons(self):
        y = int(self.season[:4])
        return [f"{y - k}{y - k + 1}" for k in range(LOOKBACK_SEASONS)]

    async def _roster(self, f, team):
        if team in self.rosters:
            return self.rosters[team]
        idx = {"full": {}, "last": {}}
        data = await f.get(f"{NHL_API}roster/{team}/current") or {}
        for grp in ("forwards", "defensemen", "goalies"):
            for p in data.get(grp) or []:
                first = (p.get("firstName") or {}).get("default", "")
                last = (p.get("lastName") or {}).get("default", "")
                pid = p.get("id")
                if not pid:
                    continue
                idx["full"][_norm_name(f"{first} {last}")] = pid
                idx["last"].setdefault(_norm_name(last), []).append((_norm_name(first), pid))
        self.rosters[team] = idx
        return idx

    def _master_lookup(self, name, team):
        if self.master is None:
            self.master = {}
            try:
                with open(self.master_path, encoding="utf-8") as fh:
                    for p in json.load(fh).get("players") or []:
                        self.master.setdefault(_norm_name(p.get("name")), []).append(
                            (p.get("team"), p.get("player_id")))
            except (OSError, ValueError, TypeError):
                pass
        hits = self.master.get(_norm_name(name)) or []
        same = [pid for t, pid in hits if t == team]
        return (same or [pid for _, pid in hits] or [None])[0]

    async def resolve_id(self, f, name, team):
        idx = await self._roster(f, team)
        n = _norm_name(name)
        if n in idx["full"]:
            return idx["full"][n]
        parts = n.split()
        if parts:
            cands = idx["last"].get(" ".join(parts[1:]) or parts[-1]) or idx["last"].get(parts[-1]) or []
            if len(cands) == 1:
                return cands[0][1]
            same_initial = [pid for first, pid in cands if first[:1] == parts[0][:1]]
            if len(same_initial) == 1:
                return same_initial[0]
        pid = self._master_lookup(name, team) if self.master_path else None
        return int(pid) if pid else None

    async def _log(self, f, pid, season, gtype):
        key = f"{pid}|{season}|{gtype}"
        completed = season != self.season
        if completed and key in self.cache:
            return self.cache[key]
        data = await f.get(f"{NHL_API}player/{pid}/game-log/{season}/{gtype}")
        if data is None:
            return []
        games = [{"date": g.get("gameDate"), "type": gtype, "team": g.get("teamAbbrev"),
                  "g": int(g.get("goals") or 0), "a": int(g.get("assists") or 0),
                  "sog": int(g.get("shots") or 0), "ppp": int(g.get("powerPlayPoints") or 0),
                  "pim": int(g.get("pim") or 0), "toi": _toi_sec(g.get("toi"))}
                 for g in (data.get("gameLog") or []) if (g.get("gameDate") or "") < self.target_date]
        games.sort(key=lambda x: x["date"], reverse=True)
        if completed:
            self.cache[key] = games[:LAST_N]
        return games

    @staticmethod
    def _agg(games):
        n = len(games)
        toi = sum(x["toi"] for x in games)
        return {"gp": n, "g": sum(x["g"] for x in games), "a": sum(x["a"] for x in games),
                "p": sum(x["g"] + x["a"] for x in games), "sog": sum(x["sog"] for x in games),
                "ppp": sum(x["ppp"] for x in games),
                "pim": sum(x.get("pim") or 0 for x in games),       # V34.5.5: penalty minutes
                "toi_pg": round(toi / 60.0 / n, 1) if n else None,
                "toi_sec_pg": int(toi / n + 0.5) if n else None}

    async def windows(self, f, pid):
        seasons = self._seasons()
        cur = seasons[0]
        season_games = await self._log(f, pid, cur, 2)
        pool = list(season_games)
        if self.target_date[5:7] in ("04", "05", "06", "07"):        # playoffs of this season
            pool += await self._log(f, pid, cur, 3)
        for prev in seasons[1:]:
            if len(pool) >= LAST_N:
                break
            pool += await self._log(f, pid, prev, 3)
            pool += await self._log(f, pid, prev, 2)
        pool.sort(key=lambda x: x["date"], reverse=True)
        last = pool[:LAST_N]
        return {"last10": self._agg(last), "season": self._agg(season_games),
                "last10_span": [last[-1]["date"], last[0]["date"]] if last else None,
                "last10_playoff_games": sum(1 for x in last if x["type"] == 3)}

    async def apply(self, f, dls, pairs=True):
        """Rebuild every player's stat windows on each DailyFaceoffLines in dls,
        then the line totals and leaders. Never raises for one player. pairs=False
        skips the defense pairs (the Projected Winner tables show forwards only)."""
        jobs = []
        for dl in dls:
            if getattr(dl, "error", None):
                continue
            for grp in ((dl.lines, dl.pairs) if pairs else (dl.lines,)):
                for ln in grp:
                    for row in ln["players"]:
                        jobs.append((dl, row))

        async def one(dl, row):
            self.stats["players"] += 1
            try:
                pid = await self.resolve_id(f, row["name"], dl.abbrev)
                if not pid:
                    raise LookupError("no NHL id")
                w = await self.windows(f, pid)
                row["dfo_last10"], row["dfo_season"] = row.get("last10"), row.get("season")
                row["last10"], row["season"] = w["last10"], w["season"]
                row["nhl_id"], row["stats_source"] = pid, "nhl"
                row["last10_span"], row["last10_playoff_games"] = w["last10_span"], w["last10_playoff_games"]
                self.stats["nhl"] += 1
            except Exception as exc:  # noqa: BLE001
                # Daily Faceoff's last-10 counts preseason; its season window
                # (this regular season) stands in for both windows instead.
                row["dfo_last10"] = row.get("last10")
                if row.get("season"):
                    row["last10"] = dict(row["season"])
                row["stats_source"] = "dfo"
                self.stats["dfo_fallback"] += 1
                self.stats["unresolved"].append(f"{dl.abbrev} {row.get('name')} ({str(exc)[:40]})")

        await asyncio.gather(*[one(dl, row) for dl, row in jobs])
        for dl in dls:
            if getattr(dl, "error", None):
                continue
            for grp in (dl.lines, dl.pairs):
                for ln in grp:
                    ln["totals"] = {w: dl._totals(ln["players"], w) for w in dl.WINDOWS}
            dl._mark_leaders()
            dl.stats_source = "nhl game logs"
        if self.cache_path:
            try:
                tmp = self.cache_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(self.cache, fh)
                os.replace(tmp, self.cache_path)
            except OSError as exc:
                log.warning("game-log cache not written: %s", exc)
        log.info("lines stats from NHL game logs: %s of %s players", self.stats["nhl"], self.stats["players"])
        return self.stats


# =============================================================================
# HOCKEY-REFERENCE - INJURY STATUS
# =============================================================================
class EspnInjuries:
    """ESPN's public NHL injury feed, the main injury list since 1.4.0: one JSON
    request, every team, each player's status (Out, Day-To-Day, Injured
    Reserve), body part, estimated return date and a short note. Same interface
    as HockeyReferenceInjuries. ESPN's own player ids differ from the NHL's, so
    players are matched by name with accents removed."""

    TAG = "ESPN"
    STATUS_CODES = {"injured reserve": "IR", "day-to-day": "DTD", "out": "OUT", "suspension": "SUSP"}

    def __init__(self):
        self.by_team = {}
        self.loaded = False
        self.error = None

    async def resolve(self, f, as_of: str = None):
        data = await f.get(ESPN_INJURIES_URL, headers=INTEL_HEADERS)
        if not isinstance(data, dict) or not isinstance(data.get("injuries"), list):
            self.error = "fetch_failed"
            return self
        return self.read(data, as_of)

    def read(self, data, as_of: str = None):
        for t in data.get("injuries") or []:
            ab = team_abbrev_from_name(t.get("displayName"))
            if not ab:
                continue
            for x in t.get("injuries") or []:
                name = ((x.get("athlete") or {}).get("displayName") or "").strip()
                if not name:
                    continue
                status = str(x.get("status") or "").strip()
                self.by_team.setdefault(ab, []).append({
                    "player_name": name, "date_injury": str(x.get("date") or "")[:10],
                    "injury_type": self.STATUS_CODES.get(status.lower(), status.upper() or "OUT"),
                    "injury_note": self.note(x, as_of), "tag": self.TAG})
        self.loaded = True
        log.info("ESPN injuries: %d players on %d teams",
                 sum(len(v) for v in self.by_team.values()), len(self.by_team))
        return self

    @staticmethod
    def note(x, as_of=None):
        """ESPN's short comment when it says something; otherwise the status, the
        body part and the estimated return built from ESPN's own fields."""
        short = str(x.get("shortComment") or "").strip()
        if len(short) > 24:
            return short
        det = x.get("details") or {}
        status = str(x.get("status") or "Out").strip() or "Out"
        part = str(det.get("type") or "").strip()
        out = status + (f" ({part.lower()})" if part and part.lower() != "undisclosed" else "")
        ret = str(det.get("returnDate") or "")[:10]
        try:
            if ret and (not as_of or ret > as_of):
                d = dt.date.fromisoformat(ret)
                out += f", estimated return {d.strftime('%b')} {d.day}"
        except ValueError:
            pass
        return out + "."

    def get(self, abbrev):
        return self.by_team.get(str(abbrev).upper(), [])

    def match_player(self, abbrev, player_name):
        target = _norm_name(player_name)
        for e in self.get(abbrev):
            if _norm_name(e["player_name"]) == target:
                return e
        return None


class CombinedInjuries:
    """The injury lists the build could reach, in order of preference (ESPN,
    then Hockey-Reference, then Daily Faceoff). The first list that names a
    player supplies his note and its source tag. A player Daily Faceoff slots
    into tonight's lineup counts as playing, whichever list names him."""

    TAG = "INJ"

    def __init__(self, sources, lineup=None):
        self.sources = [s for s in sources if s is not None and getattr(s, "loaded", False)]
        self.lineup = lineup or {}
        self.loaded = bool(self.sources)
        self.by_team = {}
        for s in self.sources:
            for ab, rows in s.by_team.items():
                seen = {_norm_name(e["player_name"]) for e in self.by_team.get(ab, [])}
                for e in rows:
                    if _norm_name(e["player_name"]) not in seen:
                        self.by_team.setdefault(ab, []).append(dict(e, tag=e.get("tag") or getattr(s, "TAG", "HR")))
                        seen.add(_norm_name(e["player_name"]))

    def get(self, abbrev):
        return self.by_team.get(str(abbrev).upper(), [])

    def match_player(self, abbrev, player_name):
        ab = str(abbrev).upper()
        if _norm_name(player_name) in self.lineup.get(ab, ()):
            return None
        for s in self.sources:
            hit = s.match_player(ab, player_name)
            if hit:
                return dict(hit, tag=hit.get("tag") or getattr(s, "TAG", "HR"))
        return None


class DailyFaceoffInjuries:
    """Fallback injury list, used when the Hockey-Reference table cannot be
    reached (it refuses GitHub's servers). Each Daily Faceoff team
    line-combination page lists the players it has out of tonight's lineup in
    an injured group (injured reserve, day to day, out), with the latest news
    note. Same interface as HockeyReferenceInjuries: by_team, loaded, get(),
    match_player(). The pages are the same ones the lines step reads, so the
    run's response cache serves them a second time for free."""

    TAG = "DFO"
    OUT_GROUP = "ir"
    LINEUP_GROUPS = ("f1", "f2", "f3", "f4", "d1", "d2", "d3", "g")

    def __init__(self):
        self.by_team = {}    # abbrev -> [entries]
        self.lineup = {}     # abbrev -> names (accents removed) Daily Faceoff has dressing tonight
        self.loaded = False
        self.error = None
        self.teams_read = 0

    async def resolve(self, f, teams):
        ok = 0
        for i in range(0, len(teams), 4):                      # gentle on the source
            chunk = teams[i:i + 4]
            res = await asyncio.gather(*[self._page(f, t) for t in chunk])
            ok += sum(1 for r in res if r)
        self.teams_read = ok
        self.loaded = ok > 0
        if not ok:
            self.error = "fetch_failed"
        log.info("Daily Faceoff injuries: %d of %d team pages read, %d players listed out",
                 ok, len(teams), sum(len(v) for v in self.by_team.values()))
        return self

    async def _page(self, f, team):
        slug = team_slug(team)
        if not slug:
            return False
        raw = await f.get(DFO_LINES_URL.format(slug=slug), as_text=True, headers=INTEL_HEADERS)
        return bool(raw) and self.add_page(team, raw)

    def add_page(self, team, raw):
        """Read one team page; True when the page parsed (even with nobody out)."""
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', raw or "", re.S)
        if not m:
            return False
        try:
            players = json.loads(m.group(1))["props"]["pageProps"]["combinations"].get("players") or []
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
            return False
        ab = str(team).upper()
        # a player Daily Faceoff also slots into tonight's lineup (a line, a pair
        # or the goalie spot) is expected to play, whatever his tag says
        dressing = {p.get("playerId") or p.get("name") for p in players
                    if p.get("groupIdentifier") in self.LINEUP_GROUPS}
        self.lineup[ab] = {_norm_name(p.get("name")) for p in players
                           if p.get("groupIdentifier") in self.LINEUP_GROUPS and p.get("name")}
        for p in players:
            if p.get("groupIdentifier") != self.OUT_GROUP or not p.get("name"):
                continue
            if (p.get("playerId") or p.get("name")) in dressing:
                continue
            status = str(p.get("injuryStatus") or "out").upper()
            news = p.get("latestNews") or {}
            note = str(news.get("details") or "").strip() or f"Listed {status} by Daily Faceoff."
            self.by_team.setdefault(ab, []).append({
                "player_name": p["name"], "date_injury": str(news.get("createdAt") or "")[:10],
                "injury_type": status, "injury_note": note, "tag": self.TAG})
        self.loaded = True
        return True

    def get(self, abbrev):
        return self.by_team.get(str(abbrev).upper(), [])

    def match_player(self, abbrev, player_name):
        """Match on the name without accents: Daily Faceoff and the NHL roster
        do not always spell accented names the same way."""
        target = _norm_name(player_name)
        for e in self.get(abbrev):
            if _norm_name(e["player_name"]) == target:
                return e
        return None


class HockeyReferenceInjuries:
    """A static HTML table: player, team, injury date, type, free-text note.
    Skater-focused; not scoped to tonight's slate, so callers filter by team
    and by recency. The note field usually names the reporting outlet, but it
    is prose, not a structured field -- shown as-is, not parsed further."""

    TAG = "HR"
    FRESH_DAYS = 21   # only treat an entry as an active-injury signal inside
                       # this window; older rows are commonly stale ("cleared
                       # for camp") and Hockey-Reference does not mark them
                       # resolved, only newly relevant ones get new dates.

    def __init__(self):
        self.by_team = {}   # abbrev -> [entries]
        self.loaded = False
        self.error = None
        self.fetched_utc = None   # set when loaded from a pre-fetched file
        self.stale = False        # file fetched more than a day before as_of

    @classmethod
    def from_file(cls, path, as_of: str = None):
        """Build from the JSON that fetch_injuries.py writes (V34.3). The
        scheduled task's cloud side can reach hockey-reference.com while the
        Windows machine's own network cannot, so the cloud fetches, drops the
        rows into the vault, and the engine reads them here. Same freshness
        filter and team mapping as resolve()."""
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        self = cls()
        self.fetched_utc = payload.get("fetched_utc")
        cutoff = None
        try:
            ref_date = dt.date.fromisoformat(as_of) if as_of else dt.date.today()
            cutoff = ref_date - dt.timedelta(days=self.FRESH_DAYS)
            if self.fetched_utc:
                fetched_day = dt.date.fromisoformat(self.fetched_utc[:10])
                self.stale = (ref_date - fetched_day).days > 1
        except ValueError:
            pass
        for cells in payload.get("rows", []):
            ab = team_abbrev_from_name(cells.get("team_name"))
            if not ab:
                continue
            entry = {
                "player_name": cells.get("player", ""),
                "date_injury": cells.get("date_injury", ""),
                "injury_type": cells.get("injury_type", ""),
                "injury_note": cells.get("injury_note", ""),
            }
            if cutoff is not None:
                parsed = self._parse_date(entry["date_injury"])
                if parsed and parsed < cutoff:
                    continue
            self.by_team.setdefault(ab, []).append(entry)
        self.loaded = True
        log.info("Hockey-Reference (file %s, fetched %s): %d teams with an entry inside %dd%s",
                 path, self.fetched_utc, len(self.by_team), self.FRESH_DAYS,
                 " STALE" if self.stale else "")
        return self

    async def resolve(self, f, as_of: str = None):
        raw = await f.get(HR_INJURIES_URL, as_text=True, headers=INTEL_HEADERS)
        if not raw:
            self.error = "fetch_failed"
            return self
        try:
            soup = BeautifulSoup(raw, "html.parser")
            table = soup.find("table", id="injuries")
            if table is None:
                table = soup.find("table")
            rows = table.find("tbody").find_all("tr") if table else []
        except Exception as exc:   # noqa: BLE001 -- a parser change should degrade, not crash a slate
            self.error = f"parse_error: {exc}"
            log.warning("Hockey-Reference: could not parse injuries table: %s", exc)
            return self

        cutoff = None
        try:
            ref_date = dt.date.fromisoformat(as_of) if as_of else dt.date.today()
            cutoff = ref_date - dt.timedelta(days=self.FRESH_DAYS)
        except ValueError:
            pass

        for r in rows:
            cells = {c.get("data-stat", ""): c.get_text(strip=True) for c in r.find_all(["th", "td"])}
            ab = team_abbrev_from_name(cells.get("team_name"))
            if not ab:
                continue
            entry = {
                "player_name": cells.get("player", ""),
                "date_injury": cells.get("date_injury", ""),
                "injury_type": cells.get("injury_type", ""),
                "injury_note": cells.get("injury_note", ""),
            }
            if cutoff is not None:
                parsed = self._parse_date(entry["date_injury"])
                if parsed and parsed < cutoff:
                    continue   # outside the freshness window
            self.by_team.setdefault(ab, []).append(entry)
        self.loaded = True
        log.info("Hockey-Reference: %d teams with an injury-table entry inside %dd",
                 len(self.by_team), self.FRESH_DAYS)
        return self

    @staticmethod
    def _parse_date(s):
        # Format observed: "Thu, May 21, 2026"
        for fmt in ("%a, %b %d, %Y", "%b %d, %Y"):
            try:
                return dt.datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    def get(self, abbrev):
        return self.by_team.get(str(abbrev).upper(), [])

    def match_player(self, abbrev, player_name):
        """Loose match: Hockey-Reference and the NHL roster do not share an
        ID, so match on normalized full name."""
        target = _norm(player_name)
        for e in self.get(abbrev):
            if _norm(e["player_name"]) == target:
                return e
        return None
