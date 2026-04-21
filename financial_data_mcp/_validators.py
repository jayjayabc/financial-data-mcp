"""입력값 검증 유틸리티.

도구 호출 전 검증하여 API 호출 낭비를 방지합니다.
검증 실패 시 ValueError를 발생시킵니다 (server.py 에서 friendly 메시지로 변환).
"""

from __future__ import annotations

import re

_CORP_CODE_RE = re.compile(r"^\d{8}$")
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_RCEPT_NO_RE = re.compile(r"^\d{14}$")
_RCEPT_NO_RE = re.compile(r"^\d{14}$")
_YYYYMMDD_RE = re.compile(r"^(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$")
_YYYYMM_RE = re.compile(r"^(19|20)\d{2}(0[1-9]|1[0-2])$")

VALID_REPORT_CODES = frozenset({"11011", "11012", "11013", "11014"})
VALID_CORP_CLASS = frozenset({"", "Y", "K", "N", "E"})
VALID_FS_DIV = frozenset({"CFS", "OFS"})
VALID_SJ_DIV = frozenset({"", "BS", "IS", "CIS", "CF", "SCE"})


def validate_corp_code(value: str, field: str = "corp_code") -> None:
    """8자리 숫자 확인."""
    if not _CORP_CODE_RE.match(value or ""):
        raise ValueError(
            f"{field}는 8자리 숫자여야 합니다. 받은 값: {value!r}. "
            f"dart_search_company 로 먼저 조회하세요."
        )


def validate_year(value: str, field: str = "bsns_year") -> None:
    """4자리 연도 (1900~2099)."""
    if not _YEAR_RE.match(value or ""):
        raise ValueError(f"{field}는 4자리 연도(YYYY)여야 합니다. 받은 값: {value!r}")


def validate_yyyymmdd(value: str, field: str, *, allow_empty: bool = True) -> None:
    """YYYYMMDD 형식."""
    if not value:
        if allow_empty:
            return
        raise ValueError(f"{field}는 필수입니다 (YYYYMMDD)")
    if not _YYYYMMDD_RE.match(value):
        raise ValueError(f"{field}는 YYYYMMDD 형식이어야 합니다. 받은 값: {value!r}")


def validate_yyyymm(value: str, field: str) -> None:
    """YYYYMM 형식."""
    if not _YYYYMM_RE.match(value or ""):
        raise ValueError(f"{field}는 YYYYMM 형식이어야 합니다. 받은 값: {value!r}")


def validate_report_code(value: str) -> None:
    if value not in VALID_REPORT_CODES:
        raise ValueError(
            f"reprt_code는 {sorted(VALID_REPORT_CODES)} 중 하나여야 합니다. "
            f"받은 값: {value!r}. (11011=사업, 11012=반기, 11013=1Q, 11014=3Q)"
        )


def validate_corp_cls(value: str) -> None:
    if value not in VALID_CORP_CLASS:
        raise ValueError(
            f"corp_cls는 Y(유가)/K(코스닥)/N(코넥스)/E(기타) 중 하나이거나 "
            f"비워야 합니다. 받은 값: {value!r}"
        )


def validate_fs_div(value: str) -> None:
    if value not in VALID_FS_DIV:
        raise ValueError(
            f"fs_div는 'CFS'(연결) 또는 'OFS'(개별)이어야 합니다. 받은 값: {value!r}"
        )


def validate_sj_div(value: str) -> None:
    if value not in VALID_SJ_DIV:
        raise ValueError(
            f"sj_div는 BS/IS/CIS/CF/SCE 중 하나이거나 비워야 합니다. "
            f"받은 값: {value!r}"
        )


def validate_rcept_no(value: str, field: str = "rcept_no") -> None:
    """14자리 숫자 접수번호 확인."""
    if not _RCEPT_NO_RE.match(value or ""):
        raise ValueError(
            f"{field}는 14자리 숫자여야 합니다. 받은 값: {value!r}. "
            f"dart_search_disclosures 결과의 rcept_no 필드를 사용하세요."
        )


def validate_corp_codes_list(
    codes: list[str], field: str = "corp_codes", max_count: int = 20
) -> None:
    """corp_code 리스트 검증."""
    if not isinstance(codes, list) or not codes:
        raise ValueError(f"{field}는 비어있지 않은 리스트여야 합니다")
    if len(codes) > max_count:
        raise ValueError(
            f"{field}는 최대 {max_count}개까지 가능합니다. 받은 개수: {len(codes)}"
        )
    for idx, code in enumerate(codes):
        if not _CORP_CODE_RE.match(code or ""):
            raise ValueError(
                f"{field}[{idx}]는 8자리 숫자여야 합니다. 받은 값: {code!r}"
            )
