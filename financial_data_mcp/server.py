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

import asyncio
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
from .dart_client import CORP_CLASS, REPORT_CODES, SJ_DIV, DartClient
from .fisis_client import LARGE_DIVISIONS, FisisClient


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
- dart_financial_statements_multi_year: 연도별 추이 분석 시 병렬 조회로 대기 시간 단축
- fisis_get_multi_statistics: 여러 통계코드 동시 조회 (예: BIS비율+NPL+수익성)
- 동일 질문 반복: 1시간 캐시 자동 적용 (추가 API 소비 없음)
- dart_search_companies: 여러 회사명 한번에 병렬 검색 (N회 호출 → 1회)
- dart_business_report: 사업보고서 주요정보 22종 (배당, 임원, 직원, 주주, 감사 등)
- dart_to_fisis_bridge: DART 기업이 금융기관인지 판별 + FISIS 대분류 안내
- dart_quota_status: API 사용량 + 캐시 hit/miss 통계 확인
- dart_document_content: 공시 원문(주석·수시공시 본문) 텍스트 조회 (rcept_no 필요)
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


# FISIS 응답에서 불필요한 메타 필드 (토큰 낭비)
_FISIS_META_FIELDS = frozenset({
    "err_msg", "errMsg", "err_cd", "errCd", "result",
})


def _compact_fisis_row(item: dict) -> dict:
    """FISIS 통계 행에서 메타 필드 제거 + 빈 값 제거."""
    return _drop_empty({k: v for k, v in item.items() if k not in _FISIS_META_FIELDS})


def _fisis_compact_list(data: dict) -> list:
    """FISIS 응답에서 리스트 추출 후 각 행을 compaction."""
    raw = _fisis_extract_list(data)
    if isinstance(raw, list):
        return [_compact_fisis_row(r) if isinstance(r, dict) else r for r in raw]
    if isinstance(raw, dict):
        return [_compact_fisis_row(raw)]
    return raw


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
            "공시": "정기공시, 주요사항보고, 발행공시, 지분공시, 외부감사 등",
            "기업개황": "회사명, 대표자, 업종, 주소, 설립일, 상장일, 홈페이지",
            "사업보고서_주요정보": (
                "dart_business_report 도구로 22종 조회 가능: "
                "dividend(배당), employee(직원), executive(임원), "
                "major_shareholder(최대주주), treasury_stock(자기주식), "
                "audit_opinion(감사의견), individual_pay(개인보수), "
                "capital_change(증자감자), stock_total(주식총수), "
                "bond_balance(사채), other_investment(타법인출자) 등"
            ),
            "공시_원문": (
                "dart_document_content 도구로 공시 원문 텍스트 조회 가능. "
                "재무제표 주석, 수시공시 본문, 사업보고서 서술 내용(사업 현황·위험요소·MD&A) 등. "
                "dart_search_disclosures의 rcept_no를 사용. "
                "section_keyword로 '주석', '우발채무', '특수관계자' 등 원하는 섹션을 필터링 가능."
            ),
        },
        "granularity": "개별 기업 단위. 1회 호출 = 1개 기업. 다중비교 최대 20개.",
        "period": "사업연도(YYYY) + 보고서유형 (11011=사업, 11012=반기, 11013=1Q, 11014=3Q)",
        "sj_div_filter": {"BS": "재무상태표", "IS": "손익계산서", "CIS": "포괄손익", "CF": "현금흐름", "SCE": "자본변동"},
        "corp_cls": {"Y": "유가증권", "K": "코스닥", "N": "코넥스", "E": "기타"},
        "strength": [
            "모든 DART 등록 기업 대상 (금융업 + 일반 기업 모두)",
            "전체 계정과목 수준 상세 재무제표",
            "개별 기업의 공시 이력 검색",
        ],
        "weakness": [
            "업권 전체 비교 시 기업별 개별 호출 필요 (N개 기업 = N+회 API 호출)",
            "판관비·충당금 등 세부 항목은 전체 재무제표(full)에서만 조회 (토큰 대량 소비)",
        ],
        "cost": "기업당 1~2 API 호출 (quota 일 20,000건)",
    },
    "FISIS": {
        "full_name": "금융통계정보시스템 (fisis.fss.or.kr)",
        "description": "금감원 감독 대상 금융기관의 업권별 통계. 은행/비은행/보험/금융투자.",
        "data_types": {
            "업권별_재무통계": "자산, 부채, 자본, 손익, 건전성, 수신, 여신 등 업권 표준 양식",
            "개별_금융기관": "업권 내 특정 금융기관의 통계 (finance_cd로 지정)",
            "시계열": "월별(YYYYMM) 시계열 데이터",
        },
        "granularity": "업권 전체 또는 개별 금융기관. 1회 호출 = 업권 전체 데이터.",
        "period": "월별 (YYYYMM ~ YYYYMM 범위 지정)",
        "lrg_div": {"A": "은행(국내은행)", "B": "비은행(신탁회사)", "C": "여신전문금융사·카드사", "D": "금융투자(증권·자산운용)"},
        "strength": [
            "1회 호출로 업권 전체 금융기관 데이터 조회",
            "금감원 표준 양식으로 기관 간 항목명 일관",
            "월별 시계열로 추이 분석에 최적",
        ],
        "weakness": [
            "금융업만 대상 (삼성전자·현대차 등 일반 기업 불가)",
            "DART보다 계정과목 수준이 제한적",
        ],
        "cost": "1~2 API 호출로 업권 전체 데이터",
        "common_stat_codes": {
            "note": "fisis_get_statistics의 stat_cd 파라미터 = fisis_list_statistics 결과의 list_no 필드값. 전체 목록은 fisis_list_statistics 호출.",
            "은행(lrg_div=A)": {
                "_재무현황": {
                    "SA003": "요약재무상태표(자산-은행계정)",
                    "SA004": "요약재무상태표(부채 및 자본-은행계정)",
                    "SA021": "요약손익계산서(은행계정) — 이자이익·수수료·판관비·당기순이익",
                    "SA022": "연결재무상태표(자산)",
                    "SA023": "연결재무상태표(부채 및 자본)",
                    "SA024": "연결손익계산서 — 연결기준 손익",
                    "SA029": "부문별 손익(이자부문)",
                    "SA030": "부문별 손익(수수료부문)",
                    "SA033": "부문별 손익(판매비와 관리비) — 판관비 세부",
                    "SA043": "대출금 운용(형태별 대출금)",
                    "SA044": "유형별 대출채권(업종별 기업대출금)",
                    "SA045": "유형별 대출채권(용도별 원화대출금)",
                    "SA028": "형태별 예수금",
                    "SA027": "영업규모",
                },
                "_주요경영지표": {
                    "SA014": "자본적정성 — BIS비율·Tier1·보통주자본비율",
                    "SA015": "여신건전성(여신건전성) — 고정이하여신비율",
                    "SA017": "수익성 — ROA·ROE·NIM",
                    "SA018": "유동성",
                    "SA019": "생산성",
                    "SA025": "대손충당금 및 대손상각현황",
                    "SA040": "연체율(원화대출금 기준)",
                    "SA041": "여신건전성(여신종별 고정이하여신)",
                    "SA042": "여신건전성(대손충당금 적립비율)",
                },
                "_보도자료통계": {
                    "SA048": "국내은행 수익구조 — 순이자이익·비이자이익",
                    "SA049": "원화자금조달",
                    "SA050": "원화자금운용",
                    "SA051": "원화대출 부문별 대출채권",
                    "SA052": "원화대출 부문별 연체율",
                    "SA053": "BIS비율 — BIS·Tier1·보통주자본비율 분기별",
                    "SA054": "부실채권비율(NPL)",
                },
            },
            "비은행_신탁(lrg_div=B)": {
                "_note": "신탁회사(은행·증권·보험 계열) 신탁계정 통계",
                "_은행신탁": {
                    "SB005": "수탁고(은행)",
                    "SB013": "재무상태표-신탁계정(은행)",
                    "SB016": "손익계산서-신탁계정(은행)",
                    "SB010": "특정금전수탁현황(은행, 14.03이후)",
                    "SB019": "금전신탁 자금조달(은행)",
                    "SB022": "금전신탁 자금운용(은행)",
                },
                "_증권신탁": {
                    "SB006": "수탁고(증권)",
                    "SB014": "재무상태표-신탁계정(증권)",
                    "SB017": "손익계산서-신탁계정(증권)",
                    "SB011": "특정금전수탁현황(증권, 14.03이후)",
                },
            },
            "여신전문금융사_카드(lrg_div=C)": {
                "_note": "카드·캐피탈·할부금융·리스사 통계 (FISIS API에서 lrg_div=C)",
                "_재무현황": {
                    "SC103": "요약재무상태표(자산)(08.03이후)",
                    "SC104": "요약재무상태표(부채 및 자본)(08.03이후)",
                    "SC118": "요약손익계산서(08.03이후)",
                    "SC218": "요약손익계산서(18.12이후) — 최신 기준",
                    "SC111": "부문별 손익(이자부문)",
                    "SC112": "부문별 손익(카드부문)",
                    "SC113": "부문별 손익(할부금융부문)",
                    "SC114": "부문별 손익(리스부문)",
                    "SC116": "부문별 자산현황(판매비와 관리비)",
                    "SC107": "부문별 자산현황(카드자산)",
                    "SC108": "부문별 자산현황(할부금융자산)",
                    "SC109": "부문별 자산현황(리스자산)",
                },
                "_주요경영지표": {
                    "SC007": "자본적정성",
                    "SC008": "여신건전성",
                    "SC009": "수익성",
                    "SC010": "유동성",
                    "SC117": "여신건전성(연체채권비율)",
                },
                "_보도자료통계": {
                    "SC120": "전업카드사 순이익 추이",
                    "SC121": "카드수 및 회원수",
                    "SC122": "카드 이용실적",
                    "SC123": "카드대출 이용실적",
                    "SC125": "수익성",
                    "SC126": "주요재무현황",
                    "SC127": "조정자기자본비율",
                },
                "_카드영업": {
                    "SC013": "카드 영업활동(신용카드이용실적)",
                    "SC119": "부문별 영업실적",
                },
            },
            "금융투자(lrg_div=D)": {
                "_note": "증권사·자산운용사 통계",
                "_재무현황": {
                    "SD103": "요약재무상태표(자산)(11.06이후)",
                    "SD104": "요약재무상태표(부채 및 자본)(11.06이후)",
                    "SD107": "요약손익계산서(11.06이후) — 증권사·자산운용사",
                    "SD012": "부문별 손익(수수료)",
                    "SD013": "부문별 손익(증권평가 및 처분)",
                    "SD014": "부문별 손익(파생상품)",
                    "SD015": "부문별 손익(이자)",
                    "SD017": "부문별 손익(판매비와 관리비)",
                    "SD018": "자산현황(대출채권)",
                    "SD021": "자산현황(유가증권)",
                },
                "_주요경영지표": {
                    "SD008": "자본적정성",
                    "SD009": "여신건전성",
                    "SD010": "수익성 — ROA·ROE",
                    "SD011": "유동성",
                },
            },
        },
    },
    "planning_framework": {
        "step1_data_needs": "질문에서 필요한 데이터 항목 구체적으로 파악 (어떤 재무항목? 어떤 기간? 어떤 대상?)",
        "step2_source_selection": "DART/FISIS의 data_types·granularity·strength·weakness·cost를 비교하여 최적 소스 결정",
        "step3_cost_estimation": "예상 API 호출 횟수 산정 (DART: 기업수×호출, FISIS: 1~2회)",
        "step4_tool_sequence": "최소 호출로 데이터를 수집하는 구체적 도구 호출 순서 수립",
        "step5_fallback": "1차 소스에서 데이터가 부족하면 다른 소스로 보완할 계획 포함",
    },
    "key_stat_codes_by_sector": {
        "note": (
            "업권별 분석 시 아래 코드를 우선 활용. "
            "2개 이상 조회 시 반드시 fisis_get_multi_statistics 사용."
        ),
        "업권_전체_집계_vs_개별_기관": (
            "업권 전체 집계: lrg_div만 지정(finance_cd 생략) → 해당 업권 전체 합산 데이터 반환. "
            "개별 기관 조회: finance_cd를 지정(fisis_list_companies로 먼저 확인). "
            "업권 전체 집계 시 fisis_list_statistics에서 '합계' 또는 '전체' 포함 코드를 우선 탐색."
        ),
        "은행(lrg_div=A)": {
            "수익성_ROA_ROE_NIM": "SA017",
            "BIS비율_Tier1_보통주자본": "SA053",
            "여신건전성_고정이하여신비율": "SA015",
            "연체율_원화대출": "SA040",
            "부실채권비율_NPL": "SA054",
            "대손충당금": "SA025",
        },
        "여신전문_카드(lrg_div=C)": {
            "요약손익계산서_최신(18.12이후)": "SC218",
            "여신건전성": "SC008",
            "연체채권비율": "SC117",
            "수익성": "SC009",
            "자본적정성": "SC007",
        },
        "금융투자(lrg_div=D)": {
            "수익성_ROA_ROE": "SD010",
            "여신건전성": "SD009",
            "자본적정성": "SD008",
        },
        "업권_공통_연체율_코드": {
            "은행": "SA040",
            "여신전문(카드_캐피탈_리스_할부)": "SC117",
            "금융투자": "SD009",
        },
    },
    "dart_fisis_cross_analysis": {
        "description": "DART 개별 기업 분석 시 FISIS 병행 조회가 필요한 지표 목록",
        "dart_to_fisis_bridge_flow": (
            "1. dart_to_fisis_bridge(corp_code) → is_financial 여부 및 fisis_lrg_div 확인. "
            "2. fisis_list_companies(lrg_div) → finance_cd 확인. "
            "3. fisis_get_multi_statistics([코드목록], finance_cd=...) → 개별 기관 통계 조회."
        ),
        "indicators_requiring_fisis": [
            "BIS비율·Tier1 (은행: SA053, 카드·여전: SC007)",
            "NPL·부실채권비율 (은행: SA054)",
            "연체율 (은행: SA040, 여전: SC117)",
            "NIM·ROA·ROE (은행: SA017, 금융투자: SD010)",
            "카드 요약손익 영업수익 구성비 (SC218 — DART 계정보다 업권 표준 상세)",
        ],
        "non_interest_income_accounts_dart": {
            "description": "비이자이익 완전 포착을 위한 DART 재무제표 계정 목록",
            "accounts": [
                "수수료이익 (fee income)",
                "트레이딩손익 (trading P&L — 유가증권평가·처분·파생상품 포함)",
                "보험손익 (insurance P&L)",
                "신탁보수수익 (trust fee)",
                "기타영업손익 (other operating income/expense)",
                "외환·외화환산손익",
            ],
            "tip": "dart_full_financial_statements(sj_div='IS')로 전체 손익계산서 조회 후 해당 계정 합산",
        },
    },
    "dart_extraction_limits": {
        "description": "DART API로 직접 추출 불가한 핵심 지표 및 FISIS/공시 대안 경로 (ACT-002)",
        "rule": "아래 지표가 필요한 시나리오에서 DART 주요계정 조회 후 빈 결과가 나오면 즉시 FISIS 또는 dart_screen_report로 대안 조회를 시도하라. 추정치 사용 시 반드시 data_gaps에 기록.",
        "indicators": {
            "CET1_비율": {
                "reason": "DART 주요계정 API에 구조화 필드 없음 (감독회계 기준)",
                "fisis_alt": "SA014 (은행 자본적정성 — BIS비율·Tier1·보통주자본비율)",
                "dart_alt": "dart_business_report(report_type='new_capital_securities') 또는 dart_screen_report로 BIS 주석 확인",
            },
            "LCR_NSFR": {
                "reason": "유동성 규제 비율은 DART 재무제표 구조화 필드 없음",
                "fisis_alt": "SA018 (은행 유동성 통계)",
                "dart_alt": "dart_screen_report 사업보고서 유동성 섹션 또는 dart_search_disclosures",
            },
            "NIM": {
                "reason": "DART API가 NIM을 직접 계정과목으로 제공하지 않음",
                "fisis_alt": "SA017 (은행 수익성 — ROA·ROE·NIM 직접 제공, finance_cd로 개별 기관 지정)",
                "dart_alt": "dart_business_report 주요경영지표 섹션 확인",
            },
            "PF_잔액_연체율": {
                "reason": "프로젝트파이낸싱 잔액·연체율은 DART 구조화 필드 없음",
                "fisis_alt": "없음 — 사업보고서 본문 텍스트 의존",
                "dart_alt": "dart_screen_report PF 익스포저 섹션 또는 dart_search_disclosures",
            },
            "AT1_세부내역": {
                "reason": "신종자본증권 세부내역 API(new_capital_securities)가 불안정",
                "fisis_alt": "없음",
                "dart_alt": "dart_search_disclosures(pblntf_ty='C') 로 신종자본증권 발행 공시 검색",
            },
        },
    },
    "fisis_finance_cd_guide": {
        "description": "FISIS 개별 기관 조회 시 finance_cd 사용법 및 SC218 분할 조회 안내 (ACT-007)",
        "SA017_finance_cd_note": (
            "SA017(수익성 ROA·ROE·NIM) 조회 시 finance_cd를 지정하지 않으면 업권 전체 합산 반환. "
            "개별 은행 NIM 조회: fisis_list_companies(lrg_div='A')로 finance_cd 확인 후 "
            "fisis_get_statistics(stat_cd='SA017', finance_cd=<확인된 코드>) 호출."
        ),
        "SC218_split_query": (
            "SC218(요약손익계산서 18.12이후)은 응답 데이터가 커 장기 조회 시 실패할 수 있음. "
            "조회 실패 시 연도 범위를 1~2년으로 줄여 재시도하라. "
            "fisis_get_statistics가 크기 초과 오류 시 자동 연도별 분할 조회를 시도함."
        ),
        "연체채권비율_코드": {
            "은행": "SA040 (원화대출금 기준 연체율)",
            "여신전문_카드_캐피탈_리스_할부": "SC117 (여신건전성 — 연체채권비율)",
            "금융투자": "SD009 (여신건전성)",
        },
    },
    "fisis_registration_status": {
        "description": "업권별 FISIS 등록 여부 — 미등록 업권은 DART 단독 전략 적용 (ACT-009)",
        "등록_업권": ["국내은행 (lrg_div=A)", "신용카드사 (lrg_div=C)", "증권사·자산운용사 (lrg_div=D)"],
        "미등록_DART_단독": [
            "캐피탈사 (현대캐피탈·KB캐피탈·신한캐피탈 등 여신전문-리스/할부) — FISIS 교차검증 불가",
            "저축은행 (일부만 등록)",
            "상호금융 (농협·수협·신협·새마을금고)",
        ],
        "rule": "캐피탈사·저축은행 분석 시 FISIS 조회를 시도하지 말고 DART 재무제표 단독 전략을 사용하라. 이를 data_gaps에 명시.",
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
#  사업보고서 주요정보 레지스트리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# (endpoint, 한글 설명) — 모두 corp_code + bsns_year + reprt_code 파라미터
BUSINESS_REPORT_TYPES: dict[str, tuple[str, str]] = {
    # 배당/주식
    "dividend": ("alotMatter.json", "배당에 관한 사항 (주당배당금, 배당성향, 배당수익률)"),
    "stock_total": ("stockTotqySttus.json", "주식의 총수 현황 (발행주식수, 유통주식수)"),
    "treasury_stock": ("tesstkAcqsDspsSttus.json", "자기주식 취득·처분 현황"),
    # 주주
    "major_shareholder": ("hyslrSttus.json", "최대주주 현황 (지분율, 보유주식수)"),
    "shareholder_change": ("hyslrChgSttus.json", "최대주주 변동 현황"),
    "minority_shareholder": ("mrhlSttus.json", "소액주주 현황"),
    # 임원/감사
    "executive": ("exctvSttus.json", "임원 현황 (등기/미등기, 성명, 직위)"),
    "outside_director": ("outcmpnyDrctrNdChangeSttus.json", "사외이사 현황"),
    "individual_pay": ("indvdlByPay.json", "개인별 보수 현황 (5억 이상 공시 대상)"),
    "total_compensation": ("hmvAuditAllSttus.json", "이사·감사 전체 보수 현황"),
    "audit_opinion": ("accnutAdtorNmNdAdtOpinion.json", "회계감사인 및 감사의견"),
    "audit_service": ("adtServcCnclsSttus.json", "감사용역 체결 현황"),
    "non_audit_service": ("accnutAdtorNonAdtServcCnclsSttus.json", "비감사 용역 현황"),
    # 직원
    "employee": ("empSttus.json", "직원 현황 (인원수, 평균근속, 평균급여)"),
    # 자본/채권
    "capital_change": ("irdsSttus.json", "증자·감자 현황"),
    "bond_balance": ("srtpdPsndbtNrdmpBlce.json", "사채 미상환 잔액"),
    "commercial_paper": ("entrprsBillSttus.json", "기업어음 미상환 잔액"),
    "new_capital_securities": ("newCapitalSttus.json", "신종자본증권 미상환 잔액"),
    "conditional_capital": ("condCapitalSttus.json", "조건부자본증권 미상환 잔액"),
    # 투자/자금
    "other_investment": ("otrCprInvstmntSttus.json", "타법인 출자 현황"),
    "public_capital_use": ("pssrpCaptalUseDtls.json", "공모자금 사용내역"),
    "private_capital_use": ("prvsrpCaptalUseDtls.json", "사모자금 사용내역"),
}


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
        # 접미사 제거 후 재시도로 유사 기업명 후보 제공 (ACT-013)
        candidates: list = []
        for suffix in ["은행", "지주", "금융지주", "금융", "증권", "보험", "캐피탈", "카드", "투자", "자산운용"]:
            if name.endswith(suffix) and len(name) > len(suffix) + 1:
                candidates = await _dart().search_company(name[: -len(suffix)], 5)
                if candidates:
                    break
        if not candidates and len(name) > 4:
            candidates = await _dart().search_company(name[: len(name) // 2 + 1], 5)
        if candidates:
            return _json({
                "status": "not_found",
                "query": name,
                "message": f"'{name}' 검색 결과 없음. 유사 기업명 후보를 확인하고 올바른 corp_code를 선택하세요.",
                "similar_candidates": [_drop_empty(r) for r in candidates],
            })
        return f"'{name}'에 대한 검색 결과가 없습니다. 검색어를 줄이거나 다른 키워드(예: 지주·금융 상위 법인명)로 시도하세요."
    return _plan_hint([_drop_empty(r) for r in results])


@mcp.tool()
@_tool_safe
async def dart_search_companies(names: list[str], limit: int = 5) -> str:
    """여러 회사명을 한번에 병렬 검색하여 기업코드를 조회합니다.

    4대 금융지주, 경쟁사 비교 등 여러 기업을 동시에 찾아야 할 때 사용.
    dart_search_company를 N번 반복 호출하는 대신 이 도구 1번으로 해결됩니다.

    Args:
        names: 회사명 리스트 (1~20개, 예: ["KB금융", "신한지주", "하나금융지주"])
        limit: 회사명당 최대 결과 수 (기본 5, 1~20)
    """
    if not isinstance(names, list) or not names:
        raise ValueError("names는 비어있지 않은 리스트여야 합니다")
    if len(names) > 20:
        raise ValueError(f"names는 최대 20개까지 가능합니다. 받은 개수: {len(names)}")
    if not (1 <= limit <= 20):
        raise ValueError(f"limit은 1~20 사이여야 합니다. 받은 값: {limit}")
    for i, name in enumerate(names):
        if not name or not name.strip():
            raise ValueError(f"names[{i}]는 비어있을 수 없습니다")

    client = _dart()

    async def _search(name: str) -> dict:
        results = await client.search_company(name.strip(), limit)
        return {
            "query": name.strip(),
            "results": [_drop_empty(r) for r in results] if results else [],
        }

    raw = await asyncio.gather(*[_search(n) for n in names], return_exceptions=True)
    all_results = []
    for name, item in zip(names, raw):
        if isinstance(item, Exception):
            all_results.append({"query": name.strip(), "error": str(item)})
        else:
            all_results.append(item)
    return _plan_hint(all_results)


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
    """DART에서 단일회사의 주요 재무계정을 조회합니다.

    자산총계, 부채총계, 자본총계, 매출액, 영업이익, 당기순이익 등 핵심 지표를
    당기/전기/전전기 비교 형태로 반환합니다.

    Args:
        corp_code: 기업코드 (8자리)
        bsns_year: 사업연도 (YYYY, 예: "2024")
        reprt_code: 보고서코드 (11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기)
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
    """DART에서 단일회사의 전체 재무제표를 조회합니다.

    주요계정보다 상세한 데이터가 필요할 때 사용하세요.
    **sj_div 파라미터로 특정 재무표만 추출하면 토큰을 1/4~1/5 수준으로 절약할 수 있습니다.**

    연결(CFS) 요청인데 결과가 비면 자동으로 개별(OFS)로 폴백합니다
    (소규모 기업은 연결재무제표를 작성하지 않는 경우가 있음).

    Args:
        corp_code: 기업코드 (8자리)
        bsns_year: 사업연도 (YYYY)
        reprt_code: 보고서코드 (11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기)
        fs_div: 재무제표구분 (CFS=연결, OFS=개별)
        sj_div: 특정 표만 필터 (BS=재무상태표, IS=손익계산서, CIS=포괄손익, CF=현금흐름, SCE=자본변동, 비워두면 전체)
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
        if not items:
            # OFS도 비어있으면 대안 도구 안내
            return _json({
                "status": "empty",
                "note": "CFS 및 OFS 데이터 모두 없습니다.",
                "fallback_tools": ["dart_financial_statements", "dart_screen_report"],
                "list": [],
            })
        # LLM에게 폴백 사실을 알림
        return _json({"note": "CFS 데이터 없음 - OFS(개별재무제표)로 폴백", "list": items})

    if not items:
        # CFS 직접 조회 결과도 비어있으면 대안 도구 안내
        return _json({
            "status": "empty",
            "note": "데이터가 없습니다.",
            "fallback_tools": ["dart_financial_statements", "dart_screen_report"],
            "list": [],
        })
    return _json(items)


@mcp.tool()
@_tool_safe
async def dart_multi_company_financials(
    corp_codes: list[str],
    bsns_year: str,
    reprt_code: str = "11011",
) -> str:
    """여러 회사의 주요 재무계정을 한번에 비교 조회합니다.

    경쟁사 비교, 동종업계 분석에 활용.
    20개 이하: DART API 단일 호출. 20개 초과: 자동으로 20개씩 분할 후 병렬 호출.
    최대 100개 기업까지 지원합니다.

    Args:
        corp_codes: 기업코드 리스트 (1~100개)
        bsns_year: 사업연도 (YYYY)
        reprt_code: 보고서코드 (11011=사업보고서 등)
    """
    v.validate_corp_codes_list(corp_codes, max_count=100)
    v.validate_year(bsns_year)
    v.validate_report_code(reprt_code)

    client = _dart()
    # 20개씩 청킹 (DART API 제한)
    chunks = [corp_codes[i:i + 20] for i in range(0, len(corp_codes), 20)]

    async def _fetch_chunk(codes: list[str]) -> list[dict]:
        data = await client.get_multi_company_financials(codes, bsns_year, reprt_code)
        return [_compact_fin_row(r) for r in data.get("list", []) or []]

    if len(chunks) == 1:
        items = await _fetch_chunk(chunks[0])
    else:
        raw = await asyncio.gather(*[_fetch_chunk(c) for c in chunks], return_exceptions=True)
        items = []
        for i, result in enumerate(raw):
            if isinstance(result, Exception):
                logger.warning("multi_company chunk %d failed: %s", i, result)
            else:
                items.extend(result)

    return _plan_hint(items)


@mcp.tool()
@_tool_safe
async def dart_financial_statements_multi_year(
    corp_code: str,
    start_year: str,
    end_year: str,
    reprt_code: str = "11011",
) -> str:
    """DART에서 단일회사의 주요 재무계정을 여러 연도에 걸쳐 병렬 조회합니다.

    연도별 추이 분석에 최적. 개별 호출 대비 대기 시간을 크게 단축합니다.
    각 연도 데이터는 1시간 캐시 적용.

    Args:
        corp_code: 기업코드 (8자리)
        start_year: 시작 연도 (YYYY, 예: "2020")
        end_year: 종료 연도 (YYYY, 예: "2024")
        reprt_code: 보고서코드 (11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기)
    """
    v.validate_corp_code(corp_code)
    v.validate_year(start_year)
    v.validate_year(end_year)
    v.validate_report_code(reprt_code)

    sy, ey = int(start_year), int(end_year)
    if sy > ey:
        raise ValueError(f"start_year({start_year})는 end_year({end_year}) 이하여야 합니다")
    if ey - sy > 10:
        raise ValueError("최대 11개 연도(10년 범위)까지 조회 가능합니다")

    client = _dart()
    years = [str(y) for y in range(sy, ey + 1)]

    async def _fetch(year: str) -> dict:
        data = await client.get_financial_statements(corp_code, year, reprt_code)
        rows = [_compact_fin_row(r) for r in data.get("list", []) or []]
        return {"year": year, "data": rows}

    raw = await asyncio.gather(*[_fetch(y) for y in years], return_exceptions=True)
    results = []
    for year, item in zip(years, raw):
        if isinstance(item, Exception):
            results.append({"year": year, "error": str(item)})
        else:
            results.append(item)
    return _plan_hint(results)


@mcp.tool()
@_tool_safe
async def dart_business_report(
    corp_code: str,
    bsns_year: str,
    report_type: str,
    reprt_code: str = "11011",
) -> str:
    """DART 사업보고서 주요정보를 조회합니다 (배당, 임원, 직원, 주주 등 22종).

    report_type으로 조회할 항목을 지정합니다. 사용 가능한 report_type:

    [배당/주식]
    - dividend: 배당에 관한 사항 (주당배당금, 배당성향, 배당수익률)
    - stock_total: 주식의 총수 현황 (발행주식수, 유통주식수)
    - treasury_stock: 자기주식 취득·처분 현황

    [주주]
    - major_shareholder: 최대주주 현황 (지분율, 보유주식수)
    - shareholder_change: 최대주주 변동 현황
    - minority_shareholder: 소액주주 현황

    [임원/감사]
    - executive: 임원 현황 (등기/미등기, 성명, 직위)
    - outside_director: 사외이사 현황
    - individual_pay: 개인별 보수 현황 (5억 이상 공시 대상)
    - total_compensation: 이사·감사 전체 보수 현황
    - audit_opinion: 회계감사인 및 감사의견
    - audit_service: 감사용역 체결 현황
    - non_audit_service: 비감사 용역 현황

    [직원]
    - employee: 직원 현황 (인원수, 평균근속, 평균급여)

    [자본/채권]
    - capital_change: 증자·감자 현황
    - bond_balance: 사채 미상환 잔액
    - commercial_paper: 기업어음 미상환 잔액
    - new_capital_securities: 신종자본증권 미상환 잔액
    - conditional_capital: 조건부자본증권 미상환 잔액

    [투자/자금]
    - other_investment: 타법인 출자 현황
    - public_capital_use: 공모자금 사용내역
    - private_capital_use: 사모자금 사용내역

    Args:
        corp_code: 기업코드 (8자리, dart_search_company로 조회)
        bsns_year: 사업연도 (YYYY, 예: "2023")
        report_type: 조회 항목 (위 목록 참조, 예: "dividend")
        reprt_code: 보고서코드 (11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기)
    """
    v.validate_corp_code(corp_code)
    v.validate_year(bsns_year)
    v.validate_report_code(reprt_code)

    if report_type not in BUSINESS_REPORT_TYPES:
        valid_types = ", ".join(sorted(BUSINESS_REPORT_TYPES.keys()))
        raise ValueError(
            f"report_type '{report_type}'은(는) 지원되지 않습니다. "
            f"사용 가능: {valid_types}"
        )

    endpoint, description = BUSINESS_REPORT_TYPES[report_type]

    try:
        data = await _dart().get_business_report(endpoint, corp_code, bsns_year, reprt_code)
    except (RuntimeError, Exception) as e:
        # new_capital_securities API 호출 실패 시 대안 도구 안내
        if report_type == "new_capital_securities":
            return _json({
                "report_type": report_type,
                "description": description,
                "error": str(e),
                "fallback_suggestion": (
                    "API 호출에 실패했습니다. "
                    "dart_search_disclosures에서 '신종자본증권 발행' 키워드로 공시를 검색하세요. "
                    "예: dart_search_disclosures(corp_code=corp_code, pblntf_ty='C')"
                ),
            })
        raise

    items = data.get("list", []) or []
    compacted = [_drop_empty(_strip_dart_meta(item)) if isinstance(item, dict) else item for item in items]

    if not compacted:
        return _json({
            "report_type": report_type,
            "description": description,
            "note": f"{bsns_year}년 {description} 데이터가 없습니다.",
            "list": [],
        })

    return _json({
        "report_type": report_type,
        "description": description,
        "list": compacted,
    })


@mcp.tool()
@_tool_safe
async def dart_list_listed_companies() -> str:
    """DART에 등록된 전체 상장기업 목록을 반환합니다.

    API 호출 없음 — 기업코드 캐시(약 90,000건)에서 stock_code가 있는 기업만 필터.
    약 2,500~3,000개 상장기업의 corp_code, corp_name, stock_code를 반환합니다.

    이 목록을 기반으로 dart_business_report, dart_multi_company_financials 등
    후속 도구를 호출하여 스크리닝 분석을 수행할 수 있습니다.

    예시 워크플로우:
    1. dart_list_listed_companies → 전체 상장기업 목록 확보
    2. dart_multi_company_financials로 재무 데이터 수집 (최대 100개씩)
    3. dart_business_report(report_type="dividend")로 배당 데이터 조회
    """
    companies = await _dart().list_listed_companies()
    return _json({
        "total": len(companies),
        "list": [_drop_empty(c) for c in companies],
    })


@mcp.tool()
@_tool_safe
async def dart_screen_report(
    corp_codes: list[str],
    bsns_year: str,
    report_type: str,
    reprt_code: str = "11011",
) -> str:
    """여러 기업의 사업보고서 주요정보를 한번에 병렬 조회하여 스크리닝합니다.

    report_type으로 22종 중 원하는 항목을 지정. dart_business_report와 동일한
    report_type을 사용하되, 여러 기업에 대해 병렬 실행합니다.

    활용 예시:
    - "배당성향 50% 이상 기업" → report_type="dividend"
    - "직원 평균연봉 1억 이상 기업" → report_type="employee"
    - "최대주주 지분율 변동 기업" → report_type="shareholder_change"
    - "감사의견 비적정 기업" → report_type="audit_opinion"
    - "자기주식 취득 기업" → report_type="treasury_stock"
    - "임원 개인보수 5억 이상" → report_type="individual_pay"
    - "최근 증자/감자 기업" → report_type="capital_change"

    최대 50개 기업까지, 부분 실패 시 성공 데이터는 보존.

    Args:
        corp_codes: 기업코드 리스트 (1~50개)
        bsns_year: 사업연도 (YYYY)
        report_type: 조회 항목 (dart_business_report과 동일한 22종)
        reprt_code: 보고서코드 (11011=사업보고서)
    """
    v.validate_corp_codes_list(corp_codes, max_count=50)
    v.validate_year(bsns_year)
    v.validate_report_code(reprt_code)

    if report_type not in BUSINESS_REPORT_TYPES:
        valid_types = ", ".join(sorted(BUSINESS_REPORT_TYPES.keys()))
        raise ValueError(
            f"report_type '{report_type}'은(는) 지원되지 않습니다. "
            f"사용 가능: {valid_types}"
        )

    endpoint, description = BUSINESS_REPORT_TYPES[report_type]
    client = _dart()

    async def _fetch(code: str) -> dict:
        data = await client.get_business_report(
            endpoint, code, bsns_year, reprt_code
        )
        items = data.get("list", []) or []
        return {
            "corp_code": code,
            "data": [_drop_empty(_strip_dart_meta(r)) for r in items] if items else [],
        }

    raw = await asyncio.gather(
        *[_fetch(c) for c in corp_codes], return_exceptions=True
    )
    results = []
    for code, item in zip(corp_codes, raw):
        if isinstance(item, Exception):
            results.append({"corp_code": code, "error": str(item)})
        else:
            results.append(item)

    return _json({"report_type": report_type, "description": description, "results": results})


@mcp.tool()
@_tool_safe
async def dart_document_content(
    rcept_no: str,
    section_keyword: str = "",
    max_chars: int = 6000,
) -> str:
    """DART 공시 원문 텍스트를 조회합니다.

    dart_search_disclosures로 얻은 rcept_no를 이용해 공시 원문(HTML)을
    다운로드하고 가시 텍스트를 추출합니다.

    접근 가능한 내용:
    - 재무제표 주석 (우발채무, 특수관계자, 금융상품, 리스 등)
    - 수시공시 본문 (주요사항보고, 자기주식, 유상증자 결정 등)
    - 사업보고서 서술 내용 (사업 현황, 위험요소, MD&A)

    활용 예시:
    1. dart_search_disclosures(corp_code=..., bgn_de="20240101") → rcept_no 획득
    2. dart_document_content(rcept_no=..., section_keyword="주석") → 주석 내용 조회

    Args:
        rcept_no: 접수번호 14자리 (dart_search_disclosures 결과의 rcept_no 필드)
        section_keyword: 반환할 섹션 키워드 (예: "주석", "우발채무", "특수관계자").
                         지정하면 해당 키워드 주변 텍스트를 우선 반환합니다.
                         비워두면 문서 앞부분부터 max_chars만큼 반환합니다.
        max_chars: 최대 반환 문자 수 (100~20000, 기본 6000).
                   공시 원문은 매우 크므로 필요한 섹션만 section_keyword로 좁히세요.
    """
    v.validate_rcept_no(rcept_no)
    if not (100 <= max_chars <= 20000):
        raise ValueError(f"max_chars는 100~20000 사이여야 합니다. 받은 값: {max_chars}")

    result = await _dart().get_document_text(
        rcept_no,
        section_keyword=section_keyword.strip(),
        max_chars=max_chars,
    )
    return _json(result)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  FISIS 도구
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
@_tool_safe
async def fisis_list_statistics(
    lrg_div: str = "",
    sml_div: str = "",
) -> str:
    """FISIS에서 조회 가능한 통계목록을 검색합니다.

    어떤 통계가 있는지 확인할 때 사용. 통계코드(stat_cd)를 확인한 후
    fisis_get_statistics 로 실제 데이터를 조회하세요.

    Args:
        lrg_div: 대분류 (A=은행, B=비은행, C=보험, D=금융투자, 비워두면 전체)
        sml_div: 소분류 (비워두면 전체)
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
    """FISIS에서 금융통계 데이터를 조회합니다.

    **단일 코드 1회 조회 전용. 복수 코드 조회 시 반드시 fisis_get_multi_statistics를 사용하세요.**

    stat_cd는 fisis_list_statistics 결과의 list_no 필드값입니다.
    plan_data_query의 common_stat_codes에 주요 코드가 정리되어 있으니 먼저 확인하세요.

    Args:
        stat_cd: 통계코드 (fisis_list_statistics 결과의 list_no 필드값, 예: "SA021")
        strt_yymm: 조회 시작월 (YYYYMM, 예: "202401")
        end_yymm: 조회 종료월 (YYYYMM, 예: "202412")
        finance_cd: 금융회사코드 (비워두면 전체)
        lrg_div: 대분류 코드 (A=은행, B=비은행, C=보험, D=금융투자)
        sml_div: 소분류 코드
        term: 조회 주기 — Q(분기, 기본값) 또는 Y(연간)
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
    return _json(_fisis_compact_list(data))


@mcp.tool()
@_tool_safe
async def fisis_get_multi_statistics(
    stat_codes: list[str],
    strt_yymm: str,
    end_yymm: str,
    finance_cd: str = "",
    lrg_div: str = "",
    sml_div: str = "",
    term: str = "Q",
) -> str:
    """여러 FISIS 통계코드를 한번에 병렬 조회합니다.

    개별 fisis_get_statistics를 반복 호출하는 대신 이 도구를 사용하면
    asyncio.gather로 병렬 실행되어 대기 시간이 크게 단축됩니다.

    예: 은행 BIS비율(SA053) + NPL비율(SA054) + 수익성(SA017)을 한번에 조회.

    Args:
        stat_codes: 통계코드 리스트 (1~10개, 예: ["SA053", "SA054", "SA017"])
        strt_yymm: 조회 시작월 (YYYYMM)
        end_yymm: 조회 종료월 (YYYYMM)
        finance_cd: 금융회사코드 (비워두면 전체)
        lrg_div: 대분류 코드 (A=은행, B=비은행, C=보험, D=금융투자)
        sml_div: 소분류 코드
        term: 조회 주기 — Q(분기) 또는 Y(연간)
    """
    if not isinstance(stat_codes, list) or not stat_codes:
        raise ValueError("stat_codes는 비어있지 않은 리스트여야 합니다")
    if len(stat_codes) > 10:
        raise ValueError(f"stat_codes는 최대 10개까지 가능합니다. 받은 개수: {len(stat_codes)}")
    v.validate_yyyymm(strt_yymm, "strt_yymm")
    v.validate_yyyymm(end_yymm, "end_yymm")
    if strt_yymm > end_yymm:
        raise ValueError(f"strt_yymm({strt_yymm})은 end_yymm({end_yymm}) 이하여야 합니다")
    if term not in ("Q", "Y"):
        raise ValueError("term은 'Q'(분기) 또는 'Y'(연간)만 허용됩니다.")

    client = _fisis()

    async def _fetch(code: str) -> dict:
        data = await client.get_statistics(
            code, strt_yymm, end_yymm, finance_cd, lrg_div, sml_div, term
        )
        return {"stat_cd": code, "data": _fisis_compact_list(data)}

    raw = await asyncio.gather(*[_fetch(c) for c in stat_codes], return_exceptions=True)
    results = []
    for code, item in zip(stat_codes, raw):
        if isinstance(item, Exception):
            results.append({"stat_cd": code, "error": str(item)})
        else:
            results.append(item)
    return _json(results)


@mcp.tool()
@_tool_safe
async def fisis_list_companies(
    lrg_div: str = "",
    sml_div: str = "",
    finance_cd: str = "",
) -> str:
    """FISIS에 등록된 금융회사 목록을 조회합니다.

    특정 권역의 회사 목록과 finance_cd 를 확인할 때 사용.

    Args:
        lrg_div: 대분류 (A=은행, B=비은행, C=보험, D=금융투자)
        sml_div: 소분류
        finance_cd: 금융회사코드 (특정 회사만 조회 시)
    """
    data = await _fisis().list_companies(lrg_div, sml_div, finance_cd)
    return _json(_fisis_extract_list(data))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DART ↔ FISIS 연결 도구
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# DART 업종코드 → FISIS 대분류 매핑 (금감원 업종 분류 기반)
_INDUTY_TO_FISIS_DIV: dict[str, tuple[str, str]] = {
    # 은행 업종코드
    "은행": ("A", "은행"),
    "641": ("A", "은행"),
    "6411": ("A", "은행"),
    "6412": ("A", "은행"),
    # 금융투자 (증권, 자산운용)
    "증권": ("D", "금융투자"),
    "661": ("D", "금융투자"),
    "6611": ("D", "금융투자"),
    "6612": ("D", "금융투자"),
    "자산운용": ("D", "금융투자"),
    # 보험
    "보험": ("C", "보험"),
    "651": ("C", "보험"),
    "6511": ("C", "보험"),
    "6512": ("C", "보험"),
    "생명보험": ("C", "보험"),
    "손해보험": ("C", "보험"),
    # 여신전문 (카드, 캐피탈, 리스, 할부)
    "카드": ("C", "여신전문금융"),
    "리스": ("C", "여신전문금융"),
    "할부": ("C", "여신전문금융"),
    "캐피탈": ("C", "여신전문금융"),
    "649": ("C", "여신전문금융"),
    "6491": ("C", "여신전문금융"),
    "6492": ("C", "여신전문금융"),
    # 신탁
    "신탁": ("B", "비은행"),
}


@mcp.tool()
@_tool_safe
async def dart_to_fisis_bridge(corp_code: str) -> str:
    """DART 기업이 금융기관인지 판별하고, FISIS 대분류 코드를 안내합니다.

    DART(개별 기업)과 FISIS(업권 통계)를 교차 분석할 때 사용.
    기업개황의 업종 정보를 분석하여 해당 기업이 어떤 FISIS 권역에 속하는지 알려줍니다.

    Args:
        corp_code: 기업코드 (8자리, dart_search_company로 조회)
    """
    v.validate_corp_code(corp_code)
    data = await _dart().get_company_overview(corp_code)

    corp_name = data.get("corp_name", "")
    induty_code = data.get("induty_code", "")
    corp_cls = data.get("corp_cls", "")

    # 업종코드로 FISIS 대분류 매핑 시도
    fisis_div = None
    fisis_label = None
    matched_key = None

    # 업종코드 직접 매칭 (상위→하위 순)
    for key in (induty_code, induty_code[:3] if len(induty_code) >= 3 else ""):
        if key and key in _INDUTY_TO_FISIS_DIV:
            fisis_div, fisis_label = _INDUTY_TO_FISIS_DIV[key]
            matched_key = key
            break

    # 회사명 키워드 매칭 (fallback)
    if not fisis_div:
        for keyword, (div, label) in _INDUTY_TO_FISIS_DIV.items():
            if not keyword.isdigit() and keyword in corp_name:
                fisis_div, fisis_label = div, label
                matched_key = keyword
                break

    result: dict[str, Any] = {
        "corp_code": corp_code,
        "corp_name": corp_name,
        "induty_code": induty_code,
        "corp_cls": corp_cls,
    }

    if fisis_div:
        result.update({
            "is_financial": True,
            "fisis_lrg_div": fisis_div,
            "fisis_sector": fisis_label,
            "matched_by": matched_key,
            "next_step": (
                f"FISIS에서 {fisis_label} 업권 통계를 조회하려면 "
                f"lrg_div='{fisis_div}'를 사용하세요. "
                f"개별 기관 통계는 fisis_list_companies(lrg_div='{fisis_div}')로 "
                f"finance_cd를 먼저 확인하세요."
            ),
        })
    else:
        result.update({
            "is_financial": False,
            "note": (
                f"'{corp_name}'은(는) 금융기관으로 분류되지 않았습니다 "
                f"(업종코드: {induty_code}). FISIS 통계 대상이 아닐 수 있습니다. "
                f"DART 재무제표를 활용하세요."
            ),
        })

    return _json(result)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  운영/진단 도구
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
@_tool_safe
async def dart_quota_status() -> str:
    """DART API 일일 요청 quota 및 캐시 효율 현황을 조회합니다.

    DART OpenAPI는 기본 20,000건/일 한도가 있습니다.
    오늘 사용량, 남은 quota, 최근 7일 이력, 캐시 hit/miss 통계를 반환합니다.

    ⚠️ 주의: 실제 네트워크 호출만 카운트하며, 캐시 hit은 제외됩니다.
    여러 프로세스가 동시에 사용하면 실측보다 낮게 집계될 수 있습니다.
    본격적인 배치 작업 전에 확인하세요.
    """
    dart = _dart()
    status = dart.quota.status()
    # 경고 메시지 포함
    if status["near_limit"]:
        status["warning"] = (
            f"⚠️ 일일 한도의 {status['usage_pct']}% 사용 중. "
            f"남은 {status['remaining']}건 이후 요청은 실패할 수 있습니다."
        )
    # 캐시 효율 통계
    status["cache"] = {
        "dart_response": dart._response_cache.stats(),
        "fisis_response": _fisis()._response_cache.stats(),
    }
    return _json(status)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  서버 실행
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def _shutdown() -> None:
    """내부 httpx 클라이언트들을 안전하게 종료."""
    tasks = []
    if _dart.cache_info().currsize > 0:
        tasks.append(_dart().aclose())
    if _fisis.cache_info().currsize > 0:
        tasks.append(_fisis().aclose())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("HTTP 클라이언트 종료 완료")


def main() -> None:
    """MCP 서버를 stdio 모드로 시작합니다."""
    import atexit

    def _sync_shutdown() -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_shutdown())
            else:
                loop.run_until_complete(_shutdown())
        except Exception:
            pass  # 종료 시 에러는 무시

    atexit.register(_sync_shutdown)
    mcp.run()


if __name__ == "__main__":
    main()
