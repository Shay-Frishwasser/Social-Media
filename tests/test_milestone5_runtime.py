from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from brandai import BrandAIStore, RuntimeSettings
from brandai.adapters.segmentation import BiRefNetProvider, SAM2Provider, SAM2Settings
from brandai.adapters.workers import WorkerLease
from brandai.db.brand_models import Strategy
from brandai.on_demand import PhotoTreatmentPlanner
from brandai.repositories import PlanningDeliveryRepository


def test_runtime_defaults_env_file_and_expanded_override(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("BRANDAI_RUNTIME_ROOT", raising=False)
    empty_env = tmp_path / "no.env"
    expected = Path(r"C:\brandai-runtime") if os.name == "nt" else home / "brandai-runtime"
    assert RuntimeSettings.from_env(empty_env).root == expected.resolve()

    env_file = tmp_path / "mac.env"
    env_file.write_text('BRANDAI_RUNTIME_ROOT="~/BrandAI % runtime"\n', encoding="utf-8")
    assert RuntimeSettings.from_env(env_file).root == Path("~/BrandAI % runtime").expanduser().resolve()
    monkeypatch.setenv("BRANDAI_RUNTIME_ROOT", str(tmp_path / "BrandAI % runtime"))
    store = BrandAIStore(RuntimeSettings.from_env(env_file))
    brand = store.create_brand("Paths")
    assert store.settings.system_db.is_file()
    assert store.settings.brand_db(brand.id).is_file()
    assert store.settings.brand_assets_dir(brand.id).is_dir()


def test_existing_brand_is_migrated_on_first_access(store):
    brand = store.create_brand("Earlier brand")
    with store.brand_session(brand.id) as session:
        legacy = PlanningDeliveryRepository(session).add_strategy({"topic": "Historical strategy"})
    # Recreate the old on-disk shape before opening it through a new store.
    engine = store.brand_engine(brand.id)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE visual_assets"))
            connection.execute(text("DROP TABLE external_evidence"))
            connection.execute(text("DROP TABLE topic_research"))
            connection.execute(text("DROP TABLE content_revisions"))
            connection.execute(text("DROP TABLE content_items"))
            connection.execute(text("DROP TABLE planned_content_slots"))
            connection.execute(text("DROP TABLE platform_rule_overrides"))
            connection.execute(text("UPDATE alembic_version SET version_num = '0003_on_demand_photo_briefs'"))
    finally:
        engine.dispose()
    reopened = BrandAIStore(store.settings)
    with reopened.brand_session(brand.id) as session:
        assert set(inspect(session.connection()).get_table_names()) >= {
            "planned_content_slots", "platform_rule_overrides", "content_items", "content_revisions",
        }
        assert session.get(Strategy, legacy.id).document == {"topic": "Historical strategy"}


def test_sam2_configuration_and_gpu_worker_contract(tmp_path, monkeypatch):
    monkeypatch.delenv("BRANDAI_SAM2_VARIANT", raising=False)
    monkeypatch.delenv("BRANDAI_SAM2_DIR", raising=False)
    runtime = RuntimeSettings(tmp_path)
    default = SAM2Settings.from_runtime(runtime)
    assert default.checkpoint == tmp_path / "models/segmentation/sam2/sam2.1_hiera_tiny.pt"
    monkeypatch.setenv("BRANDAI_SAM2_VARIANT", "sam2.1_hiera_small")
    monkeypatch.setenv("BRANDAI_SAM2_DIR", str(tmp_path / "worker-models"))
    settings = SAM2Settings.from_runtime(runtime)
    assert settings.checkpoint == tmp_path / "worker-models/sam2.1_hiera_small.pt"
    assert settings.model_config.endswith("sam2.1_hiera_s.yaml")

    steps = []

    class StubWorker:
        @contextmanager
        def lease(self, capability):
            steps.append(capability)
            try:
                yield WorkerLease("http://127.0.0.1:8080", "fake", frozenset({capability}))
            finally:
                steps.append("released")

    def runner(lease, config, source, destination):
        assert lease.worker_id == "fake" and config == settings and source.name == "original.png"
        steps.append("runner")
        return destination

    photo = PhotoTreatmentPlanner().plan("hero_cutout_scene", platform="instagram", design_system_version=1)
    assert photo.output_contract["segmentation_provider"] == "sam2"
    destination = tmp_path / "rgba.png"
    provider = SAM2Provider(settings, StubWorker(), runner)
    assert provider.cutout(tmp_path / "original.png", destination) == destination
    assert steps == ["foreground_segmentation", "runner", "released"]
    with pytest.raises(RuntimeError, match="not enabled"):
        BiRefNetProvider().cutout(tmp_path / "original.png", destination)
    monkeypatch.setenv("BRANDAI_SAM2_VARIANT", "unapproved")
    with pytest.raises(ValueError, match="tiny or small"):
        SAM2Settings.from_runtime(runtime)
