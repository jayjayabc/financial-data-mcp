# financial-data MCP

**DART(전자공시시스템) · FISIS(금융통계정보시스템) 금융 데이터 MCP 서버**

Claude Code / Claude Desktop에서 한국 기업의 공시·재무제표·금융통계를 자연어로 조회·분석합니다.

---

## ✨ 특징

- **11개 MCP 도구**로 한국 기업 데이터 풀스택 조회 (DART 6 + FISIS 3 + 운영 2)
- **토큰 효율**: 컴팩트 JSON + `sj_div` 필터로 재무제표 응답 토큰 **75% 절감** 실측
- **반복 사용 최적화**: 3단계 캐싱 (메모리 → 디스크 30일 → TTL 응답 캐시 1시간)
- **동시성 안전**: `asyncio.Lock`으로 기업코드 8MB 중복 다운로드 방지
- **자동 복구**: 503/429/transport 오류 지수 백오프 재시도 + CFS→OFS 자동 폴백
- **입력 검증**: API 호출 전 사전 차단으로 quota·토큰 낭비 방지
- **DART quota 트래킹**: 일일 20,000건 한도 실시간 추적
- **149개 단위 테스트**로 모든 기능 커버

---

## 🔑 API 키 발급 (사용 전 필수)

이 MCP 서버는 금융감독원의 **공개 API**를 사용합니다. 무료이며 개인 발급이 필요합니다.

### DART API 키 발급

1. [opendart.fss.or.kr](https://opendart.fss.or.kr) 접속
2. 우측 상단 **회원가입** → 로그인
3. **마이페이지 → API 키 신청** → 용도 입력 후 즉시 발급
4. 발급된 키를 복사해 둡니다

> 일일 20,000건 무료. 개인 키이므로 타인과 공유하지 마세요.

### FISIS API 키 발급

1. [fisis.fss.or.kr](https://fisis.fss.or.kr) 접속
2. 상단 메뉴 **오픈API → 이용신청**
3. 회원가입 → 신청서 작성 → 승인 (보통 1~2일 소요)
4. 승인 후 **마이페이지**에서 API 키 확인

> 승인 전까지 API 호출이 안 됩니다. DART보다 시간이 걸립니다.

---

## 🚀 설치 및 설정

### 1. 클론 & 의존성 설치

```bash
git clone https://github.com/jayjayabc/financial-data-mcp.git
cd financial-data-mcp
pip install -e .
```

### 2. API 키 설정

프로젝트 루트에 `.env` 파일 생성:

```bash
DART_API_KEY=여기에_DART_API_키_입력
FISIS_API_KEY=여기에_FISIS_API_키_입력
```

`.env.example` 파일을 복사해서 사용해도 됩니다:

```bash
cp .env.example .env
# 텍스트 에디터로 .env를 열어 키 입력
```

### 3. 환경 검증 (⭐ 권장)

```bash
python scripts/preflight.py
```

Python 버전, 패키지, API 키, 실제 DART/FISIS 접근 가능 여부까지 한 방에 진단합니다.
10개 항목이 모두 `[ OK ]`로 나오면 준비 완료입니다.

### 4. Claude 연결

**Claude Code CLI** (권장):

```bash
# 프로젝트 루트에 .mcp.json이 포함되어 있어 자동 인식
cd financial-data-mcp
claude
```

또는 어느 폴더에서나 쓰고 싶다면 전역 등록:

```bash
claude mcp add financial-data \
  -e DART_API_KEY=발급받은키 \
  -e FISIS_API_KEY=발급받은키 \
  --scope user \
  python -- -m financial_data_mcp
```

**Claude Desktop**:

`claude_desktop_config.json`에 추가 (`/absolute/path/to`를 실제 경로로 교체):

| OS | 설정 파일 경로 |
|----|--------------|
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |

```json
{
  "mcpServers": {
    "financial-data": {
      "command": "python",
      "args": ["-m", "financial_data_mcp"],
      "cwd": "/absolute/path/to/financial-data-mcp",
      "env": {
        "DART_API_KEY": "발급받은_DART_키",
        "FISIS_API_KEY": "발급받은_FISIS_키"
      }
    }
  }
}
```

설정 후 Claude Desktop을 **재시작**하면 도구 아이콘이 활성화됩니다.

### 5. 테스트 질문

```
"삼성전자 2024년 손익계산서 보여줘"
"삼성전자, SK하이닉스, LG전자 2024년 영업이익 비교"
"시중은행 2024년 판관비 추이 보여줘"
"오늘 DART API 얼마나 썼어?"
```

더 많은 시나리오는 [`docs/USAGE.md`](docs/USAGE.md) 참고.

---

## 🛠️ 제공 MCP 도구 (11개)

### 플래닝

| 도구 | 용도 |
|------|------|
| `plan_data_query` | 질문 분석 → DART/FISIS 중 최적 경로 선택 (항상 첫 번째로 호출) |

### DART 전자공시시스템 (일반 기업)

| 도구 | 용도 |
|------|------|
| `dart_search_company` | 회사명 → `corp_code` 검색 (다른 DART 도구 선행 조건) |
| `dart_company_overview` | 기업개황 (대표자·업종·주소·상장일 등) |
| `dart_search_disclosures` | 공시 목록 검색 (기간·유형·법인구분 필터) |
| `dart_financial_statements` | 주요 재무계정 (자산·부채·매출·이익) — **가벼움** |
| `dart_full_financial_statements` | 전체 재무제표 (`sj_div` 필터로 BS/IS/CF 선택) — **상세** |
| `dart_multi_company_financials` | 다중 회사 비교 (최대 20개, 1회 호출) |

### FISIS 금융통계정보시스템 (금융업 업권 통계)

| 도구 | 용도 |
|------|------|
| `fisis_list_statistics` | 통계 목록 검색 (대분류: A=은행, B=비은행/신탁, C=신용카드사·여신전문통계, D=금융투자, K=리스사, T=할부금융사) |
| `fisis_get_statistics` | 통계 데이터 조회 (기간·회사·항목별) |
| `fisis_list_companies` | 금융회사 목록 (권역별) |
| `fisis_list_sectors` | FISIS에 실제 등록된 전 권역 목록 + 권역별 회사 수 (A~Z 전수조사 기반 동적) |

### 운영

| 도구 | 용도 |
|------|------|
| `dart_quota_status` | DART 일일 API quota 사용 현황 (20,000건/일) |

---

## 📖 문서

| 문서 | 내용 |
|------|------|
| [`docs/USAGE.md`](docs/USAGE.md) | 실전 시나리오 10가지, 토큰 절약 팁, 로깅 설정 |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | 30+ 에러별 진단과 해결법 |
| [`.env.example`](.env.example) | 환경변수 템플릿 |

---

## 🧪 개발

### 테스트

```bash
pip install -e ".[dev]"
pytest tests/
```

149개 테스트 통과 기대.

### 로컬 디버깅

```bash
LOG_LEVEL=DEBUG python -m financial_data_mcp
```

### 프로젝트 구조

```
financial-data-mcp/
├── financial_data_mcp/       ← MCP 서버 패키지
│   ├── server.py             # FastMCP 서버 + 11개 도구
│   ├── dart_client.py        # DART OpenAPI 클라이언트
│   ├── fisis_client.py       # FISIS OpenAPI 클라이언트
│   ├── _cache.py             # TTL 메모리 캐시 + 디스크 캐시
│   ├── _http.py              # 재시도 + HTTP 에러 변환
│   ├── _quota.py             # DART quota 트래킹
│   └── _validators.py        # 입력 검증
├── tests/                    # 149개 단위 테스트
├── scripts/
│   └── preflight.py          # 환경 검증 스크립트
├── docs/
│   ├── USAGE.md
│   └── TROUBLESHOOTING.md
├── .mcp.json                 # Claude Code 자동 인식용
├── .env.example              # API 키 템플릿
└── pyproject.toml
```

---

## ❓ FAQ

**Q. API 키를 팀원과 공유해도 되나요?**
A. 권장하지 않습니다. DART API는 개인당 일일 20,000건 한도이며, 공유 시 한도가 빠르게 소진됩니다. 팀원 각자가 개인 키를 발급받아 사용하세요.

**Q. FISIS API 승인이 안 나요.**
A. 신청 후 1~2 영업일 소요됩니다. 승인 전에는 preflight에서 `[FAIL]`로 표시되며 FISIS 관련 도구만 동작하지 않습니다. DART는 즉시 사용 가능합니다.

**Q. 어떤 기업을 조회할 수 있나요?**
A. DART에 등록된 모든 기업 (약 9만 개). 단, 재무제표는 DART에 공시한 기업만 조회 가능합니다 (비상장 소규모 기업은 제한적).

**Q. 은행·보험사 데이터는 DART와 FISIS 중 어느 걸 써야 하나요?**
A. 특정 금융회사 개별 분석 → DART. 업권 전체 비교·추이 분석 → FISIS. `plan_data_query`가 자동으로 최적 경로를 선택합니다.

---

## 📜 라이선스

MIT

---

## 🙏 감사

- [금융감독원 DART OpenAPI](https://opendart.fss.or.kr) — 전자공시 데이터
- [금융감독원 FISIS OpenAPI](https://fisis.fss.or.kr) — 금융통계 데이터
- [Model Context Protocol](https://modelcontextprotocol.io) — MCP 프로토콜
