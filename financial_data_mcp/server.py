"""DART & FISIS 금융 데이터 MCP 서버.

DART(전자공시시스템)과 FISIS(금융통계정보시스템) API를 통해
기업 공시, 재무제표, 금융통계 데이터를 조회·분석합니다.

사용법:
    # 직접 실행
    python -m financial_data_mcp

    # Claude Code / Desktop 설정 예시
    {
      "mcpServers": {
        "financial-data": {
          "command": "python",
          "args": ["-m", "financial_data_mcp"],
          "cwd": "/path/to/financial-data-mcp"
        }
      }
    }

환경변수:
    DART_API_KEY: DART OpenAPI 인증키 (https://opendart.fss.or.kr)
    FISIS_API_KEY: FISIS OpenAPI 인증키 (https://fisis.fss.or.kr)
    LOG_LEVEL: 로그 레벨 (DEBUG/INFO/WARNING/ERROR, 기본 WARNING)

    서버 기동 시 프로젝트 루트의 .env 파일을 자동 로드합니다.
"""

from __future__ import annotations

import functools
import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from . import _validators as v
from .dart_client import (
    BUSINESS_REPORT_TYPES,
    CORP_CLASS,
    EQUITY_DISCLOSURE_TYPES,
    MAJOR_EVENT_TYPES,
    REPORT_CODES,
    SECURITIES_REPORT_TYPES,
    SJ_DIV,
    DartClient,
)
from .fisis_client import DIVISIONS, DIVISION_GROUPS, LARGE_DIVISIONS, FisisClient


def _load_env_file() -> None:
    """프로젝트 루트의 .env 파일을 찾아 자동 로드.

    탐색 순서 (먼저 찾은 것이 우선):
    1. 패키지 부모 디렉토리 (editable install: project_root/.env)
    2. 현재 작업 디렉토리 (사용자가 프로젝트 루트에서 실행한 경우)
    3. python-dotenv find_dotenv: CWD 상위로 올라가며 탐색

    비-editable 설치에서도 동작하도록 다중 경로 fallback 지원.
    """
    candidates = [
        Path(__file__).resolve().parent.parent / ".env",
        Path.cwd() / ".env",
    ]
    for path in candidates:
        if path.is_file():
            load_dotenv(path, override=False)
            return

    # 마지막 fallback: python-dotenv가 상위 디렉토리를 자동 탐색
    try:
        from dotenv import find_dotenv

        found = find_dotenv(usecwd=True)
        if found:
            load_dotenv(found, override=False)
    except Exception:
        pass  # .env 없으면 OS 환경변수만 사용


_load_env_file()

# 로깅 설정 (stderr로 출력 - stdio MCP는 stdout을 프로토콜용으로 사용)
_log_level = os.environ.get("LOG_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=_log_level,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("financial_data_mcp")

mcp = FastMCP(
    "financial-data",
    instructions="""\
DART(전자공시시스템)과 FISIS(금융통계정보시스템) 금융 데이터 조회·분석 MCP 서버.

중요: 금융 데이터 질문을 받으면 다른 도구 호출 전에 반드시 plan_data_query를 먼저 호출하세요.
이 도구가 DART/FISIS 데이터 구조를 분석하여 최적의 수집 전략을 수립할 수 있게 도와줍니다.

효율 팁:
- dart_full_financial_statements: sj_div로 특정 표만 필터 (IS/BS/CF) → 토큰 75% 절감
- dart_multi_company_financials: 기업 비교 시 개별 호출 대신 한 번에 최대 20개
- 동일 질문 반복: 1시간 캐시 자동 적용 (추가 API 소비 없음)
""",
)


# ── 클라이언트 싱글톤 ───────────────────────────────────────────
# lru_cache(maxsize=1)로 프로세스 생애 동안 단일 인스턴스 유지.


@lru_cache(maxsize=1)
def _dart() -> DartClient:
    key = os.environ.get("DART_API_KEY", "")
    if not key:
        raise ValueError(
            "DART_API_KEY 환경변수가 설정되지 않았습니다. "
            "https://opendart.fss.or.kr 에서 API 키를 발급받으세요."
        )
    return DartClient(key)


@lru_cache(maxsize=1)
def _fisis() -> FisisClient:
    key = os.environ.get("FISIS_API_KEY", "")
    if not key:
        raise ValueError(
            "FISIS_API_KEY 환경변수가 설정되지 않았습니다. "
            "https://fisis.fss.or.kr 에서 API 키를 발급받으세요."
        )
    return FisisClient(key)


# ── 직렬화 / 응답 가공 ──────────────────────────────────────────


def _json(data: Any) -> str:
    """토큰 절약형 JSON 직렬화 (공백 없음)."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _drop_empty(d: dict) -> dict:
    """None/빈 문자열 필드 제거로 추가 토큰 절약."""
    return {k: v for k, v in d.items() if v not in (None, "")}


# DART 응답에서 흔히 딸려오는 메타 필드 (토큰 낭비)
_DART_META_FIELDS = frozenset({"status", "message"})


def _strip_dart_meta(d: dict) -> dict:
    """DART 응답에서 status/message 등 메타 필드 제거."""
    return {k: val for k, val in d.items() if k not in _DART_META_FIELDS}


def _compact_disclosure(item: dict) -> dict:
    """공시 항목에서 필수 필드만 추출."""
    return _drop_empty(
        {
            "rcept_no": item.get("rcept_no"),
            "rcept_dt": item.get("rcept_dt"),
            "corp_name": item.get("corp_name"),
            "corp_code": item.get("corp_code"),
            "stock_code": item.get("stock_code"),
            "report_nm": item.get("report_nm"),
            "flr_nm": item.get("flr_nm"),
            "rm": item.get("rm"),
        }
    )


def _compact_fin_row(item: dict) -> dict:
    """재무계정 행에서 필수 필드만 추출 (토큰 ~60% 절감)."""
    return _drop_empty(
        {
            "corp_code": item.get("corp_code"),
            "fs_div": item.get("fs_div"),
            "sj_div": item.get("sj_div"),
            "sj_nm": item.get("sj_nm"),
            "account_nm": item.get("account_nm"),
            "curr": item.get("thstrm_amount"),
            "prev": item.get("frmtrm_amount"),
            "prev2": item.get("bfefrmtrm_amount"),
        }
    )


def _fisis_extract_list(data: dict) -> Any:
    """FISIS 응답에서 list를 방어적으로 추출."""
    if not isinstance(data, dict):
        return data
    result = data.get("result")
    if isinstance(result, dict):
        for key in ("list", "data"):
            value = result.get(key)
            if isinstance(value, list):
                return value
        return result
    for key in ("list", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return data


# ── 에러 처리 데코레이터 ────────────────────────────────────────

ToolFunc = Callable[..., Awaitable[str]]


def _tool_safe(fn: ToolFunc) -> ToolFunc:
    """도구 함수를 감싸 에러를 사용자 친화적 텍스트로 변환.

    - ValueError (검증 실패): [input error] 접두사
    - RuntimeError (API/HTTP): [api error] 접두사
    - 기타: [internal error] + 로그
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs) -> str:
        try:
            return await fn(*args, **kwargs)
        except ValueError as e:
            logger.info("%s: validation error: %s", fn.__name__, e)
            return f"[input error] {e}"
        except RuntimeError as e:
            logger.warning("%s: api error: %s", fn.__name__, e)
            return f"[api error] {e}"
        except Exception as e:
            logger.exception("%s: unexpected error", fn.__name__)
            return f"[internal error] {type(e).__name__}: {e}"

    return wrapper


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  데이터 수집 플래닝
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# DART/FISIS 데이터 카탈로그 (정적, 매 호출에 재사용)
_DATA_CATALOG = {
    "DART": {
        "full_name": "전자공시시스템 (opendart.fss.or.kr)",
        "description": "기업별 공시·재무제표. 상장/비상장 약 90,000개 기업 대상.",
        "data_types": {
            "재무제표": "BS(재무상태표), IS(손익계산서), CIS(포괄손익), CF(현금흐름), SCE(자본변동) — 연결(CFS)+개별(OFS)",
            "주요계정": "자산총계, 부채총계, 자본총계, 매출액, 영업이익, 당기순이익 (당기/전기/전전기)",
            "사업보고서_주요정보": "배당, 직원현황, 임원현황, 최대주주, 자기주식, 타법인출자, 감사의견 등 28개 항목 → dart_business_report",
            "지분공시": "대량보유(5%이상), 임원·주요주주 소유보고 → dart_equity_disclosure",
            "주요사항보고서": "증자, 감자, 합병, 분할, 사채발행, 자기주식, 소송 등 36개 이벤트 → dart_major_event",
            "증권신고서": "지분증권, 채무증권, 합병, 분할 등 6개 유형 → dart_securities_report",
            "공시서류_원본": "주석(notes) 포함 공시 원문 텍스트 → dart_document (rcept_no 필요)",
            "공시검색": "정기공시, 주요사항보고, 발행공시, 지분공시, 외부감사 등",
            "기업개황": "회사명, 대표자, 업종, 주소, 설립일, 상장일, 홈페이지",
        },
        "granularity": "개별 기업 단위. 1회 호출 = 1개 기업. 다중비교 최대 20개.",
        "period": "사업연도(YYYY) + 보고서유형 (11011=사업, 11012=반기, 11013=1Q, 11014=3Q)",
        "sj_div_filter": {"BS": "재무상태표", "IS": "손익계산서", "CIS": "포괄손익", "CF": "현금흐름", "SCE": "자본변동"},
        "corp_cls": {"Y": "유가증권", "K": "코스닥", "N": "코넥스", "E": "기타"},
        "strength": [
            "모든 DART 등록 기업 대상 (금융업 + 일반 기업 모두)",
            "전체 계정과목 수준 상세 재무제표",
            "사업보고서 28개 항목 (배당·임원·직원·감사 등) 구조화 조회",
            "주요사항보고서 36개 이벤트 (증자·합병·분할·소송 등)",
            "공시 원문(주석 포함) 텍스트 추출 가능 (dart_document)",
            "개별 기업의 공시 이력 검색",
        ],
        "weakness": [
            "업권 전체 비교 시 기업별 개별 호출 필요 (N개 기업 = N+회 API 호출)",
            "판관비·충당금 등 세부 항목은 전체 재무제표(full)에서만 조회 (토큰 대량 소비)",
            "주석(notes)은 dart_document로 원문 텍스트 추출만 가능 (구조화 JSON 없음)",
        ],
        "cost": "기업당 1~2 API 호출 (quota 일 20,000건)",
    },
    "FISIS": {
        "full_name": "금융통계정보시스템 (fisis.fss.or.kr)",
        "description": "금감원 감독 대상 금융기관의 업권별 통계. 22개 업권(은행/보험/여신전문/금융투자/저축은행/상호금융 등).",
        "data_types": {
            "업권별_재무통계": "자산, 부채, 자본, 손익, 건전성, 수신, 여신 등 업권 표준 양식",
            "개별_금융기관": "업권 내 특정 금융기관의 통계 (finance_cd로 지정)",
            "시계열": "월별(YYYYMM) 시계열 데이터",
        },
        "granularity": "업권 전체 또는 개별 금융기관. 1회 호출 = 업권 전체 데이터.",
        "period": "월별 (YYYYMM ~ YYYYMM 범위 지정)",
        "div_codes": {
            "은행": "A(국내은행), J(외국은행국내지점)",
            "비은행": "B(신탁), R(종합금융), E(상호저축은행), O(신용협동조합), Q(새마을금고), P(농협), S(수협), M(산림조합)",
            "보험": "H(생명보험), I(손해보험)",
            "여신전문": "C(신용카드), K(리스), T(할부금융), N(신기술사업금융)",
            "금융투자": "F(증권), W(선물), G(자산운용), X(투자자문), D(부동산신탁)",
            "기타": "L(금융지주회사)",
            "_usage": "이 코드를 lrg_div 파라미터에 직접 전달. 전체 목록은 fisis_list_divisions 도구로 동적 조회 가능.",
        },
        "strength": [
            "1회 호출로 업권 전체 금융기관 데이터 조회",
            "금감원 표준 양식으로 기관 간 항목명 일관",
            "월별 시계열로 추이 분석에 최적",
            "fisis_list_divisions로 전체 업권(대분류+소분류) 동적 탐색 가능",
        ],
        "weakness": [
            "금융업만 대상 (삼성전자·현대차 등 일반 기업 불가)",
            "DART보다 계정과목 수준이 제한적",
        ],
        "cost": "1~2 API 호출로 업권 전체 데이터",
        "common_stat_codes": {
            "note": "stat_cd = fisis_list_statistics 결과의 list_no 값. 아래는 자주 쓰는 코드만 발췌. 전체 목록 → fisis_list_statistics(lrg_div=코드) 호출.",
            "은행(A)": {"SA021": "요약손익계산서", "SA003": "요약재무상태표(자산)", "SA014": "자본적정성(BIS)", "SA017": "수익성(ROA·ROE)", "SA053": "BIS비율(분기별)"},
            "비은행(B)": {"SB005": "수탁고(은행)", "SB013": "재무상태표(은행신탁)", "SB016": "손익계산서(은행신탁)"},
            "여신전문(C)": {"SC218": "요약손익계산서(최신)", "SC103": "요약재무상태표(자산)", "SC007": "자본적정성", "SC122": "카드이용실적"},
            "금융투자(D)": {"SD107": "요약손익계산서", "SD103": "요약재무상태표(자산)", "SD008": "자본적정성", "SD010": "수익성(ROA·ROE)"},
            "_tip": "22개 업권(H=생명보험, I=손해보험, E=저축은행, F=증권 등) 통계코드도 fisis_list_statistics(lrg_div=코드)로 조회 가능.",
        },
    },
    "planning_framework": {
        "step1_data_needs": "질문에서 필요한 데이터 항목 구체적으로 파악 (어떤 재무항목? 어떤 기간? 어떤 대상?)",
        "step2_source_selection": "DART/FISIS의 data_types·granularity·strength·weakness·cost를 비교하여 최적 소스 결정",
        "step3_cost_estimation": "예상 API 호출 횟수 산정 (DART: 기업수×호출, FISIS: 1~2회)",
        "step4_tool_sequence": "최소 호출로 데이터를 수집하는 구체적 도구 호출 순서 수립",
        "step5_fallback": "1차 소스에서 데이터가 부족하면 다른 소스로 보완할 계획 포함",
    },
}


# 세션 내 카탈로그 전달 여부 추적 (첫 호출에만 전체 전달)
_catalog_delivered = False
# plan_data_query 호출 여부 추적 (미호출 시 데이터 도구가 힌트 포함)
_plan_called = False


@mcp.tool()
@_tool_safe
async def plan_data_query(question: str) -> str:
    """사용자 질문에 대한 최적의 데이터 수집 전략을 수립합니다.

    **다른 데이터 조회 도구를 호출하기 전에 반드시 이 도구를 먼저 호출하세요.**

    첫 호출: DART/FISIS 데이터 카탈로그(구조·강점·약점·비용) + 플래닝 프레임워크 반환.
    이후 호출: 카탈로그 생략, 플래닝 프레임워크만 반환 (토큰 절약).

    이 카탈로그를 읽고 planning_framework 5단계에 따라 최적 수집 전략을 수립하세요.

    Args:
        question: 사용자의 원래 질문 (예: "시중은행 판관비 비교", "삼성전자 재무제표")
    """
    global _catalog_delivered, _plan_called
    _plan_called = True

    if not question or not question.strip():
        raise ValueError("question은 비어있을 수 없습니다")

    if not _catalog_delivered:
        _catalog_delivered = True
        return _json({
            "user_question": question.strip(),
            "data_catalog": _DATA_CATALOG,
            "instruction": (
                "위 카탈로그의 data_types, granularity, strength, weakness, "
                "cost_per_query를 분석하여 planning_framework 5단계에 따라 "
                "최적 수집 전략을 수립하세요. 전략을 수립한 후 해당 도구들을 호출하세요."
            ),
        })
    else:
        # 2번째 이후: 카탈로그 생략 → ~130 tokens 만 소비 (vs 613)
        return _json({
            "user_question": question.strip(),
            "note": "데이터 카탈로그는 이미 전달됨. 이전 카탈로그를 참고하여 planning_framework 5단계에 따라 수집 전략을 수립하세요.",
            "planning_framework_reminder": _DATA_CATALOG["planning_framework"],
        })


def _plan_hint(result: Any) -> str:
    """plan_data_query 미호출 시 결과에 라우팅 힌트를 추가해 반환.

    데이터는 그대로 반환하되, 다음 질문을 위해 plan_data_query 사용을 권장합니다.
    """
    if _plan_called:
        return _json(result)
    return _json({
        "hint": "다음 질문부터는 plan_data_query를 먼저 호출하면 DART/FISIS 최적 경로를 자동으로 선택해 API 호출 횟수와 토큰을 절약할 수 있습니다.",
        "data": result,
    })


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DART 도구
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
@_tool_safe
async def dart_search_company(name: str, limit: int = 20) -> str:
    """회사명으로 DART 기업코드(corp_code)를 검색합니다.

    다른 DART 도구의 선행 조건. 상장기업 우선 표시.
    최초 호출 시 기업코드 목록(약 90,000건)을 다운로드하고 이후 재사용합니다.
    동일 검색어 재호출 시 메모리 캐시에서 즉시 반환됩니다.

    Args:
        name: 회사명 (부분일치 가능, 예: "삼성전자", "현대")
        limit: 최대 결과 수 (기본 20, 1~100)
    """
    if not name or not name.strip():
        raise ValueError("name 파라미터는 비어있을 수 없습니다")
    if not (1 <= limit <= 100):
        raise ValueError(f"limit은 1~100 사이여야 합니다. 받은 값: {limit}")

    results = await _dart().search_company(name, limit)
    if not results:
        return f"'{name}'에 대한 검색 결과가 없습니다."
    return _plan_hint([_drop_empty(r) for r in results])


@mcp.tool()
@_tool_safe
async def dart_company_overview(corp_code: str) -> str:
    """DART에서 기업개황을 조회합니다.

    회사명, 대표자명, 법인구분, 업종, 주소, 설립일, 상장일 등을 반환합니다.

    Args:
        corp_code: 기업코드 (8자리, dart_search_company 로 조회)
    """
    v.validate_corp_code(corp_code)
    data = await _dart().get_company_overview(corp_code)
    return _json(_drop_empty(_strip_dart_meta(data)))


@mcp.tool()
@_tool_safe
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

    특정 기업의 공시를 검색하거나, 기간/유형별로 전체 공시를 검색할 수 있습니다.

    Args:
        corp_code: 기업코드 (8자리, 비워두면 전체)
        bgn_de: 검색 시작일 (YYYYMMDD, 예: "20240101")
        end_de: 검색 종료일 (YYYYMMDD, 예: "20241231")
        corp_cls: 법인구분 (Y=유가, K=코스닥, N=코넥스, E=기타, 비워두면 전체)
        pblntf_ty: 공시유형 (A=정기공시, B=주요사항, C=발행, D=지분, E=기타, F=외부감사, G=펀드, H=자산유동화, I=거래소)
        page_no: 페이지 번호 (기본 1)
        page_count: 페이지당 건수 (1~100, 기본 10)
    """
    if corp_code:
        v.validate_corp_code(corp_code)
    v.validate_yyyymmdd(bgn_de, "bgn_de")
    v.validate_yyyymmdd(end_de, "end_de")
    v.validate_corp_cls(corp_cls)
    if not (1 <= page_count <= 100):
        raise ValueError(f"page_count는 1~100 사이여야 합니다. 받은 값: {page_count}")
    if page_no < 1:
        raise ValueError(f"page_no는 1 이상이어야 합니다. 받은 값: {page_no}")

    data = await _dart().search_disclosures(
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        corp_cls=corp_cls,
        pblntf_ty=pblntf_ty,
        page_no=page_no,
        page_count=page_count,
    )
    items = data.get("list", []) or []
    result = _drop_empty(
        {
            "total_count": data.get("total_count"),
            "total_page": data.get("total_page"),
            "page_no": data.get("page_no"),
            "list": [_compact_disclosure(it) for it in items],
        }
    )
    return _json(result)


@mcp.tool()
@_tool_safe
async def dart_financial_statements(
    corp_code: str,
    bsns_year: str,
    reprt_code: str = "11011",
) -> str:
    """단일회사 주요 재무계정 조회 (당기/전기/전전기 비교).

    Args:
        corp_code: 기업코드 (8자리)
        bsns_year: 사업연도 (YYYY)
        reprt_code: 보고서코드 (기본 11011=사업보고서)
    """
    v.validate_corp_code(corp_code)
    v.validate_year(bsns_year)
    v.validate_report_code(reprt_code)

    data = await _dart().get_financial_statements(corp_code, bsns_year, reprt_code)
    items = [_compact_fin_row(r) for r in data.get("list", []) or []]
    return _plan_hint(items)


@mcp.tool()
@_tool_safe
async def dart_full_financial_statements(
    corp_code: str,
    bsns_year: str,
    reprt_code: str = "11011",
    fs_div: str = "CFS",
    sj_div: str = "",
) -> str:
    """단일회사 전체 재무제표 조회. **sj_div로 특정 표만 필터하면 토큰 75% 절감.**

    CFS 결과가 비면 자동으로 OFS 폴백.

    Args:
        corp_code: 기업코드 (8자리)
        bsns_year: 사업연도 (YYYY)
        reprt_code: 보고서코드 (기본 11011=사업보고서)
        fs_div: CFS=연결, OFS=개별
        sj_div: BS/IS/CIS/CF/SCE (비워두면 전체 — 비권장)
    """
    v.validate_corp_code(corp_code)
    v.validate_year(bsns_year)
    v.validate_report_code(reprt_code)
    v.validate_fs_div(fs_div)
    v.validate_sj_div(sj_div)

    client = _dart()
    data = await client.get_full_financial_statements(
        corp_code, bsns_year, reprt_code, fs_div
    )
    rows = data.get("list", []) or []

    # CFS가 비어있으면 OFS로 폴백 (소규모 기업 대응)
    fallback_used = False
    if not rows and fs_div == "CFS":
        logger.info("CFS empty, falling back to OFS: %s/%s", corp_code, bsns_year)
        data = await client.get_full_financial_statements(
            corp_code, bsns_year, reprt_code, "OFS"
        )
        rows = data.get("list", []) or []
        fallback_used = True

    if sj_div:
        rows = [r for r in rows if r.get("sj_div") == sj_div]

    items = [_compact_fin_row(r) for r in rows]

    if fallback_used:
        # LLM에게 폴백 사실을 알림
        return _json({"note": "CFS 데이터 없음 - OFS(개별재무제표)로 폴백", "list": items})
    return _json(items)


@mcp.tool()
@_tool_safe
async def dart_multi_company_financials(
    corp_codes: list[str],
    bsns_year: str,
    reprt_code: str = "11011",
) -> str:
    """다중회사 주요 재무계정 비교 조회 (최대 20개).

    Args:
        corp_codes: 기업코드 리스트 (1~20개)
        bsns_year: 사업연도 (YYYY)
        reprt_code: 보고서코드 (기본 11011=사업보고서)
    """
    v.validate_corp_codes_list(corp_codes)
    v.validate_year(bsns_year)
    v.validate_report_code(reprt_code)

    data = await _dart().get_multi_company_financials(corp_codes, bsns_year, reprt_code)
    items = [_compact_fin_row(r) for r in data.get("list", []) or []]
    return _plan_hint(items)


@mcp.tool()
@_tool_safe
async def dart_business_report(
    corp_code: str,
    bsns_year: str,
    reprt_code: str = "11011",
    info_type: str = "",
) -> str:
    """사업보고서 주요정보 28개 항목 조회. info_type 비워두면 항목 목록 반환.

    자주 쓰는 info_type: 배당, 직원현황, 임원현황, 최대주주, 자기주식, 감사인명칭의견

    Args:
        corp_code: 기업코드 (8자리)
        bsns_year: 사업연도 (YYYY)
        reprt_code: 보고서코드 (기본 11011=사업보고서)
        info_type: 조회할 항목 (비워두면 목록 반환)
    """
    if not info_type:
        items = [{"type": k, "desc": m["desc"]} for k, m in BUSINESS_REPORT_TYPES.items()]
        return _json({"available_types": items, "count": len(items),
                       "usage": "info_type 파라미터에 type 값을 넣어 호출하세요."})
    v.validate_corp_code(corp_code)
    v.validate_year(bsns_year)
    v.validate_report_code(reprt_code)
    data = await _dart().get_business_report(corp_code, bsns_year, reprt_code, info_type)
    return _json(_strip_dart_meta(data))


@mcp.tool()
@_tool_safe
async def dart_equity_disclosure(
    corp_code: str,
    report_type: str = "",
) -> str:
    """지분공시 조회. report_type: 대량보유 / 임원주요주주 (비워두면 목록 반환).

    Args:
        corp_code: 기업코드 (8자리)
        report_type: 대량보유 / 임원주요주주
    """
    if not report_type:
        items = [{"type": k, "desc": m["desc"]} for k, m in EQUITY_DISCLOSURE_TYPES.items()]
        return _json({"available_types": items})
    v.validate_corp_code(corp_code)
    data = await _dart().get_equity_disclosure(corp_code, report_type)
    return _json(_strip_dart_meta(data))


@mcp.tool()
@_tool_safe
async def dart_major_event(
    corp_code: str,
    event_type: str = "",
) -> str:
    """주요사항보고서 36개 이벤트 조회. event_type 비워두면 목록 반환.

    자주 쓰는: 유상증자결정, 합병결정, 분할결정, 전환사채발행결정, 소송제기

    Args:
        corp_code: 기업코드 (8자리)
        event_type: 이벤트 유형 (비워두면 목록 반환)
    """
    if not event_type:
        items = [{"type": k, "desc": m["desc"]} for k, m in MAJOR_EVENT_TYPES.items()]
        return _json({"available_types": items, "count": len(items),
                       "usage": "event_type 파라미터에 type 값을 넣어 호출하세요."})
    v.validate_corp_code(corp_code)
    data = await _dart().get_major_event(corp_code, event_type)
    return _json(_strip_dart_meta(data))


@mcp.tool()
@_tool_safe
async def dart_securities_report(
    corp_code: str,
    report_type: str = "",
    bgn_de: str = "",
    end_de: str = "",
) -> str:
    """증권신고서 6개 유형 조회. report_type 비워두면 목록 반환.

    Args:
        corp_code: 기업코드 (8자리)
        report_type: 지분증권/채무증권/합병/분할 등 (비워두면 목록)
        bgn_de: 시작일 (YYYYMMDD, 선택)
        end_de: 종료일 (YYYYMMDD, 선택)
    """
    if not report_type:
        items = [{"type": k, "desc": m["desc"]} for k, m in SECURITIES_REPORT_TYPES.items()]
        return _json({"available_types": items})
    v.validate_corp_code(corp_code)
    v.validate_yyyymmdd(bgn_de, "bgn_de")
    v.validate_yyyymmdd(end_de, "end_de")
    data = await _dart().get_securities_report(corp_code, report_type, bgn_de, end_de)
    return _json(_strip_dart_meta(data))


@mcp.tool()
@_tool_safe
async def dart_document(
    rcept_no: str,
) -> str:
    """공시서류 원본 텍스트 추출 (주석/notes 포함). 최대 50,000자.

    Args:
        rcept_no: 접수번호 (dart_search_disclosures 결과에서 확인)
    """
    if not rcept_no or not rcept_no.strip():
        raise ValueError("rcept_no는 필수입니다. dart_search_disclosures로 먼저 확인하세요.")
    data = await _dart().get_document(rcept_no)
    return _json(data)


@mcp.tool()
@_tool_safe
async def dart_xbrl_taxonomy(
    sj_div: str,
) -> str:
    """XBRL 표준 계정과목 조회.

    Args:
        sj_div: BS/IS/CIS/CF/SCE
    """
    v.validate_sj_div(sj_div)
    if not sj_div:
        raise ValueError("sj_div는 필수입니다 (BS/IS/CIS/CF/SCE)")
    data = await _dart().get_xbrl_taxonomy(sj_div)
    return _json(_strip_dart_meta(data))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  FISIS 도구
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
@_tool_safe
async def fisis_list_divisions(
    div_cd: str = "",
) -> str:
    """FISIS 22개 업권 목록 반환 (API 호출 없음). 세부 소분류 → fisis_list_companies 사용.

    Args:
        div_cd: 특정 업권 (예: A, C, H). 비워두면 전체 22개.
    """
    divisions = _fisis().list_divisions(div_cd)
    return _json({
        "divisions": divisions,
        "count": len(divisions),
        "groups": DIVISION_GROUPS,
        "tip": "세부 소분류/소속 금융회사 → fisis_list_companies(lrg_div=코드) 호출",
    })


@mcp.tool()
@_tool_safe
async def fisis_list_statistics(
    lrg_div: str = "",
    sml_div: str = "",
) -> str:
    """FISIS 통계목록 검색. stat_cd 확인 후 fisis_get_statistics로 데이터 조회.

    Args:
        lrg_div: 업권코드 (A/C/H/F 등 22개. fisis_list_divisions 참조)
        sml_div: 소분류 (선택)
    """
    data = await _fisis().list_statistics(lrg_div, sml_div)
    return _json(_fisis_extract_list(data))


@mcp.tool()
@_tool_safe
async def fisis_get_statistics(
    stat_cd: str,
    strt_yymm: str,
    end_yymm: str,
    finance_cd: str = "",
    lrg_div: str = "",
    sml_div: str = "",
    term: str = "Q",
) -> str:
    """FISIS 금융통계 데이터 조회. stat_cd는 plan_data_query 또는 fisis_list_statistics에서 확인.

    Args:
        stat_cd: 통계코드 (예: "SA021")
        strt_yymm: 시작월 (YYYYMM)
        end_yymm: 종료월 (YYYYMM)
        finance_cd: 금융회사코드 (선택)
        lrg_div: 업권코드 (선택)
        sml_div: 소분류 (선택)
        term: Q=분기(기본), Y=연간
    """
    if not stat_cd:
        raise ValueError("stat_cd는 필수입니다. fisis_list_statistics 로 먼저 확인하세요.")
    v.validate_yyyymm(strt_yymm, "strt_yymm")
    v.validate_yyyymm(end_yymm, "end_yymm")
    if strt_yymm > end_yymm:
        raise ValueError(
            f"strt_yymm({strt_yymm})은 end_yymm({end_yymm}) 이하여야 합니다"
        )
    if term not in ("Q", "Y"):
        raise ValueError("term은 'Q'(분기) 또는 'Y'(연간)만 허용됩니다.")

    data = await _fisis().get_statistics(
        stat_cd, strt_yymm, end_yymm, finance_cd, lrg_div, sml_div, term
    )
    return _json(_fisis_extract_list(data))


@mcp.tool()
@_tool_safe
async def fisis_list_companies(
    lrg_div: str = "",
    sml_div: str = "",
    finance_cd: str = "",
) -> str:
    """FISIS 금융회사 목록 조회. finance_cd 확인용.

    Args:
        lrg_div: 업권코드 (fisis_list_divisions 참조)
        sml_div: 소분류 (선택)
        finance_cd: 특정 회사코드 (선택)
    """
    data = await _fisis().list_companies(lrg_div, sml_div, finance_cd)
    return _json(_fisis_extract_list(data))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  운영/진단 도구
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
@_tool_safe
async def dart_quota_status() -> str:
    """DART API 일일 요청 quota 사용 현황을 조회합니다.

    DART OpenAPI는 기본 20,000건/일 한도가 있습니다. 이 도구는 클라이언트가
    추적한 오늘 사용량, 남은 quota, 최근 7일 이력을 반환합니다.

    ⚠️ 주의: 실제 네트워크 호출만 카운트하며, 캐시 hit은 제외됩니다.
    여러 프로세스가 동시에 사용하면 실측보다 낮게 집계될 수 있습니다.
    본격적인 배치 작업 전에 확인하세요.
    """
    status = _dart().quota.status()
    # 경고 메시지 포함
    if status["near_limit"]:
        status["warning"] = (
            f"⚠️ 일일 한도의 {status['usage_pct']}% 사용 중. "
            f"남은 {status['remaining']}건 이후 요청은 실패할 수 있습니다."
        )
    return _json(status)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  서버 실행
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def main() -> None:
    """MCP 서버를 stdio 모드로 시작합니다."""
    mcp.run()


if __name__ == "__main__":
    main()
