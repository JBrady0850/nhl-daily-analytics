"""Browser check of the app with the sample data: every tab renders without a
script error at phone, iPad, Android-tablet and desktop sizes, nothing scrolls
sideways, My Teams is remembered, the goalie table runs from the highest save
percentage down, no wager or chat-command wording appears, and every tag, tile,
column heading and abbreviation shows its definition on hover (mouse), on tap
(touch screen) and on keyboard focus, and the Standings tab shows all 32 teams in
each of its four views (Wild Card, Division, Conference, League) and remembers the
chosen view. With a fake clock it also checks the "next update" line: the countdown,
the due and late states, the automatic checks while an update is due, and the saved
copy note."""
import functools
import http.server
import json
import os
import re
import shutil
import sys
import tempfile
import threading

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TABS = ["today", "myteams", "lines", "goalies", "injuries", "matchups", "standings", "results", "model", "dfs", "settings", "help"]
SIZES = [("phone", 390, 844, True), ("ipad", 834, 1194, True), ("android", 800, 1280, True), ("desktop", 1280, 900, False)]
BANNED = ["!bet", "discord", "points staked", "base net", "allocation"]
# Checked independently of the app's own tables: any of these left as plain text is a miss.
ABBREV_HEADS = {"#", "GP", "G", "A", "P", "SOG", "PPP", "TOI/GP", "TOI", "PIM", "PP", "Pos", "Unit", "SV%", "GSAx",
                "HDSV%", "Window", "Rest", "Status", "Win %", "Tier", "Opp", "DK", "L3", "L3 vs L5", "L5 shot%",
                "L3 shot%", "Venue shot%", "Venue GP", "TOI share", "Home edge", "Band",
                "Rk", "W", "L", "OTL", "PTS", "P%", "RW", "ROW", "GF", "GA", "DIFF", "L10", "STRK"}
TOKENS = ["GP", "SOG", "PPP", "PIM", "TOI", "RAA", "GSAx", "HDSV%", "SV%", "DFS", "DK", "GTD", "DTD", "OT", "SO",
          "PP1", "PP2", "F1", "F2", "F3", "F4", "D1", "D2", "D3", "P1", "P2", "P3", "P4", "P5", "L3", "L5", "NHL", "UTC"]
COVERAGE_JS = """(args) => {
  const [heads, tokens] = args, miss = [];
  const main = document.querySelector('main');
  main.querySelectorAll('.pill, .tile').forEach(p => { if (!p.dataset.tip) miss.push('no tip on ' + p.className + ' ' + p.textContent.trim()); });
  main.querySelectorAll('th').forEach(h => { const t = h.textContent.trim(); if ((heads.includes(t) || /^[A-Z]{3}$/.test(t)) && !h.dataset.tip) miss.push('no tip on heading ' + t); });
  const re = new RegExp('(^|[^A-Za-z0-9%/])(' + tokens.join('|') + ')(?![A-Za-z0-9%/])');
  const w = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) {
    const n = w.currentNode, el = n.parentElement;
    if (!el || el.closest('[data-tip],abbr,a,button,.gloss,code')) continue;
    const m = n.nodeValue.match(re);
    if (m) miss.push('plain abbreviation ' + m[2] + ' in "' + n.nodeValue.trim().slice(0, 40) + '"');
  }
  return miss.slice(0, 5);
}"""


STAND_CARDS = {"wildcard": 2, "division": 4, "conference": 2, "league": 1}
STAND_JS = """() => {
  const main = document.querySelector('main');
  const rows = [...main.querySelectorAll('table.standings tbody tr:not(.grp)')];
  return {
    pressed: [...main.querySelectorAll('.seg.stand button[aria-pressed="true"]')].map(b => b.dataset.arg),
    cards: main.querySelectorAll('table.standings').length,
    teams: rows.map(r => r.querySelector('.tm').textContent.trim()),
    pts: rows.map(r => +r.children[6].textContent),
    mine: rows.filter(r => r.classList.contains('lead')).map(r => r.querySelector('.tm').textContent.trim()),
    cuts: main.querySelectorAll('table.standings tr.cut').length,
    groups: [...main.querySelectorAll('table.standings tr.grp')].map(r => r.textContent.trim()),
    streakTips: main.querySelectorAll('table.standings td abbr.tip[data-tip]').length,
    sideways: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1
  };
}"""


def check_standings(pg, sample):
    """Each of the four views lists all 32 teams once, in the expected cards, and the
    choice survives a reload."""
    fails, all_teams = [], sorted(sample["teams"])
    pg.click('#tabs button[data-tab="standings"]')
    for view, n_cards in STAND_CARDS.items():
        pg.click(f'main .seg.stand button[data-arg="{view}"]')
        pg.wait_for_timeout(40)
        r = pg.evaluate(STAND_JS)
        if r["pressed"] != [view]:
            fails.append(f"standings {view}: selector shows {r['pressed']}")
        if r["cards"] != n_cards:
            fails.append(f"standings {view}: {r['cards']} tables, expected {n_cards}")
        if sorted(r["teams"]) != all_teams:
            fails.append(f"standings {view}: {len(r['teams'])} team rows, {len(set(r['teams']))} different teams")
        if r["mine"] != ["FLA"]:
            fails.append(f"standings {view}: shaded rows {r['mine']}, expected FLA")
        if r["streakTips"] < 32:
            fails.append(f"standings {view}: {r['streakTips']} streaks carry a definition")
        if r["sideways"]:
            fails.append(f"standings {view}: page scrolls sideways")
        if view == "wildcard":
            if r["cuts"] != 2 or r["groups"].count("Wild Card") != 2 or len(r["groups"]) != 6:
                fails.append(f"standings wildcard: groups {r['groups']}, playoff lines {r['cuts']}")
        if view == "league" and r["pts"] != sorted(r["pts"], reverse=True):
            fails.append(f"standings league: points not in order {r['pts'][:8]}")
    pg.click('main .seg.stand button[data-arg="division"]')
    pg.reload()
    pg.wait_for_timeout(100)
    pg.click('#tabs button[data-tab="standings"]')
    if pg.evaluate(STAND_JS)["pressed"] != ["division"]:
        fails.append("standings: chosen view not remembered after a reload")
    pg.click('main .seg.stand button[data-arg="wildcard"]')
    return fails


LINES_JS = """() => [...document.querySelectorAll('main section.card')].map(c => ({
  team: (c.querySelector('h2 .tm') || {}).textContent,
  units: [...c.querySelectorAll('tbody .unit')].map(u => u.textContent.trim()),
  pairRows: [...c.querySelectorAll('tbody tr')].filter(r => !r.classList.contains('tot')).map(r => [...r.children].map(td => td.textContent.trim())),
  totals: [...c.querySelectorAll('tbody tr.tot')].map(r => r.children[2].textContent.trim())
}))"""


def check_lines_tab(pg, sample):
    """Each projected winner shows its four forward lines and three defense pairs, and
    every defenseman's row carries his stats (John, 2026-10-23)."""
    fails = []
    pg.click('#tabs button[data-tab="lines"]')
    pg.wait_for_timeout(40)
    cards = pg.evaluate(LINES_JS)
    want = [g["projected"] for g in sample["slate"]["games"] if g.get("projected")]
    if [c["team"] for c in cards] != want:
        fails.append(f"lines tab: cards for {[c['team'] for c in cards]}, expected {want}")
    for c in cards:
        L = sample["lines"][c["team"]]
        if c["units"] != [f"F{x['line']}" for x in L["lines"]] + [f"D{x['pair']}" for x in L["pairs"]]:
            fails.append(f"lines tab {c['team']}: units {c['units']}")
        n_d = sum(len(x["players"]) for x in L["pairs"])
        d_rows = c["pairRows"][-n_d:] if n_d else []
        names = [p["name"] for x in L["pairs"] for p in x["players"]]
        if len(d_rows) != n_d or any(not any(nm in cell for cell in r) for r, nm in zip(d_rows, names)):
            fails.append(f"lines tab {c['team']}: defense pair rows missing")
        elif any(not re.match(r"^\d+$", r[4]) for r in d_rows):
            fails.append(f"lines tab {c['team']}: a defenseman has no GP")
        if sum(1 for t in c["totals"] if t.startswith("D")) != len(L["pairs"]):
            fails.append(f"lines tab {c['team']}: pair totals missing")
    return fails


def ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def check_team_places(pg, sample, ab, mob):
    """My Teams: a followed team's card shows its Wild Card, Division, Conference and
    League places, each with a definition (John, 2026-10-23)."""
    r = next(x for x in sample["standings"]["teams"] if x["team"] == ab)
    want = ["Top 3" if not r["wc_rank"] else ordinal(r["wc_rank"]), ordinal(r["div_rank"]), ordinal(r["conf_rank"]), ordinal(r["league_rank"])]
    pg.click('#tabs button[data-tab="myteams"]')
    pg.wait_for_timeout(40)
    got = pg.evaluate("""(ab) => { const c = [...document.querySelectorAll('main section.teamcard')].find(s => s.querySelector('.tc-head .tm').textContent.trim() === ab);
      if (!c) return null; return [...c.querySelectorAll('.splaces .spos')].map(e => [e.querySelector('.k').textContent.trim(), e.querySelector('b').textContent.trim(), !!e.dataset.tip]); }""", ab)
    if not got:
        return [f"my teams {ab}: no standings places"]
    out = []
    if [g[0] for g in got] != ["Wild Card", "Division", "Conference", "League"] or [g[1] for g in got] != want or not all(g[2] for g in got):
        out.append(f"my teams {ab}: places {got}, expected {want}")
    first = pg.locator("main .splaces .spos").first
    (first.tap() if mob else first.hover())
    pg.wait_for_timeout(60)
    box = pg.evaluate("(() => { const b = document.getElementById('tipbox'); return {shown: !b.hidden, title: (b.querySelector('b') || {}).textContent}; })()")
    if not box["shown"] or box["title"] != "Wild Card":
        out.append(f"my teams {ab}: the Wild Card place shows no definition ({box})")
    return out


WIN_MARKER_JS = """() => [...document.querySelectorAll('main .winbar')].map(w => {
  const p = w.querySelector('.puck'), bg = getComputedStyle(p).backgroundImage;
  const rgb = h => [1, 3, 5].map(i => parseInt(h.trim().slice(i, i + 2), 16));
  const gold = h => { const [r, g, b] = rgb(h); return r > 190 && g > 140 && b < 110; };
  const ca = w.style.getPropertyValue('--ca'), cb = w.style.getPropertyValue('--cb');
  return {sides: [ca.trim(), cb.trim()], white: p.classList.contains('lt'), wantWhite: gold(ca) || gold(cb),
          yellow: bg.includes('rgb(255, 210, 31)'), whiteFill: bg.includes('rgb(255, 255, 255)')};
})"""


def check_win_marker(pg):
    """Today: the win marker is yellow, or white where a side is a gold club color, so it
    stands out on every pair of team colors (John, 2026-10-23)."""
    pg.click('#tabs button[data-tab="today"]')
    pg.wait_for_timeout(40)
    out = []
    for m in pg.evaluate(WIN_MARKER_JS):
        if m["white"] != m["wantWhite"] or not (m["whiteFill"] if m["white"] else m["yellow"]):
            out.append(f"win marker on {m['sides']}: white={m['white']} yellow={m['yellow']}")
    return out[:3]


GOALIE_ROWS_JS = """() => [...document.querySelectorAll('main article.game')].map(card => {
  const c = card.getBoundingClientRect(), g = card.querySelector('.gks');
  if (!g) return {missing: true};
  const n = [...g.querySelectorAll('.gk-n')].map(e => e.getBoundingClientRect());
  const s = [...g.querySelectorAll('.gk-s')].map(e => (e.querySelector('.pill') || e).getBoundingClientRect());
  return {missing: n.length !== 2 || s.length !== 2,
          sameRow: s.every((r, i) => !r.height || (r.top < n[i].bottom && r.bottom > n[i].top)),
          separate: n.length === 2 && n[1].top >= n[0].bottom - 1,
          apart: s.every((r, i) => !r.width || r.left >= n[i].right - 0.5),
          inside: n.concat(s).every(r => r.right <= c.right + 0.5)};
})"""


def check_goalie_rows(pg, n_games):
    """Today: each game's two goalies sit on their own rows and each status tag stays on
    its goalie's row, inside the card (John, 2026-10-23)."""
    pg.click('#tabs button[data-tab="today"]')
    pg.wait_for_timeout(40)
    rows = pg.evaluate(GOALIE_ROWS_JS)
    if len(rows) != n_games:
        return [f"today: {len(rows)} game cards with goalie rows, expected {n_games}"]
    out = []
    for i, r in enumerate(rows):
        bad = [k for k in ("sameRow", "separate", "apart", "inside") if not r.get(k)] if not r.get("missing") else ["missing"]
        if bad:
            out.append(f"today card {i + 1}: goalie rows {bad}")
    return out[:3]


DFS_GOALIES_JS = """() => {
  const t = [...document.querySelectorAll('main section.card')].find(c => /Goalies/.test(c.querySelector('h2').textContent));
  if (!t) return null;
  const col = [...t.querySelectorAll('thead th')].findIndex(h => h.textContent.trim() === 'Win %');
  return [...t.querySelectorAll('tbody tr')].map(r => parseFloat(r.children[col].textContent));
}"""


def check_dfs_goalies(pg, n):
    """The DFS goalie board lists every probable starter, highest win % first (John, 2026-10-23)."""
    pg.click('#tabs button[data-tab="dfs"]')
    pg.wait_for_timeout(40)
    wp = pg.evaluate(DFS_GOALIES_JS)
    if wp is None or len(wp) != n:
        return [f"dfs: {None if wp is None else len(wp)} goalie rows, expected {n}"]
    if wp != sorted(wp, reverse=True):
        return [f"dfs: goalies not sorted by win % {wp}"]
    return []


PICK_JS = """() => [...document.querySelectorAll('main article.game')].map(c => {
  const p = c.querySelector('.pickline');
  if (!p) return null;
  const mp = p.querySelector('.pill.mypick') || {dataset: {}}, star = p.querySelector('.pk-star svg use');
  const sb = p.querySelector('.pk-star'), r = sb ? sb.getBoundingClientRect() : {width: 0};
  return {n: c.querySelectorAll('.pickline').length, pill: mp.textContent || '', tip: !!mp.dataset.tip,
    team: (p.querySelector('.tm') || {}).textContent || '', name: (p.querySelector('.pick') || {}).textContent || '',
    pct: (p.querySelector('.pick-pct > b') || {}).textContent || '',
    star: !!star && star.getAttribute('href') === '#i-star' && r.width >= 30,
    gold: getComputedStyle(p, '::before').backgroundImage.includes('rgb(255, 204, 31)'),
    ink: getComputedStyle(p.querySelector('.pick') || p).color};
})"""


def check_my_pick(pg, sample):
    """Every game card shows the pick on a gold banner with a star crest: a MY PICK
    tag (with its definition), the team's badge, its name and the win probability,
    in the same dark ink on gold in the light and the dark theme."""
    pg.click('#tabs button[data-tab="today"]')
    fails = []
    for scheme in ("light", "dark"):
        pg.emulate_media(color_scheme=scheme)
        got = pg.evaluate(PICK_JS)
        for g, c in zip(sample["slate"]["games"], got):
            if not g.get("projected"):
                continue
            want = {"n": 1, "pill": "My pick", "tip": True, "team": g["projected"], "name": sample["teams"][g["projected"]]["name"],
                    "pct": f"{g['win_prob'] * 100:.1f}%", "star": True, "gold": True, "ink": "rgb(15, 26, 43)"}
            if c != want:
                fails.append(f"pick banner on {g['matchup']} ({scheme}): {c} (want {want})")
                break
    pg.emulate_media(color_scheme="light")   # the contexts use the default, light
    return fails


FINAL_JS = """() => [...document.querySelectorAll('main article.game')].map(c => {
  const f = c.querySelector('.finalbar');
  if (!f) return null;
  const p = f.querySelector('.pill') || {dataset: {}, classList: []};
  return {after_banner: !!(f.previousElementSibling && f.previousElementSibling.classList.contains('pickline')),
    label: f.querySelector('.fs-lab').textContent, scores: [...f.querySelectorAll('.fs-team')].map(t => t.textContent.replace(/\\s+/g, ' ').trim()),
    winner: (f.querySelector('.fs-team.w > span') || {}).textContent || '', tag: p.textContent, tip: !!p.dataset.tip};
})"""


def check_final_results(pg, sample):
    """A finished game shows its final score on a scoreboard strip right under the
    pick banner, the winner lit, and WIN or LOSS for the pick (with a definition);
    a game not yet over shows no strip."""
    pg.click('#tabs button[data-tab="today"]')
    got, fails, seen = pg.evaluate(FINAL_JS), [], set()
    for g, c in zip(sample["slate"]["games"], got):
        r = g.get("result")
        if not r:
            if c is not None:
                fails.append(f"final strip on unfinished {g['matchup']}: {c}")
            continue
        per = "" if r["last_period"] == "REG" else "/" + r["last_period"]
        want = {"after_banner": True, "label": "Final" + per,
                "scores": [f"{g['away']} {r['away_score']}", f"{g['home']} {r['home_score']}"],
                "winner": r["winner"], "tag": "Win" if r["pick"] == "win" else "Loss", "tip": True}
        if c != want:
            fails.append(f"final strip on {g['matchup']}: {c} (want {want})")
        seen.add(r["pick"])
    if seen != {"win", "loss"}:
        fails.append(f"the sample should show a won and a lost pick, saw {sorted(seen)}")
    return fails


INJ_JS = """() => {
  const main = document.querySelector('main');
  return {
    pressed: [...main.querySelectorAll('.seg.inj button[aria-pressed="true"]')].map(b => b.dataset.arg),
    teams: [...main.querySelectorAll('section.card[data-team]')].map(s => s.dataset.team),
    rows: main.querySelectorAll('table.inj tbody tr').length,
    links: main.querySelectorAll('table.inj tbody a.plink').length,
    pills: [...main.querySelectorAll('table.inj .pill.inj')].map(p => [p.textContent.trim(), !!p.dataset.tip]),
    lineup: [...main.querySelectorAll('table.inj .pill.ok')].map(p => { const td = p.closest('td'), a = td.querySelector('a.plink');
      return [(a ? a.childNodes[0] : td.childNodes[0]).textContent.trim(), !!p.dataset.tip]; }),
    statusTip: (main.querySelector('table.inj th:nth-child(3)') || {dataset: {}}).dataset.tip || '',
    tiles: [...main.querySelectorAll('.tile b')].map(b => +b.textContent),
    empty: (main.querySelector('.card.empty') || {}).textContent || ''
  };
}"""


def check_injuries_tab(pg, sample, mob):
    """The Injuries tab lists every injured player by team with a status tag and
    its definition, links players with an NHL id, marks the one Daily Faceoff has
    in today's lineup, filters to today's games and to My Teams (FLA is followed
    by now), and remembers the filter."""
    rows, fails = sample["injuries"]["players"], []
    today = {t for g in sample["slate"]["games"] for t in (g["away"], g["home"])}
    pg.click('#tabs button[data-tab="injuries"]')
    pg.click('main .seg.inj button[data-arg="all"]')
    a = pg.evaluate(INJ_JS)
    ids = sum(1 for r in rows if r.get("player_id"))
    if a["rows"] != len(rows) or a["links"] != ids:
        fails.append(f"injuries, all teams: {a['rows']} rows and {a['links']} links (want {len(rows)} and {ids})")
    if a["tiles"][:1] != [len(rows)] or sum(a["tiles"][1:]) != len(rows):
        fails.append(f"injury tiles {a['tiles']} do not add up to {len(rows)}")
    if not all(t for _, t in a["pills"]) or len(a["pills"]) != len(rows):
        fails.append("an injury status tag has no definition")
    if "LTIR long-term injured reserve" not in a["statusTip"]:
        fails.append(f"Status heading on Injuries has the wrong definition: {a['statusTip'][:60]!r}")
    lineup = [r["name"] for r in rows if r.get("in_lineup")]
    if [n for n, _ in a["lineup"]] != lineup or not all(t for _, t in a["lineup"]):
        fails.append(f"IN LINEUP tags {a['lineup']} (want {lineup}, each with a definition)")
    pg.click('main .seg.inj button[data-arg="today"]')
    b = pg.evaluate(INJ_JS)
    want = sum(1 for r in rows if r["team"] in today)
    if b["pressed"] != ["today"] or not set(b["teams"]) <= today or b["rows"] != want:
        fails.append(f"injuries, today's games: teams {b['teams']} with {b['rows']} rows (want {want})")
    pg.click('main .seg.inj button[data-arg="mine"]')
    c = pg.evaluate(INJ_JS)
    fla = sum(1 for r in rows if r["team"] == "FLA")
    if c["teams"] != (["FLA"] if fla else []) or c["rows"] != fla:
        fails.append(f"injuries, My Teams: teams {c['teams']} with {c['rows']} rows (want FLA with {fla})")
    pg.reload()
    if pg.evaluate(INJ_JS)["pressed"] != ["mine"]:
        fails.append("the injuries filter is not remembered after a reload")
    pg.click('main .seg.inj button[data-arg="all"]')
    return fails


UPD_JS = "() => [...document.querySelectorAll('main [data-upd]')].map(e => [e.className, e.textContent.trim()])"


def check_update_status(b, http_url, file_url, sample):
    """The line under each title says when the next scheduled update is (9:00 AM and
    4:45 PM US Central, all year), counts down, turns into 'due' once a scheduled time passes without
    newer data, checks for the file on its own while due (every 5 minutes, every 15
    once late), loads newer data when it appears, and a saved copy says it does not
    update. The fixture was built at 2026-10-23T22:28:41Z; the clock is set around it."""
    from datetime import datetime, timezone
    fails = []
    built = sample["generated_utc"]
    newer = dict(sample, generated_utc="2026-10-24T14:31:00Z")
    hits = []

    def serve(state):
        def handler(route):
            hits.append(route.request.url)
            body = json.dumps(newer if state["new"] else sample)
            route.fulfill(status=200, content_type="application/json", body=body)
        return handler

    def opened(when, url=http_url, tz="America/Chicago"):
        ctx = b.new_context(viewport={"width": 1280, "height": 900}, timezone_id=tz,
                            locale="en-US", service_workers="block")
        state = {"new": False}
        ctx.route("**/data/latest.json*", serve(state))
        pg = ctx.new_page()
        pg.clock.install(time=datetime.fromisoformat(when.replace("Z", "+00:00")))
        hits.clear()
        pg.goto(url)
        pg.wait_for_timeout(300)
        return ctx, pg, state

    def lines(pg):
        return pg.evaluate(UPD_JS)

    def advance(pg, minutes):
        for _ in range(int(minutes * 3)):                 # the page ticks every 20 seconds
            pg.clock.run_for(20000)
        pg.wait_for_timeout(300)                          # let a started fetch finish

    # 1. an hour after the evening build: the next run is 14:00 UTC, 9:00 AM in Chicago
    ctx, pg, _ = opened("2026-10-23T23:28:00Z")
    got = lines(pg)
    want = "Next scheduled update about 9:00 AM CDT, in 14 h 32 min."
    if not got or got[0] != ["upd fresh", want]:
        fails.append(f"fresh status wrong: {got[:1]} (want {want})")
    advance(pg, 30)
    got = lines(pg)
    if not got or "in 14 h 2 min." not in got[0][1]:
        fails.append(f"status does not count down: {got[:1]}")
    if len(hits) != 1:
        fails.append(f"fresh data: the page should not keep checking ({len(hits)} requests)")
    pg.click("main button[data-act=refresh]")
    pg.wait_for_timeout(300)
    toast = pg.inner_text("#toast")
    if "Next scheduled update about 9:00 AM CDT" not in toast:
        fails.append(f"the check button does not say when the next update is: {toast!r}")
    for t in ("lines", "standings"):
        pg.click(f'#tabs button[data-tab="{t}"]')
        if not lines(pg):
            fails.append(f"no update status on the {t} tab")
    pg.click('#tabs button[data-tab="settings"]')
    txt = pg.inner_text("main")
    if "Twice a day, about 9:00 AM CDT and 4:45 PM CDT your time." not in txt:
        fails.append("Settings does not show the schedule in local time")
    for dt in ("Schedule", "Next update"):
        el = pg.locator("main dl.kv dt", has_text=dt)
        if el.count() != 1 or not el.first.get_attribute("data-tip"):
            fails.append(f"Settings row {dt!r} missing or without its definition")
    ctx.close()

    # 2. 20 minutes after the 14:00 UTC (9:00 AM CDT) run was due, with no newer file yet
    ctx, pg, state = opened("2026-10-24T14:20:00Z")
    got = lines(pg)
    if not got or got[0][0] != "upd due" or "New data is due: the 9:00 AM CDT update has not posted yet." not in got[0][1]:
        fails.append(f"due status wrong: {got[:1]}")
    advance(pg, 5)
    if len(hits) != 2:
        fails.append(f"while due the page should check every 5 minutes ({len(hits)} requests after 5 minutes)")
    state["new"] = True                                 # the run posts its file
    advance(pg, 5)
    got = lines(pg)
    if len(hits) != 3 or not got or got[0] != ["upd fresh", "Next scheduled update about 4:45 PM CDT, in 7 h 15 min."]:
        fails.append(f"newer data not picked up on its own: {len(hits)} requests, {got[:1]}")
    if "Updated to the latest data" not in pg.inner_text("#toast"):
        fails.append("no toast when newer data loads")
    ctx.close()

    # 3. more than three hours late: say so, and check less often
    ctx, pg, _ = opened("2026-10-24T17:10:00Z")
    got = lines(pg)
    want = "The 9:00 AM CDT update is 3 h 10 min late; GitHub may have skipped it."
    if not got or got[0][0] != "upd late" or want not in got[0][1] or "about 4:45 PM CDT" not in got[0][1]:
        fails.append(f"late status wrong: {got[:1]}")
    advance(pg, 10)
    n10 = len(hits)
    advance(pg, 5)
    if n10 != 1 or len(hits) != 2:
        fails.append(f"once late the page should check every 15 minutes ({n10} then {len(hits)} requests)")
    ctx.close()

    # 4a. standard time: the same 9:00 AM Central is 15:00 UTC after 2026-11-01
    ctx, pg, _ = opened("2026-11-02T15:20:00Z")
    got = lines(pg)
    if not got or got[0][0] != "upd due" or "the 9:00 AM CST update has not posted yet" not in got[0][1]:
        fails.append(f"standard-time status wrong: {got[:1]}")
    pg.click('#tabs button[data-tab="settings"]')
    if "Twice a day, about 9:00 AM CST and 4:45 PM CST your time." not in pg.inner_text("main"):
        fails.append("Settings schedule wrong in standard time")
    ctx.close()
    # 4b. a viewer in another time zone sees the same moment in their own time
    ctx, pg, _ = opened("2026-10-23T23:28:00Z", tz="America/New_York")
    got = lines(pg)
    if not got or got[0] != ["upd fresh", "Next scheduled update about 10:00 AM EDT, in 14 h 32 min."]:
        fails.append(f"Eastern-time viewer status wrong: {got[:1]}")
    ctx.close()

    # 4. a downloaded copy never updates, and says so
    ctx, pg, _ = opened("2026-10-24T14:20:00Z", file_url)
    got = lines(pg)
    if not got or got[0][0] != "upd copy" or "does not update" not in got[0][1]:
        fails.append(f"saved copy status wrong: {got[:1]}")
    ctx.close()
    if built != "2026-10-23T22:28:41Z":
        fails.append(f"fixture build time changed to {built}: update the times in check_update_status")
    return [f"update status: {m}" for m in fails]


SW_JS = """async () => {
  const keys = [];
  for (const name of await caches.keys()) for (const r of await (await caches.open(name)).keys()) keys.push(r.url);
  return keys.filter(u => u.includes('/data/latest.json'));
}"""


def check_sw_cache(b, http_url):
    """The offline copy keeps one entry per file: checking for newer data (which adds
    ?ts= to the address) must not leave a new 300 KB copy behind every time."""
    ctx = b.new_context(viewport={"width": 1280, "height": 900})
    pg = ctx.new_page()
    pg.goto(http_url)
    pg.wait_for_function("navigator.serviceWorker && navigator.serviceWorker.controller", timeout=15000)
    for _ in range(3):
        pg.click("main button[data-act=refresh]")
        pg.wait_for_timeout(400)
    keys = pg.evaluate(SW_JS)
    ctx.close()
    return [] if len(keys) == 1 else [f"offline cache holds {len(keys)} copies of the data file: {keys}"]


def main():
    tmp = tempfile.mkdtemp(prefix="nda_ui_")
    site = os.path.join(tmp, "app")
    shutil.copytree(os.path.join(ROOT, "app"), site)
    sample = json.load(open(os.path.join(HERE, "fixtures", "sample_latest.json"), encoding="utf-8"))
    open(os.path.join(site, "data", "latest.js"), "w", encoding="utf-8").write("window.NDA_DATA=" + json.dumps(sample) + ";\n")
    shutil.copy(os.path.join(HERE, "fixtures", "sample_latest.json"), os.path.join(site, "data", "latest.json"))
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=site)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    urls = [f"http://127.0.0.1:{httpd.server_address[1]}/index.html", "file://" + os.path.join(site, "index.html")]
    fails = []
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for url in urls:
            for name, w, h, mob in SIZES:
                ctx = b.new_context(viewport={"width": w, "height": h}, is_mobile=mob, has_touch=mob)
                # player pages are stubbed: the test never leaves this machine
                ctx.route("https://www.nhl.com/**", lambda r: r.fulfill(status=200, content_type="text/html", body="<title>NHL.com stub</title>"))
                pg = ctx.new_page()
                errs = []
                pg.on("pageerror", lambda e: errs.append(str(e)))
                pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
                pg.goto(url)
                pg.click('#tabs button[data-tab="myteams"]')
                pg.click('button.chip[data-arg="FLA"]')
                for t in TABS:
                    pg.click(f'#tabs button[data-tab="{t}"]')
                    pg.wait_for_timeout(60)
                    sw, cw = pg.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
                    if sw > cw + 1:
                        fails.append(f"{name} {t}: page scrolls sideways ({sw} > {cw})")
                    teams = list(sample["teams"])
                    for miss in pg.evaluate(COVERAGE_JS, [sorted(ABBREV_HEADS), TOKENS + teams]):
                        fails.append(f"{name} {t}: {miss}")
                    text = pg.inner_text("main").lower()
                    for word in BANNED:
                        if word in text:
                            fails.append(f"{name} {t}: banned wording '{word}'")
                n_cards = pg.evaluate("0")
                pg.click('#tabs button[data-tab="today"]')
                n_cards = pg.locator("article.game").count()
                if n_cards != len(sample["slate"]["games"]):
                    fails.append(f"{name}: {n_cards} game cards for {len(sample['slate']['games'])} games")
                labels = pg.evaluate("[...document.querySelectorAll('.winbar > span')].map(e => e.innerText.trim())")
                if len(labels) != 2 * n_cards or not all(re.match(r"^[A-Z]{3} \d", x) for x in labels):
                    fails.append(f"{name}: win bar labels broken {labels}")
                pg.click('#tabs button[data-tab="goalies"]')
                svs = [c.inner_text() for c in pg.locator("table tbody tr td:nth-child(3)").all()]
                vals = [float(v) for v in svs if v.strip().startswith(".")]
                if vals != sorted(vals, reverse=True):
                    fails.append(f"{name}: goalies not sorted by SV% {vals}")
                fails.extend(f"{name}: {m}" for m in check_standings(pg, sample))
                fails.extend(f"{name}: {m}" for m in check_goalie_rows(pg, len(sample["slate"]["games"])))
                fails.extend(f"{name}: {m}" for m in check_win_marker(pg))
                fails.extend(f"{name}: {m}" for m in check_team_places(pg, sample, "FLA", mob))
                fails.extend(f"{name}: {m}" for m in check_lines_tab(pg, sample))
                fails.extend(f"{name}: {m}" for m in check_dfs_goalies(pg, len(sample["dfs"]["goalies"])))
                fails.extend(f"{name}: {m}" for m in check_my_pick(pg, sample))
                fails.extend(f"{name}: {m}" for m in check_final_results(pg, sample))
                fails.extend(f"{name}: {m}" for m in check_injuries_tab(pg, sample, mob))
                pg.click('#tabs button[data-tab="today"]')
                tile = pg.locator(".tile").nth(2)
                if mob:
                    tile.tap()
                else:
                    tile.hover()
                pg.wait_for_timeout(80)
                box = pg.evaluate("(() => { const b = document.getElementById('tipbox'); const r = b.getBoundingClientRect(); return {shown: !b.hidden, text: b.textContent, left: r.left, right: r.right, vw: document.documentElement.clientWidth}; })()")
                if not box["shown"] or "gate" not in box["text"].lower():
                    fails.append(f"{name}: tile definition did not show ({box})")
                elif box["left"] < 0 or box["right"] > box["vw"]:
                    fails.append(f"{name}: definition box runs off the screen ({box})")
                if mob:
                    pg.locator("h1").tap()
                    pg.wait_for_timeout(50)
                    if pg.evaluate("!document.getElementById('tipbox').hidden"):
                        fails.append(f"{name}: definition stayed open after tapping elsewhere")
                pill = pg.locator("article.game .pill").first
                (pill.tap() if mob else pill.hover())
                pg.wait_for_timeout(60)
                if pg.evaluate("document.getElementById('tipbox').hidden"):
                    fails.append(f"{name}: tag definition did not show")
                if not mob:
                    pg.locator("h1").hover()
                    pg.keyboard.press("Escape")
                    pg.click('#tabs button[data-tab="dfs"]')
                    pg.keyboard.press("Shift")           # a key press marks the next focus as keyboard focus
                    pg.locator("th.has-tip").first.focus()
                    pg.wait_for_timeout(50)
                    if pg.evaluate("document.getElementById('tipbox').hidden"):
                        fails.append(f"{name}: keyboard focus did not show the definition")
                    # a heading below the fold: focusing it scrolls the page, and the
                    # definition must stay open and sit on screen next to it
                    pg.evaluate("window.scrollTo(0, 0)")
                    pg.locator("th.has-tip").last.focus()
                    pg.wait_for_timeout(80)
                    box = pg.evaluate("(() => { const b = document.getElementById('tipbox'); const r = b.getBoundingClientRect(); return {hidden: b.hidden, top: r.top, bottom: r.bottom, vh: innerHeight, sy: scrollY}; })()")
                    if box["hidden"] or box["top"] < 0 or box["bottom"] > box["vh"]:
                        fails.append(f"{name}: definition lost after focus scrolled the page ({box})")
                pg.click('#tabs button[data-tab="myteams"]')
                links = pg.evaluate("[...document.querySelectorAll('main a.plink')].map(a => [a.getAttribute('href'), a.target, a.rel, a.childNodes[0].textContent])")
                want = sum(1 for grp in ("lines", "pairs") for ln in sample["lines"]["FLA"][grp] for p in ln["players"] if p.get("nhl_id"))
                if len(links) != want or want < 15:
                    fails.append(f"{name}: {len(links)} player links on My Teams, expected {want}")
                bad = [l for l in links if not re.match(r"^https://www\.nhl\.com/player/\d{6,8}$", l[0] or "") or l[1] != "_blank" or "noopener" not in (l[2] or "")]
                if bad:
                    fails.append(f"{name}: bad player links {bad[:2]}")
                first = pg.locator("main a.plink").first
                with ctx.expect_page() as info:
                    (first.tap() if mob else first.click())
                player_page = info.value
                player_page.wait_for_load_state()
                if not player_page.url.startswith("https://www.nhl.com/player/"):
                    fails.append(f"{name}: player name opened {player_page.url}")
                if not mob and player_page.evaluate("window.name") != "nhl-player":
                    fails.append(f"{name}: player page did not open in its own window")
                if not pg.url.endswith("index.html"):
                    fails.append(f"{name}: the app itself navigated away to {pg.url}")
                player_page.close()
                for t2, sel in (("goalies", "main table tbody a.plink"), ("dfs", "main table tbody a.plink"), ("today", "article.game a.plink")):
                    pg.click(f'#tabs button[data-tab="{t2}"]')
                    if pg.locator(sel).count() == 0:
                        fails.append(f"{name} {t2}: no player links")
                pg.click('#tabs button[data-tab="myteams"]')
                n_before = pg.evaluate("(JSON.parse(localStorage.getItem('nda:v1')||'{}').teams||[]).length")
                pg.click('button.chip[data-arg="TBL"]')
                pg.click('button.chip[data-arg="TBL"]')
                if pg.evaluate("(JSON.parse(localStorage.getItem('nda:v1')||'{}').teams||[]).length") != n_before:
                    fails.append(f"{name}: team chips stopped toggling")
                pg.reload()
                pg.wait_for_timeout(100)
                kept = pg.evaluate("(JSON.parse(localStorage.getItem('nda:v1')||'{}').teams||[])")
                if kept != ["FLA"]:
                    fails.append(f"{name}: My Teams not remembered ({kept})")
                if errs:
                    fails.append(f"{name} {url[:4]}: script errors {errs[:3]}")
                ctx.close()
        fails.extend(check_update_status(b, urls[0], urls[1], sample))
        fails.extend(check_sw_cache(b, urls[0]))
        # a build note (here: the injury report fell back to Daily Faceoff) shows on
        # Today and Matchups, and Settings names the source actually used
        noted = os.path.join(tmp, "noted")
        shutil.copytree(site, noted)
        alt = json.loads(json.dumps(sample))
        alt["problems"] = ["ESPN's injury report could not be reached this run, so injuries come from "
                           "the injured lists on Daily Faceoff's team pages."]
        alt["sources"]["injuries"] = {"loaded": True, "source": "daily_faceoff", "sources": ["daily_faceoff"], "rows": 11}
        alt["standings"]["teams"][0]["clinch"] = "x"
        alt["dfs"]["goalies"] = list(reversed(alt["dfs"]["goalies"]))   # the app sorts, whatever the file order
        with open(os.path.join(noted, "data", "latest.js"), "w", encoding="utf-8") as fh:
            fh.write("window.NDA_DATA=" + json.dumps(alt) + ";\n")
        ctx = b.new_context(viewport={"width": 1280, "height": 900})
        pg = ctx.new_page()
        pg.goto("file://" + os.path.join(noted, "index.html"))
        for t in ("today", "matchups"):
            pg.click(f'#tabs button[data-tab="{t}"]')
            note = pg.locator("main .note.warn")
            if note.count() != 1 or "Daily Faceoff" not in note.inner_text():
                fails.append(f"build note missing on {t}")
        pg.click('#tabs button[data-tab="settings"]')
        if "11 players from Daily Faceoff's injured lists" not in pg.inner_text("main"):
            fails.append("Settings does not name the injury source")
        fails.extend(f"file order reversed: {m}" for m in check_dfs_goalies(pg, len(alt["dfs"]["goalies"])))
        # a team in a division spot shows Top 3 on My Teams
        pg.click('#tabs button[data-tab="myteams"]')
        pg.click('button.chip[data-arg="TBL"]')
        fails.extend(check_team_places(pg, alt, "TBL", False))
        # a clinch mark gets its definition; a file without standings says so
        pg.click('#tabs button[data-tab="standings"]')
        clinch = pg.locator("main .pill.clinch")
        if clinch.count() != 1 or "playoff spot" not in (clinch.first.get_attribute("data-tip") or ""):
            fails.append("clinch mark missing or without its definition")
        alt.pop("standings")
        with open(os.path.join(noted, "data", "latest.js"), "w", encoding="utf-8") as fh:
            fh.write("window.NDA_DATA=" + json.dumps(alt) + ";\n")
        pg.reload()
        pg.click('#tabs button[data-tab="standings"]')
        if "not in this data file yet" not in pg.inner_text("main"):
            fails.append("missing standings not explained")
        pg.click('#tabs button[data-tab="myteams"]')
        if pg.locator("main .splaces").count() or pg.locator("main section.teamcard").count() != 1:
            fails.append("My Teams without standings: places shown or card missing")
        ctx.close()
        b.close()
    httpd.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    for f in fails:
        print("FAIL", f)
    print("ui smoke:", "ok" if not fails else f"{len(fails)} problems")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
