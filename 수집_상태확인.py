# -*- coding: utf-8 -*-
"""
수집_상태확인.py — 백그라운드 수집이 지금 어떤 상태인지 한눈에 본다.

창이 꺼졌다 다시 켰을 때 이걸 돌리면:
  - 수집이 아직 도는 중인지 / 끝났는지
  - 마지막 로그 몇 줄 (진행/결과/오류)
를 보여준다.

사용법:
  venv\\Scripts\\python 수집_상태확인.py
"""
import os
import sys
import json
import time

# 윈도우 콘솔 기본 인코딩(cp949)에서 이모지(🟢✅ 등)를 출력하면 UnicodeEncodeError가
# 난다. 출력 인코딩을 UTF-8로 고정해 방지.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(SCRIPT_DIR, "수집로그_최근.log")
STATUS_PATH = os.path.join(SCRIPT_DIR, "수집상태.json")


def is_running(pid: int) -> bool:
    """해당 pid 프로세스가 살아있는지 (윈도우)."""
    try:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not h:
            return False
        exit_code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(h)
        STILL_ACTIVE = 259
        return exit_code.value == STILL_ACTIVE
    except Exception:
        return False


def main():
    if not os.path.exists(STATUS_PATH):
        print("아직 백그라운드 수집을 시작한 기록이 없어요.")
        print('  → "수집_백그라운드.py" 로 수집을 시작하면 여기서 상태를 볼 수 있어요.')
        return

    with open(STATUS_PATH, encoding="utf-8") as f:
        status = json.load(f)

    pid = status.get("pid")
    args = status.get("args") or []
    started = status.get("started_at", "?")
    running = is_running(pid) if pid else False

    print("=" * 50)
    print(f"  최근 수집  ({started} 시작)")
    print(f"  명령: {' '.join(args) if args else '(전체 자동수집)'}")
    print(f"  상태: {'🟢 아직 수집 중' if running else '✅ 끝났음(또는 종료됨)'}")
    print("=" * 50)

    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        tail = lines[-20:]
        print("\n[ 로그 마지막 부분 ]")
        print("".join(tail).rstrip())
        if running:
            print("\n…아직 진행 중이에요. 잠시 뒤 다시 실행해 보세요.")
    else:
        print("(로그 파일이 아직 없어요)")


if __name__ == "__main__":
    main()
