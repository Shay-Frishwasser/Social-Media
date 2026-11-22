from enum import StrEnum


class ClaimOrigin(StrEnum):
    OBSERVED = "observed"
    INFERRED = "inferred"


class ClaimStatus(StrEnum):
    UNREVIEWED = "UNREVIEWED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class ProfileStatus(StrEnum):
    DRAFT = "DRAFT"
    RESEARCHING = "RESEARCHING"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REVISION_PENDING = "REVISION_PENDING"


class ApprovalStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class PackageStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    DELIVERED = "DELIVERED"


class PhotoTreatmentMode(StrEnum):
    ORIGINAL_BRANDED = "original_branded"
    SMART_ENHANCE = "smart_enhance"
    HERO_CUTOUT_SCENE = "hero_cutout_scene"

