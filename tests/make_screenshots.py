"""Rewrite the README screenshots in docs/images from the sample data file.

Run only when the look of the app changes, then look at every image before
committing:  python tests/make_screenshots.py
"""
import json
import os
import shutil
import tempfile

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "docs", "images")

# (file, viewport width, height, mobile, tab, crop height in CSS pixels or None, saved width)
SHOTS = [
    ("today-desktop.png", 1280, 900, False, "today", None, 1280),
    ("today-phone.png", 390, 844, True, "today", 850, 520),
    ("myteams-phone.png", 390, 844, True, "myteams", 850, 520),
    ("goalies-tablet.png", 834, 1194, True, "goalies", 700, 900),
    ("model-desktop.png", 1280, 900, False, "model", None, 1280),
]


def main():
    tmp = tempfile.mkdtemp(prefix="nda_shots_")
    site = os.path.join(tmp, "app")
    shutil.copytree(os.path.join(ROOT, "app"), site)
    sample = json.load(open(os.path.join(HERE, "fixtures", "sample_latest.json"), encoding="utf-8"))
    with open(os.path.join(site, "data", "latest.js"), "w", encoding="utf-8") as fh:
        fh.write("window.NDA_DATA=" + json.dumps(sample) + ";\n")
    url = "file://" + os.path.join(site, "index.html")
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for name, w, h, mob, tab, crop, width in SHOTS:
            scale = 2 if mob else 1
            ctx = b.new_context(viewport={"width": w, "height": h}, is_mobile=mob, device_scale_factor=scale,
                                color_scheme="light")
            pg = ctx.new_page()
            pg.goto(url)
            pg.click('#tabs button[data-tab="myteams"]')
            for team in ("FLA", "TBL"):              # sample picks: one team off tonight, one playing
                pg.click(f'button.chip[data-arg="{team}"]')
            pg.click(f'#tabs button[data-tab="{tab}"]')
            pg.wait_for_timeout(150)
            raw = os.path.join(tmp, name)
            pg.screenshot(path=raw, full_page=crop is not None)
            ctx.close()
            im = Image.open(raw).convert("RGB")
            if crop:
                im = im.crop((0, 0, im.width, min(im.height, crop * scale)))
            if im.width > width:
                im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
            im.quantize(colors=128, method=Image.Quantize.MEDIANCUT).save(os.path.join(OUT, name), optimize=True)
            print("wrote", os.path.join("docs", "images", name))
        b.close()
    shutil.copy(os.path.join(ROOT, "app", "qr-app.png"), os.path.join(OUT, "open-app-qr.png"))
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
