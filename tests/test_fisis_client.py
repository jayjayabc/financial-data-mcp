"""FisisClient 단위 테스트."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from financial_data_mcp.fisis_client import FisisClient


def _resp(payload: dict) -> MagicMock:
    r = MagicMock()
    r.json = MagicMock(return_value=payload)
    r.raise_for_status = MagicMock()
    return r


@pytest.fixture
async def fisis_client():
    client = FisisClient("test-key")
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_list_statistics_ok(fisis_client: FisisClient):
    resp = _resp(
        {
            "result": {
                "err_msg": "정상",
                "list": [
                    {"stat_cd": "010101", "list_nm": "요약 재무제표(은행)"},
                ],
            }
        }
    )

    with patch.object(fisis_client._client, "get", AsyncMock(return_value=resp)):
        data = await fisis_client.list_statistics(lrg_div="01")

    assert data["result"]["list"][0]["stat_cd"] == "010101"


@pytest.mark.asyncio
async def test_raises_on_err_msg_in_result(fisis_client: FisisClient):
    resp = _resp({"result": {"err_msg": "인증키 오류", "err_cd": "999"}})

    with patch.object(fisis_client._client, "get", AsyncMock(return_value=resp)):
        with pytest.raises(RuntimeError, match="FISIS API 오류"):
            await fisis_client.list_statistics()


@pytest.mark.asyncio
async def test_raises_on_errMsg_camelcase(fisis_client: FisisClient):
    """camelCase 변형도 감지하는지."""
    resp = _resp({"result": {"errMsg": "잘못된 요청", "errCd": "400"}})

    with patch.object(fisis_client._client, "get", AsyncMock(return_value=resp)):
        with pytest.raises(RuntimeError, match="FISIS API 오류"):
            await fisis_client.list_statistics()


@pytest.mark.asyncio
async def test_raises_on_top_level_err(fisis_client: FisisClient):
    """result 래핑 없이 최상위에 err_msg가 있어도 감지."""
    resp = _resp({"err_msg": "서버 오류"})

    with patch.object(fisis_client._client, "get", AsyncMock(return_value=resp)):
        with pytest.raises(RuntimeError, match="FISIS API 오류"):
            await fisis_client.list_statistics()


@pytest.mark.asyncio
async def test_success_variants_not_raised(fisis_client: FisisClient):
    """'정상', '성공', '' 은 에러로 간주하지 않음."""
    for ok_msg in ("정상", "성공", "success", ""):
        resp = _resp({"result": {"err_msg": ok_msg, "list": []}})
        with patch.object(fisis_client._client, "get", AsyncMock(return_value=resp)):
            data = await fisis_client.list_statistics()
            assert "result" in data


@pytest.mark.asyncio
async def test_response_cache_reuse(fisis_client: FisisClient):
    resp = _resp({"result": {"err_msg": "정상", "list": [{"a": 1}]}})
    mock_get = AsyncMock(return_value=resp)

    with patch.object(fisis_client._client, "get", mock_get):
        await fisis_client.get_statistics("010101", "202401", "202412")
        await fisis_client.get_statistics("010101", "202401", "202412")

    assert mock_get.call_count == 1
