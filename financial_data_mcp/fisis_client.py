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

# 대분류 코드 (FISIS API 응답에서 실제 코드를 확인 후 업데이트 필요)
# fisis_list_statistics() 를 파라미터 없이 호출하면 전체 목록과 코드를 확인할 수 있음
LARGE_DIVISIONS: dict[str, str] = {
    # 예시: "은행": "A" 또는 "01" 등 — API 응답에서 확인
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
    ) -> dict:
        params: dict = {
            "statCd": stat_cd,
            "strtYymm": strt_yymm,
            "endYymm": end_yymm,
        }
        if finance_cd:
            params["financeCd"] = finance_cd
        if lrg_div:
            params["lrgDiv"] = lrg_div
        if sml_div:
            params["smlDiv"] = sml_div
        return await self._get("statisticsDataSearch.json", params)

    async def list_companies(
        self,
        lrg_div: str = "",
        sml_div: str = "",
        finance_cd: str = "",
    ) -> dict:
        params: dict = {}
        if lrg_div:
            params["lrgDiv"] = lrg_div
        if sml_div:
            params["smlDiv"] = sml_div
        if finance_cd:
            params["financeCd"] = finance_cd
        return await self._get("companyListSearch.json", params)
