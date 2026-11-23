from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass
from datetime import UTC
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from shutil import rmtree
from tempfile import TemporaryDirectory
from uuid import uuid4
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from PIL import ImageColor, ImageStat
from sqlalchemy import select
from sqlalchemy.orm import Session

from brandai.content.models import FluxDirective, PhotoDirective
from brandai.content.validator import BREAKS
from brandai.db.brand_models import Asset, BrandDesignSystem, ContentItem, ContentRevision, OnDemandPhotoBrief, VisualAsset
from brandai.errors import BrandIsolationError, GenerationBlockedError, NotFoundError
from brandai.onboarding.safe_svg import static_svg
from brandai.onboarding.visual import contrast_ratio, normalize_hex
from brandai.publishing.gate import PublishingGate
from brandai.repositories import VersionRepository
from brandai.store import BrandAIStore
from brandai.strategy.models import Platform

from .images import VisualPipeline, open_raster
from .models import DIMENSIONS, Aspect, Mode, RenderedFile, RenderOptions, VisualRender, VisualResponse
from .providers import BackgroundRequest, pixel_art_direction
from .renderer import ChromiumRenderer, LayoutStyle, Slide


DEFAULT_ASPECT: dict[Platform, Aspect] = {
    "instagram": "4:5", "tiktok": "9:16", "linkedin": "1:1", "facebook": "1:1", "youtube": "16:9", "x": "16:9",
}


@dataclass(frozen=True)
class RenderInput:
    design_id: str
    design_version: int
    revision_id: str | None
    brief_id: str | None
    platform: Platform
    mode: Mode
    aspect: Aspect
    seed: int
    slides: list[Slide]
    image: Path | None
    logo: Path | None
    style: LayoutStyle
    pixel_prompt: str
    previous_id: str | None


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _artifact(path: Path, format_name: str, page: int | None = None) -> RenderedFile:
    return RenderedFile(format=format_name, page=page, name=path.name, sha256=_sha(path), bytes=path.stat().st_size)


def _logo_matte(color: tuple[int, int, int]) -> str:
    value = "#{:02X}{:02X}{:02X}".format(*color)
    return max(("#FFFFFF", "#000000"), key=lambda matte: contrast_ratio(matte, value))


def _raster_logo(path: Path) -> tuple[str, float, str]:
    image = open_raster(path)
    image.thumbnail((720, 720))
    solid = image.getchannel("A").point(lambda alpha: 255 if alpha >= 128 else 0)
    if solid.getbbox() is None:
        raise GenerationBlockedError("the logo asset has no visible mark")
    color = tuple(int(channel) for channel in ImageStat.Stat(image.convert("RGB"), solid).median)
    stream = BytesIO()
    image.save(stream, format="PNG")
    return "data:image/png;base64," + b64encode(stream.getvalue()).decode(), image.width / image.height, _logo_matte(color)


def _svg_logo(path: Path) -> tuple[str, float, str]:
    clean, width, height, _ = static_svg(path.read_bytes())
    colors = []
    for node in ElementTree.fromstring(clean).iter():
        for attr in ("fill", "stroke"):
            if attr in node.attrib:
                try:
                    colors.append(ImageColor.getrgb(node.attrib[attr])[:3])
                except ValueError:
                    continue
    matte = _logo_matte(colors[0]) if colors else "#FFFFFF"
    return "data:image/svg+xml;base64," + b64encode(clean).decode(), width / height, matte



class VisualService:
    def __init__(self, store: BrandAIStore, pipeline: VisualPipeline, renderer: ChromiumRenderer):
        self.store, self.pipeline, self.renderer = store, pipeline, renderer

    def _assets_root(self, brand_id: str) -> Path:
        root = self.store.settings.brand_assets_dir(brand_id).resolve()
        if root != self.store.settings.brand_dir(brand_id).resolve() / "assets":
            raise BrandIsolationError("visual output must stay inside this brand's asset directory")
        return root

    @staticmethod
    def _view(row: VisualAsset) -> VisualResponse:
        return VisualResponse(
            id=row.id, previous_id=row.previous_id, content_revision_id=row.content_revision_id,
            on_demand_brief_id=row.on_demand_brief_id, design_system_id=row.design_system_id,
            design_system_version=row.design_system_version, treatment_mode=row.treatment_mode,
            seed=row.seed, sha256=row.sha256, width=row.width, height=row.height,
            aspect=row.output_json["aspect"], files=row.output_json["files"], layout=row.output_json["layout"],
            providers=row.output_json["providers"],
            created_at=row.created_at.replace(tzinfo=UTC),
        )

    @staticmethod
    def _get(session: Session, asset_id: str) -> VisualAsset:
        row = session.get(VisualAsset, asset_id)
        if row is None:
            raise NotFoundError("visual asset not found in this brand")
        return row

    def get(self, brand_id: str, asset_id: str) -> VisualResponse:
        with self.store.brand_session(brand_id) as session:
            return self._view(self._get(session, asset_id))

    def list(self, brand_id: str) -> list[VisualResponse]:
        with self.store.brand_session(brand_id) as session:
            query = select(VisualAsset).order_by(VisualAsset.created_at.desc(), VisualAsset.id).limit(50)
            return [self._view(row) for row in session.scalars(query)]

    @staticmethod
    def _asset_path(session: Session, root: Path, asset_id: str, *, logo: bool = False) -> Path:
        row = session.get(Asset, asset_id)
        if row is None or (logo and row.kind != "logo"):
            raise NotFoundError("source image or logo not found in this brand")
        path = Path(row.path).resolve()
        if not path.is_relative_to(root):
            raise BrandIsolationError("source image is outside this brand's assets")
        if not path.is_file() or path.stat().st_size > 25 * 1024 * 1024:
            raise GenerationBlockedError("source image is missing or exceeds the asset size limit")
        if row.sha256 is not None and _sha(path) != row.sha256:
            raise GenerationBlockedError("the local source asset no longer matches its recorded hash")
        return path

    def _prepare(
        self, session: Session, root: Path, payload: VisualRender, previous: VisualAsset | None,
    ) -> RenderInput:
        item = revision = brief = None
        framing = "centered"
        template = None
        if payload.content_id:
            item = session.get(ContentItem, str(payload.content_id))
            if item is None:
                raise NotFoundError("visual source content not found in this brand")
            if previous:
                revision = session.get(ContentRevision, previous.content_revision_id)
            else:
                revision = session.scalar(select(ContentRevision).where(ContentRevision.content_id == item.id)
                                          .order_by(ContentRevision.version.desc()).limit(1))
                if item.status not in {"READY_FOR_REVIEW", "APPROVED", "SCHEDULED", "PUBLISHED"}:
                    raise GenerationBlockedError("visual composition requires a review-ready content revision")
            if revision is None or revision.content_id != item.id or not revision.validation_report.get("valid"):
                raise GenerationBlockedError("the visual source revision did not pass deterministic validation")
            directive = revision.visual_directive
            if directive.get("kind") == "on_demand":
                photo = PhotoDirective.model_validate(directive)
                brief = session.get(OnDemandPhotoBrief, item.on_demand_brief_id)
                if brief is None or photo.source_asset_id != brief.source_asset_id or photo.mode != brief.treatment_mode:
                    raise GenerationBlockedError("the visual directive no longer matches its photo brief")
                selection = photo.selection
            else:
                flux = FluxDirective.model_validate(directive)
                selection = flux.selection
            framing, template = selection.framing, selection.template_id
        else:
            brief = session.get(OnDemandPhotoBrief, str(payload.on_demand_brief_id))
            if brief is None:
                raise NotFoundError("visual source photo brief not found in this brand")

        design_id = previous.design_system_id if previous else item.context_json["design_system"]["id"] if item else None
        design = session.get(BrandDesignSystem, design_id) if design_id else VersionRepository(session).assert_generation_ready()[1]
        if design is None or design.status != "APPROVED":
            raise GenerationBlockedError("visual composition requires an approved visual design system")
        approved_templates = [
            template.get("id") if isinstance(template, dict) else template for template in design.layout_templates
        ]
        if template is not None and template not in approved_templates:
            raise GenerationBlockedError("the layout is outside the approved design system")
        if template is None:
            template = next((entry for entry in approved_templates if isinstance(entry, str)), None)
        try:
            palette = tuple(normalize_hex(design.colors[role]) for role in ("primary", "secondary", "accent"))
        except (KeyError, TypeError, ValueError) as exc:
            raise GenerationBlockedError("the approved design system must define three hex color roles") from exc
        logo_id = str(payload.logo_asset_id) if payload.logo_asset_id else design.logo_safe_zones.get("asset_id")
        if logo_id is not None and not isinstance(logo_id, str):
            raise GenerationBlockedError("the approved logo reference must be an asset ID")
        logo = self._asset_path(session, root, logo_id, logo=True) if logo_id else None
        source = self._asset_path(session, root, brief.source_asset_id) if brief else None
        platform = item.platform if item else brief.platform
        if platform not in DEFAULT_ASPECT:
            raise GenerationBlockedError("the visual source platform is unsupported")
        carousel = payload.carousel if payload.carousel is not None else bool(item and item.content_depth == "carousel")
        slides = self._slides(revision, brief, carousel)
        default_seed = int.from_bytes(sha256((revision.id if revision else brief.id).encode()).digest()[:4], "big")
        return RenderInput(
            design_id=design.id, design_version=design.version, revision_id=revision.id if revision else None,
            brief_id=brief.id if brief else None, platform=platform,
            mode=f"on_demand_{brief.treatment_mode}" if brief else "flux_background_composed",
            aspect=payload.aspect or DEFAULT_ASPECT[platform], seed=payload.seed if payload.seed is not None else default_seed,
            slides=slides, image=source, logo=logo, previous_id=previous.id if previous else None,
            style=LayoutStyle(palette, design.colors, design.font_pairings, design.logo_safe_zones, template, framing),
            pixel_prompt=pixel_art_direction(design.flux_style_prefix),
        )

    @staticmethod
    def _slides(revision: ContentRevision | None, brief: OnDemandPhotoBrief | None, carousel: bool) -> list[Slide]:
        if revision:
            if not carousel:
                return [Slide(revision.hook, revision.cta)]
            paragraphs = BREAKS.split(revision.body.strip())
            if not 1 <= len(paragraphs) <= 9:
                raise GenerationBlockedError("a carousel supports up to nine evidence paragraphs")
            return [Slide(revision.hook), *(Slide("", paragraph) for paragraph in paragraphs)]
        note = brief.prompt
        headline = "עדכון מהאירוע" if any("\u05d0" <= char <= "\u05ea" for char in note) else "Event update"
        return [Slide(headline), Slide("", note)] if carousel else [Slide(headline, note)]

    def render(
        self, brand_id: str, payload: VisualRender | RenderOptions, previous_id: str | None = None,
        *, operation_id: str | None = None,
    ) -> VisualResponse:
        root = self._assets_root(brand_id)
        with self.store.brand_session(brand_id) as session:
            if operation_id and (completed := self._from_operation(session, operation_id)):
                return self._view(completed)
            previous = self._get(session, previous_id) if previous_id else None
            if previous:
                payload = VisualRender.model_validate({
                    **previous.request_json, **payload.model_dump(mode="json", exclude_unset=True),
                })
            prepared = self._prepare(session, root, payload, previous)
        output_root = root / "visuals"
        if output_root.resolve() != output_root:
            raise BrandIsolationError("the visual output directory cannot be redirected")
        output_root.mkdir(exist_ok=True)
        identifier = str(uuid4())
        destination = output_root / identifier
        size = DIMENSIONS[prepared.aspect]
        request = BackgroundRequest(size, prepared.style.palette, prepared.style.framing,
                                    prepared.pixel_prompt, prepared.seed)
        try:
            with TemporaryDirectory(prefix=".compose-", dir=output_root) as workspace:
                scratch = Path(workspace)
                output = scratch / "exports"
                output.mkdir()
                try:
                    plate = self.pipeline.plate(prepared.mode, request, prepared.image, scratch)
                    logo_uri, logo_ratio, logo_matte = (None, 1.0, "#FFFFFF")
                    if prepared.logo:
                        loader = _svg_logo if prepared.logo.suffix.lower() == ".svg" else _raster_logo
                        logo_uri, logo_ratio, logo_matte = loader(prepared.logo)
                except (OSError, RuntimeError, ValueError) as exc:
                    raise GenerationBlockedError("visual providers or source image could not be opened") from exc
                checks = self.renderer.render(plate, prepared.slides, aspect=prepared.aspect, platform=prepared.platform,
                                              style=prepared.style, logo_uri=logo_uri, logo_ratio=logo_ratio,
                                              logo_matte=logo_matte,
                                              directory=output)
                files = self._manifest(output, len(prepared.slides))
                providers = {"layout": "chromium", "composition": "pillow"}
                if prepared.mode in {"flux_background_composed", "on_demand_hero_cutout_scene"}:
                    provider = self.pipeline.background
                    providers["background"] = getattr(provider, "model_name", type(provider).__name__)
                if prepared.mode == "on_demand_hero_cutout_scene":
                    providers["segmentation"] = self.pipeline.segmentation.model_name
                with self.store.brand_session(brand_id, immediate=True) as session:
                    if operation_id and (completed := self._from_operation(session, operation_id)):
                        return self._view(completed)
                    output.rename(destination)
                    row = VisualAsset(
                        id=identifier, previous_id=prepared.previous_id, content_revision_id=prepared.revision_id,
                        on_demand_brief_id=prepared.brief_id, design_system_id=prepared.design_id,
                        design_system_version=prepared.design_version, treatment_mode=prepared.mode,
                        seed=prepared.seed, sha256=files[0].sha256, width=size[0], height=size[1],
                        request_json={**payload.model_dump(mode="json"), "aspect": prepared.aspect, "seed": prepared.seed,
                                      "carousel": len(prepared.slides) > 1},
                        output_json={"aspect": prepared.aspect, "files": [f.model_dump(mode="json") for f in files],
                                     "layout": checks.model_dump(mode="json"), "providers": providers,
                                     "operation_id": operation_id},
                    )
                    session.add(row)
                    session.flush()
                    response = self._view(row)
                return response
        except BaseException:
            if destination.is_dir():
                rmtree(destination)
            raise

    @staticmethod
    def _from_operation(session: Session, operation_id: str) -> VisualAsset | None:
        return session.scalar(select(VisualAsset).where(VisualAsset.output_json["operation_id"].as_string() == operation_id))

    @staticmethod
    def _manifest(output: Path, pages: int) -> list[RenderedFile]:
        files = [_artifact(output / f"slide-{index:02}.png", "png", page=index) for index in range(1, pages + 1)]
        if pages > 1:
            files.append(_artifact(output / "carousel.pdf", "pdf"))
            with ZipFile(output / "carousel-png.zip", "w") as archive:
                for file in files[:pages]:
                    archive.writestr(ZipInfo(file.name), (output / file.name).read_bytes(), compress_type=ZIP_DEFLATED)
            files.append(_artifact(output / "carousel-png.zip", "zip"))
        return files

    def download(self, brand_id: str, asset_id: str, format_name: str, page: int) -> tuple[bytes, RenderedFile]:
        result = self.get(brand_id, asset_id)
        if result.content_revision_id:
            with self.store.brand_session(brand_id) as session:
                revision = session.get(ContentRevision, str(result.content_revision_id))
                approved = PublishingGate(self.store).require(session, brand_id, revision.content_id, asset_id)
                for verified in approved.files:
                    manifest = verified.manifest
                    if manifest.format == format_name and (format_name != "png" or manifest.page == page):
                        return verified.data, manifest
                raise NotFoundError("visual export not found")
        file = next((item for item in result.files if item.format == format_name
                     and (format_name != "png" or item.page == page)), None)
        if file is None:
            raise NotFoundError("visual export not found")
        directory = self._assets_root(brand_id) / "visuals" / str(result.id)
        path = directory / file.name
        if path.resolve() != path or not path.is_file() or path.stat().st_size != file.bytes:
            raise GenerationBlockedError("visual export is missing or no longer matches its recorded digest")
        data = path.read_bytes()
        if len(data) != file.bytes or sha256(data).hexdigest() != file.sha256:
            raise GenerationBlockedError("visual export is missing or no longer matches its recorded digest")
        return data, file
