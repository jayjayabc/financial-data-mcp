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

# ── FISIS 업권(권역) 코드 ─────────────────────────────────────
#
# FISIS API는 22개의 개별 업권 코드를 지원합니다.
# - statisticsListSearch.json: lrgDiv 파라미터로 22개 코드 모두 사용
# - companySearch.json: partDiv 파라미터로 17개 코드 사용
# - statisticsInfoSearch.json: lrgDiv 파라미터
#
# 기존 4-그룹 분류(A=은행, B=비은행, C=보험, D=금융투자)는 상위 그룹핑이며,
# 실제 API는 아래 22개 개별 코드를 직접 받습니다.

# 전체 22개 업권 코드 (lrgDiv 기준, 통계코드 접두사 = S + 코드)
DIVISIONS: dict[str, str] = {
    # 은행
    "A": "국내은행",
    "J": "외국은행국내지점",
    # 비은행 (신탁·저축·상호금융)
    "B": "공통(신탁)",
    "R": "종합금융회사",
    "E": "상호저축은행",
    "O": "신용협동조합",
    "Q": "새마을금고",
    "P": "농업협동조합",
    "S": "수산업협동조합",
    "M": "산림조합",
    # 보험
    "H": "생명보험",
    "I": "손해보험",
    # 여신전문금융
    "C": "신용카드",
    "K": "시설대여(리스)",
    "T": "할부금융",
    "N": "신기술사업금융",
    # 금융투자
    "F": "증권",
    "W": "선물",
    "G": "자산운용",
    "X": "투자자문",
    "D": "부동산신탁",
    # 기타
    "L": "금융지주회사",
}

# companySearch.json의 partDiv에 사용 가능한 17개 코드
COMPANY_DIVISIONS: set[str] = {
    "A", "F", "D", "N", "P", "B", "J", "W", "C", "E",
    "S", "R", "H", "G", "K", "O", "M",
}

# 하위 호환용 — 기존 4-그룹 분류 (일부 코드에서 참조)
LARGE_DIVISIONS = {
    "은행": "A",
    "비은행": "B",
    "보험": "C",
    "금융투자": "D",
}

# 4-그룹 → 개별 업권 매핑 (상위 그룹이 어떤 개별 코드를 포함하는지)
DIVISION_GROUPS: dict[str, list[str]] = {
    "은행":     ["A", "J"],
    "비은행":   ["B", "R", "E", "O", "Q", "P", "S", "M"],
    "보험":     ["H", "I"],
    "여신전문": ["C", "K", "T", "N"],
    "금융투자": ["F", "W", "G", "X", "D"],
    "기타":     ["L"],
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
        """FISIS API에서 사용 가능한 업권 목록을 동적으로 조회.

        두 가지 소스를 결합합니다:
        1. 정적 매핑(DIVISIONS): 22개 전체 업권 코드 (항상 포함)
        2. companySearch 응답: 실제 API에서 세부 소분류(sml_div) 추출

        Args:
            lrg_div: 특정 업권만 조회 (예: A, C, H 등). 비워두면 전체.

        Returns:
            [{"div_cd": "A", "div_nm": "국내은행", "sml_div": "...", "sml_div_nm": "..."}, ...]
        """
        divisions: list[dict[str, str]] = []

        # 1) 정적 매핑에서 전체 업권 코드 포함
        if lrg_div:
            if lrg_div in DIVISIONS:
                divisions.append({"div_cd": lrg_div, "div_nm": DIVISIONS[lrg_div]})
        else:
            for code, name in DIVISIONS.items():
                divisions.append({"div_cd": code, "div_nm": name})

        # 2) companySearch에서 세부 소분류 동적 추출 시도
        try:
            codes_to_query = [lrg_div] if lrg_div else sorted(COMPANY_DIVISIONS)
            for code in codes_to_query:
                if code not in COMPANY_DIVISIONS:
                    continue
                data = await self._get("companySearch.json", {"partDiv": code})
                result = data.get("result", data) if isinstance(data, dict) else data
                items: list = []
                if isinstance(result, dict):
                    items = result.get("list", result.get("data", []))
                elif isinstance(result, list):
                    items = result
                if not isinstance(items, list):
                    continue

                seen: set[str] = set()
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    sml = item.get("sml_div", item.get("smlDiv", ""))
                    sml_nm = item.get("sml_div_nm", item.get("smlDivNm", ""))
                    if sml and sml not in seen:
                        seen.add(sml)
                        entry: dict[str, str] = {"div_cd": code, "div_nm": DIVISIONS.get(code, "")}
                        entry["sml_div"] = sml
                        if sml_nm:
                            entry["sml_div_nm"] = sml_nm
                        divisions.append(entry)
        except Exception:
            # API 호출 실패 시 정적 매핑만 반환
            logger.debug("companySearch 동적 조회 실패, 정적 매핑만 반환")

        # 업권 코드 → 소분류 순 정렬
        divisions.sort(key=lambda d: (d.get("div_cd", ""), d.get("sml_div", "")))
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
