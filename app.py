import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from anthropic import Anthropic
from dotenv import load_dotenv
import io, os, glob, re

load_dotenv()

# ══════════════════════════════════════════════════════════════
# 설정
# ══════════════════════════════════════════════════════════════
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DATA_DIR = "./data"

st.set_page_config(
    page_title="FISIS Analytics",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap');
* { font-family: 'Noto Sans KR', sans-serif !important; }

/* 전체 배경 */
.main .block-container { background: #f4f6f9; padding: 2rem 2rem 2rem; }
section[data-testid="stSidebar"] { background: #0f1b2d !important; }
section[data-testid="stSidebar"] * { color: #e8edf3 !important; }
section[data-testid="stSidebar"] .stRadio label { 
    padding: 8px 12px; border-radius: 6px; display: block; 
    transition: background 0.2s;
}
section[data-testid="stSidebar"] .stRadio label:hover { background: #1e3a5f; }

/* 카드 */
div[data-testid="metric-container"] {
    background: white; border-radius: 10px; padding: 16px 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    border-left: 4px solid #1a73c8;
}

/* 테이블 */
.dataframe { font-size: 13px !important; }

/* 구분선 */
hr { border-color: #e0e6ed; margin: 1.2rem 0; }
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════
# 데이터 로드 & 처리
# ══════════════════════════════════════════════════════════════
@st.cache_data
def load_all_data():
    dfs = {}
    for code, name in [("A","국내은행"),("C","신용카드사"),("K","리스사"),("T","할부금융사")]:
        files = glob.glob(os.path.join(DATA_DIR, f"FISIS_{code}_*.xlsx"))
        if files:
            try:
                xl = pd.ExcelFile(files[0])
                dfs[name] = {"path": files[0], "sheets": xl.sheet_names, "code": code}
            except Exception as e:
                st.warning(f"{name} 로드 실패: {e}")
    return dfs

@st.cache_data
def load_sheet(path, sheet):
    return pd.read_excel(path, sheet_name=sheet, engine="openpyxl")

@st.cache_data
def build_capital_summary():
    """캐피탈 업권 전사 재무요약 (리스+할부금융 통합)"""
    rows = []
    specs = [
        ("리스사",    "K", "103_요약재무상태표(자산)", "104_요약재무상태표(부채 및 자본)", "118_요약손익계산서(08.03말 이후)"),
        ("할부금융사", "T", "103_요약재무상태표(자산)", "104_요약재무상태표(부채 및 자본)", "118_요약손익계산서"),
    ]
    for sector, code, sh103, sh104, sh118 in specs:
        files = glob.glob(os.path.join(DATA_DIR, f"FISIS_{code}_*.xlsx"))
        if not files: continue
        path = files[0]
        try:
            df103 = load_sheet(path, sh103)
            df104 = load_sheet(path, sh104)
            df118 = load_sheet(path, sh118)
            latest = df103["base_month"].max()

            assets = df103[(df103["base_month"]==latest)&(df103["account_cd"]=="A")][["finance_nm","a"]].rename(columns={"a":"자산총계"})
            equity = df104[(df104["base_month"]==latest)&(df104["account_cd"]=="A2")][["finance_nm","a"]].rename(columns={"a":"자본총계"})
            ni     = df118[(df118["base_month"]==latest)&(df118["account_cd"]=="J")][["finance_nm","a"]].rename(columns={"a":"당기순이익"})
            rev    = df118[(df118["base_month"]==latest)&(df118["account_cd"]=="A")][["finance_nm","a"]].rename(columns={"a":"수익합계"})

            df = assets.merge(equity, on="finance_nm", how="left")\
                       .merge(ni,     on="finance_nm", how="left")\
                       .merge(rev,    on="finance_nm", how="left")
            df["업권"] = sector
            df["기준월"] = latest
            rows.append(df)
        except Exception as e:
            st.warning(f"{sector} 요약 실패: {e}")

    if not rows: return pd.DataFrame()

    result = pd.concat(rows, ignore_index=True)

    # 원단위 → 억원
    for col in ["자산총계","자본총계","당기순이익","수익합계"]:
        result[col] = pd.to_numeric(result[col], errors="coerce") / 1e8

    # 파생 지표
    result["ROA(%)"]    = (result["당기순이익"] / result["자산총계"] * 100).round(2)
    result["자본비율(%)"] = (result["자본총계"]  / result["자산총계"] * 100).round(1)
    result["자본잠식"]    = result["자본총계"] < 0

    # 회사명 정리 (괄호 제거)
    result["회사명"] = result["finance_nm"].str.replace(r"㈜|주식회사|\s", "", regex=True)

    result = result.sort_values("자산총계", ascending=False).reset_index(drop=True)
    result.insert(0, "순위", range(1, len(result)+1))
    return result

def fmt_val(v, unit="억"):
    """숫자 → 읽기 좋은 포맷"""
    if pd.isna(v): return "-"
    v = float(v)
    if unit == "억":
        if abs(v) >= 10000: return f"{v/10000:.1f}조"
        return f"{v:,.0f}억"
    if unit == "%": return f"{v:.2f}%"
    return f"{v:,.1f}"

def styled_table(df, money_cols=None, pct_cols=None, highlight_neg=None):
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

def build_data_context(all_data, question: str) -> str:
    """질문 키워드 → 관련 시트 데이터 추출"""
    q = question.lower()
    hints = []
    if any(k in q for k in ["가계대출","가계"]): hints += [("국내은행","SA045"),("리스사","021"),("할부금융사","021")]
    if any(k in q for k in ["자산","규모","순위","크기","1위"]): hints += [("리스사","103"),("할부금융사","103")]
    if any(k in q for k in ["순이익","손익","수익","매출","이익"]): hints += [("리스사","118"),("할부금융사","118")]
    if any(k in q for k in ["자본","부채","잠식","레버리지"]): hints += [("리스사","104"),("할부금융사","104")]
    if any(k in q for k in ["은행","뱅크","국민","신한","하나","우리","농협"]): hints += [("국내은행","SA045"),("국내은행","007")]
    if any(k in q for k in ["카드","신용카드"]): hints += [("신용카드사","103"),("신용카드사","118")]
    if any(k in q for k in ["비교","포트폴리오","구성","대출채권","리스자산"]): hints += [("리스사","103"),("할부금융사","103")]
    if not hints: hints = [("리스사","103"),("할부금융사","103")]

    parts, loaded = [], set()
    for sector, kw in hints:
        if sector not in all_data or len(loaded) >= 5: continue
        for sh in all_data[sector]["sheets"]:
            key = f"{sector}_{sh}"
            if key in loaded or kw not in sh: continue
            try:
                df = load_sheet(all_data[sector]["path"], sh)
                latest = df["base_month"].max() if "base_month" in df.columns else None
                if latest: df = df[df["base_month"] == latest]
                # 1~2단계 항목만
                if "account_cd" in df.columns:
                    df = df[df["account_cd"].astype(str).str.len() <= 2]
                # 금액 컬럼 억원 변환
                for col in ["a","b"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                        df[col] = df[col].apply(lambda x: round(x/1e8,1) if pd.notna(x) else None)
                # 불필요 컬럼 제거
                drop_cols = [c for c in ["sector_cd","stat_cd","stat_nm","finance_cd"] if c in df.columns]
                df = df.drop(columns=drop_cols)
                parts.append(f"\n[{sector} / {sh} / 기준월:{latest} / 단위:억원]\n{df.to_string(index=False, max_rows=80)}")
                loaded.add(key)
                break
            except: pass
    return "\n\n".join(parts) if parts else "관련 데이터를 찾지 못했습니다."

# ══════════════════════════════════════════════════════════════
# 사이드바
# ══════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 🏦 FISIS Analytics")
    st.markdown("<small style='color:#8aa0b8'>금융감독원 금융통계정보시스템<br>기준: 2025년 9월</small>", unsafe_allow_html=True)
    st.divider()
    menu = st.radio("", [
        "🏠  대시보드",
        "📋  재무 현황",
        "📊  차트 분석",
        "🤖  AI 분석",
        "⬇️  데이터 추출",
    ], label_visibility="collapsed")

all_data = load_all_data()

# ══════════════════════════════════════════════════════════════
# 🏠 대시보드
# ══════════════════════════════════════════════════════════════
if menu == "🏠  대시보드":
    st.markdown("# 🏦 FISIS Analytics")
    st.markdown("<span style='color:#666;font-size:14px'>금융감독원 FISIS 기반 금융권 재무 분석 플랫폼 &nbsp;|&nbsp; 기준: 2025년 9월</span>", unsafe_allow_html=True)
    st.divider()

    if not all_data:
        st.error("⚠️ data/ 폴더에 FISIS 파일을 넣어주세요.")
        st.stop()

    # KPI
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("국내은행 가계대출", "1,003.7조원", "2025년 9월")
    k2.metric("가계대출 1위", "국민은행", "182.0조원")
    k3.metric("캐피탈 업권 회사 수", "52개사", "리스26 + 할부26")
    k4.metric("캐피탈 자산 1위", "현대캐피탈", "39.8조원")
    k5.metric("자본잠식 캐피탈", "2개사 ⚠️", "씨앤에이치·무궁화")

    st.divider()

    # 캐피탈 요약 로드
    cap = build_capital_summary()

    if not cap.empty:
        col_l, col_r = st.columns([3, 2])

        with col_l:
            st.markdown("#### 📊 캐피탈 업권 자산 Top 15")
            top15 = cap.head(15).copy()
            fig = px.bar(
                top15, x="자산총계", y="회사명", orientation="h",
                color="업권", color_discrete_map={"리스사":"#1a73c8","할부금융사":"#00897b"},
                text=top15["자산총계"].apply(lambda x: fmt_val(x,"억")),
                labels={"자산총계":"자산총계(억원)"}
            )
            fig.update_layout(
                height=440, yaxis=dict(autorange="reversed"),
                plot_bgcolor="white", paper_bgcolor="white",
                margin=dict(l=0,r=20,t=20,b=0),
                legend=dict(orientation="h", yanchor="bottom", y=1.02)
            )
            fig.update_traces(textposition="outside")
            st.plotly_chart(fig, use_container_width=True)

        with col_r:
            st.markdown("#### 💰 수익성 산포도 (자산 vs ROA)")
            fig2 = px.scatter(
                cap[cap["자산총계"]>0], x="자산총계", y="ROA(%)",
                color="업권", size="자산총계", size_max=40,
                hover_name="회사명",
                color_discrete_map={"리스사":"#1a73c8","할부금융사":"#00897b"},
                labels={"자산총계":"자산총계(억)","ROA(%)":"ROA(%)"}
            )
            fig2.add_hline(y=0, line_dash="dash", line_color="red", opacity=0.5)
            fig2.update_layout(
                height=440, plot_bgcolor="white", paper_bgcolor="white",
                margin=dict(l=0,r=0,t=20,b=0),
                legend=dict(orientation="h", yanchor="bottom", y=1.02)
            )
            st.plotly_chart(fig2, use_container_width=True)

        st.divider()

        # 자본잠식 경고
        insolvent = cap[cap["자본잠식"]]
        if not insolvent.empty:
            st.markdown("#### ⚠️ 자본잠식 현황")
            cols_show = ["순위","회사명","업권","자산총계","자본총계","당기순이익","ROA(%)"]
            df_show = styled_table(insolvent[cols_show], money_cols=["자산총계","자본총계","당기순이익"], pct_cols=["ROA(%)"])
            st.dataframe(df_show, use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════
# 📋 재무 현황
# ══════════════════════════════════════════════════════════════
elif menu == "📋  재무 현황":
    st.markdown("# 📋 재무 현황")
    st.divider()

    if not all_data:
        st.error("data/ 폴더에 FISIS 파일을 넣어주세요.")
        st.stop()

    view_mode = st.radio("조회 방식", ["📊 재무요약 (핵심지표)", "🔍 원본 데이터 조회"], horizontal=True)

    if view_mode == "📊 재무요약 (핵심지표)":
        cap = build_capital_summary()
        if cap.empty:
            st.warning("캐피탈 데이터를 찾을 수 없습니다.")
            st.stop()

        # 필터
        f1, f2, f3, f4 = st.columns([2,2,2,2])
        with f1:
            sector_f = st.multiselect("업권", ["리스사","할부금융사"], default=["리스사","할부금융사"])
        with f2:
            search = st.text_input("회사명 검색", placeholder="예: 현대, KB...")
        with f3:
            sort_col = st.selectbox("정렬 기준", ["자산총계","자본총계","당기순이익","수익합계","ROA(%)","자본비율(%)"])
        with f4:
            sort_asc = st.radio("정렬 방향", ["내림차순","오름차순"], horizontal=True) == "오름차순"

        df_view = cap.copy()
        if sector_f: df_view = df_view[df_view["업권"].isin(sector_f)]
        if search:   df_view = df_view[df_view["회사명"].str.contains(search, na=False)]
        df_view = df_view.sort_values(sort_col, ascending=sort_asc).reset_index(drop=True)
        df_view.insert(0, "순위", range(1, len(df_view)+1))

        st.caption(f"총 {len(df_view)}개사")

        # 표시 컬럼만
        show_cols = ["순위","회사명","업권","자산총계","자본총계","수익합계","당기순이익","ROA(%)","자본비율(%)"]
        df_disp = styled_table(
            df_view[show_cols],
            money_cols=["자산총계","자본총계","수익합계","당기순이익"],
            pct_cols=["ROA(%)","자본비율(%)"]
        )
        st.dataframe(df_disp, use_container_width=True, hide_index=True, height=500)

        # 다운로드
        buf = io.BytesIO()
        df_view[show_cols].to_excel(buf, index=False)
        st.download_button("⬇️ 엑셀 다운로드", buf.getvalue(),
                           file_name="FISIS_캐피탈_재무요약.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    else:  # 원본 데이터 조회
        c1, c2 = st.columns(2)
        with c1: sector = st.selectbox("업권", list(all_data.keys()))
        with c2: sheet  = st.selectbox("통계항목", all_data[sector]["sheets"])

        df_raw = load_sheet(all_data[sector]["path"], sheet)

        # 표시할 컬럼 자동 선택 (분류 컬럼 제외)
        hide_cols = ["sector_cd","sector_nm","stat_cd","stat_nm","finance_cd"]
        show_cols = [c for c in df_raw.columns if c not in hide_cols]

        fc1, fc2, fc3 = st.columns([3,3,2])
        with fc1:
            if "finance_nm" in df_raw.columns:
                cos = sorted(df_raw["finance_nm"].dropna().unique())
                sel = st.multiselect("회사 (미선택=전체)", cos)
                if sel: df_raw = df_raw[df_raw["finance_nm"].isin(sel)]
        with fc2:
            if "base_month" in df_raw.columns:
                months = sorted(df_raw["base_month"].dropna().unique())
                if len(months) > 1:
                    sel_m = st.select_slider("기준월", options=months, value=(months[0], months[-1]))
                    df_raw = df_raw[(df_raw["base_month"]>=sel_m[0])&(df_raw["base_month"]<=sel_m[1])]
        with fc3:
            if "account_cd" in df_raw.columns:
                depth = st.selectbox("항목 깊이", ["전체","1단계","2단계","3단계"])
                depth_map = {"1단계":1,"2단계":2,"3단계":3}
                if depth in depth_map:
                    df_raw = df_raw[df_raw["account_cd"].astype(str).str.len() <= depth_map[depth]]

        # 금액 컬럼 포맷
        df_disp = df_raw[show_cols].copy()
        for col in ["a","b"]:
            if col in df_disp.columns:
                df_disp[col] = pd.to_numeric(df_disp[col], errors="coerce")
                # b 컬럼은 % 비율인 경우 많음
                if col == "b":
                    df_disp[col] = df_disp[col].apply(lambda x: f"{x:.2f}%" if pd.notna(x) and abs(x) <= 200 else (fmt_val(x/1e8,"억") if pd.notna(x) else "-"))
                else:
                    df_disp[col] = df_disp[col].apply(lambda x: fmt_val(x/1e8,"억") if pd.notna(x) else "-")

        st.divider()
        st.dataframe(df_disp, use_container_width=True, hide_index=True, height=480)
        st.caption(f"총 {len(df_raw):,}행")

        buf = io.BytesIO()
        df_raw[show_cols].to_excel(buf, index=False)
        st.download_button("⬇️ 엑셀 다운로드", buf.getvalue(),
                           file_name=f"FISIS_{sector}_{sheet[:20]}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ══════════════════════════════════════════════════════════════
# 📊 차트 분석
# ══════════════════════════════════════════════════════════════
elif menu == "📊  차트 분석":
    st.markdown("# 📊 차트 분석")
    st.divider()

    cap = build_capital_summary()

    tab1, tab2, tab3, tab4 = st.tabs(["🏆 순위 비교", "💰 수익성", "⚖️ 재무건전성", "🔬 포트폴리오"])

    with tab1:
        st.markdown("#### 업권별 자산 규모 순위")
        n = st.slider("상위 N개사", 5, 52, 20)
        fig = px.bar(cap.head(n), x="자산총계", y="회사명", orientation="h",
                     color="업권", color_discrete_map={"리스사":"#1a73c8","할부금융사":"#00897b"},
                     text=cap.head(n)["자산총계"].apply(lambda x: fmt_val(x,"억")),
                     labels={"자산총계":"자산총계(억원)"})
        fig.update_layout(height=max(400, n*22), yaxis=dict(autorange="reversed"),
                          plot_bgcolor="white", paper_bgcolor="white", margin=dict(l=0,r=80,t=10,b=0))
        fig.update_traces(textposition="outside")
        st.plotly_chart(fig, use_container_width=True)

    with tab2:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("#### ROA 상위 20개사")
            df_roa = cap[cap["자산총계"]>100].nlargest(20,"ROA(%)").copy()
            fig_roa = px.bar(df_roa, x="ROA(%)", y="회사명", orientation="h",
                             color="업권", color_discrete_map={"리스사":"#1a73c8","할부금융사":"#00897b"},
                             text=df_roa["ROA(%)"].apply(lambda x: f"{x:.2f}%"))
            fig_roa.update_layout(height=440, yaxis=dict(autorange="reversed"),
                                  plot_bgcolor="white", paper_bgcolor="white", margin=dict(l=0,r=60,t=10,b=0))
            fig_roa.update_traces(textposition="outside")
            st.plotly_chart(fig_roa, use_container_width=True)
        with c2:
            st.markdown("#### 순이익 상위 20개사")
            df_ni = cap.nlargest(20,"당기순이익").copy()
            fig_ni = px.bar(df_ni, x="당기순이익", y="회사명", orientation="h",
                            color="업권", color_discrete_map={"리스사":"#1a73c8","할부금융사":"#00897b"},
                            text=df_ni["당기순이익"].apply(lambda x: fmt_val(x,"억")))
            fig_ni.update_layout(height=440, yaxis=dict(autorange="reversed"),
                                 plot_bgcolor="white", paper_bgcolor="white", margin=dict(l=0,r=80,t=10,b=0))
            fig_ni.update_traces(textposition="outside")
            st.plotly_chart(fig_ni, use_container_width=True)

        # 버블
        st.markdown("#### 자산규모 vs 수익성 (버블=수익합계)")
        df_bub = cap[cap["자산총계"]>100].copy()
        fig_b = px.scatter(df_bub, x="자산총계", y="ROA(%)", size="수익합계",
                           color="업권", hover_name="회사명", size_max=50,
                           color_discrete_map={"리스사":"#1a73c8","할부금융사":"#00897b"},
                           labels={"자산총계":"자산총계(억)","ROA(%)":"ROA(%)"})
        fig_b.add_hline(y=0, line_dash="dash", line_color="red", opacity=0.4)
        fig_b.update_layout(height=420, plot_bgcolor="white", paper_bgcolor="white")
        st.plotly_chart(fig_b, use_container_width=True)

    with tab3:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("#### 자기자본비율 분포")
            df_cap2 = cap[cap["자산총계"]>100].copy()
            fig_cap = px.histogram(df_cap2, x="자본비율(%)", color="업권", nbins=20,
                                   color_discrete_map={"리스사":"#1a73c8","할부금융사":"#00897b"},
                                   labels={"자본비율(%)":"자기자본비율(%)"})
            fig_cap.update_layout(height=360, plot_bgcolor="white", paper_bgcolor="white")
            st.plotly_chart(fig_cap, use_container_width=True)
        with c2:
            st.markdown("#### 자본잠식 / 적자 현황")
            summary = pd.DataFrame({
                "구분": ["정상","자본잠식","당기순손실"],
                "리스사":    [len(cap[(cap["업권"]=="리스사")&(~cap["자본잠식"])&(cap["당기순이익"]>=0)]),
                             len(cap[(cap["업권"]=="리스사")&cap["자본잠식"]]),
                             len(cap[(cap["업권"]=="리스사")&(cap["당기순이익"]<0)])],
                "할부금융사": [len(cap[(cap["업권"]=="할부금융사")&(~cap["자본잠식"])&(cap["당기순이익"]>=0)]),
                              len(cap[(cap["업권"]=="할부금융사")&cap["자본잠식"]]),
                              len(cap[(cap["업권"]=="할부금융사")&(cap["당기순이익"]<0)])],
            })
            fig_s = px.bar(summary.melt(id_vars="구분"), x="구분", y="value", color="variable",
                           barmode="group", labels={"value":"개사수","variable":"업권"},
                           color_discrete_map={"리스사":"#1a73c8","할부금융사":"#00897b"})
            fig_s.update_layout(height=360, plot_bgcolor="white", paper_bgcolor="white")
            st.plotly_chart(fig_s, use_container_width=True)

        # 자본잠식/손실 목록
        st.markdown("#### ⚠️ 주의 대상 회사")
        at_risk = cap[(cap["자본잠식"])|(cap["당기순이익"]<0)].copy()
        show = ["순위","회사명","업권","자산총계","자본총계","당기순이익","ROA(%)","자본비율(%)"]
        df_risk = styled_table(at_risk[show], money_cols=["자산총계","자본총계","당기순이익"], pct_cols=["ROA(%)","자본비율(%)"])
        st.dataframe(df_risk, use_container_width=True, hide_index=True)

    with tab4:
        st.markdown("#### 자산 포트폴리오 비교 (애큐온캐피탈 동규모 그룹)")
        AD = {
            "애큐온":    {"대출채권":68.6,"유가증권":22.4,"리스":4.9,"할부":0.4,"신기술":1.5,"기타":1.9},
            "아이엠":    {"대출채권":51.6,"유가증권":7.7,"리스":30.2,"할부":2.3,"신기술":1.5,"기타":1.5},
            "한국캐피탈":{"대출채권":66.9,"유가증권":11.4,"리스":8.8,"할부":7.1,"신기술":1.2,"기타":2.6},
            "오릭스":    {"대출채권":24.9,"유가증권":0.0,"리스":62.7,"할부":7.6,"신기술":0.0,"기타":4.1},
            "벤츠파이낸셜":{"대출채권":26.5,"유가증권":0.0,"리스":45.0,"할부":27.2,"신기술":0.0,"기타":0.9},
        }
        CATS = ["대출채권","유가증권","리스","할부","신기술","기타"]

        p1, p2 = st.columns(2)
        with p1:
            df_port = pd.DataFrame([{"회사":co,"항목":c,"비중(%)":d[c]} for co,d in AD.items() for c in CATS])
            fig_port = px.bar(df_port, x="회사", y="비중(%)", color="항목",
                              color_discrete_sequence=px.colors.qualitative.Set2, title="자산 구성 비중")
            fig_port.update_layout(height=380, plot_bgcolor="white", paper_bgcolor="white")
            st.plotly_chart(fig_port, use_container_width=True)
        with p2:
            fig_r = go.Figure()
            CLR = ["#1a73c8","#00897b","#f57c00","#7b1fa2","#c62828"]
            for i,(co,d) in enumerate(AD.items()):
                v = [d[c] for c in CATS]+[d[CATS[0]]]
                fig_r.add_trace(go.Scatterpolar(r=v, theta=CATS+[CATS[0]], fill="toself", name=co,
                    line_color=CLR[i], fillcolor=CLR[i],
                    opacity=0.85 if co=="애큐온" else 0.12,
                    line=dict(width=3 if co=="애큐온" else 1.5)))
            fig_r.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0,80])),
                                title="레이더 차트", height=380)
            st.plotly_chart(fig_r, use_container_width=True)

# ══════════════════════════════════════════════════════════════
# 🤖 AI 분석
# ══════════════════════════════════════════════════════════════
elif menu == "🤖  AI 분석":
    st.markdown("# 🤖 AI 분석")
    st.markdown("<span style='color:#666;font-size:13px'>실제 FISIS 데이터를 읽어 답변합니다</span>", unsafe_allow_html=True)
    st.divider()

    if ANTHROPIC_API_KEY == "여기에_API_키_입력":
        st.error("⚠️ app.py 상단의 `ANTHROPIC_API_KEY`에 Claude API 키를 입력해주세요.")
        st.stop()

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    SYSTEM = """당신은 한국 금융감독원 FISIS(금융통계정보시스템) 전문 분석가입니다.
규칙:
1. [FISIS 데이터] 섹션의 실제 데이터를 최우선으로 활용해 정확하게 답변하세요.
2. 금액은 반드시 억원 또는 조원 단위로 명확히 표현하세요. (1조 = 10,000억)
3. 순위/비교 요청 시 마크다운 테이블로 정리하세요.
4. 데이터에 없는 내용은 "제공된 데이터에서 확인되지 않습니다"라고 하세요.
5. 인사이트와 시사점을 간결하게 추가하세요.
6. 모든 답변은 한국어로 하세요."""

    if "messages" not in st.session_state:
        st.session_state.messages = []

    # 예시 질문
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
                    st.session_state["pq"] = ex
                    st.rerun()
        st.divider()

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    prompt = st.session_state.pop("pq", None) or st.chat_input("FISIS 데이터에 대해 질문하세요...")

    if prompt:
        st.session_state.messages.append({"role":"user","content":prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("📊 데이터 조회 중..."):
                ctx = build_data_context(all_data, prompt)
                aug = f"[FISIS 데이터]\n{ctx}\n\n[질문]\n{prompt}"
                msgs = st.session_state.messages[:-1] + [{"role":"user","content":aug}]
                resp = client.messages.create(
                    model="claude-sonnet-4-6", max_tokens=2000,
                    system=SYSTEM, messages=msgs
                )
                reply = resp.content[0].text
                st.markdown(reply)
                st.session_state.messages.append({"role":"assistant","content":reply})

    if st.session_state.messages:
        if st.button("🗑️ 대화 초기화"):
            st.session_state.messages = []
            st.rerun()

# ══════════════════════════════════════════════════════════════
# ⬇️ 데이터 추출
# ══════════════════════════════════════════════════════════════
elif menu == "⬇️  데이터 추출":
    st.markdown("# ⬇️ 데이터 추출")
    st.divider()

    if not all_data:
        st.error("data/ 폴더에 FISIS 파일을 넣어주세요.")
        st.stop()

    tab1, tab2 = st.tabs(["📊 재무요약 다운로드", "🗂️ 원본 시트 추출"])

    with tab1:
        st.markdown("캐피탈 업권 전체 재무요약을 엑셀로 다운로드합니다.")
        cap = build_capital_summary()
        if not cap.empty:
            st.dataframe(styled_table(
                cap[["순위","회사명","업권","자산총계","자본총계","수익합계","당기순이익","ROA(%)","자본비율(%)"]],
                money_cols=["자산총계","자본총계","수익합계","당기순이익"],
                pct_cols=["ROA(%)","자본비율(%)"]
            ), use_container_width=True, hide_index=True, height=300)

            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as w:
                cap.to_excel(w, sheet_name="캐피탈_재무요약", index=False)
            st.download_button("⬇️ 재무요약 엑셀 다운로드", buf.getvalue(),
                               file_name="FISIS_캐피탈_재무요약_202509.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               type="primary")

    with tab2:
        c1, c2 = st.columns(2)
        with c1: sector = st.selectbox("업권", list(all_data.keys()))
        with c2:
            sheets = all_data[sector]["sheets"]
            sel = st.multiselect("시트 선택 (미선택=전체)", sheets)
            if not sel: sel = sheets

        st.info(f"선택: {len(sel)}개 시트")
        if st.button("📦 엑셀 생성", type="primary"):
            with st.spinner("생성 중..."):
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine="openpyxl") as w:
                    for sh in sel:
                        load_sheet(all_data[sector]["path"], sh).to_excel(w, sheet_name=sh[:31], index=False)
            st.download_button("⬇️ 다운로드", buf.getvalue(),
                               file_name=f"FISIS_{sector}_추출.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            st.success(f"✅ {len(sel)}개 시트 완료!")
