"""가벼운 캐싱 유틸리티 (외부 의존성 없음).

- load_disk_cache / save_disk_cache: JSON 파일 기반 디스크 캐시 (TTL 지원)
- TTLCache: TTL 메모리 캐시 (OrderedDict 기반 O(1) LRU 축출, hit/miss 메트릭)
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

CACHE_DIR = Path.home() / ".cache" / "financial_data_mcp"


def load_disk_cache(name: str, ttl_seconds: int) -> Any | None:
    """디스크 캐시에서 읽기. 파일이 없거나 만료 시 None 반환."""
    path = CACHE_DIR / f"{name}.json"
    if not path.exists():
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if time.time() - mtime > ttl_seconds:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_disk_cache(name: str, data: Any) -> None:
    """디스크 캐시에 저장. 실패 시 조용히 무시."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = CACHE_DIR / f"{name}.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


class TTLCache:
    """TTL 메모리 캐시 (OrderedDict 기반 O(1) LRU 축출).

    - 만료된 항목은 get 시점에 자동 제거
    - max_size 초과 시 가장 오래 저장된 항목을 O(1)로 축출
    - hit/miss 카운터로 캐시 효율 관측 가능
    - 단일 프로세스 MCP 서버에서만 사용 (thread-safe 아님)
    """

    def __init__(self, ttl_seconds: int, max_size: int = 128) -> None:
        self.ttl = ttl_seconds
        self.max_size = max_size
        self._store: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: Any) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            self.misses += 1
            return None
        ts, value = entry
        if time.time() - ts > self.ttl:
            self._store.pop(key, None)
            self.misses += 1
            return None
        # 접근한 항목을 맨 뒤로 이동 (LRU)
        self._store.move_to_end(key)
        self.hits += 1
        return value

    def set(self, key: Any, value: Any) -> None:
        if key in self._store:
            # 기존 키 업데이트 → 맨 뒤로 이동
            self._store.move_to_end(key)
        elif len(self._store) >= self.max_size:
            # 가장 오래된 항목(맨 앞) O(1) 축출
            self._store.popitem(last=False)
        self._store[key] = (time.time(), value)

    def clear(self) -> None:
        self._store.clear()

    def stats(self) -> dict[str, Any]:
        """캐시 상태 요약 (hit/miss/rate/size)."""
        total = self.hits + self.misses
        return {
            "size": len(self._store),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate_pct": round(self.hits / total * 100, 1) if total > 0 else 0.0,
        }

    def __len__(self) -> int:
        return len(self._store)
