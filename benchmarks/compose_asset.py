from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from playwright.async_api import async_playwright

from common import GENERATED_DIR, LOG_DIR, write_json

RUNTIME = Path("C:/brandai-runtime")
FONT_DIR = RUNTIME / "assets" / "fonts"
BASE_IMAGE = GENERATED_DIR / "flux2-klein-768x768.png"
OUTPUT = GENERATED_DIR / "composed-hebrew-english.png"


async def main():
    if not BASE_IMAGE.exists():
        raise FileNotFoundError(BASE_IMAGE)
    base_image_data = base64.b64encode(BASE_IMAGE.read_bytes()).decode("ascii")
    font_data = base64.b64encode((FONT_DIR / "Heebo.ttf").read_bytes()).decode("ascii")
    html = f"""<!doctype html><html dir=\"rtl\"><head><meta charset=\"utf-8\"><style>
    @font-face {{ font-family: HeeboLocal; src: url('data:font/ttf;base64,{font_data}') format('truetype'); font-weight: 100 900; font-display: block; }}
    * {{ box-sizing: border-box; }} body {{ margin: 0; width: 768px; height: 768px; overflow: hidden; font-family: HeeboLocal, sans-serif; }}
    .canvas {{ position: relative; width: 768px; height: 768px; background: url('data:image/png;base64,{base_image_data}') center/cover no-repeat; }}
    .shade {{ position:absolute; inset:0; background:linear-gradient(270deg,rgba(9,42,34,.78),rgba(9,42,34,.05) 62%); }}
    .copy {{ position:absolute; right:54px; top:58px; width:390px; color:#fff; text-align:right; direction:rtl; }}
    h1 {{ font-size:54px; line-height:1.02; margin:0 0 14px; font-weight:750; }}
    p {{ font-size:25px; line-height:1.35; margin:0; }}
    .brand {{ direction:ltr; unicode-bidi:isolate; font-weight:700; color:#f4d99c; }}
    .logo {{ position:absolute; left:48px; bottom:45px; width:148px; height:58px; direction:ltr; unicode-bidi:bidi-override; }}
    </style></head><body><div class=\"canvas\"><div class=\"shade\"></div><div class=\"copy\">
    <h1>עוצרים לרגע.<br>נשארים לקפה.</h1><p>קלייה מקומית, אווירה שכונתית,<br><span class=\"brand\">Luma Coffee</span></p></div>
    <svg class=\"logo\" viewBox=\"0 0 296 116\" xmlns=\"http://www.w3.org/2000/svg\" direction=\"ltr\" aria-label=\"Luma test logo\">
      <rect width=\"296\" height=\"116\" rx=\"22\" fill=\"#fff8e9\"/><circle cx=\"51\" cy=\"58\" r=\"27\" fill=\"#174c3c\"/>
      <path d=\"M38 60c8-16 23-16 30 0-11 10-20 10-30 0z\" fill=\"#e9c777\"/><text x=\"91\" y=\"72\" font-family=\"Arial\" font-size=\"48\" font-weight=\"700\" fill=\"#174c3c\">LUMA</text>
    </svg></div></body></html>"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 768, "height": 768}, device_scale_factor=1)
        await page.set_content(html, wait_until="load")
        fonts_ready = await page.evaluate("document.fonts.ready.then(() => document.fonts.check('700 54px HeeboLocal', 'עברית'))")
        await page.screenshot(path=str(OUTPUT))
        await browser.close()
    metrics = {"base_image": str(BASE_IMAGE), "output": str(OUTPUT), "font_ready": fonts_ready, "rtl": True}
    write_json(LOG_DIR / "composition-benchmark.json", metrics)
    print(OUTPUT)


asyncio.run(main())

