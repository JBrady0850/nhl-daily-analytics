"""Browser check of the app with the sample data: every tab renders without a
script error at phone, iPad, Android-tablet and desktop sizes, nothing scrolls
sideways, My Teams is remembered, the goalie table runs from the highest save
percentage down, no wager or chat-command wording appears, and every tag, tile,
column heading and abbreviation shows its definition on hover (mouse), on tap
(touch screen) and on keyboard focus, and the Standings tab shows all 32 teams in
each of its four views (Wild Card, Division, Conference, League) and remembers the
chosen view."""
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
TABS = ["today", "myteams", "lines", "goalies", "matchups", "standings", "results", "model", "dfs", "settings", "help"]
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
