# FISIS Analytics Dashboard

## 프로젝트 개요
금융감독원(FISIS) 데이터 기반 분석 대시보드. Streamlit 웹앱 + Claude AI 챗봇.

## 기술 스택
- Streamlit - UI 프레임워크
- Pandas - 데이터 처리
- Plotly - 차트/시각화 (matplotlib 사용 금지)
- Anthropic SDK - AI 챗봇
- python-dotenv - 환경변수 관리

## 구조
- `app.py` - 전체 앱 로직 (단일 파일)
- `data/` - FISIS Excel 데이터 (git 미추적, 100MB+ 파일)
- `.env` - API 키 (git 미추적)

## 데이터
4개 금융권역: 국내은행(A), 신용카드사(C), 리스사(K), 할부금융사(T)
파일 형식: `FISIS_{코드}_{권역명}.xlsx`

## 규칙
- `data/` 폴더의 Excel 파일은 절대 수정 금지 (원본 데이터)
- API 키는 .env로 관리 (하드코딩 금지)
- Streamlit 캐시(`@st.cache_data`) 적극 활용
- 차트는 Plotly 사용

## 실행
```bash
source venv/Scripts/activate
streamlit run app.py
```
