"""E2E 시나리오 테스트: 도구 체인 연결, 부분 실패 복구, 엣지케이스.

기존 test_server.py의 단위 테스트를 보완하여,
실무 시나리오에서 도구들이 조합되어 동작하는 흐름을 검증합니다.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from financial_data_mcp import server


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  E2E 시나리오 1: 단일기업 5개년 추이 분석 체인
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_e2e_single_company_multi_year_chain():
    """search_company → multi_year 체인이 연결되어 동작하는지."""
    mock_client = MagicMock()
    mock_client.search_company = AsyncMock(return_value=[
        {"corp_code": "00315953", "corp_name": "KB금융지주", "stock_code": "105560"},
    ])
    mock_client.get_financial_statements = AsyncMock(
        side_effect=lambda corp, year, *a, **kw: {
            "list": [{"sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": f"{year}00"}]
        }
    )

    with patch.object(server, "_dart", return_value=mock_client):
        server._plan_called = True

        # Step 1: 검색
        search_result = await server.dart_search_company(name="KB금융")
        search_data = json.loads(search_result)
        corp_code = search_data[0]["corp_code"]

        # Step 2: 5개년 조회
        multi_result = await server.dart_financial_statements_multi_year(
            corp_code=corp_code, start_year="2019", end_year="2023"
        )
        multi_data = json.loads(multi_result)

    assert corp_code == "00315953"
    assert len(multi_data) == 5
    assert multi_data[0]["year"] == "2019"
    assert multi_data[4]["year"] == "2023"
    assert multi_data[4]["data"][0]["curr"] == "202300"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  E2E 시나리오 2: 4대 금융지주 비교 체인
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_e2e_multi_company_comparison_chain():
    """search_companies → multi_company_financials 체인."""
    company_map = {
        "KB금융": {"corp_code": "00315953", "corp_name": "KB금융지주", "stock_code": "105560"},
        "신한지주": {"corp_code": "00382199", "corp_name": "신한지주", "stock_code": "055550"},
        "하나금융": {"corp_code": "00547583", "corp_name": "하나금융지주", "stock_code": "086790"},
        "우리금융": {"corp_code": "00254872", "corp_name": "우리금융지주", "stock_code": "316140"},
    }

    mock_client = MagicMock()
    mock_client.search_company = AsyncMock(
        side_effect=lambda name, limit: [company_map.get(name, {})]
    )
    mock_client.get_multi_company_financials = AsyncMock(return_value={
        "list": [
            {"corp_code": "00315953", "sj_div": "IS", "account_nm": "당기순이익", "thstrm_amount": "4600000000000"},
            {"corp_code": "00382199", "sj_div": "IS", "account_nm": "당기순이익", "thstrm_amount": "4300000000000"},
        ]
    })

    with patch.object(server, "_dart", return_value=mock_client):
        server._plan_called = True

        # Step 1: 복수 검색
        search_result = await server.dart_search_companies(
            names=["KB금융", "신한지주", "하나금융", "우리금융"]
        )
        search_data = json.loads(search_result)
        codes = [item["results"][0]["corp_code"] for item in search_data]

        # Step 2: 비교 조회
        compare_result = await server.dart_multi_company_financials(
            corp_codes=codes, bsns_year="2023"
        )
        compare_data = json.loads(compare_result)

    assert len(search_data) == 4
    assert len(codes) == 4
    assert len(compare_data) == 2
    assert compare_data[0]["curr"] == "4600000000000"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  E2E 시나리오 3: DART→FISIS 교차분석 체인
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_e2e_dart_to_fisis_cross_analysis():
    """bridge로 금융기관 확인 → FISIS 업권 통계 조회 체인."""
    mock_dart = MagicMock()
    mock_dart.get_company_overview = AsyncMock(return_value={
        "corp_name": "KB국민은행",
        "induty_code": "6411",
        "corp_cls": "Y",
    })

    mock_fisis = MagicMock()
    mock_fisis.get_statistics = AsyncMock(
        side_effect=lambda code, *a, **kw: {
            "result": {"list": [{"item": f"{code}_data", "val": "100"}]}
        }
    )

    with (
        patch.object(server, "_dart", return_value=mock_dart),
        patch.object(server, "_fisis", return_value=mock_fisis),
    ):
        # Step 1: bridge → 금융기관 여부 + FISIS 코드 확인
        bridge_result = await server.dart_to_fisis_bridge(corp_code="00104872")
        bridge_data = json.loads(bridge_result)
        assert bridge_data["is_financial"] is True
        lrg_div = bridge_data["fisis_lrg_div"]

        # Step 2: FISIS 업권 통계 조회
        fisis_result = await server.fisis_get_multi_statistics(
            stat_codes=["SA053", "SA017"],
            strt_yymm="202201", end_yymm="202412",
            lrg_div=lrg_div,
        )
        fisis_data = json.loads(fisis_result)

    assert lrg_div == "A"
    assert len(fisis_data) == 2
    assert fisis_data[0]["stat_cd"] == "SA053"


@pytest.mark.asyncio
async def test_e2e_bridge_non_financial_stops_fisis():
    """비금융 기업으로 bridge 시도 → is_financial=False 확인 후 FISIS 미호출."""
    mock_dart = MagicMock()
    mock_dart.get_company_overview = AsyncMock(return_value={
        "corp_name": "삼성전자",
        "induty_code": "2610",
        "corp_cls": "Y",
    })

    with patch.object(server, "_dart", return_value=mock_dart):
        result = await server.dart_to_fisis_bridge(corp_code="00126380")
        data = json.loads(result)

    assert data["is_financial"] is False
    assert "DART 재무제표" in data["note"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  부분 실패 복구 (return_exceptions=True)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_multi_year_partial_failure_returns_error_for_failed_years():
    """5개년 중 1개 연도 API 실패 → 해당 연도만 error, 나머지는 정상 반환."""
    call_count = 0

    async def _side_effect(corp, year, *a, **kw):
        nonlocal call_count
        call_count += 1
        if year == "2021":
            raise RuntimeError("DART HTTP 503: opendart.fss.or.kr")
        return {"list": [{"sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": f"{year}00"}]}

    mock_client = MagicMock()
    mock_client.get_financial_statements = AsyncMock(side_effect=_side_effect)

    with patch.object(server, "_dart", return_value=mock_client):
        server._plan_called = True
        result = await server.dart_financial_statements_multi_year(
            corp_code="00126380", start_year="2019", end_year="2023"
        )

    data = json.loads(result)
    assert len(data) == 5

    # 2021만 에러
    failed = [d for d in data if "error" in d]
    assert len(failed) == 1
    assert failed[0]["year"] == "2021"
    assert "503" in failed[0]["error"]

    # 나머지 4개는 정상
    success = [d for d in data if "data" in d]
    assert len(success) == 4


@pytest.mark.asyncio
async def test_multi_statistics_partial_failure():
    """3개 통계코드 중 1개 실패 → 해당 코드만 error, 나머지 정상."""
    async def _side_effect(code, *a, **kw):
        if code == "SA054":
            raise RuntimeError("FISIS API 오류: 유효하지 않은 통계코드")
        return {"result": {"list": [{"item": f"{code}_data", "val": "100"}]}}

    mock_client = MagicMock()
    mock_client.get_statistics = AsyncMock(side_effect=_side_effect)

    with patch.object(server, "_fisis", return_value=mock_client):
        result = await server.fisis_get_multi_statistics(
            stat_codes=["SA053", "SA054", "SA017"],
            strt_yymm="202201", end_yymm="202412",
        )

    data = json.loads(result)
    assert len(data) == 3

    failed = [d for d in data if "error" in d]
    assert len(failed) == 1
    assert failed[0]["stat_cd"] == "SA054"

    success = [d for d in data if "data" in d]
    assert len(success) == 2


@pytest.mark.asyncio
async def test_search_companies_partial_failure():
    """복수 검색 중 1개 검색어 실패 → 해당 쿼리만 error."""
    async def _side_effect(name, limit):
        if name == "에러기업":
            raise RuntimeError("DART 네트워크 오류")
        return [{"corp_code": "00000001", "corp_name": name, "stock_code": ""}]

    mock_client = MagicMock()
    mock_client.search_company = AsyncMock(side_effect=_side_effect)

    with patch.object(server, "_dart", return_value=mock_client):
        server._plan_called = True
        result = await server.dart_search_companies(
            names=["KB금융", "에러기업", "신한지주"]
        )

    data = json.loads(result)
    assert len(data) == 3

    assert data[0]["query"] == "KB금융"
    assert "results" in data[0]

    assert data[1]["query"] == "에러기업"
    assert "error" in data[1]

    assert data[2]["query"] == "신한지주"
    assert "results" in data[2]


@pytest.mark.asyncio
async def test_multi_company_chunked_partial_failure():
    """25개 기업(2청크) 중 2번째 청크 실패 → 1번째 청크 데이터만 반환."""
    call_count = 0

    async def _side_effect(codes, year, reprt):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("DART HTTP 503")
        return {"list": [
            {"sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "100"}
            for _ in codes
        ]}

    mock_client = MagicMock()
    mock_client.get_multi_company_financials = AsyncMock(side_effect=_side_effect)

    codes = [f"{i:08d}" for i in range(25)]
    with patch.object(server, "_dart", return_value=mock_client):
        server._plan_called = True
        result = await server.dart_multi_company_financials(
            corp_codes=codes, bsns_year="2023"
        )

    data = json.loads(result)
    # 1번째 청크(20개) 성공, 2번째(5개) 실패 → 20개만 반환
    assert len(data) == 20


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  엣지케이스: 단일 연도, 최대 범위, 부분 빈 결과
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_multi_year_single_year():
    """start_year == end_year → 1개 연도만 조회."""
    mock_client = MagicMock()
    mock_client.get_financial_statements = AsyncMock(return_value={
        "list": [{"sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "100"}]
    })

    with patch.object(server, "_dart", return_value=mock_client):
        server._plan_called = True
        result = await server.dart_financial_statements_multi_year(
            corp_code="00126380", start_year="2024", end_year="2024"
        )

    data = json.loads(result)
    assert len(data) == 1
    assert data[0]["year"] == "2024"
    assert mock_client.get_financial_statements.call_count == 1


@pytest.mark.asyncio
async def test_multi_year_max_11_years():
    """10년 범위(11개 연도)가 허용되는지."""
    mock_client = MagicMock()
    mock_client.get_financial_statements = AsyncMock(return_value={"list": []})

    with patch.object(server, "_dart", return_value=mock_client):
        server._plan_called = True
        result = await server.dart_financial_statements_multi_year(
            corp_code="00126380", start_year="2014", end_year="2024"
        )

    data = json.loads(result)
    assert len(data) == 11
    assert data[0]["year"] == "2014"
    assert data[10]["year"] == "2024"


@pytest.mark.asyncio
async def test_multi_year_partial_empty_years():
    """일부 연도 데이터가 빈 list여도 전체 결과에 포함."""
    async def _side_effect(corp, year, *a, **kw):
        if year == "2021":
            return {"list": []}  # 데이터 없음 (013 케이스)
        return {"list": [{"sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": f"{year}00"}]}

    mock_client = MagicMock()
    mock_client.get_financial_statements = AsyncMock(side_effect=_side_effect)

    with patch.object(server, "_dart", return_value=mock_client):
        server._plan_called = True
        result = await server.dart_financial_statements_multi_year(
            corp_code="00126380", start_year="2020", end_year="2023"
        )

    data = json.loads(result)
    assert len(data) == 4

    empty_year = next(d for d in data if d["year"] == "2021")
    assert empty_year["data"] == []

    filled_year = next(d for d in data if d["year"] == "2022")
    assert len(filled_year["data"]) > 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  엣지케이스: 공시 검색
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_disclosures_zero_results():
    """공시 검색 결과 0건 → 빈 list, 에러 아님."""
    mock_client = MagicMock()
    mock_client.search_disclosures = AsyncMock(return_value={
        "total_count": "0", "total_page": "0", "page_no": "1", "list": []
    })

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_search_disclosures(
            corp_code="00126380", bgn_de="20240101", end_de="20240131"
        )

    data = json.loads(result)
    assert data["list"] == []
    assert not result.startswith("[")  # 에러가 아님


@pytest.mark.asyncio
async def test_disclosures_pagination():
    """page_no, page_count 파라미터가 정확히 전달되는지."""
    mock_client = MagicMock()
    mock_client.search_disclosures = AsyncMock(return_value={
        "total_count": "50", "total_page": "10", "page_no": "3",
        "list": [{"rcept_no": "20240401000123", "report_nm": "사업보고서",
                  "rcept_dt": "20240401", "corp_name": "삼성전자"}]
    })

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_search_disclosures(
            page_no=3, page_count=5
        )

    mock_client.search_disclosures.assert_called_once_with(
        corp_code="", bgn_de="", end_de="", corp_cls="",
        pblntf_ty="", page_no=3, page_count=5,
    )
    data = json.loads(result)
    assert data["page_no"] == "3"


@pytest.mark.asyncio
async def test_disclosures_invalid_page_count_zero():
    """page_count=0 → [input error]."""
    result = await server.dart_search_disclosures(page_count=0)
    assert result.startswith("[input error]")


@pytest.mark.asyncio
async def test_disclosures_invalid_page_no_zero():
    """page_no=0 → [input error]."""
    result = await server.dart_search_disclosures(page_no=0)
    assert result.startswith("[input error]")


@pytest.mark.asyncio
async def test_disclosures_invalid_corp_cls():
    """corp_cls='Z' → [input error]."""
    result = await server.dart_search_disclosures(corp_cls="Z")
    assert result.startswith("[input error]")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  엣지케이스: FISIS 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_fisis_invalid_term():
    """term='M' → [input error]."""
    result = await server.fisis_get_statistics(
        stat_cd="SA003", strt_yymm="202401", end_yymm="202412", term="M"
    )
    assert result.startswith("[input error]")
    assert "'Q'" in result and "'Y'" in result


@pytest.mark.asyncio
async def test_fisis_start_after_end():
    """strt_yymm > end_yymm → [input error]."""
    result = await server.fisis_get_statistics(
        stat_cd="SA003", strt_yymm="202412", end_yymm="202401"
    )
    assert result.startswith("[input error]")


@pytest.mark.asyncio
async def test_fisis_multi_invalid_term():
    """fisis_get_multi_statistics에서도 term 검증."""
    result = await server.fisis_get_multi_statistics(
        stat_codes=["SA053"], strt_yymm="202401", end_yymm="202412", term="W"
    )
    assert result.startswith("[input error]")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  엣지케이스: 100개 청킹
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_multi_company_100_max_5_chunks():
    """100개 기업 → 5개 청크(각 20개) 병렬 호출."""
    mock_client = MagicMock()
    mock_client.get_multi_company_financials = AsyncMock(return_value={
        "list": [{"sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "100"}]
    })

    codes = [f"{i:08d}" for i in range(100)]
    with patch.object(server, "_dart", return_value=mock_client):
        server._plan_called = True
        result = await server.dart_multi_company_financials(
            corp_codes=codes, bsns_year="2023"
        )

    assert mock_client.get_multi_company_financials.call_count == 5
    data = json.loads(result)
    assert len(data) == 5  # 5청크 각 1행


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  plan_data_query 상태 관리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_plan_sets_global_flags():
    """plan_data_query 호출 후 _plan_called, _catalog_delivered 플래그 설정."""
    server._catalog_delivered = False
    server._plan_called = False

    result = await server.plan_data_query(question="테스트 질문")
    data = json.loads(result)

    assert server._plan_called is True
    assert server._catalog_delivered is True
    assert "data_catalog" in data

    # 리셋
    server._catalog_delivered = False
    server._plan_called = False


@pytest.mark.asyncio
async def test_plan_whitespace_only_error():
    """공백만 있는 질문 → [input error]."""
    server._plan_called = False
    result = await server.plan_data_query(question="   \t\n  ")
    assert result.startswith("[input error]")
    # plan_called는 에러 시 True로 전환되지 않아야 함 — 실제로는 _tool_safe가 잡음
    # _plan_called는 함수 내부 첫 줄에서 True 설정 후 검증하므로 True가 됨


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  응답 포맷 엣지케이스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_drop_empty_preserves_zero_and_false():
    """_drop_empty는 0과 False를 제거하지 않음."""
    result = server._drop_empty({"a": 0, "b": False, "c": "", "d": None, "e": "x"})
    assert result == {"a": 0, "b": False, "e": "x"}


def test_compact_fin_row_all_empty():
    """모든 선택 필드가 빈 값이면 빈 dict 반환."""
    raw = {"rcept_no": "x", "ord": "1"}
    compact = server._compact_fin_row(raw)
    assert compact == {}


def test_fisis_compact_list_non_dict_items():
    """FISIS 리스트에 dict가 아닌 항목이 있어도 에러 없이 통과."""
    data = {"list": ["string_item", 42, {"item": "A", "val": "1"}]}
    result = server._fisis_compact_list(data)
    assert result[0] == "string_item"
    assert result[1] == 42
    assert result[2]["item"] == "A"


def test_fisis_extract_list_non_dict_input():
    """입력이 dict가 아닌 경우 그대로 반환."""
    assert server._fisis_extract_list([1, 2, 3]) == [1, 2, 3]
    assert server._fisis_extract_list("string") == "string"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  bridge 엣지케이스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_bridge_insurance_induty_code():
    """보험 업종코드(6511) → FISIS C(보험)."""
    mock_client = MagicMock()
    mock_client.get_company_overview = AsyncMock(return_value={
        "corp_name": "삼성생명", "induty_code": "6511", "corp_cls": "Y",
    })

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_to_fisis_bridge(corp_code="00377294")

    data = json.loads(result)
    assert data["is_financial"] is True
    assert data["fisis_lrg_div"] == "C"
    assert "보험" in data["fisis_sector"]


@pytest.mark.asyncio
async def test_bridge_trust_keyword_fallback():
    """'신탁' 키워드 → FISIS B(비은행)."""
    mock_client = MagicMock()
    mock_client.get_company_overview = AsyncMock(return_value={
        "corp_name": "한국투자신탁운용", "induty_code": "7777", "corp_cls": "E",
    })

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_to_fisis_bridge(corp_code="00333333")

    data = json.loads(result)
    assert data["is_financial"] is True
    assert data["fisis_lrg_div"] == "B"
    assert data["matched_by"] == "신탁"


@pytest.mark.asyncio
async def test_bridge_empty_induty_code():
    """업종코드가 빈 문자열이고 회사명에도 키워드 없음 → 비금융."""
    mock_client = MagicMock()
    mock_client.get_company_overview = AsyncMock(return_value={
        "corp_name": "일반제조회사", "induty_code": "", "corp_cls": "E",
    })

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_to_fisis_bridge(corp_code="00444444")

    data = json.loads(result)
    assert data["is_financial"] is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  dart_business_report (사업보고서 주요정보 22종)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_business_report_dividend():
    """배당 정보(dividend) 조회가 정상 동작하는지."""
    mock_client = MagicMock()
    mock_client.get_business_report = AsyncMock(return_value={
        "status": "000",
        "message": "정상",
        "list": [
            {"se": "주당 현금배당금(원)", "thstrm": "2000", "frmtrm": "1800", "lwfr": "1500"},
            {"se": "현금배당성향(%)", "thstrm": "25.5", "frmtrm": "23.1", "lwfr": "20.0"},
        ],
    })

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_business_report(
            corp_code="00126380", bsns_year="2023", report_type="dividend"
        )

    data = json.loads(result)
    assert data["report_type"] == "dividend"
    assert "배당" in data["description"]
    assert len(data["list"]) == 2
    # status/message 메타 필드가 각 행에서 제거됨
    assert "status" not in data["list"][0]
    assert data["list"][0]["se"] == "주당 현금배당금(원)"


@pytest.mark.asyncio
async def test_business_report_employee():
    """직원 현황(employee) 조회."""
    mock_client = MagicMock()
    mock_client.get_business_report = AsyncMock(return_value={
        "status": "000",
        "list": [
            {"fo_bbm": "사무직", "sm": "남", "jan_sal_am": "85,000,000", "tot_rgnf_cnt": "5000"},
        ],
    })

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_business_report(
            corp_code="00126380", bsns_year="2023", report_type="employee"
        )

    data = json.loads(result)
    assert data["report_type"] == "employee"
    assert "직원" in data["description"]
    assert len(data["list"]) == 1


@pytest.mark.asyncio
async def test_business_report_major_shareholder():
    """최대주주(major_shareholder) 조회."""
    mock_client = MagicMock()
    mock_client.get_business_report = AsyncMock(return_value={
        "list": [
            {"nm": "국민연금공단", "relate": "최대주주", "bsis_posesn_stock_co": "50000000",
             "bsis_posesn_stock_qota_rt": "8.50"},
        ],
    })

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_business_report(
            corp_code="00126380", bsns_year="2023", report_type="major_shareholder"
        )

    data = json.loads(result)
    assert data["report_type"] == "major_shareholder"
    assert data["list"][0]["nm"] == "국민연금공단"


@pytest.mark.asyncio
async def test_business_report_invalid_type():
    """존재하지 않는 report_type → [input error]."""
    result = await server.dart_business_report(
        corp_code="00126380", bsns_year="2023", report_type="nonexistent"
    )
    assert result.startswith("[input error]")
    assert "nonexistent" in result
    assert "dividend" in result  # 사용 가능한 타입 안내


@pytest.mark.asyncio
async def test_business_report_invalid_corp_code():
    """잘못된 corp_code → API 호출 없이 [input error]."""
    result = await server.dart_business_report(
        corp_code="invalid", bsns_year="2023", report_type="dividend"
    )
    assert result.startswith("[input error]")


@pytest.mark.asyncio
async def test_business_report_empty_result():
    """데이터 없음(빈 list) → 안내 메시지 포함 정상 응답."""
    mock_client = MagicMock()
    mock_client.get_business_report = AsyncMock(return_value={
        "status": "013", "message": "조회된 데이터가 없습니다", "list": []
    })

    with patch.object(server, "_dart", return_value=mock_client):
        result = await server.dart_business_report(
            corp_code="00000001", bsns_year="2023", report_type="dividend"
        )

    data = json.loads(result)
    assert data["list"] == []
    assert "데이터가 없습니다" in data["note"]


@pytest.mark.asyncio
async def test_business_report_all_types_have_valid_endpoints():
    """레지스트리의 모든 report_type이 유효한 엔드포인트를 가리키는지."""
    for rtype, (endpoint, desc) in server.BUSINESS_REPORT_TYPES.items():
        assert endpoint.endswith(".json"), f"{rtype}: endpoint must end with .json"
        assert desc, f"{rtype}: description must not be empty"
        assert isinstance(rtype, str) and rtype.isidentifier(), f"{rtype}: must be a valid identifier"


@pytest.mark.asyncio
async def test_e2e_dividend_multi_year_chain():
    """회사 검색 → 3년 연속 배당 조회 E2E 체인."""
    mock_client = MagicMock()
    mock_client.search_company = AsyncMock(return_value=[
        {"corp_code": "00126380", "corp_name": "삼성전자", "stock_code": "005930"},
    ])

    dividends_by_year = {
        "2021": [{"se": "현금배당성향(%)", "thstrm": "55.0"}],
        "2022": [{"se": "현금배당성향(%)", "thstrm": "52.0"}],
        "2023": [{"se": "현금배당성향(%)", "thstrm": "60.0"}],
    }

    async def _mock_report(endpoint, corp, year, reprt="11011"):
        return {"list": dividends_by_year.get(year, [])}

    mock_client.get_business_report = AsyncMock(side_effect=_mock_report)

    with patch.object(server, "_dart", return_value=mock_client):
        server._plan_called = True

        # Step 1: 검색
        search_result = await server.dart_search_company(name="삼성전자")
        corp_code = json.loads(search_result)[0]["corp_code"]

        # Step 2: 3년 배당 조회
        results = []
        for year in ["2021", "2022", "2023"]:
            r = await server.dart_business_report(
                corp_code=corp_code, bsns_year=year, report_type="dividend"
            )
            results.append(json.loads(r))

    assert corp_code == "00126380"
    assert len(results) == 3
    assert results[0]["list"][0]["thstrm"] == "55.0"
    assert results[2]["list"][0]["thstrm"] == "60.0"
    # 모든 연도 배당성향 > 50%
    assert all(float(r["list"][0]["thstrm"]) > 50.0 for r in results)
