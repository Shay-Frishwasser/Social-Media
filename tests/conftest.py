from __future__ import annotations

import pytest

from brandai import BrandAIStore, RuntimeSettings


@pytest.fixture
def store(tmp_path):
    return BrandAIStore(RuntimeSettings(tmp_path / "runtime"))


@pytest.fixture
def two_brands(store):
    a = store.create_brand("Brand A", "11111111-1111-4111-8111-111111111111")
    b = store.create_brand("Brand B", "22222222-2222-4222-8222-222222222222")
    return a, b
