# 트러블슈팅 가이드

financial-data MCP 사용 중 발생하는 일반적인 문제와 해결법.

## 진단 첫 단계: preflight 실행

문제가 뭔지 모르겠다면 제일 먼저:

```bash
python scripts/preflight.py
```

실패 항목과 hint를 확인하세요. 대부분의 설정 문제는 여기서 잡힙니다.

---

## 1. 설치·설정 문제

### `ModuleNotFoundError: No module named 'financial_data_mcp'`

**원인**: 패키지가 설치되지 않았거나 잘못된 디렉토리에서 실행.

**해결**:
```bash
cd /path/to/fisis-app
pip install -e .
# 또는
uv pip install -e .
```

### `ModuleNotFoundError: No module named 'mcp'` 또는 `httpx`

**원인**: 의존성 미설치.

**해결**:
```bash
pip install -e .   # pyproject.toml의 dependencies 설치
```

### `DART_API_KEY 환경변수가 설정되지 않았습니다`

**원인**: `.env` 파일이 없거나, 프로젝트 루트가 아닌 곳에서 실행.

**해결**:
```bash
# 프로젝트 루트 확인
ls .env   # 존재해야 함

# 없으면 .env.example 복사 후 키 입력
cp .env.example .env
# 에디터로 .env 열어서 DART_API_KEY, FISIS_API_KEY 채우기
```

### Claude Desktop에서 도구 아이콘이 안 보임

**원인**:
1. `claude_desktop_config.json` 경로가 잘못됨
2. JSON 문법 오류
3. Claude Desktop 재시작 안 함
4. `command`/`args` 경로가 실제 파일과 다름

**진단**:
```bash
# 1. config 파일 위치 확인
# Windows:  %APPDATA%\Claude\claude_desktop_config.json
# Mac:      ~/Library/Application Support/Claude/claude_desktop_config.json

# 2. JSON 유효성 검사
python -c "import json; json.load(open('CONFIG_PATH'))"

# 3. MCP 서버가 직접 기동되는지
uv run financial-data-mcp
# 또는
python -m financial_data_mcp
# (Ctrl+C로 종료. 에러 없이 대기 상태면 OK)
```

**해결**: Claude Desktop 완전 종료 후 재시작 (트레이 아이콘에서 quit).

### Claude Code CLI에서 `.mcp.json`이 인식 안 됨

**원인**: 프로젝트 루트에서 `claude`를 실행하지 않음.

**해결**: `cd fisis-app && claude` 순서로 실행. `.mcp.json`은 현재 작업 디렉토리 기준으로 탐색됩니다.

---

## 2. DART API 에러

### `[api error] DART API 오류 [010]: 등록되지 않은 인증키입니다.`

**원인**: API 키가 잘못되었거나 아직 활성화되지 않음.

**해결**:
1. https://opendart.fss.or.kr 로그인
2. "인증키 관리" 페이지에서 키 상태 확인
3. 신규 발급한 키는 즉시 활성화되지 않을 수 있음 (대기)
4. `.env`의 `DART_API_KEY` 값에 공백/줄바꿈 없는지 확인

### `[api error] DART API 오류 [011]: 사용할 수 없는 키입니다.`

**원인**: 키가 비활성화됨 (장기 미사용, 약관 위반 등).

**해결**: DART에서 키 재발급.

### `[api error] DART API 오류 [020]: 요청 제한 수 초과`

**원인**: 일일 20,000건 한도 초과.

**해결**:
- `dart_quota_status` 도구로 사용량 확인
- 다음 날 자정(KST) 이후 재시도
- 캐시 활용: 같은 질문 반복 시 캐시 hit으로 quota 절약
- 필요하면 DART에서 고급 사용자 신청

### 조회 결과가 비어있음 (error가 아닌 빈 list)

**원인**: 
- 해당 사업연도의 데이터가 아직 등록되지 않음 (예: 2024년 1분기는 2024년 5월에 등록)
- 비상장 기업은 재무제표 미공시 가능

**해결**:
1. 이전 연도로 시도
2. 다른 `reprt_code` 시도 (사업보고서가 아닌 분기 등)
3. `dart_full_financial_statements`로 `fs_div='OFS'`(개별) 명시 — 연결재무제표 미작성 기업 대응 (자동 폴백되지만 수동으로도 확인 가능)

### `dart_full_financial_statements` 응답에 `note: "CFS 데이터 없음 - OFS로 폴백"` 포함

**이것은 에러가 아닙니다.** 소규모/비상장 기업이 연결재무제표를 작성하지 않아 개별재무제표로 자동 전환한 것입니다. 데이터 자체는 정상입니다.

---

## 3. FISIS API 에러

### FISIS API 접근 자체가 안 됨 (`HTTP 404`)

**원인**: `fisis_client.py`의 엔드포인트 URL이 실제 FISIS API와 다를 수 있음. 이 MCP는 FISIS 실 API 호출 없이 작성된 부분이 있어 엔드포인트 실증이 필요합니다.

**해결**:
1. `scripts/preflight.py` 실행 → FISIS 항목 확인
2. https://fisis.fss.or.kr/fisis/openapi/apiInfo.do 에서 실제 엔드포인트 확인
3. `financial_data_mcp/fisis_client.py`의 메서드별 endpoint 문자열 수정
4. 필요하면 이슈 등록해서 수정 요청

### `[api error] FISIS API 오류: <메시지>`

**원인**: FISIS API가 에러 응답을 반환.

**해결**: 에러 메시지 내용에 따라
- "인증 실패" → API 키 재확인
- "필수 파라미터 누락" → 호출 시 필수 파라미터 확인
- "요청 제한 초과" → 잠시 대기

---

## 4. 네트워크/방화벽 문제

### `[api error] DART 네트워크 오류: ConnectError`

**원인**: 방화벽/프록시가 opendart.fss.or.kr를 차단.

**해결**:
1. 일반 브라우저로 https://opendart.fss.or.kr 접속 가능 여부 확인
2. 회사망이면 IT에 요청하여 도메인 화이트리스트 등록
3. VPN 사용 중이라면 VPN 끄고 재시도
4. `HTTP_PROXY`/`HTTPS_PROXY` 환경변수 설정 필요할 수 있음:
   ```bash
   export HTTPS_PROXY=http://proxy.company.com:8080
   ```

### `[api error] DART 요청 타임아웃`

**원인**: 네트워크 느림 또는 DART 서버 부하.

**해결**:
- 자동 재시도가 3회까지 되므로 대부분 자동 해결됨
- 지속되면 DART 서비스 상태 확인 (https://opendart.fss.or.kr)

### `[api error] DART HTTP 503: ...`

**원인**: DART 서버 점검 또는 장애. 재시도 3회 모두 실패한 상태.

**해결**:
- 몇 분 후 재시도
- DART 공지사항 확인

---

## 5. 입력 검증 에러

### `[input error] corp_code는 8자리 숫자여야 합니다`

**원인**: `corp_code`에 회사명이나 잘못된 값 전달.

**해결**: `dart_search_company`로 먼저 `corp_code` 조회:
```
dart_search_company(name='삼성전자')
→ corp_code='00126380'  (이걸 다음 도구에 전달)
```

### `[input error] bsns_year는 4자리 연도(YYYY)여야 합니다`

**예**: `bsns_year='24'` (X) → `'2024'` (O)

### `[input error] corp_codes는 최대 20개까지 가능합니다`

**원인**: `dart_multi_company_financials`에 21개 이상 전달.

**해결**: 20개씩 분할해서 여러 번 호출.

### `[input error] reprt_code는 ['11011', '11012', '11013', '11014'] 중 하나여야 합니다`

**해결**:
- `11011` = 사업보고서 (연간)
- `11012` = 반기보고서
- `11013` = 1분기보고서
- `11014` = 3분기보고서

`get_api_reference()` 도구로 코드표 전체 확인 가능.

---

## 6. 성능·캐시 문제

### 첫 `dart_search_company` 호출이 10초 이상 걸림

**원인**: 기업코드 목록(~8MB) 최초 다운로드.

**해결**: 정상 동작입니다. 한 번 다운로드 후 30일간 디스크 캐시되어 이후 즉시 반환.

### 캐시가 이상한 값을 반환하는 것 같음

**해결**: 캐시 초기화
```bash
rm -rf ~/.cache/financial_data_mcp/
```

다음 호출 시 재다운로드.

### quota 카운터가 실제와 다름

**원인**: 클라이언트 측 추적이라 다음 경우 실제와 차이:
- 여러 MCP 인스턴스 동시 실행 시 각각 별도 카운터
- 파일 쓰기 실패 시 카운트 누락
- DART 대시보드의 quota와 완벽히 일치하지는 않음

**해결**: 실제 한도는 DART 웹사이트에서 확인. 클라이언트 카운터는 "경고 기준"으로 사용.

---

## 7. 디버깅

### 상세 로그 보기

```bash
LOG_LEVEL=DEBUG claude
```

stderr에 다음이 출력됩니다:
- 캐시 hit/miss
- 실제 API 호출 URL + 마스킹된 파라미터
- 재시도 발생
- quota 카운터 증가

### 직접 MCP 서버 기동 테스트

```bash
cd fisis-app
LOG_LEVEL=DEBUG uv run financial-data-mcp
```

stdin에 JSON-RPC 메시지를 넣어 수동 테스트 가능:
```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | uv run financial-data-mcp
```

### 테스트 실행

```bash
pip install -e ".[dev]"
pytest tests/
```

모두 통과하면 코드는 정상. 문제는 설정 쪽에 있을 가능성 높음.

---

## 지원

이 가이드로 해결되지 않는 문제는:
1. `preflight.py` 출력 전체를 포함해서 이슈 등록
2. `LOG_LEVEL=DEBUG` 로그 첨부
3. 재현 가능한 최소 질문/도구 호출 공유
