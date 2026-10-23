"""Render the app icons (from app/icon.svg) and the 1200x630 link-preview image
(app/og-image.png, also the README banner) from the app's own artwork and styles.

Run after changing app/icon.svg or the rink artwork:  python tests/make_art.py
Look at every image before committing."""
import os
import shutil
import tempfile

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
svg = open(f"{ROOT}/app/icon.svg").read()
inner = svg.split(">", 1)[1].rsplit("</svg>", 1)[0]
# maskable: full-bleed board, artwork inside the 80 percent safe circle
mask = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="#0f2744"/>'
        '<g transform="translate(32 32) scale(.66) translate(-32 -32)">' + inner.replace('<rect width="64" height="64" rx="14" fill="#0f2744"/>', '') + '</g></svg>')
OG = f"""<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="file://{ROOT}/app/css/style.css">
<style>
 html,body{{margin:0;background:#0b1f3a;}}
 .og{{position:relative;width:1200px;height:630px;overflow:hidden;background:radial-gradient(900px 500px at 70% 30%, #183a63 0%, #0b1f3a 60%, #07152a 100%);color:#fff;font-family:system-ui,sans-serif;}}
 .og .rink{{position:absolute;inset:-40px -60px;width:1320px;height:710px;}}
 .og .rink .r-board{{stroke:#cfe0f0;opacity:.35}} .og .rink .r-red,.og .rink .r-ref{{opacity:.5}} .og .rink .r-blue line{{stroke:#5f93d6;opacity:.45}}
 .og .rink .r-center{{stroke:#5f93d6;opacity:.45}} .og .rink .r-mid{{opacity:.55}} .og .rink .r-crease{{opacity:.35}} .og .rink .r-dot{{opacity:.5}}
 .og-in{{position:absolute;left:72px;right:72px;top:70px;}}
 .og .eyebrow{{color:#ff8a7d;font-size:30px;letter-spacing:.14em}}
 .og h1{{font:600 150px/0.9 var(--display);margin:6px 0 10px;letter-spacing:.01em;color:#fff}}
 .og p.t{{font-size:30px;line-height:1.35;color:#d6e2ee;max-width:900px;margin:0 0 34px}}
 .og .winbar{{height:64px;border-radius:32px;max-width:760px;--ca:#1f5fa8;--cb:#c0392b;--split:58%;box-shadow:inset 0 0 0 3px rgba(255,255,255,.85),0 6px 20px rgba(0,0,0,.4)}}
 .og .winbar>span{{font-size:30px;padding:6px 16px 2px}} .og .winbar .puck{{width:32px;height:32px;margin:-16px 0 0 -16px}} .og .winbar .cc{{width:46px;height:46px;margin:-23px 0 0 -23px}}
 .og .kick{{position:absolute;left:0;right:0;bottom:0;height:14px;background:#d4a017}}
 .og .url{{position:absolute;right:72px;bottom:40px;font:500 30px/1 var(--display);letter-spacing:.06em;color:#cfe0f0}}
</style></head><body><div class="og">
__RINK__
<div class="og-in"><p class="eyebrow">Free daily analytics, updated twice a day</p><h1>NHL Daily Analytics</h1>
<p class="t">Every game ranked by the projected winner's win probability. Starting goalies, lines for your teams, results and model calibration.</p>
<div class="winbar" role="img" aria-label="sample"><i class="away"></i><i class="home"></i><i class="mk gl gl1"></i><i class="mk gl gl2"></i><i class="mk bl bl1"></i><i class="mk bl bl2"></i><i class="mk cl"></i><i class="mk cc"></i><i class="puck"></i><span class="l">AWAY 58.0%</span><span class="r">HOME 42.0%</span></div>
</div><div class="url">jbrady0850.github.io/nhl-daily-analytics</div><div class="kick"></div></div></body></html>"""
js = open(f"{ROOT}/app/js/app.js").read()
rink = js.split("const RINK = `", 1)[1].split("`;", 1)[0]
OG = OG.replace("__RINK__", rink)
with sync_playwright() as pw:
    b = pw.chromium.launch()
    for name, src, size in [("icon-180.png", svg, 180), ("icon-192.png", svg, 192), ("icon-512.png", svg, 512), ("icon-maskable-512.png", mask, 512)]:
        p = b.new_page(viewport={"width": size, "height": size})
        sized = src.replace("<svg ", '<svg width="%d" height="%d" ' % (size, size), 1)
        p.set_content('<html><body style="margin:0;background:transparent">' + sized + '</body></html>')
        p.screenshot(path=f"{ROOT}/app/{name}", omit_background=True, clip={"x": 0, "y": 0, "width": size, "height": size})
        p.close()
    p = b.new_page(viewport={"width": 1200, "height": 630}, reduced_motion="reduce")
    og_path = os.path.join(tempfile.mkdtemp(prefix="nda_art_"), "og.html")
    open(og_path, "w", encoding="utf-8").write(OG)
    p.goto("file://" + og_path, wait_until="load")
    p.wait_for_timeout(300)
    p.screenshot(path=f"{ROOT}/app/og-image.png", clip={"x": 0, "y": 0, "width": 1200, "height": 630})
    b.close()
shutil.copy(f"{ROOT}/app/og-image.png", f"{ROOT}/docs/images/banner.png")
print("wrote app/icon-*.png, app/og-image.png and docs/images/banner.png")
