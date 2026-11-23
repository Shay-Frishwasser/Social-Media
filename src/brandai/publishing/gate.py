from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from uuid import UUID

from PIL import Image
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from brandai.content.context import prepare_content
from brandai.content.models import ContentContext, VisualSelection
from brandai.content.repository import revision_copy
from brandai.content.validator import validate_copy
from brandai.db.brand_models import BrandDesignSystem, BrandProfile, ContentItem, ContentRevision, VisualAsset
from brandai.errors import BrandAIError, NotFoundError
from brandai.store import BrandAIStore
from brandai.visual_studio.models import DIMENSIONS, LayoutChecks, RenderedFile
from brandai.visual_studio.renderer import PALETTE_USAGE, safe_zone

from .models import GateIssue, GateReport, PublishingBlocked


@dataclass(frozen=True)
class VerifiedFile:
    manifest: RenderedFile
    data: bytes


@dataclass(frozen=True)
class GateContext:
    item: ContentItem
    revision: ContentRevision
    visual: VisualAsset
    files: tuple[VerifiedFile, ...]


def _latest(session: Session, model):
    return session.scalar(select(model).order_by(model.version.desc()).limit(1))


class PublishingGate:
    def __init__(self, store: BrandAIStore):
        self.store = store

    def check(self, brand_id: str, content_id: str, asset_id: str | None = None) -> GateReport:
        with self.store.brand_session(brand_id) as session:
            report, _ = self.inspect(session, brand_id, content_id, asset_id)
            return report

    def require(self, session: Session, brand_id: str, content_id: str, asset_id: str | None = None) -> GateContext:
        report, context = self.inspect(session, brand_id, content_id, asset_id)
        if context is None:
            raise PublishingBlocked(report)
        return context

    def inspect(self, session: Session, brand_id: str, content_id: str,
                asset_id: str | None) -> tuple[GateReport, GateContext | None]:
        item = session.get(ContentItem, content_id)
        if item is None or item.brand_id != brand_id:
            raise NotFoundError("content item not found in this brand")
        revision = session.scalar(select(ContentRevision).where(ContentRevision.content_id == item.id)
                                  .order_by(ContentRevision.version.desc()).limit(1))
        issues = []
        report = GateReport(allowed=False, content_id=item.id, revision_id=revision.id if revision else None)

        def fail(code: str, message: str, **details: object) -> None:
            issues.append(GateIssue(code=code, message=message, details=details))

        profile = _latest(session, BrandProfile)
        design = _latest(session, BrandDesignSystem)
        context = ContentContext.model_validate(item.context_json)
        if profile is None or profile.status != "APPROVED" or profile.id != context.profile.id:
            fail("profile_approval_stale", "Approve the latest profile and regenerate content against it.")
        if design is None or design.status != "APPROVED" or design.id != context.design_system.id:
            fail("design_approval_stale", "Approve the latest design and render content against it.")

        if (revision is None or item.status not in {"APPROVED", "SCHEDULED", "PUBLISHED"}
                or item.approved_revision_version != revision.version or not item.approved_by or not item.approved_at):
            fail("revision_not_approved", "Explicitly approve the current content revision.")
        if revision and not issues:
            try:
                prepared = prepare_content(
                    self.store, session, slot_id=item.cadence_slot_id, brief_id=item.on_demand_brief_id,
                    language=item.language, pinned_strategy_id=item.strategy_id, pinned_context=context,
                    research_topic_id=revision.research_json.get("research_topic_id"), research_content_id=item.id,
                )
                selection = VisualSelection.model_validate(revision.visual_directive["selection"])
                failures, _, _ = validate_copy(revision_copy(revision), selection, prepared)
                if failures or not revision.validation_report.get("valid"):
                    fail("copy_validation_failed", "Resolve current grounding or format violations and approve a new revision.",
                         violations=[entry.model_dump(mode="json") for entry in failures])
            except (BrandAIError, ValidationError, KeyError) as exc:
                fail("validation_context_stale", "Restore valid source approvals, cadence and research provenance.",
                     reason=str(exc).splitlines()[0])

        visual = session.get(VisualAsset, asset_id) if asset_id else None
        if asset_id is None and revision:
            visual = session.scalar(select(VisualAsset).where(VisualAsset.content_revision_id == revision.id)
                                    .order_by(VisualAsset.created_at.desc(), VisualAsset.id).limit(1))
        files: tuple[VerifiedFile, ...] = ()
        if visual is None:
            fail("visual_required", "Render a visual asset for the current content revision.")
        else:
            report.visual_asset_id = UUID(visual.id)
            if revision is None or visual.content_revision_id != revision.id:
                fail("visual_revision_mismatch", "Choose a render linked to the current revision.")
            if visual.design_system_id != context.design_system.id or visual.design_system_version != context.design_system.version:
                fail("visual_design_mismatch", "Re-render using the approved design system.")
            if design is not None:
                try:
                    files = self._verify_assets(brand_id, item, visual, design)
                except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
                    fail("visual_integrity_failed", "Re-render the asset and replace invalid or missing exports.",
                         reason=str(exc).splitlines()[0])
        report.issues, report.allowed = issues, not issues
        return report, GateContext(item, revision, visual, files) if report.allowed else None

    def _verify_assets(self, brand_id: str, item: ContentItem, visual: VisualAsset,
                       design: BrandDesignSystem) -> tuple[VerifiedFile, ...]:
        aspect = visual.output_json["aspect"]
        size = DIMENSIONS[aspect]
        if size != (visual.width, visual.height):
            raise ValueError("render dimensions do not match its aspect")
        layout = LayoutChecks.model_validate(visual.output_json["layout"])
        requested = design.logo_safe_zones.get("safe_zone_percent", 6)
        factor = min(30, max(6, requested)) if type(requested) is int else 6
        minimums = safe_zone(size, aspect, item.platform, factor)
        if (not 4.5 <= layout.contrast <= 21 or layout.palette_usage != PALETTE_USAGE
                or any(layout.safe_zone_px.get(edge, 0) < value for edge, value in minimums.items())
                or sum(layout.safe_zone_px.get(edge, 0) for edge in ("left", "right")) >= size[0]
                or sum(layout.safe_zone_px.get(edge, 0) for edge in ("top", "bottom")) >= size[1]):
            raise ValueError("recorded contrast or safe zones are below policy")
        if design.logo_safe_zones.get("asset_id") and layout.logo_clear_space_px <= 0:
            raise ValueError("the approved logo has no recorded clear space")

        root = self.store.settings.brand_assets_dir(brand_id)
        directory = self.store.settings.brand_dir(brand_id).resolve() / "assets" / "visuals" / visual.id
        if root.resolve() != directory.parents[1] or directory.resolve() != directory:
            raise ValueError("visual directory is outside brand assets")
        verified = []
        manifests = [RenderedFile.model_validate(raw) for raw in visual.output_json["files"]]
        if len(manifests) > 12 or len({entry.name for entry in manifests}) != len(manifests):
            raise ValueError("visual file manifest is invalid")
        total = 0
        for entry in manifests:
            names = {"pdf": "carousel.pdf", "zip": "carousel-png.zip"}
            expected = f"slide-{entry.page:02}.png" if entry.format == "png" and entry.page else names.get(entry.format)
            if expected is None or entry.name != expected or not 0 < entry.bytes <= 32 * 1024**2:
                raise ValueError("visual manifest contains an unsupported export")
            path = directory / entry.name
            if path.resolve() != path or not path.is_file() or path.stat().st_size != entry.bytes:
                raise ValueError(f"{entry.name} is missing or altered")
            total += entry.bytes
            if total > 96 * 1024**2:
                raise ValueError("visual bundle exceeds the size limit")
            data = path.read_bytes()
            if len(data) != entry.bytes or sha256(data).hexdigest() != entry.sha256:
                raise ValueError(f"{entry.name} failed SHA-256 verification")
            if entry.format == "png":
                with Image.open(BytesIO(data)) as png:
                    if png.size != size or png.format != "PNG":
                        raise ValueError(f"{entry.name} has unexpected image dimensions")
                    png.verify()
            elif entry.format == "pdf" and not data.startswith(b"%PDF-"):
                raise ValueError("carousel PDF is invalid")
            verified.append(VerifiedFile(entry, data))
        frames = sorted(file.manifest.page for file in verified if file.manifest.format == "png")
        formats = [file.manifest.format for file in verified]
        if (not frames or frames != list(range(1, len(frames) + 1))
                or next(f.manifest.sha256 for f in verified if f.manifest.page == 1 and f.manifest.format == "png") != visual.sha256
                or (len(frames) > 1 or item.content_depth == "carousel") and (
                    len(frames) < 2 or formats.count("pdf") != 1 or formats.count("zip") != 1
                )):
            raise ValueError("required PNG or carousel exports are missing")
        return tuple(verified)
