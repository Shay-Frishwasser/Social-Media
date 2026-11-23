from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

from brandai.adapters.segmentation import ForegroundSegmentationProvider
from brandai.errors import GenerationBlockedError

from .models import Mode
from .providers import BackgroundGeneratorProvider, BackgroundRequest


def open_raster(path: Path) -> Image.Image:
    with Image.open(path) as image:
        if image.format not in {"JPEG", "PNG", "WEBP"} or image.width * image.height > 36_000_000:
            raise GenerationBlockedError("use a JPEG, PNG or WebP source of at most 36 megapixels")
        return ImageOps.exif_transpose(image).convert("RGBA")


def _subject_center(photo: Image.Image) -> tuple[float, float]:
    sample = photo.convert("L")
    sample.thumbnail((128, 128))
    detail = ImageChops.difference(sample, sample.filter(ImageFilter.GaussianBlur(3)))
    weights = np.array(detail, dtype=np.float64)
    weights = np.maximum(weights - np.percentile(weights, 65), 0)
    if not weights.sum():
        return (.5, .5)
    y, x = np.indices(weights.shape)
    return (float((x * weights).sum() / weights.sum() / max(1, sample.width - 1)),
            float((y * weights).sum() / weights.sum() / max(1, sample.height - 1)))


def smart_photo(photo: Image.Image, size: tuple[int, int]) -> Image.Image:
    focus_x, focus_y = _subject_center(photo)
    aspect = size[0] / size[1]
    width = max(1, min(photo.width, round(photo.height * aspect)))
    height = max(1, min(photo.height, round(photo.width / aspect)))
    x = max(0, min(photo.width - width, round(focus_x * photo.width - width / 2)))
    y = max(0, min(photo.height - height, round(focus_y * photo.height - height / 2)))
    crop = photo.crop((x, y, x + width, y + height)).resize(size, Image.Resampling.LANCZOS)
    rgb = ImageEnhance.Brightness(crop.convert("RGB")).enhance(1.035)
    enhanced = ImageEnhance.Color(ImageEnhance.Contrast(rgb).enhance(1.09)).enhance(1.045).convert("RGBA")
    enhanced.putalpha(crop.getchannel("A"))
    return enhanced


def _fit_subject(photo: Image.Image, size: tuple[int, int], *, height_fraction: float) -> Image.Image:
    fit = (round(size[0] * .82), round(size[1] * height_fraction))
    return ImageOps.contain(photo, fit, Image.Resampling.NEAREST)


def _position(image: Image.Image, size: tuple[int, int], center_y: float) -> tuple[int, int]:
    return ((size[0] - image.width) // 2, round(size[1] * center_y - image.height / 2))


class VisualPipeline:
    def __init__(self, background: BackgroundGeneratorProvider, segmentation: ForegroundSegmentationProvider):
        self.background, self.segmentation = background, segmentation

    def plate(self, mode: Mode, request: BackgroundRequest, source: Path | None, scratch: Path) -> Image.Image:
        canvas = Image.new("RGBA", request.size, request.palette[1])
        if mode in {"flux_background_composed", "on_demand_hero_cutout_scene"}:
            generated = self.background.generate(request)
            if not isinstance(generated, Image.Image) or generated.width * generated.height > 36_000_000:
                raise GenerationBlockedError("the background provider returned an unusable image")
            canvas = ImageOps.fit(generated.convert("RGB"), request.size, Image.Resampling.LANCZOS).convert("RGBA")
        if mode == "flux_background_composed":
            return canvas
        if source is None:
            raise GenerationBlockedError("this visual treatment requires a photo")
        photo = open_raster(source)
        if mode == "on_demand_smart_enhance":
            canvas.alpha_composite(smart_photo(photo, request.size))
            return canvas
        if mode == "on_demand_original_branded":
            fitted = _fit_subject(photo, request.size, height_fraction=.68)
            canvas.alpha_composite(fitted, _position(fitted, request.size, .46))
            return canvas

        canonical, destination = scratch / "seg-source.png", scratch / "seg-result.png"
        photo.save(canonical)
        result = self.segmentation.cutout(canonical, destination)
        if result.resolve() != destination.absolute() or destination.is_symlink():
            raise GenerationBlockedError("the segmentation worker returned an unexpected output path")
        with Image.open(destination) as cutout:
            if cutout.size != photo.size or cutout.mode != "RGBA":
                raise GenerationBlockedError("the segmentation worker must return an aligned alpha mask")
            photo.putalpha(ImageChops.multiply(photo.getchannel("A"), cutout.getchannel("A")))
        bounds = photo.getchannel("A").getbbox()
        if not bounds:
            raise GenerationBlockedError("the segmentation worker returned an empty foreground")
        subject = _fit_subject(photo.crop(bounds), request.size, height_fraction=.60)
        position = _position(subject, request.size, .48)
        shadow = Image.new("RGBA", request.size)
        shadow.paste((0, 0, 0, 145), (position[0], position[1] + round(request.size[1] * .025)), subject.getchannel("A"))
        canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(max(5, min(request.size) * .024))))
        canvas.alpha_composite(subject, position)
        return canvas
