"""Daily Faceoff page reading shared by the build (nhlda.intel) and the game-day watch
(nhlda.watch). Standard library only, so the watch runs before the build's
requirements are installed.

Daily Faceoff's starting-goalie and line-combination pages are Next.js pages whose
__NEXT_DATA__ script carries the data as JSON. robots.txt disallows only /api/ and
/cms/ (checked 2026-09-29), so these pages are allowed. Its starting-goalie entries
relay the team beat writers' own posts (mostly on X): the writer's name, the time of
the post and a link to it. X itself cannot be read: its robots.txt disallows every
automated reader (User-agent: * Disallow: /, checked 2026-11-02) and its API needs a
paid key, which this public app cannot hold.
"""
import json
import re

DFO_BASE = "https://www.dailyfaceoff.com/starting-goalies/"
DFO_LINES_URL = "https://www.dailyfaceoff.com/teams/{slug}/line-combinations"

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

# the line-combination groups the app uses (the cache keeps the same ones)
LINE_GROUPS = ("f1", "f2", "f3", "f4", "d1", "d2", "d3", "pp1", "pp2")


def _norm(name):
    return re.sub(r"\s+", " ", (name or "").replace(".", "")).strip().lower()


_NAME_TO_ABBREV = {_norm(v): k for k, v in TEAM_FULL_NAMES.items()}


def team_abbrev_from_name(full_name):
    return _NAME_TO_ABBREV.get(_norm(full_name))


def team_slug(abbrev):
    """Daily Faceoff team slug from the full team name; verified 2026-09-29
    against the site's own 32-team list."""
    name = TEAM_FULL_NAMES.get(str(abbrev).upper())
    if not name:
        return None
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace(".", "")).strip("-")


def next_data(raw):
    """The page's __NEXT_DATA__ JSON, or None when it is missing or unreadable."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', raw or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def goalie_entries(games):
    """{team: entry} from the starting-goalie page's list of games."""
    out = {}
    for g in games or []:
        for side in ("home", "away"):
            ab = team_abbrev_from_name(g.get(f"{side}TeamName"))
            if not ab:
                continue
            strength = g.get(f"{side}NewsStrengthName")
            out[ab] = {
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
    return out


def lineup_key(combinations):
    """What makes two line-combination answers the same lineup: who plays in which
    group and spot, and who is a game-time decision or injured."""
    return sorted((p.get("groupIdentifier") or "", p.get("positionIdentifier") or "", p.get("name") or "",
                   bool(p.get("gameTimeDecision")), p.get("injuryStatus") or "")
                  for p in (combinations or {}).get("players") or []
                  if p.get("groupIdentifier") in LINE_GROUPS)
