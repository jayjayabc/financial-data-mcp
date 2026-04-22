"""FISIS 전 권역 회사 레지스트리 — 런타임 부트스트랩 캐시.

설계 배경:
- 기존 dart_to_fisis_bridge 는 손으로 써넣은 키워드 테이블(_INDUTY_TO_FISIS_DIV)에
  의존했기에 투자일임·신기술금융·재보험 등 누락 권역이 많았다.
- FISIS 에 "실제로 등록된" 권역·회사 목록이 사실상의 ground truth 이므로,
  서버 생애 1회 FISIS companySearch.json 을 A~Z 전 권역에 대해 병렬 호출하여
  메모리 인덱스를 구축한다.
- 이후 bridge 는 DART 기업명·finance_cd 를 이 인덱스로 역조회하여
  실제 FISIS lrg_div(partDiv) 를 반환한다.

실패 시나리오:
- 네트워크 단절/403 시 레지스트리는 empty 로 남고, 호출 측은 기존 하드코딩
  매핑으로 폴백한다.
"""

from __future__ import annotations

import asyncio
import logging
import string
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from .fisis_client import FisisClient

logger = logging.getLogger(__name__)


# A~Z 중 실제로 FISIS partDiv 로 의미 있는 응답을 주는 코드는 소수(보통 A~T 범위).
# 빈 응답·에러는 단순히 스킵한다.
_SCAN_CODES: tuple[str, ...] = tuple(string.ascii_uppercase)


def _pick(d: dict, *keys: str) -> str | None:
    """dict 에서 여러 별칭 키 중 첫 번째로 존재하는 값을 반환 (snake/camelCase 방어)."""
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return str(v)
    return None


def _normalize_name(name: str) -> str:
    """회사명 비교용 정규화 — 공백·㈜·(주)·기타 특수문자 제거."""
    return (
        name.replace("㈜", "")
        .replace("(주)", "")
        .replace("주식회사", "")
        .replace(" ", "")
        .strip()
        .lower()
    )


@dataclass
class FisisCompany:
    finance_cd: str
    finance_nm: str
    lrg_div: str  # 우리가 호출한 partDiv (예: 'K')
    lrg_div_nm: str | None  # 응답에 포함되면 저장 (예: '리스사')
    raw: dict = field(default_factory=dict)


@dataclass
class FisisRegistry:
    """프로세스 생애 1회 구축되는 FISIS 회사 레지스트리.

    - by_finance_cd: finance_cd → FisisCompany
    - by_normalized_name: 정규화된 회사명 → [FisisCompany, ...]
    - lrg_div_labels: lrg_div 코드 → 응답에서 관찰된 한글 라벨
    """

    by_finance_cd: dict[str, FisisCompany] = field(default_factory=dict)
    by_normalized_name: dict[str, list[FisisCompany]] = field(default_factory=dict)
    lrg_div_labels: dict[str, str] = field(default_factory=dict)
    loaded: bool = False
    load_errors: dict[str, str] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def ensure_loaded(self, client: "FisisClient") -> None:
        """A~Z 전 권역 회사 목록을 병렬 조회하여 인덱스 구축. 1회만 실행."""
        if self.loaded:
            return
        async with self._lock:
            if self.loaded:
                return
            await self._load(client)
            self.loaded = True

    async def _load(self, client: "FisisClient") -> None:
        async def _fetch(code: str) -> tuple[str, list[dict] | Exception]:
            try:
                data = await client.list_companies(lrg_div=code)
                items = _extract_list(data)
                return code, items
            except Exception as e:  # noqa: BLE001
                return code, e

        results = await asyncio.gather(*[_fetch(c) for c in _SCAN_CODES])

        for code, payload in results:
            if isinstance(payload, Exception):
                self.load_errors[code] = f"{type(payload).__name__}: {payload}"
                continue
            if not payload:
                continue
            self._ingest(code, payload)

        logger.info(
            "FisisRegistry loaded: %d companies across %d sectors (errors: %d)",
            len(self.by_finance_cd),
            len({c.lrg_div for c in self.by_finance_cd.values()}),
            len(self.load_errors),
        )

    def _ingest(self, lrg_div: str, items: Iterable[dict]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            finance_cd = _pick(item, "finance_cd", "financeCd")
            finance_nm = _pick(item, "finance_nm", "financeNm", "home_nm", "homeNm")
            lrg_div_nm = _pick(item, "lrg_div_nm", "lrgDivNm")
            if not finance_cd or not finance_nm:
                continue
            company = FisisCompany(
                finance_cd=finance_cd,
                finance_nm=finance_nm,
                lrg_div=lrg_div,
                lrg_div_nm=lrg_div_nm,
                raw=dict(item),
            )
            self.by_finance_cd[finance_cd] = company
            key = _normalize_name(finance_nm)
            if key:
                self.by_normalized_name.setdefault(key, []).append(company)
            if lrg_div_nm and lrg_div not in self.lrg_div_labels:
                self.lrg_div_labels[lrg_div] = lrg_div_nm

    # ── 조회 API ────────────────────────────────────────────────

    def lookup_by_finance_cd(self, finance_cd: str) -> FisisCompany | None:
        return self.by_finance_cd.get(finance_cd)

    def lookup_by_name(self, corp_name: str) -> FisisCompany | None:
        """회사명 기반 조회. 정확 → 부분 포함 순."""
        if not corp_name:
            return None
        target = _normalize_name(corp_name)
        if not target:
            return None
        # 정확 일치
        exact = self.by_normalized_name.get(target)
        if exact:
            return exact[0]
        # 부분 포함 (양방향) — 가장 긴 매칭 우선
        candidates: list[tuple[int, FisisCompany]] = []
        for name_key, companies in self.by_normalized_name.items():
            if not name_key:
                continue
            if target in name_key or name_key in target:
                candidates.append((len(name_key), companies[0]))
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1]
        return None

    def sectors(self) -> dict[str, dict[str, int | str]]:
        """권역별 등록 회사 수 요약 — 진단/안내용."""
        counts: dict[str, int] = {}
        for c in self.by_finance_cd.values():
            counts[c.lrg_div] = counts.get(c.lrg_div, 0) + 1
        return {
            code: {
                "lrg_div_nm": self.lrg_div_labels.get(code, ""),
                "company_count": cnt,
            }
            for code, cnt in sorted(counts.items())
        }


def _extract_list(data: object) -> list[dict]:
    """FISIS 응답에서 회사 목록을 방어적으로 추출."""
    if not isinstance(data, dict):
        return []
    root = data.get("result") if isinstance(data.get("result"), dict) else data
    for key in ("list", "data"):
        v = root.get(key) if isinstance(root, dict) else None
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
    return []
