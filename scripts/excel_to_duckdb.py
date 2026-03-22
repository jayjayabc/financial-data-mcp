"""
FISIS Excel 파일 → DuckDB 변환 스크립트
실행: python scripts/excel_to_duckdb.py

data/FISIS_*.xlsx 5개 파일의 모든 시트를 DuckDB 테이블로 변환
테이블명 규칙: {sector_code}_{stat_num} (예: K_103, A_SA045)
메타 테이블: sheet_meta
출력: data/fisis.duckdb
"""

import os
import re
import glob
import duckdb
import pandas as pd
from pathlib import Path

# 경로 설정
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH  = DATA_DIR / "fisis.duckdb"

# 업권 코드 → 이름 매핑
SECTOR_MAP = {
    "A": "국내은행",
    "C": "신용카드사",
    "K": "리스사",
    "N": "신기술금융사",
    "T": "할부금융사",
}


def sanitize_table_name(sector_code: str, sheet_name: str) -> str:
    """시트명 → DuckDB 테이블명 변환
    예) '103_요약재무상태표(자산)' → 'K_103'
    """
    # 시트명 앞부분의 숫자/영문 코드 추출 (예: '103', 'SA045', 'A007')
    match = re.match(r"^([A-Za-z0-9]+)", sheet_name)
    stat_num = match.group(1) if match else sheet_name[:10]
    # 테이블명: {sector}_{stat_num}, 특수문자 제거
    raw = f"{sector_code}_{stat_num}"
    return re.sub(r"[^A-Za-z0-9_]", "_", raw)


def extract_stat_num(sheet_name: str) -> str:
    """시트명에서 통계 코드 추출 (예: '103_요약...' → '103')"""
    match = re.match(r"^([A-Za-z0-9]+)", sheet_name)
    return match.group(1) if match else sheet_name[:10]


def convert_excel_to_duckdb() -> None:
    xlsx_files = glob.glob(str(DATA_DIR / "FISIS_*.xlsx"))
    if not xlsx_files:
        print(f"[오류] {DATA_DIR}에 FISIS_*.xlsx 파일이 없습니다.")
        return

    # 기존 DB 삭제 후 재생성
    if DB_PATH.exists():
        DB_PATH.unlink()
        print(f"기존 {DB_PATH.name} 삭제")

    con = duckdb.connect(str(DB_PATH))

    # 메타 테이블 생성
    con.execute("""
        CREATE TABLE sheet_meta (
            sector_code  VARCHAR,
            sector_name  VARCHAR,
            stat_num     VARCHAR,
            table_name   VARCHAR,
            sheet_name   VARCHAR,
            row_count    INTEGER,
            columns      VARCHAR,
            excel_file   VARCHAR
        )
    """)

    total_tables = 0

    for xlsx_path in sorted(xlsx_files):
        fname = os.path.basename(xlsx_path)
        # 파일명에서 업권 코드 추출: FISIS_K_리스사.xlsx → K
        match = re.match(r"FISIS_([A-Z])_", fname)
        if not match:
            print(f"  [스킵] 파일명 패턴 불일치: {fname}")
            continue

        sector_code = match.group(1)
        sector_name = SECTOR_MAP.get(sector_code, sector_code)
        print(f"\n[{sector_code}] {sector_name} ({fname})")

        try:
            xl = pd.ExcelFile(xlsx_path, engine="openpyxl")
        except Exception as e:
            print(f"  [오류] 파일 열기 실패: {e}")
            continue

        for sheet_name in xl.sheet_names:
            table_name = sanitize_table_name(sector_code, sheet_name)
            stat_num   = extract_stat_num(sheet_name)

            try:
                df = pd.read_excel(xlsx_path, sheet_name=sheet_name, engine="openpyxl")

                if df.empty:
                    print(f"  [스킵] 빈 시트: {sheet_name}")
                    continue

                # 컬럼명 안전화 (공백/특수문자 → _)
                df.columns = [re.sub(r"[^A-Za-z0-9_가-힣]", "_", str(c)) for c in df.columns]

                # DuckDB에 테이블 저장 (이미 존재하면 덮어쓰기)
                con.execute(f'DROP TABLE IF EXISTS "{table_name}"')
                con.register("_tmp_df", df)
                con.execute(f'CREATE TABLE "{table_name}" AS SELECT * FROM _tmp_df')
                con.unregister("_tmp_df")

                # 메타 테이블에 기록
                con.execute(
                    "INSERT INTO sheet_meta VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        sector_code,
                        sector_name,
                        stat_num,
                        table_name,
                        sheet_name,
                        len(df),
                        ", ".join(df.columns.tolist()),
                        fname,
                    ]
                )

                total_tables += 1
                print(f"  OK {sheet_name[:40]:40s} -> {table_name}  ({len(df):,}행)")

            except Exception as e:
                print(f"  [오류] {sheet_name}: {e}")

    con.close()
    db_size_mb = DB_PATH.stat().st_size / (1024 * 1024)
    print(f"\n완료: {total_tables}개 테이블 → {DB_PATH} ({db_size_mb:.1f}MB)")


if __name__ == "__main__":
    convert_excel_to_duckdb()
