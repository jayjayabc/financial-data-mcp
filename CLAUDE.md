# financial-data MCP

## 프로젝트 개요
DART(전자공시시스템) · FISIS(금융통계정보시스템) 금융 데이터를 조회하는 MCP 서버.
Claude Code / Claude Desktop에서 한국 기업의 공시·재무제표·금융통계를 자연어로 조회·분석.

## 기술 스택
- MCP (Model Context Protocol) - FastMCP 기반
- httpx - 비동기 HTTP 클라이언트
- python-dotenv - 환경변수 관리
- pytest / pytest-asyncio - 테스트

## 구조
- `financial_data_mcp/` - MCP 서버 패키지
  - `server.py` - FastMCP 서버 + 19개 도구
  - `dart_client.py` - DART OpenAPI 클라이언트
  - `fisis_client.py` - FISIS OpenAPI 클라이언트
  - `_cache.py` / `_http.py` / `_quota.py` / `_validators.py` - 내부 유틸
- `tests/` - 단위 테스트 (226개)
- `scripts/preflight.py` - 환경 검증 스크립트
- `docs/` - USAGE / TROUBLESHOOTING
- `.env` - API 키 (git 미추적)

## FISIS 업권 대분류
- A=은행, B=비은행(신탁), C=여신전문(카드·캐피탈·리스·할부), D=금융투자(증권·자산운용)

## 규칙
- API 키는 `.env`로 관리 (하드코딩 금지)
- DART 일일 20,000건 quota 트래킹 준수
- 로깅 시 민감 파라미터 마스킹 (`SENSITIVE_PARAMS`)
- 모든 외부 API 호출은 재시도·캐싱 래퍼 사용

## 실행
```bash
# MCP 서버 (Claude Desktop / Claude Code에서 사용)
uv run financial-data-mcp
# 또는: python -m financial_data_mcp

# 환경 검증
python scripts/preflight.py

# 테스트
pytest tests/
```

## 환경변수
- `DART_API_KEY` - DART OpenAPI 인증키 (https://opendart.fss.or.kr)
- `FISIS_API_KEY` - FISIS OpenAPI 인증키 (https://fisis.fss.or.kr)
- `LOG_LEVEL` - 선택 (DEBUG/INFO/WARNING/ERROR, 기본값 WARNING)
