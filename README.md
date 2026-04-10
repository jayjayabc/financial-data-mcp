# 🏦 FISIS 금융 분석 대시보드

금융감독원 FISIS 데이터 기반 분석 웹앱  
Claude AI 챗봇 + 재무 테이블 조회 + 차트 시각화 + 엑셀 다운로드

---

## 📁 폴더 구조

```
fisis_app/
├── app.py               ← 메인 앱
├── requirements.txt     ← 패키지 목록
├── README.md
└── data/                ← ★ FISIS 엑셀 파일 여기에 넣기
    ├── FISIS_A_국내은행.xlsx
    ├── FISIS_C_신용카드사.xlsx
    ├── FISIS_K_리스사.xlsx
    └── FISIS_T_할부금융사.xlsx
```

---

## 🚀 로컬 실행 (처음 한 번만)

```powershell
# 1. 프로젝트 폴더로 이동
cd C:\Users\이재혁\Projects\fisis_app

# 2. 가상환경 생성 & 활성화
python -m venv venv
venv\Scripts\activate.bat

# 3. 패키지 설치
pip install -r requirements.txt

# 4. data 폴더 만들고 FISIS 파일 복사
mkdir data

# 5. 앱 실행
streamlit run app.py
```

브라우저에서 http://localhost:8501 접속

---

## ☁️ Streamlit Cloud 배포 (팀원 공유)

1. GitHub에 이 폴더 전체 올리기 (data 폴더 제외 — .gitignore 처리)
2. https://share.streamlit.io 접속
3. GitHub 저장소 연결
4. `app.py` 선택 후 Deploy
5. 생성된 URL을 팀원에게 공유

> ⚠️ FISIS 엑셀 파일은 용량이 크므로 GitHub에 올리지 말고  
> Streamlit Cloud의 Secrets 또는 별도 스토리지 사용 권장

---

## 🔑 Claude API Key

- 왼쪽 사이드바에서 입력
- 또는 Streamlit Cloud Secrets에 등록:
  ```toml
  ANTHROPIC_API_KEY = "sk-ant-..."
  ```

---

## 💡 주요 기능

| 메뉴 | 설명 |
|------|------|
| 🏠 홈 | 로드된 데이터 현황 |
| 📊 재무 조회 | 업권·회사·기간 필터링 테이블 |
| 📈 차트 분석 | 자산 포트폴리오 막대/레이더/테이블 |
| 🤖 AI 챗봇 | Claude API 기반 자유 질문 |
| ⬇️ 엑셀 다운로드 | 원하는 시트 선택 후 다운로드 |

---

## 🔌 MCP 서버 (DART & FISIS)

Claude Desktop / Claude Code에서 DART(전자공시시스템)과 FISIS(금융통계정보시스템) 데이터를 직접 조회·분석할 수 있는 MCP 서버입니다.

### API 키 발급

| 시스템 | 발급 URL | 환경변수 |
|--------|----------|----------|
| DART | https://opendart.fss.or.kr | `DART_API_KEY` |
| FISIS | https://fisis.fss.or.kr | `FISIS_API_KEY` |

### 설치 & 실행

```bash
# uv 사용 (권장)
uv pip install .

# 또는 pip
pip install .

# 실행
financial-data-mcp
# 또는
python -m financial_data_mcp
```

### Claude Desktop 설정

`claude_desktop_config.json`에 아래 내용을 추가합니다:

```json
{
  "mcpServers": {
    "financial-data": {
      "command": "uv",
      "args": ["--directory", "/path/to/fisis-app", "run", "financial-data-mcp"],
      "env": {
        "DART_API_KEY": "your-dart-api-key",
        "FISIS_API_KEY": "your-fisis-api-key"
      }
    }
  }
}
```

### 제공 도구 (10개)

**DART (전자공시시스템)**

| 도구 | 설명 |
|------|------|
| `dart_search_company` | 회사명으로 기업코드(corp_code) 검색 |
| `dart_company_overview` | 기업개황 (대표자, 업종, 주소 등) |
| `dart_search_disclosures` | 공시 목록 검색 (기간별, 유형별) |
| `dart_financial_statements` | 단일회사 주요 재무계정 (자산, 부채, 매출 등) |
| `dart_full_financial_statements` | 전체 재무제표 (재무상태표, 손익계산서 등) |
| `dart_multi_company_financials` | 다중회사 재무 비교 (최대 20개) |

**FISIS (금융통계정보시스템)**

| 도구 | 설명 |
|------|------|
| `fisis_list_statistics` | 조회 가능한 통계목록 검색 |
| `fisis_get_statistics` | 금융통계 데이터 조회 (기간별) |
| `fisis_list_companies` | 금융회사 목록 조회 |

**유틸리티**

| 도구 | 설명 |
|------|------|
| `get_api_reference` | API 코드 참조표 (보고서코드, 분류코드 등) |

### 사용 예시

```
"삼성전자 2024년 사업보고서 재무제표 보여줘"
→ dart_search_company → dart_financial_statements

"국내은행 2024년 자산 통계 비교해줘"
→ fisis_list_statistics → fisis_get_statistics

"현대자동차와 기아 재무 비교 분석해줘"
→ dart_search_company (x2) → dart_multi_company_financials
```
