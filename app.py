import io
import os

import pandas as pd
import streamlit as st
from openai import OpenAI
from dotenv import load_dotenv

from db.loader import (
    is_db_ready,
    get_connection,
    get_sheet_meta,
    get_sectors,
    get_sheets_for_sector,
    load_table,
    run_query_safe,
)
from llm.sql_generator import generate_answer

load_dotenv()

# ══════════════════════════════════════════════════════════════
# 설정
# ══════════════════════════════════════════════════════════════
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

st.set_page_config(
    page_title="FISIS Analytics",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap');
* { font-family: 'Noto Sans KR', sans-serif !important; }

.main .block-container { background: #f4f6f9; padding: 2rem 2rem 2rem; }
section[data-testid="stSidebar"] { background: #0f1b2d !important; }
section[data-testid="stSidebar"] * { color: #e8edf3 !important; }
section[data-testid="stSidebar"] .stRadio label {
    padding: 8px 12px; border-radius: 6px; display: block;
    transition: background 0.2s;
}
section[data-testid="stSidebar"] .stRadio label:hover { background: #1e3a5f; }

div[data-testid="metric-container"] {
    background: white; border-radius: 10px; padding: 16px 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    border-left: 4px solid #1a73c8;
}

.dataframe { font-size: 13px !important; }
hr { border-color: #e0e6ed; margin: 1.2rem 0; }
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════
# DB 상태 확인
# ══════════════════════════════════════════════════════════════
if not is_db_ready():
    st.error("⚠️ `data/fisis.duckdb` 파일이 없습니다.")
    st.info("터미널에서 다음 명령을 실행하여 DB를 생성하세요:\n```\npython scripts/excel_to_duckdb.py\n```")
    st.stop()

# ══════════════════════════════════════════════════════════════
# 유틸리티 함수
# ══════════════════════════════════════════════════════════════
def fmt_val(v, unit: str = "억") -> str:
    """숫자 → 읽기 좋은 포맷 (억/조 단위)"""
    if pd.isna(v):
        return "-"
    v = float(v)
    if unit == "억":
        if abs(v) >= 10000:
            return f"{v/10000:.1f}조"
        return f"{v:,.0f}억"
    if unit == "%":
        return f"{v:.2f}%"
    return f"{v:,.1f}"


def build_capital_summary() -> pd.DataFrame:
    """캐피탈 업권(리스+할부금융) 재무요약 테이블 생성"""
    rows = []
    specs = [
        ("리스사",    "K", "K_103", "K_104", "K_118"),
        ("할부금융사", "T", "T_103", "T_104", "T_118"),
    ]

    for sector_name, sector_code, tbl103, tbl104, tbl118 in specs:
        try:
            df103 = load_table(tbl103)
            df104 = load_table(tbl104)
            df118 = load_table(tbl118)
        except Exception:
            continue

        if df103.empty:
            continue

        latest = df103["base_month"].max()

        assets = (
            df103[(df103["base_month"] == latest) & (df103["account_cd"] == "A")]
            [["finance_nm", "a"]].rename(columns={"a": "자산총계"})
        )
        equity = (
            df104[(df104["base_month"] == latest) & (df104["account_cd"] == "A2")]
            [["finance_nm", "a"]].rename(columns={"a": "자본총계"})
        )
        ni = (
            df118[(df118["base_month"] == latest) & (df118["account_cd"] == "J")]
            [["finance_nm", "a"]].rename(columns={"a": "당기순이익"})
        )
        rev = (
            df118[(df118["base_month"] == latest) & (df118["account_cd"] == "A")]
            [["finance_nm", "a"]].rename(columns={"a": "수익합계"})
        )

        df = (
            assets
            .merge(equity, on="finance_nm", how="left")
            .merge(ni,     on="finance_nm", how="left")
            .merge(rev,    on="finance_nm", how="left")
        )
        df["업권"]  = sector_name
        df["기준월"] = latest
        rows.append(df)

    if not rows:
        return pd.DataFrame()

    result = pd.concat(rows, ignore_index=True)

    # 원단위 → 억원
    for col in ["자산총계", "자본총계", "당기순이익", "수익합계"]:
        result[col] = pd.to_numeric(result[col], errors="coerce") / 1e8

    result["ROA(%)"]     = (result["당기순이익"] / result["자산총계"] * 100).round(2)
    result["자본비율(%)"] = (result["자본총계"]  / result["자산총계"] * 100).round(1)
    result["자본잠식"]    = result["자본총계"] < 0
    result["회사명"]      = result["finance_nm"].str.replace(r"㈜|주식회사|\s", "", regex=True)

    result = result.sort_values("자산총계", ascending=False).reset_index(drop=True)
    result.insert(0, "순위", range(1, len(result) + 1))
    return result


def styled_table(df: pd.DataFrame, money_cols=None, pct_cols=None) -> pd.DataFrame:
    """포맷팅된 display용 DataFrame 생성"""
    d = df.copy()
    if money_cols:
        for c in money_cols:
            if c in d.columns:
                d[c] = d[c].apply(lambda x: fmt_val(x, "억"))
    if pct_cols:
        for c in pct_cols:
            if c in d.columns:
                d[c] = d[c].apply(lambda x: f"{x:.2f}%" if pd.notna(x) else "-")
    return d


# ══════════════════════════════════════════════════════════════
# 사이드바
# ══════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 🏦 FISIS Analytics")
    st.markdown(
        "<small style='color:#8aa0b8'>금융감독원 금융통계정보시스템<br>기준: 2025년 9월</small>",
        unsafe_allow_html=True,
    )
    st.divider()
    menu = st.radio(
        "",
        ["🤖  AI 챗봇", "📋  데이터 조회"],
        label_visibility="collapsed",
    )

# ══════════════════════════════════════════════════════════════
# 🤖 AI 챗봇
# ══════════════════════════════════════════════════════════════
if menu == "🤖  AI 챗봇":
    st.markdown("# 🤖 AI 챗봇")
    st.markdown(
        "<span style='color:#666;font-size:13px'>실제 FISIS 데이터를 SQL로 조회하여 답변합니다</span>",
        unsafe_allow_html=True,
    )
    st.divider()

    if not GROQ_API_KEY:
        st.error("⚠️ `.env` 파일에 `GROQ_API_KEY`를 설정하세요.")
        st.stop()

    client = OpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )

    # 대화 이력 초기화
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # 예시 질문 버튼
    if not st.session_state.messages:
        st.markdown("**💡 예시 질문**")
        examples = [
            "리스사 자산 상위 10개사 순위 알려줘",
            "자본잠식 또는 당기순손실 캐피탈사 전체 목록",
            "할부금융사 ROA 상위 5개사와 하위 5개사 비교",
            "국내은행 가계대출 현황 요약",
            "현대캐피탈과 KB캐피탈 재무 비교",
        ]
        cols = st.columns(3)
        for i, ex in enumerate(examples):
            with cols[i % 3]:
                if st.button(ex, use_container_width=True, key=f"ex_{i}"):
                    st.session_state["pending_question"] = ex
                    st.rerun()
        st.divider()

    # 이전 대화 렌더링
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # 질문 입력 (예시 버튼 또는 입력창)
    prompt = st.session_state.pop("pending_question", None) or st.chat_input(
        "FISIS 데이터에 대해 질문하세요..."
    )

    if prompt:
        # 사용자 메시지 추가
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # AI 답변 생성 (스트리밍)
        with st.chat_message("assistant"):
            with st.spinner("📊 데이터 조회 중..."):
                reply_chunks = []
                placeholder = st.empty()
                full_reply = ""

                for chunk in generate_answer(
                    client=client,
                    question=prompt,
                    conversation_history=st.session_state.messages,
                ):
                    full_reply += chunk
                    placeholder.markdown(full_reply + "▌")

                placeholder.markdown(full_reply)
                st.session_state.messages.append(
                    {"role": "assistant", "content": full_reply}
                )

    # 대화 초기화 버튼
    if st.session_state.messages:
        if st.button("🗑️ 대화 초기화"):
            st.session_state.messages = []
            st.rerun()


# ══════════════════════════════════════════════════════════════
# 📋 데이터 조회
# ══════════════════════════════════════════════════════════════
elif menu == "📋  데이터 조회":
    st.markdown("# 📋 데이터 조회")
    st.divider()

    view_mode = st.radio(
        "조회 방식",
        ["📊 재무요약 (핵심지표)", "🔍 원본 데이터 조회"],
        horizontal=True,
    )

    if view_mode == "📊 재무요약 (핵심지표)":
        cap = build_capital_summary()
        if cap.empty:
            st.warning("캐피탈 데이터를 찾을 수 없습니다. DB가 정상인지 확인하세요.")
            st.stop()

        # 필터
        f1, f2, f3, f4 = st.columns([2, 2, 2, 2])
        with f1:
            sector_f = st.multiselect(
                "업권", ["리스사", "할부금융사"],
                default=["리스사", "할부금융사"],
            )
        with f2:
            search = st.text_input("회사명 검색", placeholder="예: 현대, KB...")
        with f3:
            sort_col = st.selectbox(
                "정렬 기준",
                ["자산총계", "자본총계", "당기순이익", "수익합계", "ROA(%)", "자본비율(%)"],
            )
        with f4:
            sort_asc = st.radio("정렬 방향", ["내림차순", "오름차순"], horizontal=True) == "오름차순"

        df_view = cap.copy()
        if sector_f:
            df_view = df_view[df_view["업권"].isin(sector_f)]
        if search:
            df_view = df_view[df_view["회사명"].str.contains(search, na=False)]
        df_view = df_view.sort_values(sort_col, ascending=sort_asc).reset_index(drop=True)
        df_view.insert(0, "순위", range(1, len(df_view) + 1))

        st.caption(f"총 {len(df_view)}개사")

        show_cols = ["순위", "회사명", "업권", "자산총계", "자본총계", "수익합계", "당기순이익", "ROA(%)", "자본비율(%)"]
        df_disp = styled_table(
            df_view[show_cols],
            money_cols=["자산총계", "자본총계", "수익합계", "당기순이익"],
            pct_cols=["ROA(%)", "자본비율(%)"],
        )
        st.dataframe(df_disp, use_container_width=True, hide_index=True, height=500)

        buf = io.BytesIO()
        df_view[show_cols].to_excel(buf, index=False, engine="openpyxl")
        st.download_button(
            "⬇️ 엑셀 다운로드", buf.getvalue(),
            file_name="FISIS_캐피탈_재무요약.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    else:  # 원본 데이터 조회
        meta = get_sheet_meta()
        sectors = get_sectors()
        sector_names = [s["name"] for s in sectors]

        c1, c2 = st.columns(2)
        with c1:
            selected_sector_name = st.selectbox("업권", sector_names)
        sector_code = next(s["code"] for s in sectors if s["name"] == selected_sector_name)

        sheets = get_sheets_for_sector(sector_code)
        sheet_labels = [f"{s['stat_num']}  {s['sheet_name']}" for s in sheets]

        with c2:
            selected_label = st.selectbox("통계항목", sheet_labels)

        selected_idx = sheet_labels.index(selected_label)
        table_name   = sheets[selected_idx]["table_name"]

        df_raw = load_table(table_name)

        # 숨길 컬럼
        hide_cols = ["sector_cd", "sector_nm", "stat_cd", "stat_nm", "finance_cd"]
        show_cols = [c for c in df_raw.columns if c not in hide_cols]

        # 필터
        fc1, fc2, fc3 = st.columns([3, 3, 2])
        with fc1:
            if "finance_nm" in df_raw.columns:
                cos = sorted(df_raw["finance_nm"].dropna().unique())
                sel = st.multiselect("회사 (미선택=전체)", cos)
                if sel:
                    df_raw = df_raw[df_raw["finance_nm"].isin(sel)]
        with fc2:
            if "base_month" in df_raw.columns:
                months = sorted(df_raw["base_month"].dropna().unique())
                if len(months) > 1:
                    sel_m = st.select_slider(
                        "기준월", options=months,
                        value=(months[0], months[-1]),
                    )
                    df_raw = df_raw[
                        (df_raw["base_month"] >= sel_m[0]) &
                        (df_raw["base_month"] <= sel_m[1])
                    ]
        with fc3:
            if "account_cd" in df_raw.columns:
                depth = st.selectbox("항목 깊이", ["전체", "1단계", "2단계", "3단계"])
                depth_map = {"1단계": 1, "2단계": 2, "3단계": 3}
                if depth in depth_map:
                    df_raw = df_raw[
                        df_raw["account_cd"].astype(str).str.len() <= depth_map[depth]
                    ]

        # 금액 컬럼 포맷
        df_disp = df_raw[show_cols].copy()
        for col in ["a", "b"]:
            if col in df_disp.columns:
                df_disp[col] = pd.to_numeric(df_disp[col], errors="coerce")
                if col == "b":
                    df_disp[col] = df_disp[col].apply(
                        lambda x: f"{x:.2f}%" if pd.notna(x) and abs(x) <= 200
                        else (fmt_val(x / 1e8, "억") if pd.notna(x) else "-")
                    )
                else:
                    df_disp[col] = df_disp[col].apply(
                        lambda x: fmt_val(x / 1e8, "억") if pd.notna(x) else "-"
                    )

        st.divider()
        st.dataframe(df_disp, use_container_width=True, hide_index=True, height=480)
        st.caption(f"총 {len(df_raw):,}행")

        buf = io.BytesIO()
        df_raw[show_cols].to_excel(buf, index=False, engine="openpyxl")
        st.download_button(
            "⬇️ 엑셀 다운로드", buf.getvalue(),
            file_name=f"FISIS_{selected_sector_name}_{table_name}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
