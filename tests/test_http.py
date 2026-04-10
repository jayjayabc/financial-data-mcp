"""_http 모듈 단위 테스트."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from financial_data_mcp._http import mask_params, translate_http_error, with_retry


# ── with_retry ──────────────────────────────────────────────────


async def test_with_retry_success_on_first_attempt():
    fn = AsyncMock(return_value="ok")
    result = await with_retry(fn, max_attempts=3, backoff_base=0.001)
    assert result == "ok"
    assert fn.call_count == 1


async def test_with_retry_retries_on_transport_error():
    fn = AsyncMock(side_effect=[httpx.ConnectError("dns fail"), "ok"])
    result = await with_retry(fn, max_attempts=3, backoff_base=0.001)
    assert result == "ok"
    assert fn.call_count == 2


async def test_with_retry_retries_on_503():
    request = MagicMock()
    response = MagicMock(status_code=503)
    err = httpx.HTTPStatusError("Service Unavailable", request=request, response=response)
    fn = AsyncMock(side_effect=[err, err, "ok"])

    result = await with_retry(fn, max_attempts=3, backoff_base=0.001)
    assert result == "ok"
    assert fn.call_count == 3


async def test_with_retry_no_retry_on_4xx():
    """4xx 는 클라이언트 에러이므로 재시도하지 않음."""
    request = MagicMock()
    response = MagicMock(status_code=400)
    err = httpx.HTTPStatusError("Bad Request", request=request, response=response)
    fn = AsyncMock(side_effect=err)

    with pytest.raises(httpx.HTTPStatusError):
        await with_retry(fn, max_attempts=3, backoff_base=0.001)

    assert fn.call_count == 1  # 1회만 호출


async def test_with_retry_retries_on_429():
    """429 (rate limit) 는 재시도 대상."""
    request = MagicMock()
    response = MagicMock(status_code=429)
    err = httpx.HTTPStatusError("Too Many Requests", request=request, response=response)
    fn = AsyncMock(side_effect=[err, "ok"])

    result = await with_retry(fn, max_attempts=3, backoff_base=0.001)
    assert result == "ok"
    assert fn.call_count == 2


async def test_with_retry_exhausts_attempts_and_raises():
    fn = AsyncMock(side_effect=httpx.ConnectTimeout("timeout"))

    with pytest.raises(httpx.ConnectTimeout):
        await with_retry(fn, max_attempts=3, backoff_base=0.001)

    assert fn.call_count == 3


# ── translate_http_error ─────────────────────────────────────────


def test_translate_http_status_error():
    request = MagicMock(url="https://example.com/api?key=secret")
    response = MagicMock(status_code=500)
    err = httpx.HTTPStatusError("Server Error", request=request, response=response)

    result = translate_http_error("DART", err)
    assert isinstance(result, RuntimeError)
    assert "DART HTTP 500" in str(result)
    # URL의 쿼리스트링이 마스킹되어야 함
    assert "secret" not in str(result)


def test_translate_timeout_error():
    err = httpx.ConnectTimeout("timeout")
    result = translate_http_error("FISIS", err)
    assert isinstance(result, RuntimeError)
    assert "타임아웃" in str(result)


def test_translate_transport_error():
    err = httpx.ConnectError("dns fail")
    result = translate_http_error("DART", err)
    assert isinstance(result, RuntimeError)
    assert "네트워크 오류" in str(result)


# ── mask_params ──────────────────────────────────────────────────


def test_mask_params_hides_sensitive():
    params = {"crtfc_key": "secret123", "corp_code": "00126380", "auth": "key"}
    masked = mask_params(params)
    assert masked["crtfc_key"] == "***"
    assert masked["auth"] == "***"
    assert masked["corp_code"] == "00126380"


def test_mask_params_case_insensitive():
    masked = mask_params({"API_KEY": "secret", "Auth": "x"})
    assert masked["API_KEY"] == "***"
    assert masked["Auth"] == "***"


def test_mask_params_empty():
    assert mask_params({}) == {}
