"""DART(전자공시시스템) OpenAPI 클라이언트

API 문서: https://opendart.fss.or.kr/guide/main.do
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

import httpx

BASE_URL = "https://opendart.fss.or.kr/api"

# 보고서 코드
REPORT_CODES = {
    "사업보고서": "11011",
    "반기보고서": "11012",
    "1분기보고서": "11013",
    "3분기보고서": "11014",
}

# 법인구분
CORP_CLASS = {
    "유가증권": "Y",
    "코스닥": "K",
    "코넥스": "N",
    "기타": "E",
}


class DartClient:
    """DART OpenAPI 비동기 클라이언트"""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._corp_codes: list[dict] | None = None

    # ── 내부 헬퍼 ──────────────────────────────────────────────

    async def _get(self, endpoint: str, params: dict | None = None) -> dict:
        params = params or {}
        params["crtfc_key"] = self.api_key
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{BASE_URL}/{endpoint}", params=params)
            resp.raise_for_status()
            data = resp.json()
        # DART 오류 처리
        status = data.get("status", "000")
        if status not in ("000", None):
            msg = data.get("message", "알 수 없는 오류")
            raise RuntimeError(f"DART API 오류 [{status}]: {msg}")
        return data

    # ── 기업코드 검색 ──────────────────────────────────────────

    async def load_corp_codes(self) -> list[dict]:
        """기업코드 목록(corpCode.xml)을 다운로드하고 캐싱합니다."""
        if self._corp_codes is not None:
            return self._corp_codes

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"{BASE_URL}/corpCode.xml",
                params={"crtfc_key": self.api_key},
            )
            resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_name = zf.namelist()[0]
            xml_data = zf.read(xml_name)

        root = ET.fromstring(xml_data)
        corps = []
        for item in root.iter("list"):
            corps.append(
                {
                    "corp_code": item.findtext("corp_code", ""),
                    "corp_name": item.findtext("corp_name", ""),
                    "stock_code": item.findtext("stock_code", ""),
                    "modify_date": item.findtext("modify_date", ""),
                }
            )
        self._corp_codes = corps
        return corps

    async def search_company(self, name: str, limit: int = 20) -> list[dict]:
        """회사명으로 기업코드를 검색합니다.

        상장기업(stock_code 있는 기업)이 우선 표시됩니다.
        """
        corps = await self.load_corp_codes()
        matches = [c for c in corps if name in c["corp_name"]]
        # 상장기업 우선 정렬
        matches.sort(key=lambda c: (c["stock_code"] == "", c["corp_name"]))
        return matches[:limit]

    # ── 기업개황 ───────────────────────────────────────────────

    async def get_company_overview(self, corp_code: str) -> dict:
        """기업개황을 조회합니다.

        반환: 회사명, 대표자, 업종, 주소, 설립일, 상장일, 홈페이지 등
        """
        return await self._get("company.json", {"corp_code": corp_code})

    # ── 공시 검색 ──────────────────────────────────────────────

    async def search_disclosures(
        self,
        corp_code: str = "",
        bgn_de: str = "",
        end_de: str = "",
        corp_cls: str = "",
        pblntf_ty: str = "",
        page_no: int = 1,
        page_count: int = 10,
    ) -> dict:
        """공시 목록을 검색합니다.

        Args:
            corp_code: 기업코드 (8자리)
            bgn_de: 검색 시작일 (YYYYMMDD)
            end_de: 검색 종료일 (YYYYMMDD)
            corp_cls: 법인구분 (Y=유가, K=코스닥, N=코넥스, E=기타)
            pblntf_ty: 공시유형 (A=정기공시, B=주요사항, C=발행공시 등)
            page_no: 페이지 번호
            page_count: 페이지당 건수 (최대 100)
        """
        params: dict = {"page_no": str(page_no), "page_count": str(page_count)}
        if corp_code:
            params["corp_code"] = corp_code
        if bgn_de:
            params["bgn_de"] = bgn_de
        if end_de:
            params["end_de"] = end_de
        if corp_cls:
            params["corp_cls"] = corp_cls
        if pblntf_ty:
            params["pblntf_ty"] = pblntf_ty
        return await self._get("list.json", params)

    # ── 재무제표 ───────────────────────────────────────────────

    async def get_financial_statements(
        self,
        corp_code: str,
        bsns_year: str,
        reprt_code: str = "11011",
    ) -> dict:
        """단일회사 주요계정을 조회합니다.

        주요계정: 자산총계, 부채총계, 자본총계, 매출액, 영업이익, 당기순이익 등

        Args:
            corp_code: 기업코드 (8자리)
            bsns_year: 사업연도 (YYYY)
            reprt_code: 보고서코드 (11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기)
        """
        return await self._get(
            "fnlttSinglAcnt.json",
            {
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
            },
        )

    async def get_full_financial_statements(
        self,
        corp_code: str,
        bsns_year: str,
        reprt_code: str = "11011",
        fs_div: str = "CFS",
    ) -> dict:
        """단일회사 전체 재무제표를 조회합니다.

        Args:
            corp_code: 기업코드 (8자리)
            bsns_year: 사업연도 (YYYY)
            reprt_code: 보고서코드
            fs_div: 재무제표구분 (CFS=연결, OFS=개별)
        """
        return await self._get(
            "fnlttSinglAcntAll.json",
            {
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
                "fs_div": fs_div,
            },
        )

    async def get_multi_company_financials(
        self,
        corp_codes: list[str],
        bsns_year: str,
        reprt_code: str = "11011",
    ) -> dict:
        """다중회사 주요계정을 비교 조회합니다.

        Args:
            corp_codes: 기업코드 리스트 (최대 20개)
            bsns_year: 사업연도 (YYYY)
            reprt_code: 보고서코드
        """
        return await self._get(
            "fnlttMultiAcnt.json",
            {
                "corp_code": ",".join(corp_codes[:20]),
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
            },
        )
