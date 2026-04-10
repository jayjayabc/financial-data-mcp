"""FISIS(금융통계정보시스템) OpenAPI 클라이언트

API 문서: https://fisis.fss.or.kr/fisis/openapi/apiInfo.do
"""

from __future__ import annotations

import httpx

BASE_URL = "https://fisis.fss.or.kr/openapi"

# 대분류 코드
LARGE_DIVISIONS = {
    "은행": "01",
    "비은행": "02",
    "보험": "03",
    "금융투자": "04",
}


class FisisClient:
    """FISIS OpenAPI 비동기 클라이언트"""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def _get(self, endpoint: str, params: dict | None = None) -> dict:
        params = params or {}
        params["auth"] = self.api_key
        params["lang"] = "kr"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{BASE_URL}/{endpoint}", params=params)
            resp.raise_for_status()
            data = resp.json()
        # FISIS 오류 처리
        result = data.get("result", {})
        err_msg = result.get("err_msg", "")
        if err_msg and err_msg != "정상":
            raise RuntimeError(f"FISIS API 오류: {err_msg}")
        return data

    async def list_statistics(
        self,
        lrg_div: str = "",
        sml_div: str = "",
    ) -> dict:
        """통계목록을 조회합니다.

        Args:
            lrg_div: 대분류 코드 (01=은행, 02=비은행, 03=보험, 04=금융투자)
            sml_div: 소분류 코드
        """
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
        """통계데이터를 조회합니다.

        Args:
            stat_cd: 통계코드 (예: "010101" = 은행 요약재무제표)
            strt_yymm: 검색 시작월 (YYYYMM)
            end_yymm: 검색 종료월 (YYYYMM)
            finance_cd: 금융회사코드 (비워두면 전체)
            lrg_div: 대분류 코드
            sml_div: 소분류 코드
        """
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
        """금융회사 목록을 조회합니다.

        Args:
            lrg_div: 대분류 코드
            sml_div: 소분류 코드
            finance_cd: 금융회사코드 (특정 회사 조회 시)
        """
        params: dict = {}
        if lrg_div:
            params["lrgDiv"] = lrg_div
        if sml_div:
            params["smlDiv"] = sml_div
        if finance_cd:
            params["financeCd"] = finance_cd
        return await self._get("companyListSearch.json", params)
