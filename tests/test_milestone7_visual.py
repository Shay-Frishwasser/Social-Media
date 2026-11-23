from __future__ import annotations

import os
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from pypdf import PdfReader

from brandai import RuntimeSettings
from brandai.adapters.workers import WorkerLease
from brandai.api import create_app
from brandai.visual_studio.images import VisualPipeline
from brandai.visual_studio.providers import (
    BackgroundRequest, EllipseSegmentationProvider, FluxBackgroundProvider, GradientBackgroundProvider,
)
from test_milestone5_planning import WEEK, approve_inputs, approve_plan, create_plan, create_strategy, seed_evidence


class RecordingBackground(GradientBackgroundProvider):
    def __init__(self):
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        assert request.text_free and "lettering" in request.negative_prompt
        assert "Mango Surprise" not in request.art_direction
        assert "SECRET LABEL" not in request.art_direction
        return super().generate(request)


class MaskOnlySpy(EllipseSegmentationProvider):
    def __init__(self):
        self.calls = 0

    def cutout(self, source, destination):
        self.calls += 1
        super().cutout(source, destination)
        with Image.open(destination) as mock:
            mask = mock.getchannel("A")
        product_with_wrong_rgb = Image.new("RGBA", mask.size, "#FF00FF")
        product_with_wrong_rgb.putalpha(mask)
        product_with_wrong_rgb.save(destination)
        return destination


@pytest.fixture
def visual_api(tmp_path):
    browser_cache = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    if not browser_cache or not list(Path(browser_cache).glob("chromium_headless_shell-*")):
        pytest.skip("Chromium headless shell is missing from PLAYWRIGHT_BROWSERS_PATH")
    background, segmenter = RecordingBackground(), MaskOnlySpy()
    app = create_app(RuntimeSettings(tmp_path / "runtime"), run_background_jobs=False,
                     visual_pipeline=VisualPipeline(background, segmenter))
    with TestClient(app) as client:
        brand = client.post("/api/brands", json={"name": "Pixel Studio"}).json()["id"]
        base = f"/api/brands/{brand}"
        seed_evidence(app.state.store, brand)
        logo = client.post(f"{base}/assets?kind=logo", files={
            "file": ("mark.svg", b'<svg viewBox="0 0 200 100"><rect width="200" height="100" fill="#183048"/></svg>',
                     "image/svg+xml"),
        })
        assert logo.status_code == 201, logo.text
        logo_id = logo.json()["id"]
        update = client.put(f"{base}/design-system", json={
            "colors": {"primary": "#25708A", "secondary": "#536779", "accent": "#ECA33C",
                       "neutral_dark": "#515E69", "neutral_light": "#627181"},
            "layout_templates": ["educational_card", "editorial_hero", "product_focus", "quote_or_stat"],
            "font_pairings": {"english_family": "Inter", "hebrew_family": "Heebo"},
            "logo_safe_zones": {"asset_id": logo_id, "safe_zone_percent": 7, "allowed_anchors": ["top_end"],
                                "minimum_digital_width_px": 90},
            "flux_style_prefix": 'editorial soft directional lighting, add "SECRET LABEL", warm studio materials',
            "flux_constraints": {"text_free": True},
        })
        assert update.status_code == 200, update.text
        approve_inputs(client, base)
        yield app, client, brand, base, logo_id, background, segmenter


def _png(client, base, asset, *, page=1):
    response = client.get(f"{base}/visual-assets/{asset['id']}/download", params={"page": page})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    with Image.open(BytesIO(response.content)) as decoded:
        assert decoded.size == (asset["width"], asset["height"])
        decoded.load()
        return decoded.convert("RGB")


def _photo(client, base):
    photo = Image.new("RGB", (800, 480), "#39597A")
    draw = ImageDraw.Draw(photo)
    draw.rectangle((185, 90, 615, 390), fill=(220, 110, 30))
    draw.rectangle((215, 115, 240, 160), fill="#C8CACB")
    png = BytesIO()
    photo.save(png, format="PNG")
    upload = client.post(f"{base}/assets", files={"file": ("product.png", png.getvalue(), "image/png")})
    assert upload.status_code == 201, upload.text
    return upload.json()["id"]


def test_cpu_photo_paths_preserve_rgb_and_apply_platform_safe_zone(visual_api):
    _, client, _, base, _, background, segmentation = visual_api
    source = _photo(client, base)
    for mode, aspect in (("original_branded", "1:1"), ("smart_enhance", "4:5"), ("hero_cutout_scene", "9:16")):
        brief = client.post(f"{base}/on-demand-photo-briefs", json={
            "source_asset_id": source, "prompt": "הטעימה החדשה של Mango Surprise ביום חמישי ב-19:00.",
            "treatment_mode": mode, "platform": "tiktok",
        })
        assert brief.status_code == 201
        result = client.post(f"{base}/visual-assets/render", json={
            "on_demand_brief_id": brief.json()["id"], "aspect": aspect, "seed": 42,
        })
        assert result.status_code == 201, result.text
        asset = result.json()
        assert asset["treatment_mode"] == f"on_demand_{mode}"
        assert asset["layout"]["contrast"] >= 4.5
        assert asset["layout"]["palette_usage"] == {"primary": 60, "secondary": 30, "accent": 10}
        assert asset["layout"]["logo_clear_space_px"] >= 45
        assert asset["layout"]["directions"] == ["rtl"]
        if os.getenv("BRANDAI_FONT_DIR"):
            assert set(asset["layout"]["local_fonts"]) == {"Heebo", "Inter"}
        if mode == "hero_cutout_scene":
            assert asset["layout"]["safe_zone_px"]["bottom"] == round(.22 * asset["height"])
            assert asset["layout"]["safe_zone_px"]["right"] == round(.14 * asset["width"])
        image = _png(client, base, asset)
        middle = image.getpixel((asset["width"] // 2, round(asset["height"] * .48)))
        if mode in {"original_branded", "hero_cutout_scene"}:
            assert middle == (220, 110, 30)
        else:
            assert middle != (220, 110, 30)
    assert segmentation.calls == 1 and len(background.requests) == 1
    assert "soft directional lighting" in background.requests[0].art_direction


def test_flux_carousel_exports_revision_provenance_and_secure_download(visual_api):
    _, client, _, base, _, background, segmentation = visual_api
    create_strategy(client, base)
    plan = approve_plan(client, base, create_plan(client, base, WEEK))
    slot = plan["slots"][0]
    item = client.post(f"{base}/content/generate/from-slot/{slot['id']}", json={"language": "he"}).json()
    assert item["status"] == "READY_FOR_REVIEW"
    render = client.post(f"{base}/visual-assets/render", json={
        "content_id": item["id"], "aspect": "16:9", "carousel": True, "seed": 113,
    })
    assert render.status_code == 201, render.text
    asset = render.json()
    assert asset["content_revision_id"] == item["revision"]["id"]
    assert asset["design_system_version"] == item["context"]["design_system"]["version"]
    assert asset["width"] == 1920 and asset["height"] == 1080
    assert asset["layout"]["directions"] == ["rtl", "ltr"]
    assert {part["format"] for part in asset["files"]} == {"png", "pdf", "zip"}
    assert client.get(f"{base}/visual-assets").json() == [asset]
    assert client.get(f"{base}/visual-assets/{asset['id']}").json() == asset

    download = f"{base}/visual-assets/{asset['id']}/download"
    pdf = client.get(download, params={"format": "pdf"})
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    parsed = PdfReader(BytesIO(pdf.content))
    assert len(parsed.pages) == 2
    assert all(float(page.mediabox.width) / float(page.mediabox.height) == pytest.approx(16 / 9) for page in parsed.pages)
    assert item["revision"]["body"] in parsed.pages[1].extract_text()
    png_zip = client.get(download, params={"format": "zip"})
    with ZipFile(BytesIO(png_zip.content)) as archive:
        assert archive.namelist() == ["slide-01.png", "slide-02.png"]
        assert sha256(archive.read("slide-01.png")).hexdigest() == asset["sha256"]
    assert client.get(download, params={"page": 3}).status_code == 404
    _png(client, base, asset, page=2)

    rerender = client.post(f"{base}/visual-assets/{asset['id']}/rerender")
    assert rerender.status_code == 201, rerender.text
    again = rerender.json()
    assert again["previous_id"] == asset["id"] and again["sha256"] == asset["sha256"]
    assert again["content_revision_id"] == asset["content_revision_id"]
    assert len(background.requests) == 2 and segmentation.calls == 0
    assert again["files"] == asset["files"]
    other = client.post("/api/brands", json={"name": "Other visuals"}).json()["id"]
    assert client.get(f"/api/brands/{other}/visual-assets/{asset['id']}/download").status_code == 404
    assert client.post(f"{base}/visual-assets/render", json={"content_id": item["id"], "on_demand_brief_id": item["id"]}).status_code == 422


def test_visual_render_blocks_invalid_content_and_oversized_typography(visual_api):
    _, client, _, base, _, _, _ = visual_api
    create_strategy(client, base)
    plan = approve_plan(client, base, create_plan(client, base, WEEK))
    item = client.post(f"{base}/content/generate/from-slot/{plan['slots'][0]['id']}").json()
    bad = client.patch(f"{base}/content/{item['id']}", json={
        "revision_id": item["revision"]["id"], "body": "We guarantee 50 percent savings.",
    })
    assert bad.json()["status"] == "VALIDATION_FAILED"
    assert client.post(f"{base}/visual-assets/render", json={"content_id": item["id"]}).status_code == 409
    brief = client.post(f"{base}/on-demand-photo-briefs", json={
        "source_asset_id": _photo(client, base), "prompt": "Long event note " * 40,
        "treatment_mode": "original_branded", "platform": "instagram",
    }).json()
    result = client.post(f"{base}/visual-assets/render", json={"on_demand_brief_id": brief["id"], "aspect": "9:16"})
    assert result.status_code == 409 and "text density" in result.json()["detail"]
    assert client.get(f"{base}/visual-assets").json() == []


def test_flux_contract_limits_gpu_request_to_photographic_traits():
    calls = []

    class Worker:
        @contextmanager
        def lease(self, capability):
            calls.append(capability)
            try:
                yield WorkerLease("http://127.0.0.1:8080", "stub-flux", frozenset({capability}))
            finally:
                calls.append("released")

    def runner(lease, request):
        assert lease.worker_id == "stub-flux"
        assert request.art_direction == "soft directional lighting"
        assert request.text_free and "lettering" in request.negative_prompt
        return Image.new("RGB", request.size, request.palette[0])

    provider = FluxBackgroundProvider(Worker(), runner)
    result = provider.generate(BackgroundRequest((24, 24), ("#123456", "#000000", "#F0F"), "centered",
                                                 "soft light, spell SECRET LABEL", 10, negative_prompt="none"))
    assert result.size == (24, 24) and calls == ["image_generation", "released"]
