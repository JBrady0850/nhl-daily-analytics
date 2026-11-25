"""Numbered data updates (1.22.0).

John, 2026-11-25: "We need to add version numbers so we can track when things are
updated". He chose numbered data updates, shown in the app. Every build of the data
file gets the next number for its slate date: 2026-11-25.7 is the seventh update of
the November 25 data. Each update also records why it ran (the scheduled update, the
game-day watch and what it saw, or a run by hand) and what it changed compared with
the file it replaced: goalies, picks and win chances, final scores, the injury list,
line combinations, Game Scores, the season record and the model's learning cycles.

The record of recent updates is kept in state/updates.json (the GitHub bot commits it
with the data), and the newest ones are published in the data file as `update` and
`update_log` for the app's Help tab.
"""
import datetime as dt
import json
import os

HIST_NAME = "updates.json"
KEEP = 60               # updates kept in state/updates.json
PUBLISH = 30            # newest updates published in the data file (update_log)
MAX_CHANGES = 12        # change lines per update; the rest are counted in one last line
MAX_INJURY = 6          # injury lines per update before they are counted
PROB_STEP = 0.02        # a win chance must move this much (2 points) to be listed
WHY_MAX = 300

FINAL = {"FINAL", "OFF"}


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def number(history, date):
    """The next update number for a slate date: one more than the highest so far."""
    return 1 + max((u.get("n") or 0 for u in history if u.get("date") == date), default=0)


def _day(date):
    try:
        d = dt.date.fromisoformat(date)
    except (TypeError, ValueError):
        return str(date)
    return f"{d:%a}, {d:%b} {d.day}"


def _gk(side):
    g = (side or {}).get("goalie") or {}
    src = str(g.get("source") or "").upper()
    how = ("confirmed" if "CONFIRMED" in src else "projected" if "PROJECTED" in src
           else "rotation guess" if "ROTATION" in src else "not confirmed" if src else "")
    return g.get("name"), how


def _pct(p):
    return f"{100 * p:.1f}%" if isinstance(p, (int, float)) else "?"


def _games(prev, new):
    out = []
    old = {str(g.get("game_id") or g.get("matchup")): g for g in ((prev or {}).get("slate") or {}).get("games") or []}
    for g in ((new.get("slate") or {}).get("games") or []):
        o = old.get(str(g.get("game_id") or g.get("matchup")))
        if not o:
            continue
        m = g.get("matchup") or f"{g.get('away')} @ {g.get('home')}"
        r = g.get("result")
        if r and not o.get("result"):
            won = {"win": ", the pick won", "loss": ", the pick lost"}.get(r.get("pick"), "")
            per = {"OT": " in overtime", "SO": " in a shootout"}.get(r.get("last_period"), "")
            out.append(f"Final: {g.get('away')} {r.get('away_score')}, {g.get('home')} {r.get('home_score')}{per}{won}")
            continue
        for side, ab in (("away_side", g.get("away")), ("home_side", g.get("home"))):
            (n0, h0), (n1, h1) = _gk(o.get(side)), _gk(g.get(side))
            if n1 and (n0, h0) != (n1, h1):
                out.append(f"{ab} goalie: {n0 or 'none named'}{f' ({h0})' if h0 else ''} to {n1}{f' ({h1})' if h1 else ''}")
        if g.get("pick_locked"):
            continue
        if g.get("projected") and o.get("projected") and g["projected"] != o["projected"]:
            out.append(f"{m} pick: {o['projected']} {_pct(o.get('win_prob'))} to {g['projected']} {_pct(g.get('win_prob'))}")
        elif (isinstance(g.get("win_prob"), (int, float)) and isinstance(o.get("win_prob"), (int, float))
              and abs(g["win_prob"] - o["win_prob"]) >= PROB_STEP):
            out.append(f"{m}: {g.get('projected')} win chance {_pct(o['win_prob'])} to {_pct(g['win_prob'])}")
    return out


def _injuries(prev, new):
    def rows(d):
        return {(r.get("team"), str(r.get("player_id") or r.get("name"))): r
                for r in ((d or {}).get("injuries") or {}).get("players") or []}
    if not (prev or {}).get("injuries"):
        return []
    a, b = rows(prev), rows(new)
    if not a or not b:
        return []        # a report did not load on one side: the build's own note says so
    out = []
    for k in sorted(set(b) - set(a)):
        r = b[k]
        out.append(f"Injury list: added {r.get('name')} ({r.get('team')}, {r.get('status') or 'listed'})")
    for k in sorted(set(a) - set(b)):
        r = a[k]
        out.append(f"Injury list: removed {r.get('name')} ({r.get('team')})")
    for k in sorted(set(a) & set(b)):
        if a[k].get("status") != b[k].get("status"):
            out.append(f"Injury list: {b[k].get('name')} ({b[k].get('team')}) {a[k].get('status')} to {b[k].get('status')}")
    if len(out) > MAX_INJURY:
        out = out[:MAX_INJURY] + [f"Injury list: {len(out) - MAX_INJURY} more changes"]
    return out


def _lineup(t):
    t = t or {}
    return (tuple(tuple(p.get("nhl_id") or p.get("name") for p in ln.get("players") or []) for ln in t.get("lines") or []),
            tuple(tuple(p.get("nhl_id") or p.get("name") for p in ln.get("players") or []) for ln in t.get("pairs") or []))


def _lines(prev, new):
    a, b = (prev or {}).get("lines") or {}, new.get("lines") or {}
    moved = sorted(ab for ab in b if ab in a and (b[ab] or {}).get("lines") and (a[ab] or {}).get("lines")
                   and _lineup(a[ab]) != _lineup(b[ab]))
    return [f"Lines changed: {', '.join(moved)}"] if moved else []


def _gamescore(prev, new):
    a = ((prev or {}).get("gamescore") or {}).get("teams") or {}
    b = (new.get("gamescore") or {}).get("teams") or {}
    if not a:
        return []
    added = sorted(ab for ab in b if (a.get(ab) or {}).get("game_id") != b[ab].get("game_id"))
    better = sorted(ab for ab in b if ab not in added and a.get(ab)
                    and (a[ab].get("inputs"), a[ab].get("pending")) != (b[ab].get("inputs"), b[ab].get("pending")))
    out = []
    if added:
        out.append(f"New Game Score (latest finished game): {', '.join(added)}")
    if better:
        out.append(f"Game Score rescored with newer inputs: {', '.join(better)}")
    return out


def _season(prev, new):
    out = []
    g0 = (((prev or {}).get("results") or {}).get("season") or {}).get("games")
    g1 = ((new.get("results") or {}).get("season") or {}).get("games")
    if isinstance(g0, int) and isinstance(g1, int) and g1 > g0:
        out.append(f"Season record: {g1 - g0} more game{'s' if g1 - g0 != 1 else ''} settled ({g1} in all)")
    c0 = ((((prev or {}).get("model") or {}).get("learning")) or {}).get("cycles")
    c1 = (((new.get("model") or {}).get("learning")) or {}).get("cycles")
    if isinstance(c0, int) and isinstance(c1, int) and c1 > c0:
        out.append(f"Model learning cycle {c1} run")
    return out


def changes(prev, new):
    """What this update changed compared with the file it replaces, in plain lines."""
    same_day = bool(prev) and prev.get("date") == new.get("date")
    out = [] if same_day else [f"First update for {_day(new.get('date'))}: {(new.get('slate') or {}).get('game_count', 0)} games"]
    if same_day:
        out += _games(prev, new)
    if prev:
        out += _gamescore(prev, new) + _season(prev, new) + _injuries(prev, new) + _lines(prev, new)
    if not out:
        out = ["No change to goalies, picks, results, injuries, lines or Game Scores"]
    if len(out) > MAX_CHANGES:
        out = out[:MAX_CHANGES - 1] + [f"{len(out) - MAX_CHANGES + 1} more changes"]
    return out


def why_text(event, why):
    """Why the update ran, from the workflow (NDA_EVENT, NDA_WHY), in one line."""
    event = str(event or "")
    why = " ".join(str(why or "").split())[:WHY_MAX]
    if event.startswith("workflow_dispatch"):
        return "Run by hand"
    if why:
        return why
    return "Built outside the workflow" if not event else "Automatic update"


def stamp(state_dir, prev, new, event=None, why=None):
    """Number this update, record it in state/updates.json, and return (update,
    update_log) for the data file: the new record and the newest PUBLISH records,
    newest first."""
    path = os.path.join(state_dir, HIST_NAME)
    hist = (_load(path) or {}).get("updates") or []
    hist = [u for u in hist if isinstance(u, dict)]
    date = new.get("date")
    n = number(hist, date)
    rec = {"id": f"{date}.{n}", "date": date, "n": n, "utc": new.get("generated_utc"),
           "why": why_text(event, why), "changes": changes(prev, new)}
    hist = (hist + [rec])[-KEEP:]
    os.makedirs(state_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"updates": hist}, fh, indent=1, ensure_ascii=True)
    os.replace(tmp, path)
    return rec, list(reversed(hist[-PUBLISH:]))
