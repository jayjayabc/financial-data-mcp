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


class FisisClient:
    """FISIS OpenAPI 비동기 클라이언트. 싱글톤으로 재사용되어야 함."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": "financial-data-mcp/0.1"},
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30.0,
            ),
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
        except httpx.HTTPStatusError as e:
            # 응답 크기 초과(413) 또는 페이로드 한도 초과 시 분할 조회 안내
            if e.response.status_code in (413, 414, 431):
                raise RuntimeError(
                    f"FISIS API 응답 크기 초과 [{e.response.status_code}]: "
                    "연도 범위를 줄이거나 기관을 분리하여 재시도하세요. "
                    f"원본 오류: {e}"
                ) from e
            raise translate_http_error("FISIS", e) from e
        except (httpx.TransportError, httpx.TimeoutException) as e:
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
                # 응답 크기 초과 관련 오류 감지 시 분할 조회 안내
                size_keywords = ("크기", "용량", "초과", "limit", "size", "large", "too big")
                if any(kw in msg.lower() for kw in size_keywords):
                    raise RuntimeError(
                        f"FISIS API 응답 크기 초과{suffix}: {msg}. "
                        "연도 범위를 줄이거나 기관을 분리하여 재시도하세요."
                    )
                raise RuntimeError(f"FISIS API 오류{suffix}: {msg}")

        _check(data)
        result = data.get("result") if isinstance(data, dict) else None
        if isinstance(result, dict):
            _check(result)

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
