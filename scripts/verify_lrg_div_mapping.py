"""라이브 FISIS API로 lrg_div 매핑을 검증한다.

4가지 진단 항목:
1. A~M 각 lrg_div 코드가 실제로 존재하고 어떤 업권인지
2. dart_to_fisis_bridge('00609193') 데라게란덴이 K/리스사로 반환되는지
3. fisis_list_companies(lrg_div='K')에서 데라게란덴(0011663) 조회 가능한지
4. 64911(DART 업종코드) → K 매핑이 실제 데라게란덴 corp_overview 응답과 일치하는지

보험·캐피탈·할부금융 unverified 항목도 함께 확인한다.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from financial_data_mcp.fisis_client import FisisClient  # noqa: E402
from financial_data_mcp.dart_client import DartClient  # noqa: E402
from financial_data_mcp import server  # noqa: E402


async def dump_lrg_div_partDiv(fisis: FisisClient) -> dict[str, list[dict]]:
    """companySearch.json에 A~Z 각 partDiv로 호출하여 등록된 권역을 덤프."""
    results: dict[str, list[dict]] = {}
    for code in [chr(c) for c in range(ord("A"), ord("Z") + 1)]:
        try:
            data = await fisis.list_companies(lrg_div=code)
            raw = data.get("result", data) if isinstance(data, dict) else data
            items = raw.get("list") if isinstance(raw, dict) else None
            if isinstance(items, list) and items:
                results[code] = items
        except Exception as e:
            results[code] = [{"_error": str(e)}]
    return results


async def dump_lrg_div_statistics(fisis: FisisClient) -> dict[str, list[dict]]:
    """statisticsListSearch.json에 A~Z 각 lrgDiv로 호출."""
    results: dict[str, list[dict]] = {}
    for code in [chr(c) for c in range(ord("A"), ord("Z") + 1)]:
        try:
            data = await fisis.list_statistics(lrg_div=code)
            raw = data.get("result", data) if isinstance(data, dict) else data
            items = raw.get("list") if isinstance(raw, dict) else None
            if isinstance(items, list) and items:
                results[code] = items
        except Exception as e:
            results[code] = [{"_error": str(e)}]
    return results


def summarize(items: list[dict], limit: int = 3) -> list[dict]:
    """응답 샘플만 요약."""
    if items and isinstance(items[0], dict) and "_error" in items[0]:
        return items
    out = []
    for it in items[:limit]:
        out.append({k: v for k, v in it.items() if k in ("finance_cd", "finance_nm", "lrg_div_nm", "sml_div_nm", "list_no", "list_nm")})
    return out + ([{"_total_count": len(items)}] if len(items) > limit else [])


async def main() -> None:
    fisis_key = os.environ.get("FISIS_API_KEY", "")
    dart_key = os.environ.get("DART_API_KEY", "")
    if not fisis_key or not dart_key:
        print("!! API 키 누락"); return

    fisis = FisisClient(fisis_key)
    dart = DartClient(dart_key)

    print("=" * 70)
    print("[1] FISIS companySearch.json — A~Z partDiv 회사 목록 덤프")
    print("=" * 70)
    comp = await dump_lrg_div_partDiv(fisis)
    for code, items in comp.items():
        sample = summarize(items)
        print(f"\npartDiv={code}: {len(items)}건")
        for s in sample:
            print(f"  {s}")

    print("\n" + "=" * 70)
    print("[2] FISIS statisticsListSearch.json — A~Z lrgDiv 통계 목록 덤프")
    print("=" * 70)
    stats = await dump_lrg_div_statistics(fisis)
    for code, items in stats.items():
        sample = summarize(items)
        print(f"\nlrgDiv={code}: {len(items)}건")
        for s in sample:
            print(f"  {s}")

    print("\n" + "=" * 70)
    print("[3] 데라게란덴 시나리오")
    print("=" * 70)
    # 3-1: DART corp_overview로 업종코드 확인
    try:
        overview = await dart.get_company_overview("00609193")
        print(f"\nDART get_company_overview('00609193'):")
        print(f"  corp_name={overview.get('corp_name')}")
        print(f"  induty_code={overview.get('induty_code')}")
        print(f"  corp_cls={overview.get('corp_cls')}")
    except Exception as e:
        print(f"  DART 에러: {e}")

    # 3-2: dart_to_fisis_bridge 호출
    os.environ["DART_API_KEY"] = dart_key
    os.environ["FISIS_API_KEY"] = fisis_key
    try:
        result = await server.dart_to_fisis_bridge("00609193")
        data = json.loads(result)
        print(f"\nserver.dart_to_fisis_bridge('00609193'):")
        for k, v in data.items():
            print(f"  {k}: {v}")
    except Exception as e:
        print(f"  bridge 에러: {e}")

    # 3-3: fisis_list_companies(lrg_div='K')에서 데라게란덴 찾기
    try:
        data = await fisis.list_companies(lrg_div="K")
        raw = data.get("result", data) if isinstance(data, dict) else data
        items = raw.get("list") if isinstance(raw, dict) else []
        print(f"\nfisis_list_companies(lrg_div='K'): 총 {len(items)}건")
        matches = [it for it in items if "데라게란덴" in (it.get("finance_nm") or "") or it.get("finance_cd") == "0011663"]
        for m in matches:
            print(f"  MATCH: {m}")
        if not matches:
            print("  데라게란덴 미발견. 샘플 3건:")
            for s in items[:3]:
                print(f"    {s}")
    except Exception as e:
        print(f"  에러: {e}")

    print("\n" + "=" * 70)
    print("[4] 보험(6511)·할부금융(64912)·캐피탈 lrg_div 확정")
    print("=" * 70)
    # 보험사 — 삼성생명 corp_code 00126256 (추정) 또는 다른 보험사
    # 할부금융/캐피탈도 DART 업종코드로 확인
    for name, corp_code in [("삼성생명", "00126256"), ("현대캐피탈", "00164645"), ("현대카드", "00128564")]:
        try:
            o = await dart.get_company_overview(corp_code)
            print(f"\n{name} ({corp_code}): corp_name={o.get('corp_name')}, induty_code={o.get('induty_code')}")
        except Exception as e:
            print(f"  {name} 에러: {e}")


if __name__ == "__main__":
    asyncio.run(main())
