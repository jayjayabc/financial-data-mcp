"""FISIS(금융통계정보시스템) OpenAPI 클라이언트.

API 문서: https://fisis.fss.or.kr/fisis/openapi/apiInfo.do

주요 최적화:
- 싱글톤 재사용 전제: 인스턴스 내부에 httpx.AsyncClient, 응답 캐시 보관
- 응답 캐시: 통계 데이터는 변동이 적어 1시간 TTL 캐시
- 재시도: 지수 백오프 (3회, 5xx/429/transport 에러)
- 에러 파싱 방어적 처리: 엔드포인트별로 result 래핑/err_msg 스키마가 달라질 수 있음
"""

from __future__ import annotations

import logging

import httpx

from ._cache import TTLCache
from ._http import mask_params, translate_http_error, with_retry

logger = logging.getLogger("financial_data_mcp.fisis")

BASE_URL = "https://fisis.fss.or.kr/openapi"
RESPONSE_TTL_SECONDS = 3600  # 1시간

# 대분류 코드 (2026-04-12 실 API 검증 완료)
LARGE_DIVISIONS = {
    "은행": "A",
    "비은행": "B",
    "보험": "C",
    "금융투자": "D",
}

# 알려진 소분류 코드 (lrg_div별 sml_div 참조 매핑)
# 실제 API 응답에서 동적으로 조회하려면 FisisClient.list_divisions() 사용
SMALL_DIVISIONS: dict[str, dict[str, str]] = {
    "A": {
        "은행": "A",
        "_note": "통계 엔드포인트에서는 sml_div 없이 lrg_div=A만으로 조회 가능. "
                 "금융회사 조회(companySearch)에서 세부 업권 확인.",
    },
    "B": {
        "은행신탁": "B010",
        "증권신탁": "B020",
        "보험신탁": "B030",
        "_note": "비은행 권역. fisis_list_divisions 도구로 최신 코드 확인 권장.",
    },
    "C": {
        "신용카드사": "C010",
        "할부금융사": "C020",
        "시설대여(리스)사": "C030",
        "신기술금융사": "C040",
        "_note": "여신전문금융사 권역 (카드·캐피탈·리스·신기술). "
                 "fisis_list_divisions 도구로 최신 코드 확인 권장.",
    },
    "D": {
        "증권회사": "D010",
        "자산운용사": "D020",
        "투자자문사": "D030",
        "_note": "금융투자 권역. fisis_list_divisions 도구로 최신 코드 확인 권장.",
    },
}


class FisisClient:
    """FISIS OpenAPI 비동기 클라이언트. 싱글톤으로 재사용되어야 함."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": "financial-data-mcp/0.1"},
        )
        self._response_cache = TTLCache(
            ttl_seconds=RESPONSE_TTL_SECONDS, max_size=256
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(
        self,
        endpoint: str,
        params: dict | None = None,
        use_cache: bool = True,
    ) -> dict:
        params = dict(params or {})
        params["auth"] = self.api_key
        params.setdefault("lang", "kr")

        cache_key: tuple | None = None
        if use_cache:
            cache_key = (endpoint, tuple(sorted(params.items())))
            cached = self._response_cache.get(cache_key)
            if cached is not None:
                logger.debug("cache hit: %s %s", endpoint, mask_params(params))
                return cached

        logger.debug("api call: %s %s", endpoint, mask_params(params))

        async def _do() -> dict:
            resp = await self._client.get(f"/{endpoint}", params=params)
            resp.raise_for_status()
            return resp.json()

        try:
            data = await with_retry(_do, label=f"FISIS {endpoint}")
        except (httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException) as e:
            raise translate_http_error("FISIS", e) from e

        # FISIS 응답 스키마는 엔드포인트마다 다를 수 있어 방어적으로 에러 체크
        self._raise_if_error(data)

        if cache_key is not None:
            self._response_cache.set(cache_key, data)
        return data

    @staticmethod
    def _raise_if_error(data: dict) -> None:
        """FISIS 응답에서 에러를 발견하면 RuntimeError 발생.

        - result 딕셔너리 안의 err_msg / errMsg
        - 최상위 err_msg / errMsg
        - '정상'/'성공'/'success'/빈 문자열은 정상으로 간주
        """

        def _check(obj: dict) -> None:
            if not isinstance(obj, dict):
                return
            msg = obj.get("err_msg") or obj.get("errMsg") or ""
            code = obj.get("err_cd") or obj.get("errCd") or ""
            if msg and msg not in ("정상", "성공", "success"):
                suffix = f" [{code}]" if code else ""
                raise RuntimeError(f"FISIS API 오류{suffix}: {msg}")

        _check(data)
        result = data.get("result") if isinstance(data, dict) else None
        if isinstance(result, dict):
            _check(result)

    async def list_divisions(
        self,
        lrg_div: str = "",
    ) -> list[dict[str, str]]:
        """FISIS API에서 사용 가능한 업권(대분류/소분류) 목록을 동적으로 조회.

        companySearch 응답에서 고유한 (대분류, 소분류) 조합을 추출합니다.
        API가 반환하는 실제 코드를 기반으로 하므로 항상 최신 상태를 반영합니다.

        Args:
            lrg_div: 특정 대분류만 조회 (A/B/C/D). 비워두면 전체.

        Returns:
            [{"lrg_div": "A", "lrg_div_nm": "은행", "sml_div": "...", "sml_div_nm": "..."}, ...]
        """
        data = await self._get("companySearch.json", {"partDiv": lrg_div} if lrg_div else {})

        # 응답에서 회사 리스트 추출
        result = data.get("result", data) if isinstance(data, dict) else data
        items = []
        if isinstance(result, dict):
            items = result.get("list", result.get("data", []))
        elif isinstance(result, list):
            items = result
        if not isinstance(items, list):
            items = []

        # 고유한 업권 조합 추출
        seen: set[tuple[str, str]] = set()
        divisions: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            lrg = item.get("part_div", item.get("partDiv", ""))
            lrg_nm = item.get("part_div_nm", item.get("partDivNm", ""))
            sml = item.get("sml_div", item.get("smlDiv", ""))
            sml_nm = item.get("sml_div_nm", item.get("smlDivNm", ""))
            key = (lrg, sml)
            if key not in seen:
                seen.add(key)
                entry: dict[str, str] = {}
                if lrg:
                    entry["lrg_div"] = lrg
                if lrg_nm:
                    entry["lrg_div_nm"] = lrg_nm
                if sml:
                    entry["sml_div"] = sml
                if sml_nm:
                    entry["sml_div_nm"] = sml_nm
                divisions.append(entry)

        # 대분류 → 소분류 순 정렬
        divisions.sort(key=lambda d: (d.get("lrg_div", ""), d.get("sml_div", "")))
        return divisions

    async def list_statistics(
        self,
        lrg_div: str = "",
        sml_div: str = "",
    ) -> dict:
        params: dict = {}
        if lrg_div:
            params["lrgDiv"] = lrg_div
        if sml_div:
            params["smlDiv"] = sml_div
        return await self._get("statisticsListSearch.json", params)

    async def get_statistics(
        self,
        stat_cd: str,
        strt_yymm: str,
        end_yymm: str,
        finance_cd: str = "",
        lrg_div: str = "",
        sml_div: str = "",
        term: str = "Q",
    ) -> dict:
        """FISIS 통계 데이터 조회.

        Args:
            stat_cd: 통계 코드 (list_no 필드값, 예: SA053)
            strt_yymm: 시작 연월 YYYYMM (예: 202312)
            end_yymm: 종료 연월 YYYYMM (예: 202412)
            finance_cd: 금융회사 코드 (예: 0010927)
            lrg_div: 대분류 코드 (A=은행, B=비은행, C=보험, D=금융투자)
            sml_div: 소분류 코드
            term: 주기 — Q(분기, 기본값) 또는 Y(연간)
        """
        params: dict = {
            "listNo": stat_cd,
            "startBaseMm": strt_yymm,
            "endBaseMm": end_yymm,
            "term": term,
        }
        if finance_cd:
            params["financeCd"] = finance_cd
        if lrg_div:
            params["lrgDiv"] = lrg_div
        if sml_div:
            params["smlDiv"] = sml_div
        return await self._get("statisticsInfoSearch.json", params)

    async def list_companies(
        self,
        lrg_div: str = "",
        sml_div: str = "",
        finance_cd: str = "",
    ) -> dict:
        """금융회사 목록 조회.

        Args:
            lrg_div: 대분류 코드 → partDiv로 전달 (A=은행, B=비은행, C=보험, D=금융투자)
            sml_div: 소분류 코드
            finance_cd: 특정 금융회사 코드
        """
        params: dict = {}
        if lrg_div:
            params["partDiv"] = lrg_div  # companySearch.json은 partDiv 파라미터 사용
        if sml_div:
            params["smlDiv"] = sml_div
        if finance_cd:
            params["financeCd"] = finance_cd
        return await self._get("companySearch.json", params)
