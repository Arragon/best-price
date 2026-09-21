"""共享 fixture。所有测试均离线，使用 tests/fake_adapter.py 的合成数据。"""

from __future__ import annotations

import pytest

from tests.fake_adapter import FakeAdapter, body_listings
from tests.helpers import build_client


@pytest.fixture
def adapter() -> FakeAdapter:
    return FakeAdapter(pages=[body_listings(7001, 8)])


@pytest.fixture
def client(tmp_path, adapter):
    with build_client(tmp_path, adapter) as test_client:
        yield test_client
