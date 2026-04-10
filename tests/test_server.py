"""server.py 단위 테스트: 응답 가공, sj_div 필터, 컴팩트 JSON 등."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from financial_data_mcp import server


# ── 순수 함수 테스트 ────────────────────────────────────────────


def test_json_is_compact():
    """indent나 공백 없이 직렬화."""
    out = server._json({"a": 1, "b": "테스트"})
    assert out == '{"a":1,"b":"테스트"}'


def test_drop_empty():
    assert server._drop_empty({"a": 1, "b": "", "c": None, "d": "x"}) == {
        "a": 1,
        "d": "x",
    }


def test_compact_fin_row_essentials_only():
    raw = {
        "rcept_no": "20240101",
        "reprt_code": "11011",
        "bsns_year": "2024",
        "corp_code": "001",
        "stock_code": "005930",
        "fs_div": "CFS",
        "fs_nm": "연결재무제표",
        "sj_div": "BS",
        "sj_nm": "재무상태표",
        "account_nm": "자산총계",
        "thstrm_amount": "100",
        "frmtrm_amount": "90",
        "bfefrmtrm_amount": "80",
        "ord": "1",
        "currency": "KRW",
    }
    compact = server._compact_fin_row(raw)

    # 불필요 필드 제거
    for removed in ("rcept_no", "reprt_code", "bsns_year", "stock_code", "fs_nm", "ord", "currency"):
        assert removed not in compact

    # 핵심 필드 유지 및 키 재명명
    assert compact["sj_div"] == "BS"
    assert compact["sj_nm"] == "재무상태표"
    assert compact["account_nm"] == "자산총계"
    assert compact["curr"] == "100"
    assert compact["prev"] == "90"
    assert compact["prev2"] == "80"


def test_compact_disclosure_essentials_only():
    raw = {
        "corp_code": "001",
        "corp_name": "삼성전자",
        "stock_code": "005930",
        "corp_cls": "Y",
        "report_nm": "사업보고서",
        "rcept_no": "20240401000123",
        "flr_nm": "삼성전자",
        "rcept_dt": "20240401",
        "rm": "정정",
    }
    compact = server._compact_disclosure(raw)
    assert "corp_cls" not in compact  # 불필요
    assert compact["rcept_no"] == "20240401000123"
    assert compact["report_nm"] == "사업보고서"


def test_fisis_extract_list_from_result():
    data = {"result": {"err_msg": "정상", "list": [{"a": 1}, {"a": 2}]}}
    assert server._fisis_extract_list(data) == [{"a": 1}, {"a": 2}]


def test_fisis_extract_list_from_top_level():
    data = {"list": [{"a": 1}]}
    assert server._fisis_extract_list(data) == [{"a": 1}]


def test_fisis_extract_list_fallback():
    """list 없으면 result 전체 반환."""
    data = {"result": {"err_msg": "정상", "other": "x"}}
    result = server._fisis_extract_list(data)
    assert result == {"err_msg": "정상", "other": "x"}


# ── 도구 레벨 테스트 (mock client) ──────────────────────────────


def _install_dart_mock(mock_client: MagicMock) -> None:
    """server._dart 를 재정의해서 mock_client 를 반환하도록 함."""
    server._dart.cache_clear()
    server._dart.__wrapped__ = lambda: mock_client  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_dart_full_fs_sj_div_filter():
    """sj_div='IS' 필터가 손익계산서 행만 남기는지."""
    raw = {
        "list": [
            {"sj_div": "BS", "sj_nm": "재무상태표", "account_nm": "자산총계", "thstrm_amount": "100"},
            {"sj_div": "IS", "sj_nm": "손익계산서", "account_nm": "매출액", "thstrm_amount": "200"},
            {"sj_div": "IS", "sj_nm": "손익계산서", "account_nm": "영업이익", "thstrm_amount": "50"},
            {"sj_div": "CF", "sj_nm": "현금흐름표", "account_nm": "영업활동현금흐름", "thstrm_amount": "30"},
        ]
    }

    mock_client = MagicMock()
    mock_client.get_full_financial_statements = AsyncMock(return_value=raw)

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_full_financial_statements(
            corp_code="00126380", bsns_year="2024", sj_div="IS"
        )

    data = json.loads(result)
    assert len(data) == 2
    assert all(row["sj_div"] == "IS" for row in data)
    assert [row["account_nm"] for row in data] == ["매출액", "영업이익"]


@pytest.mark.asyncio
async def test_dart_full_fs_no_filter_returns_all():
    raw = {
        "list": [
            {"sj_div": "BS", "sj_nm": "BS", "account_nm": "a", "thstrm_amount": "1"},
            {"sj_div": "IS", "sj_nm": "IS", "account_nm": "b", "thstrm_amount": "2"},
        ]
    }

    mock_client = MagicMock()
    mock_client.get_full_financial_statements = AsyncMock(return_value=raw)

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_full_financial_statements(
            corp_code="00126380", bsns_year="2024"
        )

    data = json.loads(result)
    assert len(data) == 2


@pytest.mark.asyncio
async def test_dart_financial_statements_returns_compact_json():
    raw = {
        "list": [
            {
                "rcept_no": "x",
                "fs_div": "CFS",
                "sj_div": "BS",
                "sj_nm": "재무상태표",
                "account_nm": "자산총계",
                "thstrm_amount": "100",
                "frmtrm_amount": "90",
                "bfefrmtrm_amount": "80",
                "ord": "1",
            }
        ]
    }

    mock_client = MagicMock()
    mock_client.get_financial_statements = AsyncMock(return_value=raw)

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_financial_statements(
            corp_code="00126380", bsns_year="2024"
        )

    # 컴팩트 JSON은 ": " 이나 ", " 공백이 없어야 함
    assert ", " not in result
    assert ": " not in result

    data = json.loads(result)
    assert data[0]["curr"] == "100"
    assert "rcept_no" not in data[0]
    assert "ord" not in data[0]


@pytest.mark.asyncio
async def test_dart_search_company_returns_compact():
    mock_client = MagicMock()
    mock_client.search_company = AsyncMock(
        return_value=[
            {"corp_code": "001", "corp_name": "삼성전자", "stock_code": "005930", "modify_date": "20240101"},
            {"corp_code": "002", "corp_name": "삼성전자서비스", "stock_code": "", "modify_date": "20240101"},
        ]
    )

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_search_company(name="삼성전자")

    data = json.loads(result)
    # 빈 stock_code는 drop_empty 에 의해 제거되어야 함
    assert "stock_code" in data[0]
    assert "stock_code" not in data[1]


@pytest.mark.asyncio
async def test_dart_search_company_empty_returns_message():
    mock_client = MagicMock()
    mock_client.search_company = AsyncMock(return_value=[])

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_search_company(name="없는회사")

    assert "검색 결과가 없습니다" in result


@pytest.mark.asyncio
async def test_fisis_list_statistics_extracts_list():
    raw = {"result": {"err_msg": "정상", "list": [{"stat_cd": "010101", "list_nm": "은행요약"}]}}

    mock_client = MagicMock()
    mock_client.list_statistics = AsyncMock(return_value=raw)

    with patch.object(server, "_fisis", return_value=mock_client):
        result = await server.fisis_list_statistics(lrg_div="01")

    data = json.loads(result)
    assert isinstance(data, list)
    assert data[0]["stat_cd"] == "010101"


@pytest.mark.asyncio
async def test_get_api_reference_contains_expected_keys():
    result = await server.get_api_reference()
    data = json.loads(result)

    assert "DART_REPORT" in data
    assert "DART_CORP_CLASS" in data
    assert "DART_SJ_DIV" in data
    assert "FISIS_LARGE_DIV" in data
    assert data["DART_REPORT"]["사업보고서"] == "11011"
    assert data["DART_SJ_DIV"]["IS"] == "손익계산서"
