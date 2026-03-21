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
