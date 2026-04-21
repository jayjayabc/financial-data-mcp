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


class _HtmlTextExtractor(HTMLParser):
    """HTML에서 가시적 텍스트만 추출. script/style/head 태그 내용 무시."""

    _SKIP_TAGS = frozenset({"script", "style", "head"})

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth: int = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag.lower() in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._parts.append(text)

    def get_text(self) -> str:
        raw = "\n".join(self._parts)
        return re.sub(r"\n{3,}", "\n\n", raw)


def _extract_html_text(html: str) -> str:
    """HTML 문자열에서 가시 텍스트 추출."""
    extractor = _HtmlTextExtractor()
    try:
        extractor.feed(html)
    except Exception:
        pass
    return extractor.get_text()


def _decode_bytes(data: bytes) -> str:
    """바이트를 UTF-8 → EUC-KR → CP949 순으로 디코딩."""
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


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
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30.0,
            ),
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
            keys_to_remove = list(self._search_cache.keys())[: SEARCH_CACHE_MAX // 2]
            for k in keys_to_remove:
                self._search_cache.pop(k, None)
        self._search_cache[cache_key] = results
        return results

    async def list_listed_companies(
        self,
        corp_cls: str = "",
    ) -> list[dict]:
        """상장기업 목록 반환. API 호출 없음 (기업코드 캐시에서 필터).

        Args:
            corp_cls: 시장 필터 — Y(유가증권), K(코스닥), 빈 문자열(전체)
        """
        corps = await self.load_corp_codes()
        listed = [c for c in corps if c["stock_code"]]
        if corp_cls:
            # stock_code의 시장 구분은 corp_codes에 없으므로
            # corp_cls 필터는 caller에서 처리 (서버 도구에서 company_overview 등 활용)
            pass
        listed.sort(key=lambda c: c["corp_name"])
        return listed

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

    # ── 사업보고서 주요정보 (범용) ────────────────────────────────

    async def get_business_report(
        self,
        endpoint: str,
        corp_code: str,
        bsns_year: str,
        reprt_code: str = "11011",
    ) -> dict:
        """사업보고서 주요정보 범용 조회.

        배당, 임원, 직원, 주주, 자기주식 등 대부분의 사업보고서 항목이
        동일한 파라미터(corp_code, bsns_year, reprt_code)를 사용합니다.
        """
        return await self._get(
            endpoint,
            {
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
            },
            use_cache=True,
        )

    # ── 다중 기업 비교 ────────────────────────────────────────────

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

    # ── 공시 원문 ─────────────────────────────────────────────────

    async def get_document_text(
        self,
        rcept_no: str,
        section_keyword: str = "",
        max_chars: int = 6000,
    ) -> dict:
        """공시 원문 ZIP을 다운로드해 HTML 텍스트를 추출한다.

        DART document.json 엔드포인트는 성공 시 ZIP 바이너리를,
        실패 시 JSON 에러 응답을 반환한다.
        """
        resp = await self._raw_get(
            "/document.json",
            {"crtfc_key": self.api_key, "rcept_no": rcept_no},
            timeout=60.0,
        )

        # 에러 응답은 JSON으로 반환됨
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type or resp.content[:1] == b"{":
            try:
                data = resp.json()
                status = data.get("status", "000")
                if status != "000":
                    raise RuntimeError(
                        f"DART API 오류 [{status}]: {data.get('message', '알 수 없는 오류')}"
                    )
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"DART 응답 파싱 실패: {e}") from e

        try:
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
        except zipfile.BadZipFile:
            raise RuntimeError(
                f"rcept_no={rcept_no!r} 문서를 ZIP으로 열 수 없습니다. "
                "접수번호가 올바른지 확인하세요."
            )

        with zf:
            names = zf.namelist()
            html_files = sorted(
                n for n in names if n.lower().endswith((".htm", ".html"))
            )
            if not html_files:
                return {
                    "rcept_no": rcept_no,
                    "error": "ZIP 내 HTML 파일 없음",
                    "files": names,
                }

            parts: list[str] = []
            for fname in html_files:
                raw = zf.read(fname)
                text = _extract_html_text(_decode_bytes(raw))
                if text.strip():
                    parts.append(text)

        full_text = "\n\n".join(parts)

        # 섹션 키워드 필터: 키워드 발견 위치 기준으로 앞뒤 컨텍스트 반환
        if section_keyword and section_keyword in full_text:
            idx = full_text.find(section_keyword)
            start = max(0, idx - 300)
            excerpt = full_text[start: start + max_chars]
            return {
                "rcept_no": rcept_no,
                "section_keyword": section_keyword,
                "text": excerpt,
                "total_chars": len(full_text),
                "returned_chars": len(excerpt),
                "truncated": (start + max_chars) < len(full_text),
            }

        excerpt = full_text[:max_chars]
        return {
            "rcept_no": rcept_no,
            "text": excerpt,
            "total_chars": len(full_text),
            "returned_chars": len(excerpt),
            "truncated": len(full_text) > max_chars,
        }
