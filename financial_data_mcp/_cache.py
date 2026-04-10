"""가벼운 캐싱 유틸리티 (외부 의존성 없음).

- load_disk_cache / save_disk_cache: JSON 파일 기반 디스크 캐시 (TTL 지원)
- TTLCache: 단순 TTL 메모리 캐시 (크기 제한, 가장 오래된 항목 축출)
"""

from __future__ import annotations

import json
import time
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
    """단순 TTL 메모리 캐시.

    - 만료된 항목은 get 시점에 자동 제거
    - max_size 초과 시 가장 오래 저장된 항목부터 축출
    - 단일 프로세스 MCP 서버에서만 사용 (thread-safe 아님)
    """

    def __init__(self, ttl_seconds: int, max_size: int = 128) -> None:
        self.ttl = ttl_seconds
        self.max_size = max_size
        self._store: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.time() - ts > self.ttl:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: Any, value: Any) -> None:
        if key not in self._store and len(self._store) >= self.max_size:
            oldest = min(self._store, key=lambda k: self._store[k][0])
            del self._store[oldest]
        self._store[key] = (time.time(), value)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)
