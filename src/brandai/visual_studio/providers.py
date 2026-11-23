import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from random import Random
from typing import Protocol

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from brandai.adapters.workers import GPUWorkerProvider, WorkerLease
from brandai.onboarding.visual import normalize_hex


_PHOTOGRAPHIC_TRAITS = (
    (r"soft|diffused|directional", "soft directional lighting"),
    (r"dramatic|low.key|chiaroscuro", "dramatic shadow and depth"),
    (r"natural light|daylight", "natural daylight"),
    (r"overhead|top.down|flat.lay", "overhead camera angle"),
    (r"macro|close.up|detail", "close-up photographic detail"),
    (r"editorial", "editorial photographic composition"),
    (r"studio|minimal|neutral", "restrained studio materials"),
    (r"space|simple|uncluttered", "intentional negative space"),
    (r"warm|golden", "warm color temperature"),
    (r"cool|blue hour", "cool color temperature"),
    (r"texture|grain", "subtle natural surface texture"),
)


def pixel_art_direction(prefix: str) -> str:
    # Project the locked style onto known photographic traits. No free-form text from
    # captions, evidence, event notes or even the approved prompt reaches pixel generation.
    traits = [description for pattern, description in _PHOTOGRAPHIC_TRAITS if re.search(pattern, prefix, re.I)]
    return ", ".join(traits) or "soft directional lighting, clean studio materials, restrained palette"


NEGATIVE_PROMPT = "text, lettering, glyphs, writing, logos, watermarks, signs"


@dataclass(frozen=True, slots=True)
class BackgroundRequest:
    size: tuple[int, int]
    palette: tuple[str, str, str]
    composition: str
    art_direction: str
    seed: int
    text_free: bool = True
    negative_prompt: str = NEGATIVE_PROMPT


class BackgroundGeneratorProvider(Protocol):
    def generate(self, request: BackgroundRequest) -> Image.Image: ...


class GradientBackgroundProvider:
    model_name = "pillow-gradient"

    def generate(self, request: BackgroundRequest) -> Image.Image:
        primary, secondary, accent = request.palette
        linear = Image.linear_gradient("L").resize(request.size)
        if request.seed % 2:
            linear = ImageOps.mirror(linear.rotate(180))
        field = ImageOps.colorize(linear, secondary, primary)
        light = ImageOps.colorize(Image.radial_gradient("L").resize(request.size), accent, secondary)
        return Image.blend(field, light, .08 + Random(request.seed).random() * .12)


class FluxBackgroundProvider:
    """Lease a provisioned FLUX.2 Klein 4B runner without binding the pipeline to a host."""

    model_name = "FLUX.2 Klein 4B"

    def __init__(self, worker: GPUWorkerProvider,
                 runner: Callable[[WorkerLease, BackgroundRequest], Image.Image]):
        self.worker, self.runner = worker, runner

    def generate(self, request: BackgroundRequest) -> Image.Image:
        if not request.text_free:
            raise ValueError("background generation requires a text-free request")
        if request.composition not in {"centered", "copy_space_start", "copy_space_end", "wide_editorial"}:
            raise ValueError("background composition must use an approved framing")
        safe = replace(request, art_direction=pixel_art_direction(request.art_direction), negative_prompt=NEGATIVE_PROMPT,
                       palette=tuple(normalize_hex(color) for color in request.palette))
        with self.worker.lease("image_generation") as lease:
            return self.runner(lease, safe)


class EllipseSegmentationProvider:
    """Deterministic test/local preview mask implementing the SAM 2 adapter interface."""

    model_name = "ellipse-preview"

    def cutout(self, source: Path, destination: Path) -> Path:
        with Image.open(source) as image:
            photo = image.convert("RGBA")
        width, height = photo.size
        mask = Image.new("L", photo.size)
        ImageDraw.Draw(mask).ellipse((round(.13 * width), round(.13 * height),
                                      round(.87 * width), round(.87 * height)), fill=255)
        photo.putalpha(mask.filter(ImageFilter.GaussianBlur(max(1, min(photo.size) * .008))))
        photo.save(destination)
        return destination
