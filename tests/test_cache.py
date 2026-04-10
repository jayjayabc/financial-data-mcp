"""_cache 모듈 단위 테스트."""

import time

from financial_data_mcp._cache import TTLCache


def test_set_and_get():
    cache = TTLCache(ttl_seconds=60)
    cache.set("k", "v")
    assert cache.get("k") == "v"


def test_miss_returns_none():
    cache = TTLCache(ttl_seconds=60)
    assert cache.get("missing") is None


def test_expiry_removes_entry():
    cache = TTLCache(ttl_seconds=0)
    cache.set("k", "v")
    time.sleep(0.01)
    assert cache.get("k") is None
    # 만료된 항목은 자동 제거
    assert len(cache) == 0


def test_max_size_evicts_oldest():
    cache = TTLCache(ttl_seconds=60, max_size=2)
    cache.set("a", 1)
    time.sleep(0.001)
    cache.set("b", 2)
    time.sleep(0.001)
    cache.set("c", 3)  # a 가 축출되어야 함
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3


def test_update_same_key_does_not_grow():
    cache = TTLCache(ttl_seconds=60, max_size=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("a", 10)  # 업데이트 (새 항목 아님)
    assert len(cache) == 2
    assert cache.get("a") == 10
    assert cache.get("b") == 2


def test_clear():
    cache = TTLCache(ttl_seconds=60)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.clear()
    assert len(cache) == 0
