"""라이브 FISIS API로 전 권역 레지스트리를 구축하고 lrg_div 매핑을 검증한다.

검증 항목:
1. FisisRegistry.ensure_loaded 로 A~Z 전 권역 회사목록을 한 번에 수집
2. 권역별 등록 회사 수 + 한글 라벨(lrg_div_nm) 요약
3. 데라게란덴(0011663) → K/리스사 조회 확인
4. 투자일임업 등 하드코딩에 없는 권역도 함께 포착되는지 확인
5. 전체 스냅샷을 scripts/fisis_registry_snapshot.json 으로 저장
   (필요 시 checked into repo 하여 오프라인 참조 자료로 활용 가능)

실행: `uv run python scripts/verify_lrg_div_mapping.py`
환경변수: FISIS_API_KEY, DART_API_KEY (.env 로드)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from financial_data_mcp._fisis_registry import FisisRegistry  # noqa: E402
from financial_data_mcp.dart_client import DartClient  # noqa: E402
from financial_data_mcp.fisis_client import FisisClient  # noqa: E402
from financial_data_mcp import server  # noqa: E402


SNAPSHOT_PATH = ROOT / "scripts" / "fisis_registry_snapshot.json"


async def main() -> None:
    fisis_key = os.environ.get("FISIS_API_KEY", "")
    dart_key = os.environ.get("DART_API_KEY", "")
    if not fisis_key or not dart_key:
        print("!! .env 에 FISIS_API_KEY, DART_API_KEY 설정 필요")
        sys.exit(1)

    fisis = FisisClient(fisis_key)
    dart = DartClient(dart_key)

    print("=" * 70)
    print("[1] FisisRegistry 부트스트랩 — A~Z 전 권역 병렬 조회")
    print("=" * 70)
    registry = FisisRegistry()
    await registry.ensure_loaded(fisis)
    print(f"\n총 회사 수: {len(registry.by_finance_cd)}")
    print(f"발견 권역 수: {len(registry.sectors())}")
    print(f"로드 에러: {len(registry.load_errors)} 코드")

    print("\n권역별 등록 요약:")
    for code, info in registry.sectors().items():
        print(f"  {code}: {info['lrg_div_nm']:15s} ({info['company_count']}개)")

    if registry.load_errors:
        print("\n로드 에러 (참고):")
        for code, err in list(registry.load_errors.items())[:5]:
            print(f"  {code}: {err}")

    print("\n" + "=" * 70)
    print("[2] 데라게란덴(0011663) 조회")
    print("=" * 70)
    hit = registry.lookup_by_finance_cd("0011663")
    if hit:
        print(f"  finance_cd={hit.finance_cd}, finance_nm={hit.finance_nm}")
        print(f"  lrg_div={hit.lrg_div}, lrg_div_nm={hit.lrg_div_nm}")
    else:
        print("  !! 데라게란덴(0011663) 레지스트리에서 찾을 수 없음")
        print("  K 권역 샘플:")
        k_companies = [c for c in registry.by_finance_cd.values() if c.lrg_div == "K"]
        for c in k_companies[:5]:
            print(f"    {c.finance_cd} | {c.finance_nm}")

    by_name = registry.lookup_by_name("데라게란덴㈜")
    print(f"\n  lookup_by_name('데라게란덴㈜'): {by_name}")

    print("\n" + "=" * 70)
    print("[3] dart_to_fisis_bridge 엔드투엔드 — 데라게란덴")
    print("=" * 70)
    os.environ["DART_API_KEY"] = dart_key
    os.environ["FISIS_API_KEY"] = fisis_key
    server._fisis_registry.cache_clear()
    try:
        result = await server.dart_to_fisis_bridge("00609193")
        for k, v in json.loads(result).items():
            print(f"  {k}: {v}")
    except Exception as e:
        print(f"  에러: {e}")

    print("\n" + "=" * 70)
    print("[4] 하드코딩에 없는 권역 샘플 (투자일임·재보험·신기술금융 등)")
    print("=" * 70)
    hardcoded_covered = {"A", "B", "C", "D", "K", "T"}
    extra_sectors = {
        code: info for code, info in registry.sectors().items()
        if code not in hardcoded_covered
    }
    if extra_sectors:
        for code, info in extra_sectors.items():
            samples = [c for c in registry.by_finance_cd.values() if c.lrg_div == code][:3]
            print(f"  {code}: {info['lrg_div_nm']} ({info['company_count']}개)")
            for c in samples:
                print(f"    - {c.finance_cd} | {c.finance_nm}")
    else:
        print("  (하드코딩 A/B/C/D/K/T 외 추가 권역 없음 — FISIS 등록 범위 확인 필요)")

    print("\n" + "=" * 70)
    print(f"[5] 스냅샷 저장 → {SNAPSHOT_PATH.relative_to(ROOT)}")
    print("=" * 70)
    snapshot = {
        "total_companies": len(registry.by_finance_cd),
        "sectors": registry.sectors(),
        "load_errors": registry.load_errors,
        "companies": [
            {
                "finance_cd": c.finance_cd,
                "finance_nm": c.finance_nm,
                "lrg_div": c.lrg_div,
                "lrg_div_nm": c.lrg_div_nm,
            }
            for c in registry.by_finance_cd.values()
        ],
    }
    SNAPSHOT_PATH.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  저장 완료 ({len(snapshot['companies'])}개 회사)")

    # DART 간단 확인 (선택)
    print("\n" + "=" * 70)
    print("[6] DART corp_overview — 업종코드 샘플")
    print("=" * 70)
    for name, corp_code in [("데라게란덴", "00609193"), ("삼성생명", "00126256")]:
        try:
            o = await dart.get_company_overview(corp_code)
            print(f"  {name} ({corp_code}): induty_code={o.get('induty_code')}, corp_name={o.get('corp_name')}")
        except Exception as e:
            print(f"  {name}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
