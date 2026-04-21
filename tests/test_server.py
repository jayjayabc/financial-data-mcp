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
        server._plan_called = True  # plan 호출 완료 상태로 설정
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
        server._plan_called = True  # plan 호출 완료 상태로 설정
        result = await server.dart_search_company(name="삼성전자")

    data = json.loads(result)
    # 빈 stock_code는 drop_empty 에 의해 제거되어야 함
    assert "stock_code" in data[0]
    assert "stock_code" not in data[1]


@pytest.mark.asyncio
async def test_dart_search_company_without_plan_includes_hint():
    """plan_data_query 미호출 시 hint 키가 포함된 응답 반환."""
    mock_client = MagicMock()
    mock_client.search_company = AsyncMock(
        return_value=[{"corp_code": "001", "corp_name": "삼성전자", "stock_code": "005930"}]
    )

    with patch.object(server, "_dart", return_value=mock_client):
        server._plan_called = False  # plan 미호출 상태
        result = await server.dart_search_company(name="삼성전자")

    data = json.loads(result)
    assert "hint" in data
    assert "data" in data
    assert data["data"][0]["corp_code"] == "001"


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
async def test_plan_data_query_first_call_includes_catalog():
    """첫 호출은 전체 카탈로그 포함."""
    server._catalog_delivered = False  # 상태 리셋
    result = await server.plan_data_query(question="삼성전자 재무제표")
    data = json.loads(result)
    assert "data_catalog" in data
    assert "DART" in data["data_catalog"]
    assert "FISIS" in data["data_catalog"]
    assert data["data_catalog"]["DART"]["sj_div_filter"]["IS"] == "손익계산서"
    assert "은행" in data["data_catalog"]["FISIS"]["lrg_div"]["A"]


@pytest.mark.asyncio
async def test_plan_data_query_subsequent_call_omits_catalog():
    """2번째 이후 호출은 카탈로그 생략 (토큰 절약)."""
    server._catalog_delivered = True  # 이미 전달된 상태
    result = await server.plan_data_query(question="은행 판관비 비교")
    data = json.loads(result)
    assert "data_catalog" not in data
    assert "note" in data
    assert "planning_framework_reminder" in data
    server._catalog_delivered = False  # 다른 테스트 영향 방지


# ── 입력 검증 → 친화적 에러 메시지 ─────────────────────────────


@pytest.mark.asyncio
async def test_invalid_corp_code_returns_friendly_error():
    """잘못된 corp_code는 API 호출 없이 즉시 에러 응답."""
    mock_client = MagicMock()
    # 호출되면 안 됨
    mock_client.get_financial_statements = AsyncMock()

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_financial_statements(
            corp_code="삼성", bsns_year="2024"
        )

    assert result.startswith("[input error]")
    assert "corp_code" in result
    mock_client.get_financial_statements.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_year_returns_friendly_error():
    mock_client = MagicMock()
    mock_client.get_financial_statements = AsyncMock()

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_financial_statements(
            corp_code="00126380", bsns_year="24"
        )

    assert result.startswith("[input error]")
    assert "bsns_year" in result
    mock_client.get_financial_statements.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_report_code_returns_friendly_error():
    mock_client = MagicMock()
    mock_client.get_financial_statements = AsyncMock()

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_financial_statements(
            corp_code="00126380", bsns_year="2024", reprt_code="annual"
        )

    assert result.startswith("[input error]")
    assert "reprt_code" in result


@pytest.mark.asyncio
async def test_multi_company_over_100_returns_friendly_error():
    """100개 초과 기업코드는 에러 (자동 청킹 한계)."""
    mock_client = MagicMock()
    mock_client.get_multi_company_financials = AsyncMock()

    codes = [f"{i:08d}" for i in range(105)]

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_multi_company_financials(
            corp_codes=codes, bsns_year="2024"
        )

    assert result.startswith("[input error]")
    assert "최대 100개" in result
    mock_client.get_multi_company_financials.assert_not_called()


@pytest.mark.asyncio
async def test_api_error_returns_friendly_error():
    """클라이언트가 RuntimeError 던지면 [api error] 접두사로 포장."""
    mock_client = MagicMock()
    mock_client.get_financial_statements = AsyncMock(
        side_effect=RuntimeError("DART HTTP 503: opendart.fss.or.kr")
    )

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_financial_statements(
            corp_code="00126380", bsns_year="2024"
        )

    assert result.startswith("[api error]")
    assert "503" in result


@pytest.mark.asyncio
async def test_search_company_empty_name_returns_error():
    mock_client = MagicMock()
    mock_client.search_company = AsyncMock()

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_search_company(name="   ")

    assert result.startswith("[input error]")
    mock_client.search_company.assert_not_called()


@pytest.mark.asyncio
async def test_search_company_limit_out_of_range():
    mock_client = MagicMock()
    mock_client.search_company = AsyncMock()

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_search_company(name="삼성", limit=200)

    assert result.startswith("[input error]")
    assert "1~100" in result


# ── CFS → OFS 폴백 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_fs_falls_back_to_ofs_when_cfs_empty():
    """CFS가 빈 리스트면 OFS로 자동 폴백 + note 메시지 포함."""
    cfs_empty = {"list": []}
    ofs_data = {
        "list": [
            {
                "fs_div": "OFS",
                "sj_div": "BS",
                "sj_nm": "재무상태표",
                "account_nm": "자산총계",
                "thstrm_amount": "1000",
            }
        ]
    }

    mock_client = MagicMock()
    mock_client.get_full_financial_statements = AsyncMock(
        side_effect=[cfs_empty, ofs_data]
    )

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_full_financial_statements(
            corp_code="00000001", bsns_year="2024"
        )

    data = json.loads(result)
    assert "note" in data
    assert "OFS" in data["note"]
    assert len(data["list"]) == 1
    assert data["list"][0]["account_nm"] == "자산총계"

    # 두 번 호출됐는지 (CFS, OFS)
    assert mock_client.get_full_financial_statements.call_count == 2


@pytest.mark.asyncio
async def test_full_fs_no_fallback_when_cfs_has_data():
    cfs_data = {
        "list": [
            {"fs_div": "CFS", "sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "1"}
        ]
    }

    mock_client = MagicMock()
    mock_client.get_full_financial_statements = AsyncMock(return_value=cfs_data)

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_full_financial_statements(
            corp_code="00126380", bsns_year="2024"
        )

    data = json.loads(result)
    # 폴백이 아니면 list 바로 반환 (note 없음)
    assert isinstance(data, list)
    assert mock_client.get_full_financial_statements.call_count == 1


# ── 013 응답 처리 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_company_overview_strips_dart_meta():
    """status/message 같은 메타 필드가 응답에서 제거되는지."""
    raw = {
        "status": "000",
        "message": "정상",
        "corp_name": "삼성전자",
        "ceo_nm": "한종희",
    }

    mock_client = MagicMock()
    mock_client.get_company_overview = AsyncMock(return_value=raw)

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_company_overview(corp_code="00126380")

    data = json.loads(result)
    assert "status" not in data
    assert "message" not in data
    assert data["corp_name"] == "삼성전자"
    assert data["ceo_nm"] == "한종희"


# ── _strip_dart_meta / _tool_safe 직접 단위 테스트 ────────────


def test_strip_dart_meta():
    assert server._strip_dart_meta({"status": "000", "a": 1, "message": "x"}) == {"a": 1}


@pytest.mark.asyncio
async def test_tool_safe_catches_value_error():
    @server._tool_safe
    async def failing():
        raise ValueError("bad input")

    result = await failing()
    assert result == "[input error] bad input"


@pytest.mark.asyncio
async def test_tool_safe_catches_runtime_error():
    @server._tool_safe
    async def failing():
        raise RuntimeError("api down")

    result = await failing()
    assert result == "[api error] api down"


@pytest.mark.asyncio
async def test_tool_safe_catches_unexpected_error():
    @server._tool_safe
    async def failing():
        raise KeyError("oops")

    result = await failing()
    assert result.startswith("[internal error]")
    assert "KeyError" in result


# ── FISIS 응답 compaction ─────────────────────────────────────


def test_compact_fisis_row_drops_meta():
    """FISIS 행에서 err_msg 등 메타 필드가 제거되는지."""
    row = {"base_month": "202401", "company_nm": "은행A", "item_nm": "자산총계", "val": "1000", "err_msg": "정상"}
    compacted = server._compact_fisis_row(row)
    assert "err_msg" not in compacted
    assert compacted["company_nm"] == "은행A"
    assert compacted["val"] == "1000"


def test_compact_fisis_row_drops_empty():
    """빈 값이 제거되는지."""
    row = {"a": "1", "b": "", "c": None, "d": "x"}
    compacted = server._compact_fisis_row(row)
    assert compacted == {"a": "1", "d": "x"}


def test_fisis_compact_list_from_result():
    """_fisis_compact_list가 리스트 추출 + compaction을 수행하는지."""
    data = {"result": {"err_msg": "정상", "list": [
        {"item": "A", "val": "1", "err_msg": "정상"},
        {"item": "B", "val": "2", "errMsg": ""},
    ]}}
    result = server._fisis_compact_list(data)
    assert len(result) == 2
    assert "err_msg" not in result[0]
    assert "errMsg" not in result[1]
    assert result[0]["item"] == "A"


# ── fisis_get_statistics compaction 반영 ─────────────────────


@pytest.mark.asyncio
async def test_fisis_get_statistics_returns_compacted():
    """fisis_get_statistics가 compacted 응답을 반환하는지."""
    raw = {"result": {"err_msg": "정상", "list": [
        {"base_month": "202401", "item_nm": "자산총계", "val": "100", "err_cd": ""},
    ]}}

    mock_client = MagicMock()
    mock_client.get_statistics = AsyncMock(return_value=raw)

    with patch.object(server, "_fisis", return_value=mock_client):
        result = await server.fisis_get_statistics(
            stat_cd="SA003", strt_yymm="202401", end_yymm="202412"
        )

    data = json.loads(result)
    assert isinstance(data, list)
    assert "err_msg" not in data[0]
    assert "err_cd" not in data[0]
    assert data[0]["item_nm"] == "자산총계"


# ── fisis_get_multi_statistics ────────────────────────────────


@pytest.mark.asyncio
async def test_fisis_multi_statistics_returns_grouped():
    """여러 stat_cd를 병렬 조회하고 그룹핑된 결과를 반환하는지."""
    def _make_response(stat_cd):
        return {"result": {"err_msg": "정상", "list": [
            {"item": f"{stat_cd}_item", "val": "100"},
        ]}}

    mock_client = MagicMock()
    mock_client.get_statistics = AsyncMock(
        side_effect=lambda code, *a, **kw: _make_response(code)
    )

    with patch.object(server, "_fisis", return_value=mock_client):
        result = await server.fisis_get_multi_statistics(
            stat_codes=["SA053", "SA054"],
            strt_yymm="202401",
            end_yymm="202412",
        )

    data = json.loads(result)
    assert len(data) == 2
    assert data[0]["stat_cd"] == "SA053"
    assert data[1]["stat_cd"] == "SA054"
    assert data[0]["data"][0]["item"] == "SA053_item"


@pytest.mark.asyncio
async def test_fisis_multi_statistics_validates_empty_list():
    result = await server.fisis_get_multi_statistics(
        stat_codes=[], strt_yymm="202401", end_yymm="202412"
    )
    assert result.startswith("[input error]")


@pytest.mark.asyncio
async def test_fisis_multi_statistics_validates_max_count():
    result = await server.fisis_get_multi_statistics(
        stat_codes=[f"SA{i:03d}" for i in range(15)],
        strt_yymm="202401",
        end_yymm="202412",
    )
    assert result.startswith("[input error]")
    assert "최대 10개" in result


# ── dart_financial_statements_multi_year ──────────────────────


@pytest.mark.asyncio
async def test_dart_multi_year_returns_grouped_by_year():
    """연도별 데이터가 그룹핑되어 반환되는지."""
    def _make_response(corp, year, *a, **kw):
        return {"list": [
            {"sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": f"{year}00"},
        ]}

    mock_client = MagicMock()
    mock_client.get_financial_statements = AsyncMock(side_effect=_make_response)

    with patch.object(server, "_dart", return_value=mock_client):
        server._plan_called = True
        result = await server.dart_financial_statements_multi_year(
            corp_code="00126380",
            start_year="2022",
            end_year="2024",
        )

    data = json.loads(result)
    assert len(data) == 3
    assert data[0]["year"] == "2022"
    assert data[1]["year"] == "2023"
    assert data[2]["year"] == "2024"
    assert data[2]["data"][0]["curr"] == "202400"


@pytest.mark.asyncio
async def test_dart_multi_year_validates_year_range():
    """start_year > end_year 이면 에러."""
    result = await server.dart_financial_statements_multi_year(
        corp_code="00126380",
        start_year="2024",
        end_year="2020",
    )
    assert result.startswith("[input error]")


@pytest.mark.asyncio
async def test_dart_multi_year_validates_max_range():
    """11년 초과 범위이면 에러."""
    result = await server.dart_financial_statements_multi_year(
        corp_code="00126380",
        start_year="2010",
        end_year="2025",
    )
    assert result.startswith("[input error]")
    assert "11개 연도" in result


@pytest.mark.asyncio
async def test_dart_multi_year_parallel_execution():
    """asyncio.gather로 병렬 실행되는지 (호출 횟수 확인)."""
    mock_client = MagicMock()
    mock_client.get_financial_statements = AsyncMock(
        return_value={"list": []}
    )

    with patch.object(server, "_dart", return_value=mock_client):
        server._plan_called = True
        await server.dart_financial_statements_multi_year(
            corp_code="00126380",
            start_year="2020",
            end_year="2024",
        )

    # 5개 연도 = 5번 호출
    assert mock_client.get_financial_statements.call_count == 5


# ── dart_search_companies (복수 기업명 병렬 검색) ────────────


@pytest.mark.asyncio
async def test_search_companies_returns_grouped():
    """여러 회사명을 병렬 검색하고 그룹핑된 결과를 반환하는지."""
    mock_client = MagicMock()
    mock_client.search_company = AsyncMock(
        side_effect=lambda name, limit: [
            {"corp_code": "001", "corp_name": name, "stock_code": "005930"}
        ]
    )

    with patch.object(server, "_dart", return_value=mock_client):
        server._plan_called = True
        result = await server.dart_search_companies(
            names=["KB금융", "신한지주", "하나금융지주"]
        )

    data = json.loads(result)
    assert len(data) == 3
    assert data[0]["query"] == "KB금융"
    assert data[1]["query"] == "신한지주"
    assert data[2]["query"] == "하나금융지주"
    assert data[0]["results"][0]["corp_name"] == "KB금융"
    # 3번 병렬 호출
    assert mock_client.search_company.call_count == 3


@pytest.mark.asyncio
async def test_search_companies_validates_empty():
    result = await server.dart_search_companies(names=[])
    assert result.startswith("[input error]")


@pytest.mark.asyncio
async def test_search_companies_validates_max_count():
    result = await server.dart_search_companies(
        names=[f"회사{i}" for i in range(25)]
    )
    assert result.startswith("[input error]")
    assert "최대 20개" in result


@pytest.mark.asyncio
async def test_search_companies_empty_name_in_list():
    result = await server.dart_search_companies(names=["KB금융", "  ", "신한"])
    assert result.startswith("[input error]")


@pytest.mark.asyncio
async def test_search_companies_no_results():
    """검색 결과가 없는 회사명도 빈 리스트로 정상 반환."""
    mock_client = MagicMock()
    mock_client.search_company = AsyncMock(return_value=[])

    with patch.object(server, "_dart", return_value=mock_client):
        server._plan_called = True
        result = await server.dart_search_companies(names=["없는회사"])

    data = json.loads(result)
    assert data[0]["query"] == "없는회사"
    assert data[0]["results"] == []


# ── dart_multi_company_financials 자동 청킹 ──────────────────


@pytest.mark.asyncio
async def test_multi_company_auto_chunks_over_20():
    """20개 초과 기업코드가 자동으로 20개씩 분할되어 병렬 호출되는지."""
    codes = [f"{i:08d}" for i in range(25)]

    mock_client = MagicMock()
    mock_client.get_multi_company_financials = AsyncMock(
        return_value={"list": [
            {"sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "100"}
        ]}
    )

    with patch.object(server, "_dart", return_value=mock_client):
        server._plan_called = True
        result = await server.dart_multi_company_financials(
            corp_codes=codes, bsns_year="2023"
        )

    # 25개 → 20 + 5 = 2번 호출
    assert mock_client.get_multi_company_financials.call_count == 2
    data = json.loads(result)
    assert len(data) == 2  # 각 청크에서 1행씩


@pytest.mark.asyncio
async def test_multi_company_single_chunk_under_20():
    """20개 이하는 단일 호출."""
    codes = [f"{i:08d}" for i in range(5)]

    mock_client = MagicMock()
    mock_client.get_multi_company_financials = AsyncMock(
        return_value={"list": [
            {"sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "100"}
        ]}
    )

    with patch.object(server, "_dart", return_value=mock_client):
        server._plan_called = True
        result = await server.dart_multi_company_financials(
            corp_codes=codes, bsns_year="2023"
        )

    assert mock_client.get_multi_company_financials.call_count == 1


@pytest.mark.asyncio
async def test_multi_company_max_100_validation():
    """100개 초과 시 에러."""
    codes = [f"{i:08d}" for i in range(105)]
    result = await server.dart_multi_company_financials(
        corp_codes=codes, bsns_year="2023"
    )
    assert result.startswith("[input error]")
    assert "최대 100개" in result


# ── dart_to_fisis_bridge ─────────────────────────────────────


@pytest.mark.asyncio
async def test_bridge_identifies_bank():
    """은행 업종코드(6411)를 가진 기업이 FISIS A(은행)로 매핑되는지."""
    mock_client = MagicMock()
    mock_client.get_company_overview = AsyncMock(return_value={
        "status": "000",
        "corp_name": "KB금융지주",
        "induty_code": "6411",
        "corp_cls": "Y",
    })

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_to_fisis_bridge(corp_code="00315953")

    data = json.loads(result)
    assert data["is_financial"] is True
    assert data["fisis_lrg_div"] == "A"
    assert "은행" in data["fisis_sector"]


@pytest.mark.asyncio
async def test_bridge_identifies_securities():
    """증권사 업종코드(6611)가 FISIS D(금융투자)로 매핑되는지."""
    mock_client = MagicMock()
    mock_client.get_company_overview = AsyncMock(return_value={
        "corp_name": "미래에셋증권",
        "induty_code": "6611",
        "corp_cls": "Y",
    })

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_to_fisis_bridge(corp_code="00111111")

    data = json.loads(result)
    assert data["is_financial"] is True
    assert data["fisis_lrg_div"] == "D"


@pytest.mark.asyncio
async def test_bridge_non_financial():
    """비금융 기업(제조업)이 is_financial=False로 반환되는지."""
    mock_client = MagicMock()
    mock_client.get_company_overview = AsyncMock(return_value={
        "corp_name": "삼성전자",
        "induty_code": "2610",
        "corp_cls": "Y",
    })

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_to_fisis_bridge(corp_code="00126380")

    data = json.loads(result)
    assert data["is_financial"] is False
    assert "금융기관으로 분류되지 않았습니다" in data["note"]


@pytest.mark.asyncio
async def test_bridge_fallback_to_name_match():
    """업종코드 매칭 실패 시 회사명 키워드로 fallback 매핑."""
    mock_client = MagicMock()
    mock_client.get_company_overview = AsyncMock(return_value={
        "corp_name": "신한카드",
        "induty_code": "9999",  # 매핑 안 되는 코드
        "corp_cls": "E",
    })

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_to_fisis_bridge(corp_code="00222222")

    data = json.loads(result)
    assert data["is_financial"] is True
    assert data["matched_by"] == "카드"


@pytest.mark.asyncio
async def test_bridge_invalid_corp_code():
    result = await server.dart_to_fisis_bridge(corp_code="invalid")
    assert result.startswith("[input error]")


# ── dart_document_content ──────────────────────────────────────


@pytest.mark.asyncio
async def test_document_content_success():
    """정상 문서 조회 시 텍스트와 메타정보를 반환한다."""
    mock_client = MagicMock()
    mock_client.get_document_text = AsyncMock(return_value={
        "rcept_no": "20240101000001",
        "text": "삼성전자 사업보고서 주석 내용",
        "total_chars": 30,
        "returned_chars": 30,
        "truncated": False,
    })

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_document_content(
            rcept_no="20240101000001",
            section_keyword="주석",
            max_chars=6000,
        )

    data = json.loads(result)
    assert data["text"] == "삼성전자 사업보고서 주석 내용"
    assert data["truncated"] is False
    mock_client.get_document_text.assert_called_once_with(
        "20240101000001", section_keyword="주석", max_chars=6000
    )


@pytest.mark.asyncio
async def test_document_content_invalid_rcept_no():
    """14자리가 아닌 rcept_no는 [input error]를 반환한다."""
    result = await server.dart_document_content(rcept_no="1234")
    assert result.startswith("[input error]")


@pytest.mark.asyncio
async def test_document_content_invalid_max_chars():
    """max_chars 범위 초과 시 [input error]를 반환한다."""
    result = await server.dart_document_content(
        rcept_no="20240101000001", max_chars=99999
    )
    assert result.startswith("[input error]")
