from __future__ import annotations

from sqlalchemy import Connection, Engine, text

from .base import BrandBase, GlobalBase
from . import brand_models as _brand_models  # noqa: F401
from . import global_models as _global_models  # noqa: F401


IMMUTABLE_TABLES = ("brand_profiles", "brand_design_systems", "content_package_revisions")


def create_global_schema(engine: Engine) -> None:
    GlobalBase.metadata.create_all(engine)


def create_brand_schema(bind: Engine | Connection) -> None:
    BrandBase.metadata.create_all(bind)

    def configure(connection: Connection) -> None:
        connection.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS source_chunks_fts "
            "USING fts5(text, content='source_chunks', content_rowid='rowid', tokenize='trigram')"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS source_chunks_ai AFTER INSERT ON source_chunks BEGIN "
            "INSERT INTO source_chunks_fts(rowid, text) VALUES (new.rowid, new.text); END"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS source_chunks_ad AFTER DELETE ON source_chunks BEGIN "
            "INSERT INTO source_chunks_fts(source_chunks_fts, rowid, text) VALUES('delete', old.rowid, old.text); END"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS source_chunks_au AFTER UPDATE ON source_chunks BEGIN "
            "INSERT INTO source_chunks_fts(source_chunks_fts, rowid, text) VALUES('delete', old.rowid, old.text); "
            "INSERT INTO source_chunks_fts(rowid, text) VALUES (new.rowid, new.text); END"
        )
        for table in IMMUTABLE_TABLES:
            for operation in ("UPDATE", "DELETE"):
                name = f"immutable_{table}_{operation.lower()}"
                connection.execute(text(
                    f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {operation} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} is immutable'); END"
                ))
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            configure(connection)
    else:
        configure(bind)
