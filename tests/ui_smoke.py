"""Browser check of the app with the sample data: every tab renders without a
script error at phone, iPad, Android-tablet and desktop sizes, nothing scrolls
sideways, My Teams is remembered, the goalie table runs from the highest save
percentage down, no wager or chat-command wording appears, and every tag, tile,
column heading and abbreviation shows its definition on hover (mouse), on tap
(touch screen) and on keyboard focus."""
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
TABS = ["today", "myteams", "lines", "goalies", "matchups", "results", "model", "dfs", "settings", "help"]
SIZES = [("phone", 390, 844, True), ("ipad", 834, 1194, True), ("android", 800, 1280, True), ("desktop", 1280, 900, False)]
BANNED = ["!bet", "discord", "points staked", "base net", "allocation"]
# Checked independently of the app's own tables: any of these left as plain text is a miss.
ABBREV_HEADS = {"#", "GP", "G", "A", "P", "SOG", "PPP", "TOI/GP", "TOI", "PIM", "PP", "Pos", "Unit", "SV%", "GSAx",
                "HDSV%", "Window", "Rest", "Status", "Win %", "Tier", "Opp", "DK", "L3", "L3 vs L5", "L5 shot%",
                "L3 shot%", "Venue shot%", "Venue GP", "TOI share", "Home edge", "Band"}
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
