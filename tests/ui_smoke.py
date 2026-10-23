"""Browser check of the app with the sample data: every tab renders without a
script error at phone, iPad, Android-tablet and desktop sizes, nothing scrolls
sideways, My Teams is remembered, the goalie table runs from the highest save
percentage down, and no wager or chat-command wording appears."""
import functools
import http.server
import json
import os
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
                ctx = b.new_context(viewport={"width": w, "height": h}, is_mobile=mob)
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
                    text = pg.inner_text("main").lower()
                    for word in BANNED:
                        if word in text:
                            fails.append(f"{name} {t}: banned wording '{word}'")
                n_cards = pg.evaluate("0")
                pg.click('#tabs button[data-tab="today"]')
                n_cards = pg.locator("article.game").count()
                if n_cards != len(sample["slate"]["games"]):
                    fails.append(f"{name}: {n_cards} game cards for {len(sample['slate']['games'])} games")
                pg.click('#tabs button[data-tab="goalies"]')
                svs = [c.inner_text() for c in pg.locator("table tbody tr td:nth-child(3)").all()]
                vals = [float(v) for v in svs if v.strip().startswith(".")]
                if vals != sorted(vals, reverse=True):
                    fails.append(f"{name}: goalies not sorted by SV% {vals}")
                pg.reload()
                pg.wait_for_timeout(100)
                kept = pg.evaluate("(JSON.parse(localStorage.getItem('nda:v1')||'{}').teams||[])")
                if kept != ["FLA"]:
                    fails.append(f"{name}: My Teams not remembered ({kept})")
                if errs:
                    fails.append(f"{name} {url[:4]}: script errors {errs[:3]}")
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
