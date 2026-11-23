from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    root: Path

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "RuntimeSettings":
        if env_file is not None:
            load_dotenv(env_file)
        else:
            load_dotenv()
        default = Path(r"C:\brandai-runtime") if os.name == "nt" else Path.home() / "brandai-runtime"
        root = os.environ.get("BRANDAI_RUNTIME_ROOT") or default
        return cls(root=Path(root).expanduser().resolve())

    @property
    def system_db(self) -> Path:
        return self.root / "system.db"

    @property
    def brands_dir(self) -> Path:
        return self.root / "brands"

    @property
    def playbooks_dir(self) -> Path:
        return self.root / "playbooks"

    def directory(self, variable: str, relative_default: str) -> Path:
        configured = os.environ.get(variable)
        return Path(configured).expanduser().resolve() if configured else self.root / relative_default

    def brand_dir(self, brand_id: str) -> Path:
        return self.brands_dir / brand_id

    def brand_db(self, brand_id: str) -> Path:
        return self.brand_dir(brand_id) / "brand.db"

    def brand_assets_dir(self, brand_id: str) -> Path:
        return self.brand_dir(brand_id) / "assets"

    def ensure_layout(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.brands_dir.mkdir(parents=True, exist_ok=True)
        self.playbooks_dir.mkdir(parents=True, exist_ok=True)

