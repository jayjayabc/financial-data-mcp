"""DartClient 단위 테스트 (실 API 호출 없이 mock)."""

import asyncio
import io
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from financial_data_mcp.dart_client import DartClient


def _make_corp_zip(corps: list[dict]) -> bytes:
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
    # 디스크 캐시 자동 우회 (테스트마다 network path 타도록)
    with (
        patch("financial_data_mcp.dart_client.load_disk_cache", return_value=None),
        patch("financial_data_mcp.dart_client.save_disk_cache"),
    ):
        yield client
    await client.aclose()


# ── 기본 동작 ─────────────────────────────────────────────────


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
    assert results[0]["stock_code"] == "005930"
    assert results[0]["corp_code"] == "00000001"


async def test_load_corp_codes_memory_cached(dart_client: DartClient):
    zip_bytes = _make_corp_zip([{"code": "001", "name": "테스트", "stock": ""}])
    resp = _make_bytes_response(zip_bytes)
    mock_get = AsyncMock(return_value=resp)

    with patch.object(dart_client._client, "get", mock_get):
        await dart_client.load_corp_codes()
        await dart_client.load_corp_codes()
        await dart_client.load_corp_codes()

    assert mock_get.call_count == 1


# ── 동시성: load_corp_codes Lock ───────────────────────────────


async def test_load_corp_codes_lock_prevents_concurrent_downloads(
    dart_client: DartClient,
):
    """동시에 들어온 3개 호출이 단 1번만 다운로드하는지 검증."""
    zip_bytes = _make_corp_zip([{"code": "001", "name": "테스트", "stock": ""}])
    resp = _make_bytes_response(zip_bytes)

    call_count = 0

    async def slow_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # 첫 호출은 일부러 느리게
        await asyncio.sleep(0.05)
        return resp

    with patch.object(dart_client._client, "get", side_effect=slow_get):
        # 동시에 3개 호출
        results = await asyncio.gather(
            dart_client.load_corp_codes(),
            dart_client.load_corp_codes(),
            dart_client.load_corp_codes(),
        )

    # Lock 덕분에 실제 네트워크 호출은 1번만
    assert call_count == 1
    # 모든 호출이 동일 결과를 받음
    assert results[0] is results[1] is results[2]


# ── 013 에러 처리 ──────────────────────────────────────────────


async def test_get_handles_013_gracefully(dart_client: DartClient):
    """013은 예외 없이 정상 응답으로 처리 (원래 필드 보존)."""
    resp = _make_json_response(
        {"status": "013", "message": "조회된 데이터가 없습니다"}
    )

    with patch.object(dart_client._client, "get", AsyncMock(return_value=resp)):
        data = await dart_client._get("list.json", {"corp_code": "00000000"})

    # 예외 없이 반환되고 원래 응답 구조 유지
    assert data["status"] == "013"
    assert "message" in data
    # list 키는 강제로 추가하지 않음 (응답 원형 보존)


async def test_get_raises_on_auth_error(dart_client: DartClient):
    resp = _make_json_response({"status": "010", "message": "등록되지 않은 키"})

    with patch.object(dart_client._client, "get", AsyncMock(return_value=resp)):
        with pytest.raises(RuntimeError, match="DART API 오류"):
            await dart_client._get("list.json", {"corp_code": "xxx"})


# ── 응답 캐시 ──────────────────────────────────────────────────


async def test_response_cache_reuses_result(dart_client: DartClient):
    resp = _make_json_response({"status": "000", "corp_name": "삼성"})
    mock_get = AsyncMock(return_value=resp)

    with patch.object(dart_client._client, "get", mock_get):
        await dart_client.get_company_overview("00126380")
        await dart_client.get_company_overview("00126380")
        await dart_client.get_company_overview("00126380")

    assert mock_get.call_count == 1


async def test_response_cache_distinguishes_params(dart_client: DartClient):
    resp = _make_json_response({"status": "000", "list": []})
    mock_get = AsyncMock(return_value=resp)

    with patch.object(dart_client._client, "get", mock_get):
        await dart_client.get_financial_statements("00000001", "2023")
        await dart_client.get_financial_statements("00000001", "2024")
        await dart_client.get_financial_statements("00000002", "2024")

    assert mock_get.call_count == 3


# ── 검색 캐시 ──────────────────────────────────────────────────


async def test_search_cache_reuses_results(dart_client: DartClient):
    """동일 검색어 재호출 시 선형 스캔 반복 안 함."""
    zip_bytes = _make_corp_zip(
        [{"code": f"{i:08d}", "name": f"회사{i}", "stock": ""} for i in range(100)]
    )
    resp = _make_bytes_response(zip_bytes)
    mock_get = AsyncMock(return_value=resp)

    with patch.object(dart_client._client, "get", mock_get):
        r1 = await dart_client.search_company("회사1", limit=10)
        r2 = await dart_client.search_company("회사1", limit=10)
        r3 = await dart_client.search_company("회사1", limit=10)

    # 검색 캐시가 있어도 동일 결과
    assert r1 == r2 == r3
    # 네트워크는 1번 (corp_codes 다운로드만)
    assert mock_get.call_count == 1
    # 검색 캐시에 저장됨
    assert "회사1::10" in dart_client._search_cache


async def test_search_cache_different_limit_is_separate(dart_client: DartClient):
    zip_bytes = _make_corp_zip(
        [{"code": f"{i:08d}", "name": f"회사{i}", "stock": ""} for i in range(100)]
    )
    resp = _make_bytes_response(zip_bytes)

    with patch.object(dart_client._client, "get", AsyncMock(return_value=resp)):
        r1 = await dart_client.search_company("회사", limit=5)
        r2 = await dart_client.search_company("회사", limit=10)

    assert len(r1) == 5
    assert len(r2) == 10


# ── 재시도 동작 ────────────────────────────────────────────────


async def test_retry_on_503(dart_client: DartClient):
    """503 에러는 재시도되어야 함."""
    ok_resp = _make_json_response({"status": "000", "list": []})

    request = MagicMock()
    bad_resp = MagicMock()
    bad_resp.status_code = 503
    bad_resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("503", request=request, response=bad_resp)
    )

    call_count = 0

    async def flaky(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            return bad_resp
        return ok_resp

    # backoff을 0으로 만들기 위해 with_retry의 backoff_base를 우회할 수는 없으니
    # sleep을 0으로 몽키패치
    with (
        patch.object(dart_client._client, "get", side_effect=flaky),
        patch("financial_data_mcp._http.asyncio.sleep", AsyncMock()),
    ):
        data = await dart_client.get_financial_statements("00000001", "2024")

    assert data["status"] == "000"
    assert call_count == 2


async def test_http_error_wrapped_as_runtime_error(dart_client: DartClient):
    """HTTPStatusError 가 최종적으로 RuntimeError로 변환되는지."""
    request = MagicMock()
    request.url = "https://opendart.fss.or.kr/api/list.json?crtfc_key=secret"
    bad_resp = MagicMock()
    bad_resp.status_code = 404
    bad_resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("404", request=request, response=bad_resp)
    )

    with patch.object(dart_client._client, "get", AsyncMock(return_value=bad_resp)):
        with pytest.raises(RuntimeError, match="DART HTTP 404"):
            await dart_client._get("list.json", {"corp_code": "00000001"})


async def test_transport_error_wrapped(dart_client: DartClient):
    """TransportError 도 RuntimeError로 변환."""
    with (
        patch.object(
            dart_client._client,
            "get",
            AsyncMock(side_effect=httpx.ConnectError("dns fail")),
        ),
        patch("financial_data_mcp._http.asyncio.sleep", AsyncMock()),
    ):
        with pytest.raises(RuntimeError, match="네트워크 오류"):
            await dart_client._get("list.json", {"corp_code": "00000001"})


# ── multi-company는 클라이언트 수준에서 truncation 제거됨 ──────
# (서버 층 validate_corp_codes_list에서 20개 초과 시 에러 발생)


async def test_multi_company_passes_all_codes(dart_client: DartClient):
    """클라이언트는 더 이상 truncate하지 않음 (서버에서 검증)."""
    resp = _make_json_response({"status": "000", "list": []})
    mock_get = AsyncMock(return_value=resp)

    corp_codes = [f"{i:08d}" for i in range(5)]

    with patch.object(dart_client._client, "get", mock_get):
        await dart_client.get_multi_company_financials(corp_codes, "2024")

    call = mock_get.call_args
    params = call.kwargs["params"]
    assert params["corp_code"] == ",".join(corp_codes)
