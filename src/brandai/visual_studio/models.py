from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from brandai.strategy.models import Model


Aspect = Literal["1:1", "4:5", "9:16", "16:9"]
Mode = Literal["flux_background_composed", "on_demand_original_branded", "on_demand_smart_enhance",
               "on_demand_hero_cutout_scene"]
DIMENSIONS: dict[Aspect, tuple[int, int]] = {"1:1": (1080, 1080), "4:5": (1080, 1350),
                                            "9:16": (1080, 1920), "16:9": (1920, 1080)}


class RenderOptions(Model):
    aspect: Aspect | None = None
    carousel: bool | None = None
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)
    logo_asset_id: UUID | None = None


class VisualRender(RenderOptions):
    content_id: UUID | None = None
    on_demand_brief_id: UUID | None = None

    @model_validator(mode="after")
    def exactly_one_source(self) -> Self:
        if (self.content_id is None) == (self.on_demand_brief_id is None):
            raise ValueError("select one content item or photo brief")
        return self


class RenderedFile(Model):
    format: Literal["png", "pdf", "zip"]
    page: int | None = None
    name: str
    sha256: str
    bytes: int


class LayoutChecks(Model):
    contrast: float
    safe_zone_px: dict[str, int]
    logo_clear_space_px: int
    palette_usage: dict[str, int]
    local_fonts: list[str]
    directions: list[Literal["rtl", "ltr"]]
    template_id: str | None


class VisualResponse(Model):
    id: UUID
    previous_id: UUID | None
    content_revision_id: UUID | None
    on_demand_brief_id: UUID | None
    design_system_id: UUID
    design_system_version: int
    treatment_mode: Mode
    seed: int
    sha256: str
    width: int
    height: int
    aspect: Aspect
    files: list[RenderedFile]
    providers: dict[str, str]
    layout: LayoutChecks
    created_at: AwareDatetime
