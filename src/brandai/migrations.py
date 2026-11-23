from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def _upgrade(db_path: Path, tree: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "alembic" / tree).replace("%", "%%"))
    url = f"sqlite+pysqlite:///{db_path.resolve().as_posix()}"
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")


def upgrade_global(db_path: Path) -> None:
    _upgrade(db_path, "global")


def upgrade_brand(db_path: Path) -> None:
    _upgrade(db_path, "brand")
