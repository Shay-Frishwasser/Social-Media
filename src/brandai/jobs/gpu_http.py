from dataclasses import asdict
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image

from brandai.adapters.segmentation import SAM2Settings
from brandai.adapters.workers import WorkerLease
from brandai.visual_studio.providers import BackgroundRequest


MAX_RESULT = 25 * 1024 * 1024


def _render(lease: WorkerLease, route: str, **kwargs) -> bytes:
    with httpx.Client(trust_env=False, timeout=httpx.Timeout(900, connect=5)) as client:
        with client.stream("POST", f"{lease.base_url}{route}", follow_redirects=False, **kwargs) as response:
            response.raise_for_status()
            if response.headers.get("content-type", "").split(";")[0] != "image/png":
                raise ValueError("worker must return a PNG")
            result = bytearray()
            for part in response.iter_bytes():
                result.extend(part)
                if len(result) > MAX_RESULT:
                    raise ValueError("worker output exceeds the PNG size limit")
            return bytes(result)


def render_background(lease: WorkerLease, request: BackgroundRequest) -> Image.Image:
    data = _render(lease, "/worker/background", json=asdict(request))
    with Image.open(BytesIO(data)) as image:
        if image.format != "PNG" or image.width * image.height > 36_000_000:
            raise ValueError("worker output is not a supported PNG")
        return image.convert("RGB")


def segment_photo(lease: WorkerLease, settings: SAM2Settings, source: Path, destination: Path) -> Path:
    with source.open("rb") as photo:
        result = _render(lease, "/worker/segment", data={"variant": settings.variant},
                         files={"photo": ("photo.png", photo, "image/png")})
    destination.write_bytes(result)
    return destination
