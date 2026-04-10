#!/usr/bin/env python3
"""financial-data-mcp 로컬 환경 검증 스크립트.

집 PC에서 MCP 서버가 제대로 동작할지 사전 점검합니다.
DART/FISIS 실 API에 ping을 보내 엔드포인트 URL까지 실증합니다.

사용법:
    python scripts/preflight.py

종료 코드:
    0 = 모든 검사 통과
    1 = 하나 이상 실패
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── 색상 코드 (modern Windows terminal도 지원) ─────────────────
if os.name == "nt":
    os.system("")  # enable ANSI on Windows

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

OK = f"{GREEN}[ OK ]{RESET}"
NG = f"{RED}[FAIL]{RESET}"
WN = f"{YELLOW}[WARN]{RESET}"
INFO = f"{BLUE}[INFO]{RESET}"


@dataclass
class Check:
    label: str
    ok: bool
    message: str = ""
    hint: str = ""


results: list[Check] = []


def report(label: str, ok: bool | None, message: str = "", hint: str = "") -> None:
    """검사 결과 출력 및 기록. ok=None이면 WARN으로 표시."""
    if ok is None:
        tag = WN
    else:
        tag = OK if ok else NG
    print(f"{tag} {label}")
    if message:
        print(f"       {DIM}{message}{RESET}")
    if ok is False and hint:
        print(f"       {YELLOW}→ {hint}{RESET}")
    if ok is not None:
        results.append(Check(label, ok, message, hint))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  개별 검사
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def check_python_version() -> bool:
    ver = sys.version_info
    ok = ver >= (3, 10)
    report(
        "Python 3.10+",
        ok,
        f"{ver.major}.{ver.minor}.{ver.micro}",
        "Python 3.10 이상을 설치하세요",
    )
    return ok


def check_project_root() -> Path | None:
    project_root = Path(__file__).resolve().parent.parent
    pkg = project_root / "financial_data_mcp"
    if not pkg.is_dir():
        report(
            "프로젝트 구조",
            False,
            f"{pkg} 없음",
            "fisis-app 레포 루트에서 실행했는지 확인 (git clone 후)",
        )
        return None
    report("프로젝트 구조", True, str(project_root))
    return project_root


def load_env(project_root: Path) -> tuple[str, str]:
    env_path = project_root / ".env"
    if not env_path.is_file():
        report(
            ".env 파일",
            False,
            str(env_path),
            f"프로젝트 루트에 .env 파일 생성. {project_root / '.env.example'} 참고",
        )
        return "", ""

    report(".env 파일", True, str(env_path))

    dart_key = ""
    fisis_key = ""
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                v = v.strip().strip('"').strip("'")
                if k.strip() == "DART_API_KEY":
                    dart_key = v
                elif k.strip() == "FISIS_API_KEY":
                    fisis_key = v
    except Exception as e:
        report(".env 파싱", False, str(e))
        return "", ""

    report(
        "DART_API_KEY",
        bool(dart_key),
        f"len={len(dart_key)}" if dart_key else "",
        "https://opendart.fss.or.kr 에서 발급",
    )
    report(
        "FISIS_API_KEY",
        bool(fisis_key),
        f"len={len(fisis_key)}" if fisis_key else "",
        "https://fisis.fss.or.kr 에서 발급",
    )
    return dart_key, fisis_key


def check_dependencies() -> list[str]:
    required = [
        ("httpx", "httpx"),
        ("mcp", "mcp"),
        ("python-dotenv", "dotenv"),
    ]
    missing = []
    for pkg, mod in required:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)

    report(
        "필수 패키지",
        len(missing) == 0,
        "httpx, mcp, python-dotenv",
        f"pip install -e . (누락: {', '.join(missing)})" if missing else "",
    )
    return missing


async def check_dart_api(dart_key: str) -> None:
    if not dart_key:
        report("DART API 접근", False, "DART_API_KEY 없음", "위의 .env 설정 참고")
        return

    try:
        import httpx
    except ImportError:
        report("DART API 접근", False, "httpx 미설치")
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 삼성전자(00126380) 기업개황 조회로 핑
            resp = await client.get(
                "https://opendart.fss.or.kr/api/company.json",
                params={"crtfc_key": dart_key, "corp_code": "00126380"},
            )
    except httpx.TransportError as e:
        err_name = type(e).__name__
        if "Proxy" in err_name:
            hint = (
                "프록시 차단 (회사 방화벽/VPN/샌드박스 환경). "
                "HTTP_PROXY/HTTPS_PROXY 환경변수 확인 또는 집에서 재실행"
            )
        else:
            hint = "네트워크/방화벽 확인"
        report("DART API 접근", False, f"{err_name}: {e}", hint)
        return
    except Exception as e:
        report("DART API 접근", False, f"{type(e).__name__}: {e}")
        return

    if resp.status_code != 200:
        report(
            "DART API 접근",
            False,
            f"HTTP {resp.status_code}",
            "서비스 장애 가능 - https://opendart.fss.or.kr 확인",
        )
        return

    try:
        data = resp.json()
    except Exception:
        report("DART API 접근", False, "응답이 JSON이 아님")
        return

    status = data.get("status", "")
    corp_name = data.get("corp_name", "")
    if status == "000":
        report("DART API 접근", True, f"삼성전자 조회 성공 ({corp_name})")
    elif status == "010":
        report(
            "DART API 접근",
            False,
            "등록되지 않은 API 키",
            "https://opendart.fss.or.kr 에서 키 상태 확인",
        )
    elif status == "011":
        report("DART API 접근", False, "사용할 수 없는 키", "키 재발급 필요")
    elif status == "020":
        report(
            "DART API 접근",
            False,
            "일일 요청 한도 초과 (20,000건)",
            "다음 날 자정 이후 재시도",
        )
    else:
        report(
            "DART API 접근",
            False,
            f"DART status {status}: {data.get('message', '')}",
        )


async def check_fisis_api(fisis_key: str) -> None:
    """FISIS 엔드포인트 실증: 여러 후보 URL을 시도."""
    if not fisis_key:
        report("FISIS API 접근", False, "FISIS_API_KEY 없음", "위의 .env 설정 참고")
        return

    try:
        import httpx
    except ImportError:
        report("FISIS API 접근", False, "httpx 미설치")
        return

    # 엔드포인트 후보 (현재 코드와 일치하는지 검증)
    base_urls = [
        "https://fisis.fss.or.kr/openapi",
    ]
    endpoints = [
        "/statisticsListSearch.json",
    ]

    errors: list[str] = []
    for base in base_urls:
        for ep in endpoints:
            url = f"{base}{ep}"
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        url,
                        params={"auth": fisis_key, "lang": "kr", "lrgDiv": "01"},
                    )
            except httpx.TransportError as e:
                errors.append(f"{url}: {type(e).__name__}")
                continue
            except Exception as e:
                errors.append(f"{url}: {type(e).__name__}")
                continue

            if resp.status_code != 200:
                errors.append(f"{url}: HTTP {resp.status_code}")
                continue

            try:
                data = resp.json()
            except Exception:
                errors.append(f"{url}: non-JSON 응답")
                continue

            # 에러 메시지 검사
            err_msg = ""
            if isinstance(data, dict):
                result = data.get("result", {})
                if isinstance(result, dict):
                    err_msg = result.get("err_msg") or result.get("errMsg") or ""
                err_msg = err_msg or data.get("err_msg") or data.get("errMsg") or ""

            if err_msg and err_msg not in ("정상", "성공", "success", ""):
                errors.append(f"{url}: API 오류 - {err_msg}")
                continue

            # 성공
            report(
                "FISIS API 접근",
                True,
                f"{url} 응답 정상",
            )
            return

    # 모든 후보 실패
    report(
        "FISIS API 접근",
        False,
        "; ".join(errors[:3]),
        "FISIS 엔드포인트 URL이 fisis_client.py와 다를 수 있음. "
        "https://fisis.fss.or.kr/fisis/openapi/apiInfo.do 에서 실제 URL 확인 후 "
        "financial_data_mcp/fisis_client.py의 BASE_URL/엔드포인트 수정",
    )


def check_cache_dir() -> None:
    cache_dir = Path.home() / ".cache" / "financial_data_mcp"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        test = cache_dir / ".preflight_test"
        test.write_text("ok")
        test.unlink()
        report("디스크 캐시 쓰기", True, str(cache_dir))
    except Exception as e:
        report("디스크 캐시 쓰기", False, f"{cache_dir}: {e}", "홈 디렉토리 권한 확인")


def find_claude_desktop_config() -> Path | None:
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA", "")
        return Path(base) / "Claude" / "claude_desktop_config.json" if base else None
    elif system == "Darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    else:  # Linux
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def check_claude_desktop() -> None:
    config_path = find_claude_desktop_config()
    if config_path is None or not config_path.exists():
        report(
            "Claude Desktop 설정",
            None,  # WARN - Claude Code CLI 단독 사용 시 정상
            f"{config_path}: 없음 (Claude Code CLI 단독 사용 시 정상)",
        )
        return

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as e:
        report("Claude Desktop 설정", False, f"JSON 파싱 실패: {e}")
        return

    servers = config.get("mcpServers", {})
    if "financial-data" in servers:
        report("Claude Desktop 설정", True, "financial-data 서버 등록됨")
    else:
        report(
            "Claude Desktop 설정",
            False,
            f"financial-data 미등록 (현재 등록: {list(servers.keys())})",
            f"{config_path} 에 financial-data MCP 서버 추가 (README 참고)",
        )


def check_mcp_json(project_root: Path) -> None:
    mcp_json = project_root / ".mcp.json"
    report(
        ".mcp.json",
        mcp_json.is_file(),
        str(mcp_json),
        "Claude Code CLI는 프로젝트 루트의 .mcp.json을 자동 인식",
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  메인
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def async_checks(dart_key: str, fisis_key: str) -> None:
    await check_dart_api(dart_key)
    await check_fisis_api(fisis_key)


def print_header() -> None:
    print()
    print(f"{BOLD}financial-data-mcp Preflight Check{RESET}")
    print(f"{DIM}로컬 환경 검증 - MCP 서버를 사용할 준비가 되었는지 확인합니다{RESET}")
    print()


def print_summary() -> int:
    print()
    print(f"{BOLD}{'=' * 50}{RESET}")
    passed = sum(1 for r in results if r.ok)
    failed = sum(1 for r in results if not r.ok)
    total = len(results)
    print(
        f"총 {total}개 검사: "
        f"{GREEN}{passed} 통과{RESET}, "
        f"{RED}{failed} 실패{RESET}"
    )

    if failed == 0:
        print()
        print(f"{GREEN}{BOLD}✓ 모든 검사 통과 - MCP 서버 사용 준비 완료{RESET}")
        print()
        print("다음 단계:")
        print("  1. 로컬 Claude Code CLI: `claude` 실행 (프로젝트 루트에서)")
        print("  2. Claude Desktop: 재시작 후 도구 아이콘 확인")
        print("  3. 테스트 질문: \"삼성전자 2024년 재무제표 보여줘\"")
        return 0

    print()
    print(f"{RED}{BOLD}실패한 검사 ({failed}):{RESET}")
    for r in results:
        if not r.ok:
            print(f"  • {r.label}")
            if r.hint:
                print(f"    {YELLOW}→ {r.hint}{RESET}")
    print()
    return 1


def main() -> int:
    print_header()

    if not check_python_version():
        return print_summary()

    project_root = check_project_root()
    if project_root is None:
        return print_summary()

    dart_key, fisis_key = load_env(project_root)
    missing = check_dependencies()

    # httpx 없으면 네트워크 검사 스킵
    if "httpx" not in missing:
        try:
            asyncio.run(async_checks(dart_key, fisis_key))
        except Exception as e:
            report("API 접근 검사", False, f"asyncio 오류: {e}")

    check_cache_dir()
    check_mcp_json(project_root)
    check_claude_desktop()

    return print_summary()


if __name__ == "__main__":
    sys.exit(main())
