# financial-data MCP

**DART(전자공시시스템) · FISIS(금융통계정보시스템) 금융 데이터 MCP 서버**

Claude AI와 대화하듯 한국 기업의 공시·재무제표·금융통계를 조회·분석합니다.

---

## 이 도구가 뭔가요?

### Claude Code란?

Claude Code는 Anthropic이 만든 **AI 코딩 어시스턴트 CLI**입니다. 터미널에서 `claude`를 실행하면 Claude AI와 대화하면서 코드 작성, 데이터 분석, 파일 조작 등을 할 수 있습니다.

### MCP(Model Context Protocol)란?

MCP는 Claude가 외부 도구를 호출할 수 있게 해주는 표준 프로토콜입니다. **이 저장소는 MCP 서버**로, Claude가 DART·FISIS API를 대신 호출하고 결과를 요약해 줍니다.

쉽게 말하면:

```
사용자 → "KB국민은행 2024년 ROE 알려줘"
  ↓
Claude가 financial-data MCP 서버에 요청
  ↓
MCP 서버가 FISIS API 호출 → 데이터 파싱 → Claude에게 반환
  ↓
Claude가 자연어로 분석 결과 답변
```

API 문서를 읽거나 파이썬 코드를 작성할 필요 없이, **대화만으로** 금융 데이터를 분석할 수 있습니다.

---

## 주요 기능

- **19개 MCP 도구**로 한국 기업 데이터 풀스택 조회 (DART 14개 + FISIS 4개 + 운영 1개)
- **토큰 효율**: 컴팩트 JSON + `sj_div` 필터로 재무제표 응답 토큰 **75% 절감** 실측
- **반복 사용 최적화**: 3단계 캐싱 (메모리 → 디스크 30일 → TTL 응답 캐시 1시간)
- **동시성 안전**: `asyncio.Lock`으로 기업코드 8MB 중복 다운로드 방지
- **자동 복구**: 503/429/transport 오류 지수 백오프 재시도 + CFS→OFS 자동 폴백
- **입력 검증**: API 호출 전 사전 차단으로 quota·토큰 낭비 방지
- **DART quota 트래킹**: 일일 20,000건 한도 실시간 추적
- **234개 단위 테스트**로 모든 기능 커버

---

## 시작하기 (처음 사용하는 분)

아래 순서대로 진행하면 30분 안에 사용 가능합니다.

### Step 1. Claude Code 설치

먼저 Node.js(18 이상)가 필요합니다. 없으면 [nodejs.org](https://nodejs.org)에서 설치하세요.

```bash
npm install -g @anthropic-ai/claude-code
```

설치 후 터미널에서 `claude --version`이 출력되면 성공입니다.

> **Claude Code가 이미 있으신 분**은 이 단계를 건너뛰세요.

### Step 2. Claude Code 로그인

Claude Code는 두 가지 인증 방식을 지원합니다. **이미 Claude Code를 사용 중이라면 이 단계를 건너뛰세요.**

**방법 A: Claude.ai 구독으로 로그인 (Pro/Max/Team — 대부분 해당)**

```bash
claude login
```

브라우저가 열리면 Claude.ai 계정으로 로그인합니다. API 키가 별도로 필요하지 않습니다.

**방법 B: Anthropic API 키 (구독 없이 토큰 과금 방식)**

1. [console.anthropic.com](https://console.anthropic.com) 접속 → 회원가입
2. **API Keys** 메뉴 → **Create Key**
3. 발급된 키를 환경변수로 설정: `export ANTHROPIC_API_KEY=발급받은키`

### Step 3. DART / FISIS API 키 발급

이 MCP 서버는 금융감독원의 **공개 API**를 사용합니다. 무료이며 개인 발급이 필요합니다.

#### DART API 키 (즉시 발급)

1. [opendart.fss.or.kr](https://opendart.fss.or.kr) 접속
2. 우측 상단 **회원가입** → 로그인
3. **마이페이지 → API 키 신청** → 용도 입력 후 즉시 발급
4. 발급된 키를 복사해 둡니다

> 일일 20,000건 무료. 팀원 각자 개인 키를 발급받으세요 (공유 시 한도 빠르게 소진).

#### FISIS API 키 (1~2 영업일 소요)

1. [fisis.fss.or.kr](https://fisis.fss.or.kr) 접속
2. 상단 메뉴 **오픈API → 이용신청**
3. 회원가입 → 신청서 작성 → 승인 대기
4. 승인 후 **마이페이지**에서 API 키 확인

> 승인 전까지 FISIS 도구만 사용 불가. DART는 즉시 사용 가능합니다.

### Step 4. 저장소 클론 및 설치

```bash
git clone https://github.com/jayjayabc/financial-data-mcp.git
cd financial-data-mcp
pip install -e .
```

Python 3.10 이상이 필요합니다. `python --version`으로 확인하세요.

### Step 5. API 키 설정

프로젝트 루트에 `.env` 파일을 만들어 키를 입력합니다:

```bash
cp .env.example .env
```

`.env` 파일을 텍스트 에디터로 열어 수정:

```
DART_API_KEY=여기에_DART_API_키_입력
FISIS_API_KEY=여기에_FISIS_API_키_입력
```

### Step 6. 환경 검증

```bash
python scripts/preflight.py
```

아래와 같이 10개 항목이 모두 `[ OK ]`로 나오면 준비 완료입니다:

```
[ OK ] Python 3.10+
[ OK ] 패키지 설치 확인
[ OK ] DART_API_KEY 설정
[ OK ] FISIS_API_KEY 설정
[ OK ] DART API 접근 가능
[ OK ] FISIS API 접근 가능
...
```

### Step 7. Claude Code에 MCP 서버 연결

어느 폴더에서나 이 MCP를 쓸 수 있도록 **전역 등록** 합니다:

```bash
claude mcp add financial-data \
  -e DART_API_KEY=발급받은_DART_키 \
  -e FISIS_API_KEY=발급받은_FISIS_키 \
  --scope user \
  python -- -m financial_data_mcp
```

> **Windows 사용자**: 명령어가 길어 줄바꿈이 안 되면 한 줄로 붙여 입력하세요.

등록 확인:

```bash
claude mcp list
# financial-data  python -m financial_data_mcp  (user)  ✓
```

### Step 8. 첫 번째 질문

터미널에서 `claude`를 실행한 후 아무 폴더에서나 대화를 시작하세요:

```
> 삼성전자 2024년 손익계산서 보여줘
> KB국민은행과 신한은행 2024년 ROE 비교해줘
> 시중은행 BIS비율 최근 3년 추이 알려줘
```

---

## Claude Desktop 사용자 설정

Claude Desktop 앱을 사용하는 경우 아래 설정 파일에 추가하세요.

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
      "cwd": "/절대경로/financial-data-mcp",
      "env": {
        "DART_API_KEY": "발급받은_DART_키",
        "FISIS_API_KEY": "발급받은_FISIS_키"
      }
    }
  }
}
```

> `/절대경로/financial-data-mcp`를 실제 경로로 교체하세요.  
> Windows 예시: `C:\\Users\\홍길동\\financial-data-mcp`

설정 후 Claude Desktop을 **재시작**하면 도구 아이콘이 활성화됩니다.

---

## 제공 MCP 도구 (19개)

### 플래닝 (1개)

| 도구 | 설명 |
|------|------|
| `plan_data_query` | 질문을 분석해 DART/FISIS 중 최적 조회 경로를 선택합니다. 복잡한 분석 전에 먼저 호출하면 효율이 높아집니다. |

### DART 전자공시시스템 (14개)

| 도구 | 설명 |
|------|------|
| `dart_search_company` | 회사명으로 DART 고유 기업코드(`corp_code`) 검색. 단일 기업 조회 시 사용. |
| `dart_search_companies` | 여러 회사명을 한 번에 병렬 검색. 비교 분석 시 개별 호출 대신 이것을 사용하세요. |
| `dart_company_overview` | 기업개황 (대표자, 업종, 주소, 상장일, 회계월 등). |
| `dart_list_listed_companies` | 코스피/코스닥 전체 상장 기업 목록 조회. |
| `dart_search_disclosures` | 공시 목록 검색. 기간·유형·법인구분 등으로 필터링 가능. |
| `dart_financial_statements` | 주요 재무계정(자산·부채·매출·영업이익 등) 요약. **응답이 가볍습니다.** |
| `dart_full_financial_statements` | 전체 재무제표. `sj_div` 파라미터로 BS(재무상태표)/IS(손익계산서)/CF(현금흐름표) 선택 가능. |
| `dart_multi_company_financials` | 최대 20개 기업 재무 비교를 1회 호출로 처리. 경쟁사 비교에 최적. |
| `dart_financial_statements_multi_year` | 연도별 재무 추이를 병렬 조회. 여러 연도를 한 번에 가져옵니다. |
| `dart_business_report` | 사업보고서 22종 주요 정보 (배당, 임원 현황, 직원 수, 주요 주주, 감사 의견 등). |
| `dart_screen_report` | 요약 재무정보 화면 조회. `dart_full_financial_statements`가 빈 결과일 때 폴백으로 사용. |
| `dart_document_content` | 공시 원문 텍스트 조회. 재무제표 주석, 수시공시 본문 등 구조화 API가 제공하지 않는 내용에 접근. `rcept_no`(14자리 접수번호) 필요. |
| `dart_to_fisis_bridge` | DART 기업이 금융기관인지 판별하고 FISIS 조회에 필요한 업권 코드를 반환. 은행·보험사 분석 시 진입점으로 사용. |
| `dart_quota_status` | DART 일일 API quota 사용 현황(20,000건/일) 및 캐시 hit/miss 통계. |

### FISIS 금융통계정보시스템 (4개)

| 도구 | 설명 |
|------|------|
| `fisis_list_statistics` | 이용 가능한 통계 목록 검색. 업권(`lrg_div`)으로 필터링. |
| `fisis_get_statistics` | 단일 통계코드 데이터 조회. 기간·회사·항목 지정 가능. |
| `fisis_get_multi_statistics` | 복수 통계코드를 한 번에 조회. 2개 이상의 통계를 동시에 가져올 때 사용. |
| `fisis_list_companies` | 업권별 금융회사 목록. `lrg_div` 코드로 특정 업권 회사만 조회 가능. |

---

## FISIS 업권(lrg_div) 코드 안내

FISIS 통계는 업권별로 구분됩니다. 아래 코드를 `lrg_div` 파라미터에 입력하세요.

| 업권 | 코드 | 비고 |
|------|------|------|
| **은행** | | |
| 국내은행 | `A` | KB국민·신한·하나·우리·NH농협 등 |
| 외국은행 국내지점 | `J` | |
| **보험** | | |
| 생명보험 | `H` | 삼성생명·한화생명 등 |
| 손해보험 | `I` | 삼성화재·현대해상 등 |
| **금융투자** | | |
| 증권사(투자매매I) | `F` | |
| 증권사(투자매매II) | `W` | |
| 자산운용사 | `G` | |
| 투자자문사 | `X` | |
| 종합금융 | `D` | |
| 부동산신탁 | `M` | |
| **비은행** | | |
| 신용카드사 | `C` | 삼성카드·현대카드 등 |
| 리스사 | `K` | |
| 할부금융사 | `T` | |
| 신기술금융사 | `N` | |
| 저축은행 | `E` | |
| 신협 | `O` | |
| 농협(단위) | `Q` | |
| 수협(단위) | `P` | |
| 산림조합 | `S` | |
| **기타** | | |
| 금융지주회사 | `L` | KB금융·신한금융 등 |
| 공통(신탁) | `B` | |
| 공통(파생상품) | `R` | |

> ⚠️ **주의**: 보험사 조회 시 `H`(생명) 또는 `I`(손해)를 사용하세요. `C`는 신용카드사 코드입니다.

---

## 주요 FISIS 통계코드

자주 사용하는 통계코드 목록입니다.

| 통계코드 | 내용 | 주기 |
|---------|------|------|
| `SA017` | 은행 수익성 (ROA·ROE·NIM) | 분기 |
| `SA053` | 은행 BIS비율 (자기자본비율) | 분기 |
| `SA018` | 은행 유동성 (LCR·NSFR) | 분기 |
| `SA021` | 은행 요약손익계산서 | 분기 |
| `SA014` | 은행 자본적정성 (CET1·Tier1) | 분기 |
| `SC218` | 여신전문금융사 손익계산서 | 분기 |
| `SD107` | 금융투자 손익계산서 | 분기 |

특정 업권의 통계코드를 모를 경우 `fisis_list_statistics`에 업권 코드를 넣어 목록을 확인하세요.

---

## 실전 사용 예시

Claude Code 터미널에서 대화하듯 입력합니다.

### 기업 재무 분석

```
삼성전자 2024년 손익계산서 보여줘
카카오·네이버·쿠팡 2024년 영업이익 비교해줘
현대차 최근 5년 매출·영업이익 추이 분석해줘
```

### 금융업 분석

```
국내 시중은행 2024년 NIM(순이자마진) 비교해줘
KB금융·신한금융·하나금융 2024년 ROE 비교해줘
시중은행 BIS비율 최근 3년 추이 보여줘
삼성생명 2024년 주요 손익 지표 알려줘
```

### 공시 검색

```
삼성전자 최근 3개월 공시 목록 보여줘
LG에너지솔루션 최신 사업보고서에서 주요 위험 요인 찾아줘
```

### 운영

```
오늘 DART API 얼마나 썼어?
```

---

## DART가 없는 지표 → FISIS 대안

일부 지표는 DART 구조화 API로 직접 조회할 수 없습니다. 이때 FISIS를 사용하세요.

| 지표 | FISIS 대안 | DART 대안 |
|------|-----------|----------|
| CET1 비율 | `SA014` | `dart_business_report` (new_capital_securities) |
| LCR / NSFR | `SA018` | `dart_search_disclosures` |
| NIM | `SA017` (finance_cd 지정) | `dart_business_report` 주요경영지표 |
| PF 잔액·연체율 | 없음 | `dart_document_content` 사업보고서 본문 |
| AT1 세부내역 | 없음 | `dart_search_disclosures` (pblntf_ty='C') |

---

## 문서

| 문서 | 내용 |
|------|------|
| [`docs/USAGE.md`](docs/USAGE.md) | 실전 시나리오 10가지, 토큰 절약 팁, 로깅 설정 |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | 30+ 에러별 진단과 해결법 |
| [`.env.example`](.env.example) | 환경변수 템플릿 |

---

## 개발자 참고

### 테스트 실행

```bash
pip install -e ".[dev]"
pytest tests/
```

234개 테스트 통과 기대.

### 로컬 디버깅

```bash
LOG_LEVEL=DEBUG python -m financial_data_mcp
```

### 프로젝트 구조

```
financial-data-mcp/
├── financial_data_mcp/         ← MCP 서버 패키지
│   ├── server.py               # FastMCP 서버 + 19개 도구 정의
│   ├── dart_client.py          # DART OpenAPI 클라이언트
│   ├── fisis_client.py         # FISIS OpenAPI 클라이언트 (22개 업권 코드)
│   ├── _cache.py               # TTL 메모리 캐시 + 디스크 캐시
│   ├── _http.py                # 재시도 + HTTP 에러 변환
│   ├── _quota.py               # DART quota 트래킹
│   └── _validators.py          # 입력 검증
├── tests/                      # 234개 단위 테스트
│   ├── test_dart_client.py
│   ├── test_fisis_client.py
│   ├── test_server.py
│   └── test_e2e_scenarios.py
├── scripts/
│   └── preflight.py            # 환경 검증 스크립트
├── docs/
│   ├── USAGE.md
│   └── TROUBLESHOOTING.md
├── .mcp.json                   # Claude Code 프로젝트 자동 인식용
├── .env.example                # API 키 템플릿
└── pyproject.toml
```

---

## FAQ

**Q. Claude Code를 처음 써봐요. 어디서부터 시작해야 하나요?**  
A. [시작하기 섹션](#시작하기-처음-사용하는-분)의 Step 1~8을 순서대로 따라 하세요. 30분 정도면 첫 분석을 돌릴 수 있습니다.

**Q. Claude Code 없이 Claude.ai 웹에서도 사용할 수 있나요?**  
A. Claude.ai 웹 버전은 MCP를 지원하지 않습니다. Claude Code CLI 또는 Claude Desktop 앱이 필요합니다.

**Q. API 키를 팀원과 공유해도 되나요?**  
A. 권장하지 않습니다. DART API는 개인당 일일 20,000건 한도이며, 공유 시 한도가 빠르게 소진됩니다. 팀원 각자가 개인 키를 발급받아 사용하세요.

**Q. FISIS API 승인이 안 나요.**  
A. 신청 후 1~2 영업일 소요됩니다. 승인 전에는 preflight에서 `[FAIL]`로 표시되며 FISIS 관련 4개 도구만 동작하지 않습니다. DART 14개 도구는 즉시 사용 가능합니다.

**Q. 어떤 기업을 조회할 수 있나요?**  
A. DART에 등록된 모든 기업(약 9만 개). 단, 재무제표는 DART에 공시한 기업만 조회 가능합니다(비상장 소규모 기업은 제한적).

**Q. 은행·보험사 데이터는 DART와 FISIS 중 어느 걸 써야 하나요?**  
A. 특정 금융회사 개별 분석 → DART. 업권 전체 비교·추이 분석 → FISIS. `plan_data_query`가 자동으로 최적 경로를 선택해 줍니다.

**Q. `dart_to_fisis_bridge`는 언제 써야 하나요?**  
A. 조회하려는 기업이 금융기관인지 불명확할 때 먼저 이 도구를 호출하세요. 기업이 금융기관이면 FISIS 업권 코드(`lrg_div`)까지 알려줍니다.

**Q. 에러가 나요.**  
A. 먼저 `python scripts/preflight.py`로 환경을 점검하세요. 그래도 해결 안 되면 [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md)를 참고하거나 이슈를 남겨주세요.

---

## 라이선스

MIT

---

## 감사

- [금융감독원 DART OpenAPI](https://opendart.fss.or.kr) — 전자공시 데이터
- [금융감독원 FISIS OpenAPI](https://fisis.fss.or.kr) — 금융통계 데이터
- [Model Context Protocol](https://modelcontextprotocol.io) — MCP 프로토콜
