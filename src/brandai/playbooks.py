from __future__ import annotations

import shutil
from pathlib import Path

from .store import BrandAIStore


def seed_playbooks(store: BrandAIStore, source_dir: Path) -> list[Path]:
    seeded = []
    for source in sorted(source_dir.glob("*/v*.json")):
        target = store.settings.playbooks_dir / source.parent.name / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != source.read_bytes():
            raise ValueError(f"refusing to overwrite immutable playbook {target}")
        if not target.exists():
            shutil.copy2(source, target)
        store.register_playbook(target)
        seeded.append(target)
    return seeded
