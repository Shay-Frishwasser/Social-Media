from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup

OFL_FONT_CATALOG = {"heebo": "Heebo", "assistant": "Assistant", "rubik": "Rubik",
                    "noto sans hebrew": "Noto Sans Hebrew", "inter": "Inter"}
DEFAULT_COLORS = ("#2457D6", "#17223B", "#F4B942")


@dataclass(slots=True)
class VisualSignals:
    colors: list[str]
    detected_fonts: list[str]
    recommended_fonts: dict[str, str]
    logo_candidates: list[str]
    style_prefix: str


@dataclass(slots=True)
class AgencyVisualSystem:
    color_roles: dict
    contrast: dict
    typography: dict
    logo_geometry: dict
    photography: dict
    feed_cohesion: dict
    flux_style_prefix: str
    flux_constraints: dict

    def to_document(self) -> dict: return asdict(self)


def extract_visual_signals(html: str, base_url: str) -> VisualSignals:
    soup = BeautifulSoup(html, "html.parser")
    css = "\n".join(tag.get_text(" ") for tag in soup.find_all("style"))
    css += "\n" + "\n".join(tag.get("style", "") for tag in soup.select("[style]"))
    colors, fonts, logos = _colors(css), _fonts(css), []
    for node in soup.select("img[src], link[rel~='icon'][href], meta[property='og:image'][content]"):
        value = node.get("src") or node.get("href") or node.get("content")
        context = " ".join([value or "", node.get("alt", ""), " ".join(node.get("class", [])), node.get("id", "")])
        if value and ("logo" in context.lower() or node.name == "link"): logos.append(urljoin(base_url, value))
    selected = next((OFL_FONT_CATALOG[key] for font in fonts for key in OFL_FONT_CATALOG if key in font.lower()), None)
    hebrew = selected if selected in {"Heebo", "Assistant", "Rubik", "Noto Sans Hebrew"} else "Heebo"
    english = selected or "Inter"
    palette = ", ".join(colors[:4]) if colors else "brand-approved role-based palette"
    style = (f"cohesive editorial brand photography, palette constrained to {palette}, consistent lighting and "
             "camera language, intentional negative space for later typography, text-free, no letters, no logos")
    return VisualSignals(colors[:8], fonts[:8], {"hebrew": hebrew, "english": english},
                         list(dict.fromkeys(logos))[:8], style)


def build_agency_visual_system(signals: VisualSignals) -> AgencyVisualSystem:
    primary, secondary, accent = list(dict.fromkeys(signals.colors + list(DEFAULT_COLORS)))[:3]
    roles = {"primary": primary, "secondary": secondary, "accent": accent,
             "neutral_light": "#FFFFFF", "neutral_dark": "#111827",
             "usage_ratio": {"primary": 60, "secondary": 30, "accent": 10},
             "provenance": "observed_css" if signals.colors else "provisional_default"}
    pairs = {}
    for name, color in (("primary", primary), ("secondary", secondary), ("accent", accent)):
        white, dark = round(contrast_ratio(color, "#FFFFFF"), 2), round(contrast_ratio(color, "#111827"), 2)
        pairs[name] = {"on_white": white, "on_dark": dark,
                       "recommended_text": "#FFFFFF" if white >= dark else "#111827",
                       "normal_text_aa": max(white, dark) >= 4.5, "large_text_aa": max(white, dark) >= 3.0}
    typography = {"hebrew_family": signals.recommended_fonts["hebrew"],
                  "english_family": signals.recommended_fonts["english"], "fallback": "Arial, sans-serif",
                  "base_px": 16, "scale": {"caption": 14, "body": 16, "subheading": 24, "headline": 40, "display": 56},
                  "weights": {"body": 400, "emphasis": 600, "headline": 700},
                  "line_height": {"hebrew_body": 1.55, "english_body": 1.5, "headline": 1.15},
                  "direction_policy": "Set direction per text block; never reverse mixed-script brand names.",
                  "detected_families": signals.detected_fonts}
    logo = {"clear_space_unit": "x = logo mark height or cap height when mark is unavailable",
            "minimum_clear_space": "1x on every side", "minimum_digital_width_px": 48, "safe_zone_percent": 6,
            "allowed_anchors": ["top_start", "top_end", "bottom_start", "bottom_end"],
            "prohibited": ["stretch", "recolor_without_approval", "place_on_low_contrast", "add_effects"],
            "candidates": signals.logo_candidates}
    photography = {"subject": "authentic primary subject with natural material detail",
                   "lighting": "consistent soft directional key light with controlled highlights",
                   "composition": "single focal hierarchy and deliberate copy-safe negative space",
                   "color_grade": f"harmonize shadows/highlights with {primary}, {secondary}, and restrained {accent}",
                   "camera_language": "consistent perspective, depth of field, and crop family across a campaign",
                   "avoid": ["stock-photo clichés", "embedded text", "invented logos", "inconsistent filters"]}
    feed = {"template_families": ["editorial_hero", "educational_card", "product_focus", "quote_or_stat"],
            "repetition_rules": ["reuse grid, margins, type scale, and logo anchor", "rotate content formats, not identity"],
            "thumbnail_test": "asset remains recognizable at 160px width", "max_simultaneous_type_styles": 3}
    constraints = {"text_free": True, "no_letters": True, "no_logos": True, "preserve_copy_space": True,
                   "palette_hex": [primary, secondary, accent]}
    return AgencyVisualSystem(roles, pairs, typography, logo, photography, feed, signals.style_prefix, constraints)


def contrast_ratio(foreground: str, background: str) -> float:
    first, second = relative_luminance(foreground), relative_luminance(background)
    return (max(first, second) + .05) / (min(first, second) + .05)


def relative_luminance(hex_color: str) -> float:
    value = normalize_hex(hex_color)
    channels = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4 for channel in channels]
    return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]


def normalize_hex(value: str) -> str:
    if not re.fullmatch(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?", value): raise ValueError("invalid hex color")
    if len(value) == 4: value = "#" + "".join(character * 2 for character in value[1:])
    return value.upper()


def _colors(css: str) -> list[str]:
    normalized = [normalize_hex(value) for value in re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", css)]
    return [value for value, _ in Counter(v for v in normalized if v not in {"#FFFFFF", "#000000"}).most_common()]


def _fonts(css: str) -> list[str]:
    result = []
    for group in re.findall(r"font-family\s*:\s*([^;}]+)", css, re.I):
        for font in group.split(","):
            cleaned = font.strip(" '\"")
            if cleaned and cleaned.lower() not in {"sans-serif", "serif", "system-ui", "inherit"}: result.append(cleaned)
    return list(dict.fromkeys(result))
