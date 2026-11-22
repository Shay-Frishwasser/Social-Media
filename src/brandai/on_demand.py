from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


PhotoMode = Literal["original_branded", "smart_enhance", "hero_cutout_scene"]


@dataclass(frozen=True, slots=True)
class PhotoTreatmentRecipe:
    mode: PhotoMode
    preserve_source_pixels: bool
    operations: tuple[str, ...]
    requires_gpu_capabilities: frozenset[str]
    output_contract: dict


class PhotoTreatmentPlanner:
    """Converts an approved brief into a provider-neutral, auditable treatment recipe."""

    def plan(self, mode: PhotoMode, *, platform: str, design_system_version: int) -> PhotoTreatmentRecipe:
        common = {"platform": platform, "design_system_version": design_system_version,
                  "compositor": "chromium", "typography_direction": "per_block"}
        if mode == "original_branded":
            return PhotoTreatmentRecipe(mode, True,
                ("validate_source", "fit_without_pixel_edits", "apply_brand_frame", "place_logo_in_safe_zone", "render_typography"),
                frozenset(), common)
        if mode == "smart_enhance":
            return PhotoTreatmentRecipe(mode, False,
                ("validate_source", "detect_subject", "crop_for_platform", "adjust_exposure_contrast_white_balance",
                 "apply_brand_grade", "place_logo_in_safe_zone", "render_typography"),
                frozenset({"subject_detection"}), common)
        if mode == "hero_cutout_scene":
            return PhotoTreatmentRecipe(mode, True,
                ("validate_source", "segment_foreground", "preserve_foreground_rgba", "generate_text_free_brand_scene",
                 "composite_untouched_foreground", "place_logo_in_safe_zone", "render_typography"),
                frozenset({"foreground_segmentation", "image_generation"}),
                {**common, "foreground_policy": "source RGB pixels remain unchanged; only alpha edge refinement is allowed"})
        raise ValueError(f"unsupported photo treatment mode: {mode}")
