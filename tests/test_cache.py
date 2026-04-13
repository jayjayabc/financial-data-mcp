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


# ── hit/miss 메트릭 테스트 ─────────────────────────────────────


def test_hit_miss_counters():
    """hit/miss 카운터가 정확하게 증가하는지."""
    cache = TTLCache(ttl_seconds=60)
    cache.set("a", 1)

    cache.get("a")       # hit
    cache.get("a")       # hit
    cache.get("missing") # miss

    assert cache.hits == 2
    assert cache.misses == 1


def test_expired_entry_counts_as_miss():
    """만료된 항목 조회는 miss로 집계."""
    cache = TTLCache(ttl_seconds=0)
    cache.set("a", 1)
    time.sleep(0.01)
    cache.get("a")  # 만료 → miss

    assert cache.hits == 0
    assert cache.misses == 1


def test_stats_returns_correct_structure():
    """stats()가 올바른 필드와 값을 반환하는지."""
    cache = TTLCache(ttl_seconds=60, max_size=10)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")       # hit
    cache.get("missing") # miss

    stats = cache.stats()
    assert stats["size"] == 2
    assert stats["max_size"] == 10
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_rate_pct"] == 50.0


def test_stats_empty_cache():
    """빈 캐시의 stats는 hit_rate 0."""
    cache = TTLCache(ttl_seconds=60)
    stats = cache.stats()
    assert stats["hit_rate_pct"] == 0.0
    assert stats["size"] == 0


# ── LRU 동작 테스트 (OrderedDict) ─────────────────────────────


def test_lru_evicts_least_recently_used():
    """get으로 접근한 항목은 축출 대상에서 제외됨."""
    cache = TTLCache(ttl_seconds=60, max_size=3)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)

    # a를 접근 → a가 가장 최근이 됨
    cache.get("a")

    # d 삽입 → b가 축출 (b가 가장 오래 미접근)
    cache.set("d", 4)

    assert cache.get("a") is not None  # 살아있음 (최근 접근)
    assert cache.get("b") is None      # 축출됨
    assert cache.get("c") is not None
    assert cache.get("d") is not None


def test_update_moves_to_end():
    """동일 키 업데이트 시 축출 우선순위가 뒤로 이동."""
    cache = TTLCache(ttl_seconds=60, max_size=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("a", 10)  # a를 업데이트 → 맨 뒤로

    cache.set("c", 3)   # b가 축출 (가장 앞)
    assert cache.get("a") == 10
    assert cache.get("b") is None
    assert cache.get("c") == 3
