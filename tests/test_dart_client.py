"""DartClient 단위 테스트 (실 API 호출 없이 mock)."""

import io
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from financial_data_mcp.dart_client import DartClient


def _make_corp_zip(corps: list[dict]) -> bytes:
    """Mock corpCode.xml.zip 생성."""
    items_xml = "".join(
        f"<list>"
        f"<corp_code>{c['code']}</corp_code>"
        f"<corp_name>{c['name']}</corp_name>"
        f"<stock_code>{c.get('stock', '')}</stock_code>"
        f"<modify_date>20240101</modify_date>"
        f"</list>"
        for c in corps
    )
    xml = f"<?xml version='1.0' encoding='UTF-8'?><result>{items_xml}</result>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml)
    return buf.getvalue()


def _make_json_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json = MagicMock(return_value=payload)
    resp.raise_for_status = MagicMock()
    return resp


def _make_bytes_response(content: bytes) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture
async def dart_client():
    client = DartClient("test-key")
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_search_company_prefers_listed(dart_client: DartClient):
    zip_bytes = _make_corp_zip(
        [
            {"code": "00000001", "name": "삼성전자", "stock": "005930"},
            {"code": "00000002", "name": "삼성전자서비스", "stock": ""},
            {"code": "00000003", "name": "삼성전자로직스", "stock": ""},
        ]
    )
    resp = _make_bytes_response(zip_bytes)

    with patch.object(dart_client._client, "get", AsyncMock(return_value=resp)):
        results = await dart_client.search_company("삼성전자", limit=5)

    assert len(results) == 3
    assert results[0]["stock_code"] == "005930"  # 상장 우선
    assert results[0]["corp_code"] == "00000001"


@pytest.mark.asyncio
async def test_load_corp_codes_memory_cached(dart_client: DartClient):
    zip_bytes = _make_corp_zip(
        [{"code": "001", "name": "테스트", "stock": ""}]
    )
    resp = _make_bytes_response(zip_bytes)
    mock_get = AsyncMock(return_value=resp)

    with (
        patch.object(dart_client._client, "get", mock_get),
        # 디스크 캐시는 건너뛰도록 패치
        patch("financial_data_mcp.dart_client.load_disk_cache", return_value=None),
        patch("financial_data_mcp.dart_client.save_disk_cache"),
    ):
        await dart_client.load_corp_codes()
        await dart_client.load_corp_codes()
        await dart_client.load_corp_codes()

    # 메모리 캐시 덕분에 네트워크 호출은 1번만 발생
    assert mock_get.call_count == 1


@pytest.mark.asyncio
async def test_get_handles_013_as_empty(dart_client: DartClient):
    resp = _make_json_response(
        {"status": "013", "message": "조회된 데이터가 없습니다"}
    )

    with patch.object(dart_client._client, "get", AsyncMock(return_value=resp)):
        data = await dart_client._get("list.json", {"corp_code": "xxx"})

    # 013은 예외가 아닌 빈 list 응답으로 처리
    assert data["list"] == []
    assert "message" in data


@pytest.mark.asyncio
async def test_get_raises_on_auth_error(dart_client: DartClient):
    resp = _make_json_response({"status": "010", "message": "등록되지 않은 키"})

    with patch.object(dart_client._client, "get", AsyncMock(return_value=resp)):
        with pytest.raises(RuntimeError, match="DART API 오류"):
            await dart_client._get("list.json", {"corp_code": "xxx"})


@pytest.mark.asyncio
async def test_response_cache_reuses_result(dart_client: DartClient):
    """동일 파라미터 재요청 시 네트워크 호출 없이 캐시 사용."""
    resp = _make_json_response({"status": "000", "corp_name": "삼성"})
    mock_get = AsyncMock(return_value=resp)

    with patch.object(dart_client._client, "get", mock_get):
        await dart_client.get_company_overview("00126380")
        await dart_client.get_company_overview("00126380")
        await dart_client.get_company_overview("00126380")

    assert mock_get.call_count == 1


@pytest.mark.asyncio
async def test_response_cache_distinguishes_params(dart_client: DartClient):
    """다른 파라미터는 별도 캐시."""
    resp = _make_json_response({"status": "000", "list": []})
    mock_get = AsyncMock(return_value=resp)

    with patch.object(dart_client._client, "get", mock_get):
        await dart_client.get_financial_statements("001", "2023")
        await dart_client.get_financial_statements("001", "2024")  # 다른 연도
        await dart_client.get_financial_statements("002", "2024")  # 다른 회사

    assert mock_get.call_count == 3


@pytest.mark.asyncio
async def test_multi_company_truncates_to_20(dart_client: DartClient):
    resp = _make_json_response({"status": "000", "list": []})
    mock_get = AsyncMock(return_value=resp)

    corp_codes = [f"{i:08d}" for i in range(25)]

    with patch.object(dart_client._client, "get", mock_get):
        await dart_client.get_multi_company_financials(corp_codes, "2024")

    # 호출 시 최대 20개만 포함됐는지 확인
    call = mock_get.call_args
    params = call.kwargs["params"]
    assert len(params["corp_code"].split(",")) == 20
