"""DART(전자공시시스템) OpenAPI 클라이언트.

API 문서: https://opendart.fss.or.kr/guide/main.do

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
import re
import xml.etree.ElementTree as ET
import zipfile
from html.parser import HTMLParser

import httpx

from ._cache import TTLCache, load_disk_cache, save_disk_cache
from ._http import mask_params, translate_http_error, with_retry
from ._quota import QuotaTracker

logger = logging.getLogger("financial_data_mcp.dart")

BASE_URL = "https://opendart.fss.or.kr/api"
DART_VIEWER_BASE = "https://dart.fss.or.kr"
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


class _DartHTMLParser(HTMLParser):
    """DART viewer HTML에서 테이블/텍스트를 추출하는 경량 파서.

    외부 의존성 없이 표준 라이브러리만 사용.
    테이블이 있으면 TSV 형식, 없으면 일반 텍스트 반환.
    """

    _SKIP_TAGS = frozenset({"script", "style"})

    def __init__(self) -> None:
        super().__init__()
        # 테이블 파싱 상태
        self._tables: list[list[list[str]]] = []  # [table][row][cell]
        self._in_table = 0        # 중첩 table 깊이
        self._in_row = False
        self._in_cell = False
        self._cell_text: list[str] = []
        self._current_row: list[str] = []
        self._current_table: list[list[str]] = []
        # script/style 건너뛰기
        self._skip_depth = 0
        # 테이블 외 텍스트
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "table":
            self._in_table += 1
            if self._in_table == 1:
                self._current_table = []
        elif tag == "tr" and self._in_table:
            self._in_row = True
            self._current_row = []
        elif tag in ("td", "th") and self._in_row:
            self._in_cell = True
            self._cell_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in ("td", "th") and self._in_cell:
            self._current_row.append(" ".join(self._cell_text).strip())
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if any(self._current_row):
                self._current_table.append(self._current_row)
            self._in_row = False
        elif tag == "table" and self._in_table:
            if self._in_table == 1 and self._current_table:
                self._tables.append(self._current_table)
                self._current_table = []
            self._in_table = max(0, self._in_table - 1)

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        if self._in_cell:
            self._cell_text.append(text)
        elif not self._in_table:
            self._text_parts.append(text)

    def result(self) -> str:
        if self._tables:
            parts: list[str] = []
            for table in self._tables:
                tsv = "\n".join("\t".join(cells) for cells in table)
                parts.append(tsv)
            return "\n\n".join(parts)
        if self._text_parts:
            return "\n".join(self._text_parts)
        return "(내용 없음)"


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
        self._viewer_client = httpx.AsyncClient(
            base_url=DART_VIEWER_BASE,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": "financial-data-mcp/0.1"},
            follow_redirects=True,
        )
        self._response_cache = TTLCache(
            ttl_seconds=RESPONSE_TTL_SECONDS, max_size=256
        )
        self.quota = QuotaTracker()

    async def aclose(self) -> None:
        """내부 httpx 클라이언트 종료."""
        await self._client.aclose()
        await self._viewer_client.aclose()

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

    # ── DART 공시 문서 뷰어 ────────────────────────────────────

    # DART main.do 페이지에서 TreeNode JS를 파싱하여 dcmNo/eleId 추출
    _TREE_NODE_RE = re.compile(
        r'new\s+TreeNode\(\s*"([^"]*?)"\s*,\s*"([^"]*?)"\s*\)'
    )
    _DCM_NO_RE = re.compile(r"dcmNo=(\d+)")
    _ELE_ID_RE = re.compile(r"eleId=(\d+)")

    async def get_document_list(self, rcp_no: str) -> list[dict]:
        """공시 접수번호(rcept_no)에 해당하는 문서 섹션 목록을 반환.

        DART main.do 페이지의 TreeNode JavaScript를 파싱하여
        각 섹션의 dcm_no, ele_id, title을 추출합니다.
        """
        async def _do() -> httpx.Response:
            resp = await self._viewer_client.get(
                "/dsaf001/main.do",
                params={"rcpNo": rcp_no},
            )
            resp.raise_for_status()
            return resp

        try:
            resp = await with_retry(_do, label=f"DART viewer main.do/{rcp_no}")
        except (httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException) as e:
            raise translate_http_error("DART viewer", e) from e

        html = resp.text
        sections: list[dict] = []
        seen: set[tuple[str, str]] = set()

        for match in self._TREE_NODE_RE.finditer(html):
            title = match.group(1).strip()
            url_fragment = match.group(2)

            dcm_match = self._DCM_NO_RE.search(url_fragment)
            ele_match = self._ELE_ID_RE.search(url_fragment)
            if not dcm_match:
                continue

            dcm_no = dcm_match.group(1)
            ele_id = ele_match.group(1) if ele_match else "0"
            key = (dcm_no, ele_id)

            if key not in seen:
                seen.add(key)
                sections.append({
                    "dcm_no": dcm_no,
                    "ele_id": ele_id,
                    "title": title,
                })

        return sections

    async def read_document(
        self,
        rcp_no: str,
        dcm_no: str,
        ele_id: str = "0",
    ) -> str:
        """공시 문서의 특정 섹션 HTML을 가져와 텍스트로 변환.

        DART viewer.do 페이지에서 HTML을 다운로드하고
        BeautifulSoup으로 테이블/텍스트를 추출합니다.
        """
        params = {
            "rcpNo": rcp_no,
            "dcmNo": dcm_no,
            "eleId": ele_id,
            "offset": "0",
            "length": "9999999",
            "dtd": "dart4.xsd",
        }

        async def _do() -> httpx.Response:
            resp = await self._viewer_client.get(
                "/report/viewer.do",
                params=params,
            )
            resp.raise_for_status()
            return resp

        try:
            resp = await with_retry(_do, label=f"DART viewer/{rcp_no}/{dcm_no}/{ele_id}")
        except (httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException) as e:
            raise translate_http_error("DART viewer", e) from e

        return self._parse_viewer_html(resp.text)

    @staticmethod
    def _parse_viewer_html(html: str) -> str:
        """DART viewer HTML에서 재무 테이블과 텍스트를 추출.

        표준 라이브러리 html.parser 사용 (외부 의존성 없음).
        """
        parser = _DartHTMLParser()
        parser.feed(html)
        return parser.result()
