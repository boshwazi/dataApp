"""Render the datawazi brand files in frontend/public from the mark in favicon.svg.

Run after changing the mark: python frontend/brand/render.py (needs Python Playwright; uses Microsoft Edge).
"""
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

PUBLIC = Path(__file__).resolve().parent.parent / "public"
BACKEND_BRAND = Path(__file__).resolve().parents[2] / "backend" / "app" / "brand"
MARK = (PUBLIC / "favicon.svg").read_text(encoding="utf-8")
FONTS = ("<link href='https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700&display=swap' "
         "rel='stylesheet'>")


def mark(size: int, square: bool = False) -> str:
    """The mark at `size` px; `square` drops the rounded corners for icons the platform rounds itself."""
    svg = MARK.replace("<svg ", f"<svg width='{size}' height='{size}' ", 1)
    return svg.replace('rx="12" ', "", 1) if square else svg


SOCIAL = f"""{FONTS}
<div style="width:1200px;height:630px;box-sizing:border-box;padding:88px 96px;background:#f6f7f9;
            font-family:Inter,sans-serif;color:#16202e;display:flex;flex-direction:column;justify-content:space-between">
  <div style="display:flex;align-items:center;gap:20px">
    {mark(76)}<span style="font-size:60px;font-weight:700;letter-spacing:-0.045em">datawazi</span>
  </div>
  <div>
    <div style="font-size:64px;font-weight:700;letter-spacing:-0.03em;line-height:1.08;max-width:900px">
      From messy data to checked dashboards.</div>
    <div style="margin-top:22px;font-size:28px;color:#4b5563;max-width:1008px">
      Get data, clean it, model it and report on it, with an AI that shows its work.</div>
  </div>
</div>"""

# file name -> (width, height, html)
FILES = {
    "favicon-32.png": (32, 32, mark(32)),
    "apple-touch-icon.png": (180, 180, mark(180, square=True)),
    "icon-192.png": (192, 192, mark(192)),
    "icon-512.png": (512, 512, mark(512)),
    "email-mark.png": (56, 56, mark(56)),  # shown at 28 px in emails, sharp on high-density screens
    "social-card.png": (1200, 630, SOCIAL),
}

with sync_playwright() as p:
    # Microsoft Edge by default (on every Windows PC); BROWSER_CHANNEL=chromium uses Playwright's own browser.
    browser = p.chromium.launch(channel=os.getenv("BROWSER_CHANNEL", "msedge"))
    for name, (w, h, body) in FILES.items():
        page = browser.new_page(viewport={"width": w, "height": h})
        page.set_content(f"<html><body style='margin:0;background:transparent'>{body}</body></html>",
                         wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.screenshot(path=PUBLIC / name, omit_background=True, clip={"x": 0, "y": 0, "width": w, "height": h})
        page.close()
        print("wrote", name)
    # The server attaches the email mark to the emails it sends.
    (BACKEND_BRAND / "email-mark.png").write_bytes((PUBLIC / "email-mark.png").read_bytes())
    browser.close()
