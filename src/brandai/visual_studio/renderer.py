from __future__ import annotations

import re
from base64 import b64encode
from dataclasses import dataclass
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image
from playwright.sync_api import Error as BrowserError
from playwright.sync_api import sync_playwright

from brandai.errors import GenerationBlockedError
from brandai.onboarding.visual import contrast_ratio, normalize_hex
from brandai.strategy.models import Platform

from .models import Aspect, LayoutChecks


FONT_FILES = {"Heebo": "heebo/Heebo.ttf", "Assistant": "assistant/Assistant.ttf",
              "Rubik": "rubik/Rubik.ttf", "Noto Sans Hebrew": "notosanshebrew/NotoSansHebrew.ttf",
              "Inter": "inter/Inter.ttf"}
PALETTE_USAGE = {"primary": 60, "secondary": 30, "accent": 10}


@dataclass(frozen=True)
class Slide:
    headline: str
    detail: str = ""


@dataclass(frozen=True)
class LayoutStyle:
    palette: tuple[str, str, str]
    colors: dict
    fonts: dict
    logo_geometry: dict
    template_id: str | None
    framing: str


def safe_zone(size: tuple[int, int], aspect: Aspect, platform: Platform, requested: int) -> dict[str, int]:
    width, height = size
    # A local editorial policy with extra room for vertical player chrome.
    floors = (
        {"top": 12, "bottom": 22, "left": 8, "right": 14}
        if aspect == "9:16" and platform in {"instagram", "tiktok", "youtube"}
        else {"top": 6, "bottom": 6, "left": 6, "right": 6}
    )
    return {edge: round(max(requested, percent) * (height if edge in {"top", "bottom"} else width) / 100)
            for edge, percent in floors.items()}


def _image_uri(image: Image.Image) -> str:
    stream = BytesIO()
    image.save(stream, format="PNG")
    return "data:image/png;base64," + b64encode(stream.getbuffer()).decode("ascii")


def _direction(value: str) -> Literal["rtl", "ltr"]:
    from unicodedata import bidirectional

    for char in value:
        kind = bidirectional(char)
        if kind in {"R", "AL"}:
            return "rtl"
        if kind == "L":
            return "ltr"
    return "ltr"


def _text_tag(tag: str, text: str, role: str) -> str:
    direction = _direction(text)
    return f'<{tag} class="{role}" dir="{direction}">{escape(text)}</{tag}>' if text else ""


def _pick_ink(branded: dict, backdrop: str) -> tuple[str, float]:
    options = []
    for value in (branded.get("neutral_dark"), branded.get("neutral_light")):
        if isinstance(value, str):
            try:
                options.append(normalize_hex(value))
            except ValueError:
                pass
    for candidates in (options, ["#000000", "#FFFFFF"]):
        if candidates:
            ink = max(candidates, key=lambda c: contrast_ratio(c, backdrop))
            contrast = contrast_ratio(ink, backdrop)
            if contrast >= 4.5:
                return ink, contrast
    raise GenerationBlockedError("unable to meet the text contrast requirement")


def _bounded(value: object, *, default: int, low: int, high: int) -> int:
    return min(high, max(low, value)) if type(value) is int else default


class ChromiumRenderer:
    def __init__(self, font_root: Path):
        self.font_root = font_root.resolve()

    def _fonts(self, fonts: dict) -> tuple[str, str, str, list[str]]:
        he = fonts.get("hebrew_family", "Heebo")
        en = fonts.get("english_family", "Inter")
        he = he if isinstance(he, str) and he in FONT_FILES and he != "Inter" else "Heebo"
        en = en if isinstance(en, str) and en in FONT_FILES else "Inter"
        local = []
        rules = []
        for family in dict.fromkeys((he, en)):
            path = (self.font_root / FONT_FILES[family]).resolve()
            if path.is_relative_to(self.font_root) and path.is_file() and path.stat().st_size <= 3_000_000:
                data = b64encode(path.read_bytes()).decode("ascii")
                rules.append(f'@font-face{{font-family:"{family}";src:url(data:font/ttf;base64,{data});font-weight:100 900}}')
                local.append(family)
        return he, en, "".join(rules), local

    def render(
        self, plate: Image.Image, slides: list[Slide], *, aspect: Aspect, platform: Platform,
        style: LayoutStyle, logo_uri: str | None, logo_ratio: float, logo_matte: str, directory: Path,
    ) -> LayoutChecks:
        width, height = plate.size
        primary, secondary, accent = style.palette
        ink, ratio = _pick_ink(style.colors, secondary)
        he, en, font_rules, local = self._fonts(style.fonts)
        zones = safe_zone(plate.size, aspect, platform,
                          _bounded(style.logo_geometry.get("safe_zone_percent"), default=6, low=6, high=30))
        desired_logo_width = _bounded(style.logo_geometry.get("minimum_digital_width_px"), default=48, low=48, high=width)
        logo_width = max(round(min(width, height) * .10), desired_logo_width)
        logo_height = max(1, round(logo_width / logo_ratio)) if logo_uri else 0
        if logo_uri and logo_width + logo_height * 2 > width - zones["left"] - zones["right"]:
            raise GenerationBlockedError("the approved logo and its clear space do not fit this aspect ratio")
        anchors = style.logo_geometry.get("allowed_anchors", [])
        valid_anchors = ("top_start", "top_end", "bottom_start", "bottom_end")
        anchor = next((value for value in anchors if value in valid_anchors), "top_start") if isinstance(anchors, list) else "top_start"
        panel_width = ".92" if style.framing == "wide_editorial" else ".78" if width > height else "1"
        panel_align = {"copy_space_start": "flex-start", "copy_space_end": "flex-end"}.get(style.framing, "center")
        minimum = min(width, height)
        scale = style.fonts.get("scale")
        scale = scale if isinstance(scale, dict) else {}
        headline_size = round(minimum * min(.082, max(.046, _bounded(scale.get("headline"), default=40, low=26, high=64) / 670)))
        detail_size = round(minimum * min(.045, max(.027, _bounded(scale.get("body"), default=16, low=12, high=30) / 485)))
        if style.template_id == "quote_or_stat":
            headline_size = round(headline_size * .92)
        if style.template_id == "educational_card":
            detail_size = round(detail_size * 1.12)
        density = 300 if aspect == "9:16" and platform in {"instagram", "tiktok", "youtube"} else 450
        if any(len(item.headline) > (100 if aspect == "9:16" else 140) or len(item.detail) > density for item in slides):
            raise GenerationBlockedError("the selected copy exceeds this visual template's text density")
        horizontal = zones["left"] + zones["right"]
        panel_px = round((width - horizontal) * float(panel_width))
        frames = self._frames(slides, _image_uri(plate), logo_uri, anchor)
        html = f"""<!doctype html><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'">
<style>
{font_rules}
@page {{ size: {width}px {height}px; margin: 0; }}
* {{ box-sizing: border-box; print-color-adjust: exact; -webkit-print-color-adjust: exact; }}
body {{ padding:0; margin:0; }}
.frame {{ position: relative; width:{width}px; height:{height}px; break-after: page; overflow:hidden; background:{secondary}; }}
.frame:last-child {{ break-after: auto; }}
.plate {{ position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }}
.safe {{ position:absolute; top:{zones["top"]}px; bottom:{zones["bottom"]}px; left:{zones["left"]}px;
         right:{zones["right"]}px; display:flex; flex-direction:column; gap:{round(minimum*.024)}px; }}
.spacer {{ flex:1; min-height:0; }}
.copy {{ width:{panel_px}px; align-self:{panel_align}; background:{secondary}; color:{ink};
         padding:{round(minimum*.035)}px; max-width:100%; border-radius:{round(minimum*.018)}px;
         box-shadow:0 12px 40px rgb(0 0 0 / .23); }}
.stripe {{ height:{round(minimum*.009)}px; margin-bottom:{round(minimum*.021)}px; border-radius:12px;
           background:linear-gradient(to right,{primary} 60%,{secondary} 60% 90%,{accent} 90%);
           outline:1px solid rgb(255 255 255 / .25); }}
.logo {{ align-self:flex-start; width:max-content; padding:{logo_height}px; border-radius:14px;
         display:block; line-height:0; background:{logo_matte}; }}
.logo.end {{ align-self:flex-end; }}
.logo img {{ width:{logo_width}px; height:auto; }}
h1,p {{ margin:0; font-family:"{en}", system-ui, sans-serif; text-align:start; overflow-wrap:anywhere; unicode-bidi:isolate; }}
[dir=rtl] {{ font-family:"{he}", system-ui, sans-serif; }}
h1 {{ font-size:{headline_size}px; line-height:1.18; font-weight:700; }}
p {{ font-size:{detail_size}px; line-height:1.45; font-weight:400; }}
h1+p {{ margin-top:{round(minimum*.018)}px; }}
</style>{frames}"""
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
                    page.route("**/*", lambda route: route.abort())
                    page.set_content(html, wait_until="load", timeout=15000)
                    page.evaluate("Promise.all([...document.images].map(image => image.decode()))")
                    page.evaluate("document.fonts.ready")
                    overflow = page.locator(".safe").evaluate_all("""nodes => nodes.some(node => {
                      const safe = node.getBoundingClientRect();
                      return [...node.querySelectorAll('.copy,.logo')].some(el => {
                        const b = el.getBoundingClientRect();
                        return b.top < safe.top - 1 || b.bottom > safe.bottom + 1 ||
                               b.left < safe.left - 1 || b.right > safe.right + 1 ||
                               el.scrollWidth > el.clientWidth + 1;
                      });
                    })""")
                    if overflow:
                        raise GenerationBlockedError("layout content exceeds approved logo or text safe zones")
                    for index in range(len(slides)):
                        page.locator(".frame").nth(index).screenshot(path=directory / f"slide-{index + 1:02}.png",
                                                                      animations="disabled")
                    if len(slides) > 1:
                        pdf = page.pdf(print_background=True, prefer_css_page_size=True)
                        # Normalize Chromium's PDF metadata timestamps without shifting byte offsets.
                        pdf = re.sub(rb"(/(?:CreationDate|ModDate)\s*\(D:)\d{14}", rb"\g<1>20000101000000", pdf)
                        (directory / "carousel.pdf").write_bytes(pdf)
                finally:
                    browser.close()
        except BrowserError as exc:
            raise GenerationBlockedError("Chromium could not compose the visual; check the local Playwright installation") from exc
        return LayoutChecks(
            contrast=round(ratio, 3), safe_zone_px=zones, logo_clear_space_px=logo_height, palette_usage=PALETTE_USAGE,
            local_fonts=local, template_id=style.template_id,
            directions=list(dict.fromkeys(_direction(value) for slide in slides for value in (slide.headline, slide.detail) if value)),
        )

    @staticmethod
    def _frames(slides: list[Slide], plate_uri: str, logo: str | None, anchor: str) -> str:
        mark = f'<span class="logo {anchor.split("_")[1]}"><img alt="" src="{logo}"></span>' if logo else ""
        top, bottom = (mark, "") if anchor.startswith("top") else ("", mark)
        direction = _direction(slides[0].headline or slides[0].detail)
        return "".join(
            f'<main class="frame" dir="{direction}">'
            f'<img class="plate" alt="" src="{plate_uri}"><section class="safe">'
            f'{top}<div class="spacer"></div><article class="copy"><div class="stripe"></div>'
            f'{_text_tag("h1", slide.headline, "headline")}{_text_tag("p", slide.detail, "detail")}'
            f'</article>{bottom}</section></main>' for slide in slides
        )
