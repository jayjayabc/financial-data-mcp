# financial-data MCP

DART(전자공시) + FISIS(금융통계) 데이터를 Claude AI에게 제공하는 MCP 서버. 이 파일은 **이 코드베이스를 수정·확장하는 작업자**(사람·AI 모두)를 위한 가이드입니다.

## 프로젝트 정체성

- 단일 패키지: `financial_data_mcp/`
- 목적: 한국 기업·금융업권 데이터를 자연어로 조회·분석할 수 있도록 MCP 도구 19개 제공
- 동료들이 이 repo를 clone해서 자신의 Claude Code/Desktop에 등록해 사용하는 형태

## 기술 스택

- **Python 3.10+** (type hints, f-strings 활용)
- **FastMCP** — MCP 서버 프레임워크
- **httpx (async)** — DART/FISIS API 호출
- **pytest** — 234개 단위 테스트
- **uv** 또는 **pip** — 패키지 관리 (둘 다 지원)

## 코드 구조

```
financial_data_mcp/
├── server.py          # FastMCP 도구 19개 정의 + _DATA_CATALOG (plan_data_query 응답 데이터)
├── dart_client.py     # DART OpenAPI 비동기 클라이언트
├── fisis_client.py    # FISIS OpenAPI 비동기 클라이언트 + FISIS_SECTOR_CODES (22개 lrg_div)
├── _cache.py          # TTL 메모리·디스크 캐시
├── _http.py           # 재시도·에러 변환
├── _quota.py          # DART 일일 quota 트래킹
└── _validators.py     # 입력값 사전 검증 (corp_code, yyyymm, rcept_no 등)

tests/                 # 234개 pytest, 신규 도구·로직 추가 시 반드시 테스트 추가
docs/                  # USAGE.md, TROUBLESHOOTING.md
scripts/preflight.py   # 환경 검증 (Python·패키지·API키·실제 API 접근)
```

## 핵심 도메인 지식 (수정 전 반드시 숙지)

### FISIS lrg_div 코드 = 22개 (4개 아님)

`fisis_client.py`의 `FISIS_SECTOR_CODES`가 단일 출처. 주요 함정:
- `lrg_div`는 `fisis_list_statistics` 호출 시 **필수** (비우면 API 100 에러)
- D=종합금융, F=증권사 (D를 "금융투자"로 해석하면 안 됨)
- C=신용카드사 단독, 리스(K)·할부(T)·신기술(N)은 별도 코드
- W=선물사 (구버전 표기 "투자매매II" 금지)
- 보험은 H(생명) / I(손해) — C로 조회 시 신용카드사 데이터가 나옴

### 도구 호출 권장 순서

`plan_data_query`를 첫 번째로 호출하면 `_DATA_CATALOG`가 반환됨. 이 안에 업권별 통계코드·매핑이 들어있어 후속 호출이 정확해짐. server.py 수정 시 카탈로그도 함께 갱신할 것.

### DART 한계 → FISIS 보완

CET1·LCR·NIM 등 일부 지표는 DART 구조화 API에서 직접 추출 불가 → FISIS 통계코드로 보완. 매핑은 README의 "DART가 없는 지표 → FISIS 대안" 표 참조.

## 작업 규칙

### 코드 스타일

- 한국어 주석·docstring (변수·함수명은 영어)
- import 순서: 표준 → 서드파티 → 로컬
- API 키 하드코딩 금지 → `.env` + `os.environ`
- pandas 사용 시 `.copy()`로 SettingWithCopyWarning 방지

### MCP 도구 추가·수정 시

1. `server.py`에서 `@mcp.tool()` 데코레이터로 함수 정의
2. docstring의 `Args:` 섹션이 Claude가 보는 도구 설명이 됨 — 정확한 파라미터 값(예: lrg_div 22개 코드)을 반드시 명시
3. 입력 검증은 `_validators.py` 함수 사용 (신규 패턴은 여기에 추가)
4. API 클라이언트는 `_dart()` / `_fisis()` 싱글톤 게터로 접근
5. `tests/test_server.py`에 단위 테스트, 신규 시나리오는 `test_e2e_scenarios.py`
6. `_DATA_CATALOG`(plan_data_query 응답)에 새 도구·통계코드를 등록

### 테스트

```bash
source venv/Scripts/activate   # Windows bash
pytest tests/                  # 전체 (~6초)
pytest tests/test_server.py -k "fisis"  # 특정 모듈
```

234개 통과를 깨뜨리지 않을 것. 신규 기능은 테스트 동반 PR.

### 커밋·푸시

- 한국어 커밋 메시지
- `.claude/`, `eval/`, `fisis_test*.py`는 `.gitignore`로 제외 (로컬 전용)
- `git push` 전 `pytest tests/` 통과 확인

## 실행 방법

### 로컬 개발

```bash
pip install -e ".[dev]"        # 개발 의존성
python -m financial_data_mcp   # MCP 서버 직접 실행 (디버깅용)
LOG_LEVEL=DEBUG python -m financial_data_mcp   # 상세 로깅
```

### Claude Code 등록 (사용자 설치)

설치·연결 절차는 [README.md](README.md) 참조.

## 자주 하는 실수

- **`fisis_list_statistics`를 lrg_div 없이 호출** → API 100 에러. 도구 docstring·`_DATA_CATALOG`가 이 점을 명시하도록 유지할 것
- **DART `corp_code` 8자리 검증 누락** → `validate_corp_code` 사용
- **신규 lrg_div 추가 누락** — 한 곳을 바꿀 땐 다섯 곳을 함께 봐야 함:
  1. `fisis_client.py` `FISIS_SECTOR_CODES` / `FISIS_LARGE_GROUPS`
  2. `server.py` `_DATA_CATALOG.lrg_div`
  3. `server.py` `key_stat_codes_by_sector`
  4. `server.py` `fisis_registration_status.등록_업권`
  5. `server.py` `_INDUTY_TO_FISIS_DIV` (DART 업종→FISIS 매핑)
  6. README의 lrg_div 표

## 참고 문서

- [README.md](README.md) — 사용자 설치·사용 가이드 (동료 배포용)
- [docs/USAGE.md](docs/USAGE.md) — 실전 시나리오
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — 에러 진단
