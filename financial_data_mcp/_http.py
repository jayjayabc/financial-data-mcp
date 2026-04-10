"""HTTP 유틸리티: 재시도, 에러 변환, 민감정보 마스킹.

- with_retry: 지수 백오프 재시도 (transport/timeout/5xx/429 대상)
- translate_http_error: httpx 예외를 RuntimeError로 변환 (사용자 친화적 메시지)
- mask_params: 로깅용 파라미터에서 API 키 마스킹
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

import httpx

logger = logging.getLogger("financial_data_mcp.http")

T = TypeVar("T")

# 5xx + 429(rate limit) 만 재시도. 4xx는 요청 자체의 문제라 재시도 무의미.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

SENSITIVE_PARAMS = frozenset({"crtfc_key", "auth", "api_key", "apikey", "key", "token"})


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    backoff_base: float = 1.0,
    label: str = "request",
) -> T:
    """지수 백오프 재시도.

    재시도 대상:
    - httpx.TransportError (네트워크/DNS/연결 실패)
    - httpx.TimeoutException (타임아웃)
    - httpx.HTTPStatusError with status in RETRYABLE_STATUS

    재시도하지 않는 에러는 즉시 전파.
    """
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return await fn()
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in RETRYABLE_STATUS:
                raise
            last_exc = e
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last_exc = e

        if attempt >= max_attempts - 1:
            break
        wait = backoff_base * (2**attempt)
        logger.warning(
            "%s 재시도 %d/%d: %s (대기 %.1fs)",
            label,
            attempt + 1,
            max_attempts,
            type(last_exc).__name__,
            wait,
        )
        await asyncio.sleep(wait)

    assert last_exc is not None
    raise last_exc


def translate_http_error(source: str, exc: Exception) -> RuntimeError:
    """httpx 예외를 사용자 친화적 RuntimeError로 변환.

    Args:
        source: "DART" 또는 "FISIS" 등 데이터 출처 이름
        exc: 원본 예외
    """
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        # 민감정보 마스킹된 URL
        url = str(exc.request.url).split("?")[0]
        return RuntimeError(f"{source} HTTP {code}: {url}")
    if isinstance(exc, httpx.TimeoutException):
        return RuntimeError(f"{source} 요청 타임아웃 (네트워크가 느리거나 서버 지연)")
    if isinstance(exc, httpx.TransportError):
        return RuntimeError(f"{source} 네트워크 오류: {exc}")
    return RuntimeError(f"{source} 알 수 없는 오류: {exc}")


def mask_params(params: dict) -> dict:
    """로깅용: 민감 필드 마스킹."""
    result = {}
    for k, v in params.items():
        if k.lower() in SENSITIVE_PARAMS:
            result[k] = "***"
        else:
            result[k] = v
    return result
