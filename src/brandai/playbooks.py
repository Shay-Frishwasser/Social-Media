from __future__ import annotations

import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile

from .store import BrandAIStore


def copy_playbook_if_missing(source: Path, target: Path) -> None:
    """Publish a complete file without overwriting another seeder's version."""
    if target.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
        staging = Path(temporary.name)
    try:
        shutil.copyfile(source, staging)
        try:
            target.hardlink_to(staging)
        except FileExistsError:
            pass
    finally:
        staging.unlink(missing_ok=True)


def seed_playbooks(store: BrandAIStore, source_dir: Path) -> list[Path]:
    seeded = []
    for source in sorted(source_dir.glob("*/v*.json")):
        target = store.settings.playbooks_dir / source.parent.name / source.name
        copy_playbook_if_missing(source, target)
        if target.read_bytes() != source.read_bytes():
            raise ValueError(f"refusing to overwrite immutable playbook {target}")
        store.register_playbook(target)
        seeded.append(target)
    return seeded
