"""_validators 모듈 단위 테스트."""

import pytest

from financial_data_mcp import _validators as v


# ── corp_code ────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["00126380", "00000001", "99999999"])
def test_corp_code_valid(value):
    v.validate_corp_code(value)


@pytest.mark.parametrize(
    "value",
    ["", "1234567", "123456789", "삼성전자", "0012638a", None],
)
def test_corp_code_invalid(value):
    with pytest.raises(ValueError, match="corp_code"):
        v.validate_corp_code(value)


# ── year ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["2024", "1999", "2000", "2099"])
def test_year_valid(value):
    v.validate_year(value)


@pytest.mark.parametrize("value", ["24", "202", "20245", "abcd", ""])
def test_year_invalid(value):
    with pytest.raises(ValueError):
        v.validate_year(value)


# ── yyyymmdd ─────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["20240101", "20241231", "20250228"])
def test_yyyymmdd_valid(value):
    v.validate_yyyymmdd(value, "test", allow_empty=False)


@pytest.mark.parametrize(
    "value",
    ["2024-01-01", "20241301", "20240132", "2024010", "abcd1234"],
)
def test_yyyymmdd_invalid(value):
    with pytest.raises(ValueError):
        v.validate_yyyymmdd(value, "test", allow_empty=False)


def test_yyyymmdd_empty_allowed():
    v.validate_yyyymmdd("", "test", allow_empty=True)


def test_yyyymmdd_empty_not_allowed():
    with pytest.raises(ValueError):
        v.validate_yyyymmdd("", "test", allow_empty=False)


# ── yyyymm ───────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["202401", "202412", "199901"])
def test_yyyymm_valid(value):
    v.validate_yyyymm(value, "test")


@pytest.mark.parametrize("value", ["2024", "202413", "202400", "abcdef"])
def test_yyyymm_invalid(value):
    with pytest.raises(ValueError):
        v.validate_yyyymm(value, "test")


# ── report_code ──────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["11011", "11012", "11013", "11014"])
def test_report_code_valid(value):
    v.validate_report_code(value)


@pytest.mark.parametrize("value", ["11015", "1101", "", "annual"])
def test_report_code_invalid(value):
    with pytest.raises(ValueError):
        v.validate_report_code(value)


# ── corp_cls ─────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["", "Y", "K", "N", "E"])
def test_corp_cls_valid(value):
    v.validate_corp_cls(value)


@pytest.mark.parametrize("value", ["x", "유가"])
def test_corp_cls_invalid(value):
    with pytest.raises(ValueError):
        v.validate_corp_cls(value)


# ── fs_div / sj_div ──────────────────────────────────────────────


@pytest.mark.parametrize("value", ["CFS", "OFS"])
def test_fs_div_valid(value):
    v.validate_fs_div(value)


@pytest.mark.parametrize("value", ["", "cfs", "X"])
def test_fs_div_invalid(value):
    with pytest.raises(ValueError):
        v.validate_fs_div(value)


@pytest.mark.parametrize("value", ["", "BS", "IS", "CIS", "CF", "SCE"])
def test_sj_div_valid(value):
    v.validate_sj_div(value)


@pytest.mark.parametrize("value", ["bs", "XX", "손익"])
def test_sj_div_invalid(value):
    with pytest.raises(ValueError):
        v.validate_sj_div(value)


# ── corp_codes list ──────────────────────────────────────────────


def test_corp_codes_list_valid():
    v.validate_corp_codes_list(["00126380", "00401731"])


def test_corp_codes_list_empty():
    with pytest.raises(ValueError, match="비어있지"):
        v.validate_corp_codes_list([])


def test_corp_codes_list_not_list():
    with pytest.raises(ValueError):
        v.validate_corp_codes_list("not a list")  # type: ignore


def test_corp_codes_list_too_many():
    codes = [f"{i:08d}" for i in range(25)]
    with pytest.raises(ValueError, match="최대 20개"):
        v.validate_corp_codes_list(codes)


def test_corp_codes_list_invalid_item():
    with pytest.raises(ValueError, match=r"corp_codes\[1\]"):
        v.validate_corp_codes_list(["00126380", "invalid"])
