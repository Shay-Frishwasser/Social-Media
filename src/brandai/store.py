from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from uuid import UUID, uuid4

from sqlalchemy import select, text

from .config import RuntimeSettings
from .db.engine import create_sqlite_engine, session_factory
from .db.global_models import Brand, PlatformPlaybook
from .errors import BrandIsolationError, NotFoundError
from .migrations import upgrade_brand, upgrade_global


class BrandAIStore:
    def __init__(self, settings: RuntimeSettings):
        self.settings = settings
        settings.ensure_layout()
        upgrade_global(settings.system_db)
        self.global_engine = create_sqlite_engine(settings.system_db)
        self.GlobalSession = session_factory(self.global_engine)
        self._migrated_brands: set[str] = set()
        self._brand_migration_lock = Lock()

    def create_brand(self, name: str, brand_id: str | None = None) -> Brand:
        brand_id = brand_id or str(uuid4())
        UUID(brand_id)
        brand_dir = self.settings.brand_dir(brand_id)
        assets_dir = self.settings.brand_assets_dir(brand_id)
        assets_dir.mkdir(parents=True, exist_ok=False)
        db_path = self.settings.brand_db(brand_id)
        upgrade_brand(db_path)
        with self.GlobalSession.begin() as session:
            row = Brand(id=brand_id, name=name, db_path=str(db_path.resolve()), assets_path=str(assets_dir.resolve()))
            session.add(row)
        with self._brand_migration_lock:
            self._migrated_brands.add(brand_id)
        return row

    def brand_engine(self, brand_id: str):
        UUID(brand_id)
        with self.GlobalSession() as session:
            row = session.get(Brand, brand_id)
            if row is None:
                raise NotFoundError(f"unknown brand {brand_id}")
        expected = self.settings.brand_db(brand_id).resolve()
        actual = Path(row.db_path).resolve()
        if actual != expected or not actual.is_relative_to(self.settings.brands_dir.resolve()):
            raise BrandIsolationError("registry database path violates brand boundary")
        with self._brand_migration_lock:
            if brand_id not in self._migrated_brands:
                upgrade_brand(actual)
                self._migrated_brands.add(brand_id)
        return create_sqlite_engine(actual)

    @contextmanager
    def brand_session(self, brand_id: str, *, immediate: bool = False):
        engine = self.brand_engine(brand_id)
        try:
            with session_factory(engine).begin() as session:
                if immediate:
                    session.execute(text("BEGIN IMMEDIATE"))
                yield session
        finally:
            engine.dispose()

    def register_playbook(self, path: Path, *, expected: tuple[str, int] | None = None) -> PlatformPlaybook:
        payload = path.read_bytes()
        content = json.loads(payload)
        if expected and (content["platform"], content["version"]) != expected:
            raise ValueError("playbook does not match the requested platform and version")
        digest = hashlib.sha256(payload).hexdigest()
        criteria = (PlatformPlaybook.platform == content["platform"], PlatformPlaybook.version == content["version"])
        with self.GlobalSession() as session:
            row = session.scalar(select(PlatformPlaybook).where(*criteria))
            if row:
                if row.sha256 != digest:
                    raise ValueError("a playbook version is immutable; increment its version")
                return row
        with self.GlobalSession.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.scalar(select(PlatformPlaybook).where(*criteria))
            if row:
                if row.sha256 != digest:
                    raise ValueError("a playbook version is immutable; increment its version")
                return row
            row = PlatformPlaybook(platform=content["platform"], version=content["version"],
                                   file_path=str(path.resolve()), sha256=digest, content=content)
            session.add(row); session.flush(); return row
