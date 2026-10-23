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
import logging
import os
from collections import defaultdict

from . import intel as I
from .data import NHL_BASE, STATS_BASE, parse_toi_seconds

log = logging.getLogger("nhlda.lines")

SHIFT_TYPE = 517             # shift rows in the NHL shift chart (other codes are goal events)
POS_FORWARD = {"C": "C", "L": "LW", "R": "RW"}


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
        rows = sorted((row(p) for p in grp), key=lambda r: order.get(r["pos"], 9))
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
    got = {}
    if use_dfo:
        for i in range(0, len(teams), 4):                     # gentle on the source
            chunk = teams[i:i + 4]
            res = await asyncio.gather(*[I.DailyFaceoffLines(t).resolve(f) for t in chunk])
            got.update({t: dl for t, dl in zip(chunk, res)})
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
        out[t] = d
    status = {"teams": len(teams),
              "daily_faceoff": sum(1 for d in out.values() if d["source"] == "daily_faceoff"),
              "shift_chart": sum(1 for d in out.values() if d["source"] == "shift_chart"),
              "missing": [t for t, d in out.items() if d["source"] == "none"],
              "players": stats.get("players"), "nhl_stats": stats.get("nhl"),
              "unresolved": len(stats.get("unresolved") or [])}
    return out, status
