"""
DuckDB 연결 및 쿼리 모듈
fisis.duckdb에 대한 모든 읽기 작업을 담당
"""

import os
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

DB_PATH = Path(__file__).parent.parent / "data" / "fisis.duckdb"


@st.cache_resource
def get_connection() -> duckdb.DuckDBPyConnection:
    """DuckDB 읽기 전용 연결 (앱 전체에서 공유)"""
    if not DB_PATH.exists():
        st.error(f"⚠️ DB 파일 없음: {DB_PATH}\n`python scripts/excel_to_duckdb.py` 를 먼저 실행하세요.")
        st.stop()
    return duckdb.connect(str(DB_PATH), read_only=True)


def is_db_ready() -> bool:
    """DB 파일 존재 여부 확인"""
    return DB_PATH.exists()


@st.cache_data(ttl=3600)
def get_sheet_meta() -> pd.DataFrame:
    """sheet_meta 테이블 전체 조회"""
    con = get_connection()
    return con.execute("SELECT * FROM sheet_meta ORDER BY sector_code, stat_num").df()


@st.cache_data(ttl=3600)
def get_sectors() -> list[dict]:
    """업권 목록 반환 [{code, name}, ...]"""
    meta = get_sheet_meta()
    seen = {}
    for _, row in meta.iterrows():
        code = row["sector_code"]
        if code not in seen:
            seen[code] = row["sector_name"]
    return [{"code": k, "name": v} for k, v in seen.items()]


@st.cache_data(ttl=3600)
def get_sheets_for_sector(sector_code: str) -> list[dict]:
    """특정 업권의 시트 목록 반환 [{table_name, sheet_name, stat_num}, ...]"""
    meta = get_sheet_meta()
    rows = meta[meta["sector_code"] == sector_code]
    return rows[["table_name", "sheet_name", "stat_num"]].to_dict("records")


@st.cache_data(ttl=3600)
def load_table(table_name: str, limit: int = 0) -> pd.DataFrame:
    """테이블 전체 또는 N행 조회"""
    con = get_connection()
    sql = f'SELECT * FROM "{table_name}"'
    if limit > 0:
        sql += f" LIMIT {limit}"
    return con.execute(sql).df()


@st.cache_data(ttl=3600)
def query(sql: str) -> pd.DataFrame:
    """안전한 SELECT 쿼리 실행 (읽기 전용 보장)"""
    # SELECT 문만 허용
    stripped = sql.strip().upper()
    if not stripped.startswith("SELECT") and not stripped.startswith("WITH"):
        raise ValueError("SELECT 또는 WITH 쿼리만 허용됩니다.")
    con = get_connection()
    return con.execute(sql).df()


def run_query_safe(sql: str, max_rows: int = 200, timeout_sec: int = 10) -> tuple[pd.DataFrame | None, str | None]:
    """
    Text-to-SQL 결과 실행용 래퍼
    Returns: (DataFrame, error_message)
    """
    try:
        stripped = sql.strip().upper()
        if not (stripped.startswith("SELECT") or stripped.startswith("WITH")):
            return None, "SELECT 또는 WITH 쿼리만 실행할 수 있습니다."

        con = get_connection()

        # LIMIT 없으면 자동 추가
        if "LIMIT" not in stripped:
            sql = sql.rstrip(";") + f" LIMIT {max_rows}"

        df = con.execute(sql).df()
        return df, None

    except Exception as e:
        return None, str(e)


@st.cache_data(ttl=3600)
def get_schema_summary() -> str:
    """
    LLM 프롬프트용 스키마 요약 문자열 생성
    sheet_meta를 읽어서 테이블 목록과 대표 컬럼 정보를 반환
    """
    meta = get_sheet_meta()

    lines = ["# FISIS DuckDB 스키마\n"]
    lines.append("## sheet_meta 테이블\n")
    lines.append("| sector_code | sector_name | stat_num | table_name | sheet_name | row_count |\n")
    lines.append("|---|---|---|---|---|---|\n")

    for _, row in meta.iterrows():
        lines.append(
            f'| {row["sector_code"]} | {row["sector_name"]} | {row["stat_num"]}'
            f' | {row["table_name"]} | {row["sheet_name"][:30]} | {row["row_count"]:,} |\n'
        )

    lines.append("\n## 데이터 테이블 컬럼 구조\n")
    lines.append("모든 데이터 테이블의 공통 컬럼:\n")
    lines.append("- `base_month`: 기준월 (예: 202509)\n")
    lines.append("- `finance_cd`: 금융회사 코드\n")
    lines.append("- `finance_nm`: 금융회사명 (예: 현대캐피탈, KB캐피탈)\n")
    lines.append("- `account_cd`: 계정과목 코드 (1-2자리=대분류, 3자리=중분류)\n")
    lines.append("- `account_nm`: 계정과목명 (예: 자산총계, 부채총계, 당기순이익)\n")
    lines.append("- `a`: 금액 VARCHAR 타입 (원 단위). 반드시 TRY_CAST(a AS DOUBLE)로 변환 후 계산. 억원 환산: TRY_CAST(a AS DOUBLE)/1e8\n")
    lines.append("- `b`: DOUBLE 타입. 비율(%) 또는 전기 금액\n")

    lines.append("\n## 주요 account_cd 코드\n")
    lines.append("재무상태표(103, 104 테이블):\n")
    lines.append("- `A` = 자산총계, `B` = 부채총계, `A2` = 자본총계\n")
    lines.append("손익계산서(118 테이블):\n")
    lines.append("- `A` = 수익합계, `J` = 당기순이익\n")

    return "".join(lines)
