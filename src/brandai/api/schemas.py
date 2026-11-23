from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, HttpUrl, StringConstraints

from brandai.jobs.models import SocialHandles


class BrandCreate(BaseModel):
    name: str = Field(min_length=1, max_length=250)


class ResearchRequest(BaseModel):
    website_url: HttpUrl
    company_name: str = Field(min_length=1, max_length=250)
    social_handles: SocialHandles = Field(default_factory=list)


class ClaimDecision(BaseModel):
    approved_by: str = "local-owner"


class ClaimOverride(BaseModel):
    value: Any
    approved_by: str = "local-owner"


class KnowledgeGapAnswer(BaseModel):
    question: str
    answer: str = Field(min_length=1)
    approved_by: str = "local-owner"


class DesignSystemUpdate(BaseModel):
    colors: dict
    font_pairings: dict
    logo_safe_zones: dict
    layout_templates: list = []
    flux_style_prefix: str
    flux_constraints: dict
    created_by: str = "local-owner"


class PhotoBriefCreate(BaseModel):
    source_asset_id: str
    prompt: str = Field(min_length=1, max_length=2000)
    treatment_mode: Literal["original_branded", "smart_enhance", "hero_cutout_scene"]
    platform: Literal["instagram", "tiktok", "linkedin", "facebook", "youtube", "x"]
    transient_overrides: dict = {}
    created_by: str = "local-owner"


FactText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]


class ManualProfileRequest(BaseModel):
    offering: FactText
    audience: FactText
    differentiator: FactText
