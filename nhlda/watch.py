"""Game-day watch: rebuild when a starting goalie or a lineup changes.

1.17.0 (John, 2026-11-02: "The Red Wings and Panthers game lineup is wrong. Need to
find a better way at determining the projected goalie and last minute changes.
Social media is the most updated"). The 9:00 AM update built Detroit at Florida with
Akira Schmid in goal; at 9:51 AM Central the Miami Herald's Jordan McPherson posted
that Jacob Markstrom would return and start, and Daily Faceoff relayed it, but the
app waited for the 4:45 PM update, and goalie or lineup news from warmups comes
after that one.

So on game days, from 9:30 AM Central until the last game of the day starts, every
automatic run of the Daily data workflow (the outside timer's "lineup-watch" event
every 15 minutes, with cron lines as a backup) asks this module whether to build.
It reads Daily Faceoff's starting-goalie page (one request) and, for teams whose game
starts within LINES_BEFORE_MIN minutes, their line-combination pages, and compares
them with Daily Faceoff's answer saved at the last build (state/dfo_cache.json). It
builds only when something changed: another goalie named, a report confirmed, a newer
report, or a different lineup. A main update that is due (nhlda.schedule) always
builds.

Daily Faceoff is the social-media route: its goalie entries relay the beat writers'
own posts (mostly on X) within minutes, with the writer's name, the time and a link.
X cannot be read directly (see nhlda.dfo).

    python -m nhlda.watch app/data/latest.json state    # prints build=true or build=false

Standard library only, so it runs before the build's requirements are installed.
"""
import datetime as dt
import json
import os
import sys
import urllib.request

from . import dfo
from . import schedule as S

WATCH_FROM = (9, 30)            # US Central: after the 9:00 AM main update
LINES_BEFORE_MIN = 90           # lineup pages are read this close to a team's puck drop
STARTED = {"LIVE", "CRIT", "FINAL", "OFF"}
UTC = dt.timezone.utc


def _t(text):
    return S.parse_utc(text)


def upcoming(data, now):
    """Today's games that have not started, when the data file holds today's slate (US
    Central date) and the watch window is open; else []."""
    local = now.astimezone(S.CENTRAL)
    if (data or {}).get("date") != local.date().isoformat() or (local.hour, local.minute) < WATCH_FROM:
        return []
    out = []
    for g in ((data.get("slate") or {}).get("games") or []):
        start = _t(g.get("start_utc"))
        if start and start > now and g.get("state") not in STARTED:
            out.append(g)
    return out


def lines_due(games, now):
    """Teams whose game starts within LINES_BEFORE_MIN minutes."""
    soon = []
    for g in games:
        start = _t(g.get("start_utc"))
        if start and (start - now).total_seconds() <= LINES_BEFORE_MIN * 60:
            soon += [g["away"], g["home"]]
    return soon


def _who(e):
    if not e:
        return "nothing saved"
    return f"{e.get('goalie_name') or 'no goalie'} ({e.get('strength') or 'no report'})"


def decide(data, cache, now, goalies, lines):
    """Reasons to build now (empty: nothing changed). goalies: Daily Faceoff's
    {team: entry} read now, or None when it did not answer; lines: {team:
    combinations or None} read now for the teams in lines_due()."""
    games = upcoming(data, now)
    if not games:
        return []
    saved_g = (cache or {}).get("goalies") or {}
    saved_by = (saved_g.get("by_team") or {}) if saved_g.get("date") == data.get("date") else {}
    saved_lines = (cache or {}).get("lines") or {}
    reasons = []
    for g in games:
        for ab in (g["away"], g["home"]):
            new = (goalies or {}).get(ab)
            if goalies is not None and new:
                old = saved_by.get(ab)
                if not old or any(new.get(k) != old.get(k) for k in ("goalie_name", "strength", "reported_at")):
                    reasons.append(f"{ab} goalie: {_who(old)} -> {_who(new)}"
                                   + (f", reported by {new['source_name']}" if new.get("source_name") else ""))
            if ab in (lines or {}) and lines[ab] is not None:
                old_c = (saved_lines.get(ab) or {}).get("combinations")
                if dfo.lineup_key(lines[ab]) != dfo.lineup_key(old_c):
                    reasons.append(f"{ab} lineup changed on Daily Faceoff"
                                   + (f" ({lines[ab].get('sourceName')})" if lines[ab].get("sourceName") else ""))
    return reasons


def _get(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers=dfo.INTEL_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - a source that does not answer means "cannot tell"
        print(f"watch: {url}: {exc}", file=sys.stderr)
        return None


def read_goalies(date):
    payload = dfo.next_data(_get(dfo.DFO_BASE + date))
    try:
        return dfo.goalie_entries(payload["props"]["pageProps"].get("data") or [])
    except (KeyError, TypeError, AttributeError):
        return None


def read_lines(team):
    slug = dfo.team_slug(team)
    payload = dfo.next_data(_get(dfo.DFO_LINES_URL.format(slug=slug))) if slug else None
    try:
        return payload["props"]["pageProps"]["combinations"]
    except (KeyError, TypeError):
        return None


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def check(data_path, state_dir, now=None):
    """(build, reasons) for the workflow."""
    now = now or dt.datetime.now(UTC)
    data = _load(data_path) or {}
    due, why = S.due(data.get("generated_utc"), now)
    if due:
        return True, [why]
    games = upcoming(data, now)
    if not games:
        return False, ["no game left to start today, or before the watch window opens"]
    goalies = read_goalies(data["date"])
    lines = {ab: read_lines(ab) for ab in lines_due(games, now)}
    reasons = decide(data, _load(os.path.join(state_dir, "dfo_cache.json")), now, goalies, lines)
    if not reasons:
        heard = "no answer" if goalies is None else f"{len(games)} games' goalies"
        return False, [f"no change ({heard}; lineups of {len(lines)} teams)"]
    return True, reasons


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    data_path = argv[0] if argv else "app/data/latest.json"
    state_dir = argv[1] if len(argv) > 1 else "state"
    build, reasons = check(data_path, state_dir)
    print(f"build={'true' if build else 'false'}")
    for r in reasons:
        print(("Build: " if build else "Skip: ") + r, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
