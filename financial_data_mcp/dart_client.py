"""DART(전자공시시스템) OpenAPI 클라이언트.

API 문서: https://opendart.fss.or.kr/guide/main.do

전체 81개 엔드포인트 지원:
- DS001: 공시정보 (공시검색, 기업개황, 공시서류원본)
- DS002: 사업보고서 주요정보 28개 (배당, 임원, 직원, 최대주주, 감사 등)
- DS003: 재무정보 (주요계정, 전체재무제표, XBRL)
- DS004: 지분공시 (대량보유, 임원·주요주주 소유)
- DS005: 주요사항보고서 36개 (증자, 합병, 분할, 사채, 소송 등)
- DS006: 증권신고서 6개 (지분증권, 채무증권, 합병, 분할 등)

주요 최적화:
- 싱글톤 재사용 전제: 인스턴스 내부에 httpx.AsyncClient, 응답 캐시, 기업코드 캐시 보관
- 기업코드: 메모리 → 디스크(30일) → 네트워크 순 조회. asyncio.Lock 으로 동시 다운로드 방지
- 응답 캐시: 재무제표 등 변동 적은 조회는 1시간 TTL 메모리 캐시
- 검색 캐시: 동일 쿼리 반복 시 선형 스캔 재실행 방지
- 재시도: 지수 백오프 (3회, 5xx/429/transport 에러)
- DART 에러코드 '013'(조회 결과 없음)을 예외가 아닌 정상 응답으로 처리
"""

from __future__ import annotations

import asyncio
import io
import logging
import xml.etree.ElementTree as ET
import zipfile

import httpx

from ._cache import TTLCache, load_disk_cache, save_disk_cache
from ._http import mask_params, translate_http_error, with_retry
from ._quota import QuotaTracker

logger = logging.getLogger("financial_data_mcp.dart")

BASE_URL = "https://opendart.fss.or.kr/api"
CORP_CODE_CACHE_NAME = "dart_corp_codes"
CORP_CODE_TTL_SECONDS = 30 * 86400  # 30일
RESPONSE_TTL_SECONDS = 3600  # 1시간
SEARCH_CACHE_MAX = 256

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

# 재무제표 구분 (sj_div)
SJ_DIV = {
    "BS": "재무상태표",
    "IS": "손익계산서",
    "CIS": "포괄손익계산서",
    "CF": "현금흐름표",
    "SCE": "자본변동표",
}

# ── DS002: 사업보고서 주요정보 (28개) ──────────────────────────
# 공통 파라미터: corp_code, bsns_year, reprt_code
BUSINESS_REPORT_TYPES: dict[str, dict[str, str]] = {
    "증자감자": {"path": "irdsSttus.json", "desc": "증자(감자) 현황"},
    "주식총수": {"path": "stockTotqySttus.json", "desc": "주식의 총수 현황"},
    "배당": {"path": "alotMatter.json", "desc": "배당에 관한 사항"},
    "자기주식": {"path": "tesstkAcqsDspsSttus.json", "desc": "자기주식 취득 및 처분 현황"},
    "최대주주": {"path": "hyslrSttus.json", "desc": "최대주주 현황"},
    "최대주주변동": {"path": "hyslrChgSttus.json", "desc": "최대주주 변동현황"},
    "소액주주": {"path": "mrhlSttus.json", "desc": "소액주주 현황"},
    "임원현황": {"path": "exctvSttus.json", "desc": "임원 현황"},
    "직원현황": {"path": "empSttus.json", "desc": "직원 현황 (인원수·평균근속·평균급여)"},
    "이사감사개인별보수": {"path": "hmvAuditIndvdlBySttus.json", "desc": "이사·감사의 개인별 보수현황"},
    "이사감사전체보수": {"path": "hmvAuditAllSttus.json", "desc": "이사·감사 전체의 보수현황"},
    "개인별보수5억이상": {"path": "indvdlByPay.json", "desc": "개인별 보수지급 금액 (5억이상 상위5인)"},
    "타법인출자": {"path": "otrCprInvstmntSttus.json", "desc": "타법인 출자현황"},
    "사외이사변동": {"path": "outcmpnyDrctrNdChangeSttus.json", "desc": "사외이사 및 그 변동현황"},
    "신종자본증권미상환": {"path": "newCaplScritsNrdmpBlce.json", "desc": "신종자본증권 미상환 잔액"},
    "조건부자본증권미상환": {"path": "cndlCaplScritsNrdmpBlce.json", "desc": "조건부자본증권 미상환 잔액"},
    "회사채미상환": {"path": "cprndNrdmpBlce.json", "desc": "회사채 미상환 잔액"},
    "단기사채미상환": {"path": "srtpdPsndbtNrdmpBlce.json", "desc": "단기사채 미상환 잔액"},
    "기업어음미상환": {"path": "entrprsBilScritsNrdmpBlce.json", "desc": "기업어음증권 미상환 잔액"},
    "채무증권발행": {"path": "detScritsIsuAcmslt.json", "desc": "채무증권 발행실적"},
    "사모자금사용내역": {"path": "prvsrpCptalUseDtls.json", "desc": "사모자금의 사용내역"},
    "공모자금사용내역": {"path": "pssrpCptalUseDtls.json", "desc": "공모자금의 사용내역"},
    "이사감사보수승인금액": {"path": "drctrAdtAllMendngSttusGmtsckConfmAmount.json", "desc": "이사·감사 전체 보수현황 (주총 승인금액)"},
    "이사감사보수유형별": {"path": "drctrAdtAllMendngSttusMendngPymntamtTyCl.json", "desc": "이사·감사 전체 보수현황 (유형별)"},
    "미등기임원보수": {"path": "unrstExctvMendngSttus.json", "desc": "미등기임원 보수현황"},
    "감사인명칭의견": {"path": "accnutAdtorNmNdAdtOpinion.json", "desc": "회계감사인의 명칭 및 감사의견"},
    "감사용역체결": {"path": "adtServcCnclsSttus.json", "desc": "감사용역체결현황"},
    "비감사용역계약": {"path": "accnutAdtorNonAdtServcCnclsSttus.json", "desc": "회계감사인과의 비감사용역 계약체결 현황"},
}

# ── DS004: 지분공시 (2개) ──────────────────────────────────────
# 파라미터: corp_code
EQUITY_DISCLOSURE_TYPES: dict[str, dict[str, str]] = {
    "대량보유": {"path": "majorstock.json", "desc": "대량보유 상황보고 (5% 이상 지분변동)"},
    "임원주요주주": {"path": "elestock.json", "desc": "임원·주요주주 소유보고"},
}

# ── DS005: 주요사항보고서 (36개) ────────────────────────────────
# 파라미터: corp_code
MAJOR_EVENT_TYPES: dict[str, dict[str, str]] = {
    "부도발생": {"path": "dfOcr.json", "desc": "부도발생"},
    "영업정지": {"path": "bsnSp.json", "desc": "영업정지"},
    "회생절차개시": {"path": "ctrcvsBgrq.json", "desc": "회생절차 개시신청"},
    "해산사유발생": {"path": "dsRsOcr.json", "desc": "해산사유 발생"},
    "유상증자결정": {"path": "piicDecsn.json", "desc": "유상증자 결정"},
    "무상증자결정": {"path": "fricDecsn.json", "desc": "무상증자 결정"},
    "유무상증자결정": {"path": "pifricDecsn.json", "desc": "유무상증자 결정"},
    "감자결정": {"path": "crDecsn.json", "desc": "감자 결정"},
    "채권은행관리개시": {"path": "bnkMngtPcbg.json", "desc": "채권은행 등의 관리절차 개시"},
    "채권은행관리중단": {"path": "bnkMngtPcsp.json", "desc": "채권은행 등의 관리절차 중단"},
    "소송제기": {"path": "lwstLg.json", "desc": "소송 등의 제기"},
    "해외상장결정": {"path": "ovLstDecsn.json", "desc": "해외 증권시장 상장 결정"},
    "해외상장폐지결정": {"path": "ovDlstDecsn.json", "desc": "해외 증권시장 상장폐지 결정"},
    "해외상장": {"path": "ovLst.json", "desc": "해외 증권시장 상장"},
    "해외상장폐지": {"path": "ovDlst.json", "desc": "해외 증권시장 상장폐지"},
    "전환사채발행결정": {"path": "cvbdIsDecsn.json", "desc": "전환사채권 발행결정"},
    "신주인수권부사채발행결정": {"path": "bdwtIsDecsn.json", "desc": "신주인수권부사채권 발행결정"},
    "교환사채발행결정": {"path": "exbdIsDecsn.json", "desc": "교환사채권 발행결정"},
    "코코본드발행결정": {"path": "wdCocobdIsDecsn.json", "desc": "조건부자본증권(상각형) 발행결정"},
    "자산양수도풋백옵션": {"path": "astInhtrfEtcPtbkOpt.json", "desc": "자산양수도(풋백옵션)"},
    "타법인주식양도결정": {"path": "otcprStkInvscrTrfDecsn.json", "desc": "타법인 주식 및 출자증권 양도결정"},
    "타법인주식양수결정": {"path": "otcprStkInvscrInhDecsn.json", "desc": "타법인 주식 및 출자증권 양수결정"},
    "유형자산양도결정": {"path": "tgastTrfDecsn.json", "desc": "유형자산 양도 결정"},
    "유형자산양수결정": {"path": "tgastInhDecsn.json", "desc": "유형자산 양수 결정"},
    "영업양도결정": {"path": "bsnTrfDecsn.json", "desc": "영업양도 결정"},
    "영업양수결정": {"path": "bsnInhDecsn.json", "desc": "영업양수 결정"},
    "자기주식취득결정": {"path": "tsstkAqDecsn.json", "desc": "자기주식 취득 결정"},
    "자기주식처분결정": {"path": "tsstkDpDecsn.json", "desc": "자기주식 처분 결정"},
    "자기주식신탁계약체결": {"path": "tsstkAqTrctrCnsDecsn.json", "desc": "자기주식취득 신탁계약 체결 결정"},
    "자기주식신탁계약해지": {"path": "tsstkAqTrctrCcDecsn.json", "desc": "자기주식취득 신탁계약 해지 결정"},
    "주식교환이전결정": {"path": "stkExtrDecsn.json", "desc": "주식교환·이전 결정"},
    "합병결정": {"path": "cmpMgDecsn.json", "desc": "회사합병 결정"},
    "분할결정": {"path": "cmpDvDecsn.json", "desc": "회사분할 결정"},
    "분할합병결정": {"path": "cmpDvmgDecsn.json", "desc": "회사분할합병 결정"},
    "주권관련사채양수결정": {"path": "stkrtbdInhDecsn.json", "desc": "주권관련 사채권 양수 결정"},
    "주권관련사채양도결정": {"path": "stkrtbdTrfDecsn.json", "desc": "주권관련 사채권 양도 결정"},
}

# ── DS006: 증권신고서 (6개) ────────────────────────────────────
# 파라미터: corp_code, bgn_de, end_de
SECURITIES_REPORT_TYPES: dict[str, dict[str, str]] = {
    "지분증권": {"path": "estkRs.json", "desc": "지분증권"},
    "채무증권": {"path": "bdRs.json", "desc": "채무증권"},
    "증권예탁증권": {"path": "stkdpRs.json", "desc": "증권예탁증권"},
    "합병": {"path": "mgRs.json", "desc": "합병 등"},
    "주식교환이전": {"path": "extrRs.json", "desc": "주식의 포괄적 교환·이전"},
    "분할": {"path": "dvRs.json", "desc": "분할"},
}


class DartClient:
    """DART OpenAPI 비동기 클라이언트.

    싱글톤으로 재사용되어야 함 (server.py 의 lru_cache 참고).
    """

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._corp_codes: list[dict] | None = None
        self._corp_codes_lock = asyncio.Lock()
        self._search_cache: dict[str, list[dict]] = {}
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": "financial-data-mcp/0.1"},
        )
        self._response_cache = TTLCache(
            ttl_seconds=RESPONSE_TTL_SECONDS, max_size=256
        )
        self.quota = QuotaTracker()

    async def aclose(self) -> None:
        """내부 httpx 클라이언트 종료."""
        await self._client.aclose()

    # ── 내부 HTTP 헬퍼 ─────────────────────────────────────────

    async def _raw_get(self, path: str, params: dict, *, timeout: float | None = None) -> httpx.Response:
        """재시도 + 상태 검사를 포함한 원시 GET. 성공 시 quota 카운터 증가."""
        async def _do() -> httpx.Response:
            kwargs = {"params": params}
            if timeout is not None:
                kwargs["timeout"] = timeout
            resp = await self._client.get(path, **kwargs)
            resp.raise_for_status()
            return resp

        try:
            resp = await with_retry(_do, label=f"DART {path}")
        except (httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException) as e:
            raise translate_http_error("DART", e) from e

        # 성공한 네트워크 호출만 quota 소비 (재시도 포함 1회로 카운트)
        self.quota.increment()
        return resp

    async def _get(
        self,
        endpoint: str,
        params: dict | None = None,
        use_cache: bool = False,
    ) -> dict:
        params = dict(params or {})
        params["crtfc_key"] = self.api_key

        cache_key: tuple | None = None
        if use_cache:
            cache_key = (endpoint, tuple(sorted(params.items())))
            cached = self._response_cache.get(cache_key)
            if cached is not None:
                logger.debug("cache hit: %s %s", endpoint, mask_params(params))
                return cached

        logger.debug("api call: %s %s", endpoint, mask_params(params))
        resp = await self._raw_get(f"/{endpoint}", params)
        data = resp.json()

        status = data.get("status", "000")
        if status == "013":
            # 조회 결과 없음 - 정상 응답으로 처리
            # 원래 응답 구조를 보존하고 message만 명시
            data["message"] = data.get("message", "조회된 데이터가 없습니다")
        elif status not in ("000", None):
            msg = data.get("message", "알 수 없는 오류")
            raise RuntimeError(f"DART API 오류 [{status}]: {msg}")

        if cache_key is not None:
            self._response_cache.set(cache_key, data)
        return data

    # ── 기업코드 ───────────────────────────────────────────────

    async def load_corp_codes(self) -> list[dict]:
        """기업코드 목록 로드. 메모리 → 디스크(30일) → 네트워크 순.

        asyncio.Lock 으로 동시 호출 시 중복 다운로드 방지.
        """
        if self._corp_codes is not None:
            return self._corp_codes

        async with self._corp_codes_lock:
            # 락 획득 후 재확인 (다른 코루틴이 이미 로드했을 수 있음)
            if self._corp_codes is not None:
                return self._corp_codes

            # 디스크 캐시
            cached = load_disk_cache(CORP_CODE_CACHE_NAME, CORP_CODE_TTL_SECONDS)
            if isinstance(cached, list) and cached:
                logger.info("corp_codes: 디스크 캐시 사용 (%d건)", len(cached))
                self._corp_codes = cached
                return cached

            # 네트워크 다운로드 (약 8MB ZIP)
            logger.info("corp_codes: 네트워크 다운로드 시작")
            resp = await self._raw_get(
                "/corpCode.xml",
                {"crtfc_key": self.api_key},
                timeout=60.0,
            )

            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                xml_name = zf.namelist()[0]
                xml_data = zf.read(xml_name)

            root = ET.fromstring(xml_data)
            corps: list[dict] = []
            for item in root.iter("list"):
                corps.append(
                    {
                        "corp_code": item.findtext("corp_code", ""),
                        "corp_name": item.findtext("corp_name", ""),
                        "stock_code": (item.findtext("stock_code", "") or "").strip(),
                        "modify_date": item.findtext("modify_date", ""),
                    }
                )

            logger.info("corp_codes: %d건 로드 완료", len(corps))
            self._corp_codes = corps
            save_disk_cache(CORP_CODE_CACHE_NAME, corps)
            return corps

    async def search_company(self, name: str, limit: int = 20) -> list[dict]:
        """회사명 부분일치 검색. 상장기업 우선 정렬. 검색 결과는 메모리 캐시."""
        cache_key = f"{name}::{limit}"
        cached = self._search_cache.get(cache_key)
        if cached is not None:
            return cached

        corps = await self.load_corp_codes()
        matches = [c for c in corps if name in c["corp_name"]]
        matches.sort(key=lambda c: (c["stock_code"] == "", c["corp_name"]))
        results = matches[:limit]

        # 캐시 크기 제한 (검색어 다양성 방어)
        if len(self._search_cache) >= SEARCH_CACHE_MAX:
            # 가장 오래된(삽입 순) 절반 제거 (OrderedDict 아니지만 dict는 insertion-ordered)
            keys_to_remove = list(self._search_cache.keys())[: SEARCH_CACHE_MAX // 2]
            for k in keys_to_remove:
                self._search_cache.pop(k, None)
        self._search_cache[cache_key] = results
        return results

    # ── 기업개황 ───────────────────────────────────────────────

    async def get_company_overview(self, corp_code: str) -> dict:
        return await self._get(
            "company.json",
            {"corp_code": corp_code},
            use_cache=True,
        )

    # ── 공시 ───────────────────────────────────────────────────

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
        # 공시는 최신성 중요 - 캐시하지 않음
        return await self._get("list.json", params)

    # ── 재무제표 ───────────────────────────────────────────────

    async def get_financial_statements(
        self,
        corp_code: str,
        bsns_year: str,
        reprt_code: str = "11011",
    ) -> dict:
        return await self._get(
            "fnlttSinglAcnt.json",
            {
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
            },
            use_cache=True,
        )

    async def get_full_financial_statements(
        self,
        corp_code: str,
        bsns_year: str,
        reprt_code: str = "11011",
        fs_div: str = "CFS",
    ) -> dict:
        return await self._get(
            "fnlttSinglAcntAll.json",
            {
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
                "fs_div": fs_div,
            },
            use_cache=True,
        )

    async def get_multi_company_financials(
        self,
        corp_codes: list[str],
        bsns_year: str,
        reprt_code: str = "11011",
    ) -> dict:
        return await self._get(
            "fnlttMultiAcnt.json",
            {
                "corp_code": ",".join(corp_codes),
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
            },
            use_cache=True,
        )

    # ── DS002: 사업보고서 주요정보 ─────────────────────────────

    async def get_business_report(
        self,
        corp_code: str,
        bsns_year: str,
        reprt_code: str,
        info_type: str,
    ) -> dict:
        """사업보고서 주요정보 조회. info_type으로 28개 항목 중 선택."""
        meta = BUSINESS_REPORT_TYPES.get(info_type)
        if not meta:
            raise ValueError(
                f"지원하지 않는 info_type: '{info_type}'. "
                f"사용 가능: {', '.join(sorted(BUSINESS_REPORT_TYPES))}"
            )
        return await self._get(
            meta["path"],
            {"corp_code": corp_code, "bsns_year": bsns_year, "reprt_code": reprt_code},
            use_cache=True,
        )

    # ── DS004: 지분공시 ────────────────────────────────────────

    async def get_equity_disclosure(
        self,
        corp_code: str,
        report_type: str,
    ) -> dict:
        """지분공시 조회. report_type: 대량보유 / 임원주요주주."""
        meta = EQUITY_DISCLOSURE_TYPES.get(report_type)
        if not meta:
            raise ValueError(
                f"지원하지 않는 report_type: '{report_type}'. "
                f"사용 가능: {', '.join(sorted(EQUITY_DISCLOSURE_TYPES))}"
            )
        return await self._get(
            meta["path"],
            {"corp_code": corp_code},
            use_cache=True,
        )

    # ── DS005: 주요사항보고서 ──────────────────────────────────

    async def get_major_event(
        self,
        corp_code: str,
        event_type: str,
    ) -> dict:
        """주요사항보고서 조회. event_type으로 36개 이벤트 중 선택."""
        meta = MAJOR_EVENT_TYPES.get(event_type)
        if not meta:
            raise ValueError(
                f"지원하지 않는 event_type: '{event_type}'. "
                f"사용 가능: {', '.join(sorted(MAJOR_EVENT_TYPES))}"
            )
        return await self._get(
            meta["path"],
            {"corp_code": corp_code},
            use_cache=True,
        )

    # ── DS006: 증권신고서 ──────────────────────────────────────

    async def get_securities_report(
        self,
        corp_code: str,
        report_type: str,
        bgn_de: str = "",
        end_de: str = "",
    ) -> dict:
        """증권신고서 조회. report_type으로 6개 유형 중 선택."""
        meta = SECURITIES_REPORT_TYPES.get(report_type)
        if not meta:
            raise ValueError(
                f"지원하지 않는 report_type: '{report_type}'. "
                f"사용 가능: {', '.join(sorted(SECURITIES_REPORT_TYPES))}"
            )
        params: dict = {"corp_code": corp_code}
        if bgn_de:
            params["bgn_de"] = bgn_de
        if end_de:
            params["end_de"] = end_de
        return await self._get(meta["path"], params, use_cache=True)

    # ── DS001: 공시서류 원본 ───────────────────────────────────

    async def get_document(self, rcept_no: str) -> dict:
        """공시서류 원본파일(ZIP) 다운로드 후 텍스트 추출.

        주석(notes) 등 전체 공시 원문에 접근할 때 사용.
        ZIP 내 XML/HTML을 파싱하여 텍스트로 반환합니다.
        """
        import re
        from html.parser import HTMLParser

        resp = await self._raw_get(
            "/document.xml",
            {"crtfc_key": self.api_key, "rcept_no": rcept_no},
            timeout=60.0,
        )

        # ZIP 해제
        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                file_list = zf.namelist()
                result: dict = {"rcept_no": rcept_no, "files": file_list, "documents": []}

                for fname in file_list:
                    if not any(fname.lower().endswith(ext) for ext in (".xml", ".htm", ".html")):
                        continue
                    raw = zf.read(fname)
                    # 인코딩 감지
                    for enc in ("utf-8", "euc-kr", "cp949"):
                        try:
                            text = raw.decode(enc)
                            break
                        except (UnicodeDecodeError, LookupError):
                            continue
                    else:
                        text = raw.decode("utf-8", errors="replace")

                    # HTML 태그 제거하여 순수 텍스트 추출
                    class _TagStripper(HTMLParser):
                        def __init__(self) -> None:
                            super().__init__()
                            self.parts: list[str] = []
                        def handle_data(self, data: str) -> None:
                            self.parts.append(data)

                    stripper = _TagStripper()
                    stripper.feed(text)
                    clean = " ".join(stripper.parts)
                    clean = re.sub(r"\s+", " ", clean).strip()

                    # 너무 긴 문서는 앞부분만 (토큰 절약)
                    if len(clean) > 50000:
                        clean = clean[:50000] + f"...[전체 {len(clean)}자 중 50000자까지 표시]"

                    result["documents"].append({"file": fname, "text": clean})

                return result
        except zipfile.BadZipFile:
            raise RuntimeError(f"DART document.xml 응답이 유효한 ZIP이 아닙니다 (rcept_no={rcept_no})")

    # ── DS003: XBRL 택사노미 ───────────────────────────────────

    async def get_xbrl_taxonomy(self, sj_div: str) -> dict:
        """XBRL 택사노미(재무제표 표준 계정과목) 조회."""
        return await self._get(
            "xbrlTaxonomy.json",
            {"sj_div": sj_div},
            use_cache=True,
        )
