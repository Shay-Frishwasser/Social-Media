from pathlib import Path

from sqlalchemy import inspect, select, text

from brandai.db.global_models import PlatformPlaybook
from brandai.playbooks import seed_playbooks


def test_migrations_and_six_versioned_playbooks(store):
    source = Path(__file__).parents[1] / "playbooks"
    paths = seed_playbooks(store, source)
    assert len(paths) == 6
    assert all(p.is_relative_to(store.settings.playbooks_dir) for p in paths)
    with store.GlobalSession() as session:
        rows = session.scalars(select(PlatformPlaybook)).all()
        assert {r.platform for r in rows} == {"instagram", "tiktok", "linkedin", "facebook", "youtube", "x"}
    assert "alembic_version" in inspect(store.global_engine).get_table_names()


def test_brand_migration_reaches_source_payload_head(store, two_brands):
    brand, _ = two_brands
    engine = store.brand_engine(brand.id)
    try:
        assert {column["name"] for column in inspect(engine).get_columns("sources")} >= {"raw_content", "media_type"}
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0005_content_pipeline"
    finally:
        engine.dispose()
