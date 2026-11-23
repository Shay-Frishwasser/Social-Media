from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from sqlalchemy.orm import Session

from brandai.content.repository import revision_copy
from brandai.content.validator import caption_text
from brandai.db.brand_models import BrandClaim, PlannedContentSlot, Source

from .gate import GateContext
from .models import JERUSALEM


@dataclass(frozen=True)
class DeliveryBundle:
    data: bytes
    manifest: dict


def publish_window(session: Session, context: GateContext, now: datetime,
                   due: datetime | None) -> tuple[str, str]:
    if due is None and context.item.cadence_slot_id:
        slot = session.get(PlannedContentSlot, context.item.cadence_slot_id)
        if slot and slot.scheduled_at_utc.replace(tzinfo=UTC) > now:
            due = slot.scheduled_at_utc.replace(tzinfo=UTC)
    if due is None:
        tomorrow = now.astimezone(JERUSALEM).date() + timedelta(days=1)
        due = datetime.combine(tomorrow, time(10), tzinfo=JERUSALEM)
    start = due.astimezone(JERUSALEM)
    end = (start.astimezone(UTC) + timedelta(hours=2)).astimezone(JERUSALEM)
    return start.isoformat(), end.isoformat()


def build_bundle(session: Session, context: GateContext, now: datetime, due: datetime | None = None) -> DeliveryBundle:
    item, revision, visual = context.item, context.revision, context.visual
    copy = revision_copy(revision)
    brand_claims = []
    for claim_id in revision.cited_claim_ids:
        claim = session.get(BrandClaim, claim_id)
        source = session.get(Source, claim.source_id) if claim and claim.source_id else None
        if claim:
            brand_claims.append({"id": claim.id, "field": claim.field_key, "quoted_span": claim.quoted_span,
                                 "source_id": claim.source_id, "source_url": source.url if source else None})
    files = [file for file in context.files if file.manifest.format in {"png", "pdf"}]
    start, end = publish_window(session, context, now, due)
    manifest = {
        "schema_version": 1, "brand_id": item.brand_id, "content_id": item.id, "revision_id": revision.id,
        "visual_asset_id": visual.id, "visual_seed": visual.seed, "visual_treatment": visual.treatment_mode,
        "design_system_id": visual.design_system_id, "design_system_version": visual.design_system_version,
        "profile": item.context_json["profile"], "strategy_id": item.strategy_id, "platform": item.platform,
        "playbook_version": item.playbook_version, "playbook_sha256": item.context_json["playbook"]["sha256"],
        "playbook_override_version": item.context_json["playbook"]["override_version"],
        "copy": copy.model_dump(mode="json"), "caption": caption_text(copy),
        "approved_by": item.approved_by, "approved_at": item.approved_at.replace(tzinfo=UTC).isoformat(),
        "brand_claims": brand_claims, "external_citations": revision.research_json.get("external_citations", []),
        "publish_window": {"timezone": "Asia/Jerusalem", "start": start, "end": end},
        "files": [file.manifest.model_dump(mode="json") for file in files],
    }
    archive = BytesIO()
    with ZipFile(archive, "w") as output:
        entries = {"manifest.json": json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode(),
                   "copy.txt": manifest["caption"].encode()}
        entries.update({f"media/{file.manifest.name}": file.data for file in files})
        for name, data in entries.items():
            output.writestr(ZipInfo(name), data, compress_type=ZIP_DEFLATED)
    return DeliveryBundle(archive.getvalue(), manifest)
