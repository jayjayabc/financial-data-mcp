"""DART & FISIS 금융 데이터 MCP 서버

DART(전자공시시스템)과 FISIS(금융통계정보시스템) API를 통해
기업 공시, 재무제표, 금융통계 데이터를 조회·분석하는 MCP 서버입니다.

사용법:
    # 직접 실행
    python -m financial_data_mcp

    # Claude Desktop 설정 (claude_desktop_config.json)
    {
        "mcpServers": {
            "financial-data": {
                "command": "uv",
                "args": ["--directory", "/path/to/fisis-app", "run", "financial-data-mcp"],
                "env": {
                    "DART_API_KEY": "your-dart-api-key",
                    "FISIS_API_KEY": "your-fisis-api-key"
                }
            }
        }
    }

환경변수:
    DART_API_KEY: DART OpenAPI 인증키 (https://opendart.fss.or.kr 에서 발급)
    FISIS_API_KEY: FISIS OpenAPI 인증키 (https://fisis.fss.or.kr 에서 발급)
"""

from __future__ import annotations

import json
import os

from mcp.server.fastmcp import FastMCP

from .dart_client import DartClient, REPORT_CODES, CORP_CLASS
from .fisis_client import FisisClient, LARGE_DIVISIONS

mcp = FastMCP(
    "financial-data",
    description="DART(전자공시시스템)과 FISIS(금융통계정보시스템) 금융 데이터 조회·분석 MCP 서버",
)


# ── 클라이언트 팩토리 ─────────────────────────────────────────────

def _dart() -> DartClient:
    key = os.environ.get("DART_API_KEY", "")
    if not key:
        raise ValueError(
            "DART_API_KEY 환경변수가 설정되지 않았습니다. "
            "https://opendart.fss.or.kr 에서 API 키를 발급받으세요."
        )
    return DartClient(key)


def _fisis() -> FisisClient:
    key = os.environ.get("FISIS_API_KEY", "")
    if not key:
        raise ValueError(
            "FISIS_API_KEY 환경변수가 설정되지 않았습니다. "
            "https://fisis.fss.or.kr 에서 API 키를 발급받으세요."
        )
    return FisisClient(key)


def _json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DART 도구
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
async def dart_search_company(name: str, limit: int = 20) -> str:
    """DART에서 회사명으로 기업코드(corp_code)를 검색합니다.

    다른 DART 도구를 사용하려면 먼저 이 도구로 corp_code를 조회하세요.
    상장기업이 우선 표시됩니다.

    Args:
        name: 검색할 회사명 (예: "삼성전자", "현대자동차")
        limit: 최대 결과 수 (기본 20)

    Returns:
        기업코드, 회사명, 종목코드 목록 (JSON)
    """
    results = await _dart().search_company(name, limit)
    if not results:
        return f"'{name}'에 대한 검색 결과가 없습니다."
    return _json(results)


@mcp.tool()
async def dart_company_overview(corp_code: str) -> str:
    """DART에서 기업개황(회사 기본정보)을 조회합니다.

    회사명, 대표자명, 법인구분, 업종, 주소, 설립일, 상장일, 홈페이지 등을 반환합니다.

    Args:
        corp_code: 기업코드 (8자리, dart_search_company로 조회)
    """
    data = await _dart().get_company_overview(corp_code)
    return _json(data)


@mcp.tool()
async def dart_search_disclosures(
    corp_code: str = "",
    bgn_de: str = "",
    end_de: str = "",
    corp_cls: str = "",
    pblntf_ty: str = "",
    page_no: int = 1,
    page_count: int = 10,
) -> str:
    """DART에서 공시 목록을 검색합니다.

    특정 기업의 공시를 검색하거나, 기간별·유형별로 전체 공시를 검색할 수 있습니다.

    Args:
        corp_code: 기업코드 (8자리, 비워두면 전체)
        bgn_de: 검색 시작일 (YYYYMMDD, 예: "20240101")
        end_de: 검색 종료일 (YYYYMMDD, 예: "20241231")
        corp_cls: 법인구분 (Y=유가증권, K=코스닥, N=코넥스, E=기타)
        pblntf_ty: 공시유형 (A=정기공시, B=주요사항보고, C=발행공시, D=지분공시, E=기타공시, F=외부감사, G=펀드, H=자산유동화, I=거래소공시, J=공정위공시)
        page_no: 페이지 번호 (기본 1)
        page_count: 페이지당 건수 (기본 10, 최대 100)

    Returns:
        공시 목록 (접수번호, 공시제목, 회사명, 공시일 등)
    """
    data = await _dart().search_disclosures(
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        corp_cls=corp_cls,
        pblntf_ty=pblntf_ty,
        page_no=page_no,
        page_count=page_count,
    )
    return _json(data)


@mcp.tool()
async def dart_financial_statements(
    corp_code: str,
    bsns_year: str,
    reprt_code: str = "11011",
) -> str:
    """DART에서 단일회사의 주요 재무계정을 조회합니다.

    자산총계, 부채총계, 자본총계, 매출액, 영업이익, 당기순이익 등
    핵심 재무지표를 당기/전기/전전기 비교 형태로 반환합니다.

    Args:
        corp_code: 기업코드 (8자리)
        bsns_year: 사업연도 (YYYY, 예: "2024")
        reprt_code: 보고서코드 (11011=사업보고서, 11012=반기보고서, 11013=1분기보고서, 11014=3분기보고서)

    Returns:
        주요 재무계정 데이터 (연결/개별, 계정명, 당기/전기/전전기 금액)
    """
    data = await _dart().get_financial_statements(corp_code, bsns_year, reprt_code)
    return _json(data)


@mcp.tool()
async def dart_full_financial_statements(
    corp_code: str,
    bsns_year: str,
    reprt_code: str = "11011",
    fs_div: str = "CFS",
) -> str:
    """DART에서 단일회사의 전체 재무제표를 조회합니다.

    재무상태표, 손익계산서, 포괄손익계산서, 현금흐름표의 전체 계정과목을 반환합니다.
    dart_financial_statements보다 상세한 데이터가 필요할 때 사용하세요.

    Args:
        corp_code: 기업코드 (8자리)
        bsns_year: 사업연도 (YYYY)
        reprt_code: 보고서코드 (11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기)
        fs_div: 재무제표구분 (CFS=연결재무제표, OFS=개별재무제표)

    Returns:
        전체 재무제표 계정과목 데이터
    """
    data = await _dart().get_full_financial_statements(
        corp_code, bsns_year, reprt_code, fs_div
    )
    return _json(data)


@mcp.tool()
async def dart_multi_company_financials(
    corp_codes: list[str],
    bsns_year: str,
    reprt_code: str = "11011",
) -> str:
    """DART에서 여러 회사의 주요 재무계정을 한번에 비교 조회합니다.

    동일 업종 내 기업 비교, 경쟁사 분석 등에 활용하세요. 최대 20개 기업까지 가능합니다.

    Args:
        corp_codes: 기업코드 리스트 (최대 20개, 예: ["00126380", "00164779"])
        bsns_year: 사업연도 (YYYY)
        reprt_code: 보고서코드 (11011=사업보고서)

    Returns:
        다중회사 주요 재무계정 비교 데이터
    """
    data = await _dart().get_multi_company_financials(corp_codes, bsns_year, reprt_code)
    return _json(data)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  FISIS 도구
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
async def fisis_list_statistics(
    lrg_div: str = "",
    sml_div: str = "",
) -> str:
    """FISIS에서 조회 가능한 통계목록을 검색합니다.

    어떤 통계데이터가 있는지 확인할 때 사용합니다.
    통계코드(stat_cd)를 확인한 후 fisis_get_statistics로 데이터를 조회하세요.

    Args:
        lrg_div: 대분류 코드 (01=은행, 02=비은행, 03=보험, 04=금융투자, 비워두면 전체)
        sml_div: 소분류 코드 (비워두면 전체)

    Returns:
        통계목록 (통계코드, 통계명, 분류 등)
    """
    data = await _fisis().list_statistics(lrg_div, sml_div)
    return _json(data)


@mcp.tool()
async def fisis_get_statistics(
    stat_cd: str,
    strt_yymm: str,
    end_yymm: str,
    finance_cd: str = "",
    lrg_div: str = "",
    sml_div: str = "",
) -> str:
    """FISIS에서 금융통계 데이터를 조회합니다.

    특정 통계코드의 실제 데이터를 기간별로 조회합니다.
    통계코드는 fisis_list_statistics로 먼저 확인하세요.

    Args:
        stat_cd: 통계코드 (예: "010101")
        strt_yymm: 조회 시작월 (YYYYMM, 예: "202401")
        end_yymm: 조회 종료월 (YYYYMM, 예: "202412")
        finance_cd: 금융회사코드 (비워두면 전체 회사)
        lrg_div: 대분류 코드
        sml_div: 소분류 코드

    Returns:
        통계 데이터 (기간, 회사, 계정, 금액 등)
    """
    data = await _fisis().get_statistics(
        stat_cd, strt_yymm, end_yymm, finance_cd, lrg_div, sml_div
    )
    return _json(data)


@mcp.tool()
async def fisis_list_companies(
    lrg_div: str = "",
    sml_div: str = "",
    finance_cd: str = "",
) -> str:
    """FISIS에 등록된 금융회사 목록을 조회합니다.

    특정 권역(은행, 비은행 등)의 금융회사 목록과 코드를 확인할 때 사용합니다.
    금융회사코드(finance_cd)를 확인한 후 fisis_get_statistics에서 활용하세요.

    Args:
        lrg_div: 대분류 코드 (01=은행, 02=비은행, 03=보험, 04=금융투자, 비워두면 전체)
        sml_div: 소분류 코드 (비워두면 전체)
        finance_cd: 금융회사코드 (특정 회사만 조회 시)

    Returns:
        금융회사 목록 (회사코드, 회사명 등)
    """
    data = await _fisis().list_companies(lrg_div, sml_div, finance_cd)
    return _json(data)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  참조 정보 도구
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
async def get_api_reference() -> str:
    """DART·FISIS API에서 자주 사용하는 코드 참조표를 반환합니다.

    보고서코드, 법인구분, 대분류코드 등 API 파라미터에 필요한 코드를 확인할 수 있습니다.
    """
    ref = {
        "DART_보고서코드": REPORT_CODES,
        "DART_법인구분": CORP_CLASS,
        "FISIS_대분류": LARGE_DIVISIONS,
        "사용_예시": {
            "삼성전자_재무제표_조회_순서": [
                "1. dart_search_company(name='삼성전자') → corp_code 확인",
                "2. dart_financial_statements(corp_code='00126380', bsns_year='2024') → 주요계정",
                "3. dart_full_financial_statements(corp_code='00126380', bsns_year='2024', fs_div='CFS') → 전체 연결재무제표",
            ],
            "은행_통계_조회_순서": [
                "1. fisis_list_statistics(lrg_div='01') → 은행 통계목록 확인",
                "2. fisis_list_companies(lrg_div='01') → 은행 회사목록 확인",
                "3. fisis_get_statistics(stat_cd='010101', strt_yymm='202401', end_yymm='202412') → 데이터 조회",
            ],
        },
    }
    return _json(ref)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  서버 실행
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def main() -> None:
    """MCP 서버를 시작합니다."""
    mcp.run()


if __name__ == "__main__":
    main()
