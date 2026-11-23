from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from brandai.db.brand_models import PlatformRuleOverride
from brandai.errors import GenerationBlockedError
from brandai.playbooks import copy_playbook_if_missing
from brandai.store import BrandAIStore

from .models import DEPTHS, EffectivePlaybook, Platform, PlatformOverrideUpdate, PlatformOverrides


# Local planning defaults. Entries follow DEPTHS order.
DEPTH_PRIORS = {
    "instagram": (15, 20, 5, 15, 15, 30),
    "tiktok": (15, 25, 5, 20, 30, 5),
    "linkedin": (10, 20, 20, 30, 10, 10),
    "facebook": (15, 25, 10, 20, 20, 10),
    "youtube": (20, 15, 30, 25, 10, 0),
    "x": (35, 20, 15, 20, 10, 0),
}


class BrandPlaybooks:
    def __init__(self, store: BrandAIStore, session: Session):
        self.store, self.session = store, session

    def override(self, platform: Platform, update: PlatformOverrideUpdate) -> EffectivePlaybook:
        previous = self._latest(platform)
        self.session.add(PlatformRuleOverride(
            platform=platform, version=previous.version + 1 if previous else 1,
            document=update.model_dump(mode="json", exclude={"created_by"}), created_by=update.created_by,
        ))
        self.session.flush()
        return self.resolve(platform)

    def _latest(self, platform: Platform) -> PlatformRuleOverride | None:
        return self.session.scalar(select(PlatformRuleOverride).where(
            PlatformRuleOverride.platform == platform,
        ).order_by(PlatformRuleOverride.version.desc()).limit(1))

    def resolve(self, platform: Platform, plan_overrides: PlatformOverrides | None = None) -> EffectivePlaybook:
        relative = Path(platform) / "v1.json"
        target = self.store.settings.playbooks_dir / relative
        if not target.is_file():
            configured = os.environ.get("BRANDAI_PLAYBOOK_SOURCE")
            source = Path(configured).expanduser() if configured else Path(__file__).resolve().parents[3] / "playbooks"
            try:
                copy_playbook_if_missing(source / relative, target)
            except FileNotFoundError as exc:
                raise GenerationBlockedError(f"missing bundled playbook {source / relative}") from exc
        try:
            row = self.store.register_playbook(target, expected=(platform, 1))
            rules = row.content
            prior = rules["cadence_prior"]
            if "range_per_week" in prior:
                frequency = prior["range_per_week"][0]
            elif "range_per_day" in prior:
                frequency = 7 * prior["range_per_day"][0]
            else:
                frequency = prior["long_form_per_week"][0] + prior["shorts_per_week"][0]
            link_policy = rules.get("link_policy") or rules.get("caption_link_policy") or "Use supported link placements."
            if not isinstance(frequency, int) or not 1 <= frequency <= 42:
                raise ValueError("invalid cadence prior")
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise GenerationBlockedError(f"invalid {platform} v1 playbook: {exc}") from exc
        saved = self._latest(platform)
        fields = saved.document if saved else {}
        if plan_overrides:
            fields = {**fields, **plan_overrides.model_dump(mode="json", exclude_unset=True)}
        overrides = PlatformOverrides.model_validate(fields)
        if platform == "linkedin" and overrides.account_type:
            account_label = {
                "company_page": "Company Page: use a direct, relevant link when the post serves a Page objective.",
                "personal_profile": "Personal profile: lead with firsthand insight and link when it adds context.",
            }[overrides.account_type]
            link_policy = f"{account_label} {link_policy}"
        return EffectivePlaybook(
            platform=platform, playbook_version=row.version, sha256=row.sha256,
            override_version=saved.version if saved else None, rules=rules, overrides=overrides,
            posts_per_week=overrides.posts_per_week or frequency,
            depth_mix=overrides.depth_mix or dict(zip(DEPTHS, DEPTH_PRIORS[platform], strict=True)),
            link_policy=overrides.link_policy or link_policy,
        )
