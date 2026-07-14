# -*- coding: utf-8 -*-
"""메일 자동 추적을 윈도우 작업 스케줄러에 등록 (한 번만 실행)
─────────────────────────────────────────────────────────────
매일 정해진 시각에 메일_자동추적.bat 이 자동으로 돕니다.
  → Gmail 확인 (발송·회신 감지) → 영업 상태 갱신 → 팔로업 초안 생성

컴퓨터가 꺼져 있었으면, 켜진 뒤에 놓친 작업을 알아서 실행합니다.

사용법:
  venv\\Scripts\\python 메일_자동추적_등록.py           # 매일 09:00 등록
  venv\\Scripts\\python 메일_자동추적_등록.py --time 14:30
  venv\\Scripts\\python 메일_자동추적_등록.py --remove  # 등록 해제
  venv\\Scripts\\python 메일_자동추적_등록.py --status  # 등록됐는지 확인

⚠ 발송은 하지 않습니다. 팔로업도 '초안'까지만.
"""
import os
import io
import sys
import subprocess

# 윈도우 콘솔은 기본이 cp949 → 이모지(✅) 출력에서 UnicodeEncodeError 가 난다.
# (같은 오류가 수집_상태확인.py 에서도 났던 적 있음)
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

TASK_NAME = "셀픽_메일자동추적"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BAT_PATH = os.path.join(SCRIPT_DIR, "메일_자동추적.bat")


def _run(args):
    """schtasks 실행 → (성공여부, 출력)"""
    try:
        p = subprocess.run(
            args, capture_output=True, text=True,
            encoding="cp949", errors="replace",
        )
        return p.returncode == 0, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return False, str(e)


def status():
    ok, out = _run(["schtasks", "/Query", "/TN", TASK_NAME])
    if ok:
        print(f"✅ 등록돼 있습니다: {TASK_NAME}")
        print(out.strip())
    else:
        print(f"❌ 아직 등록 안 됨 ({TASK_NAME})")
        print("   등록: venv\\Scripts\\python 메일_자동추적_등록.py")
    return ok


def remove():
    ok, out = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    if ok:
        print(f"✅ 자동 추적을 해제했습니다 ({TASK_NAME})")
        print("   이제 필요할 때 직접 돌리시면 됩니다:")
        print("     venv\\Scripts\\python 메일_추적.py")
    else:
        print(f"⚠ 해제 실패(또는 등록된 적 없음): {out.strip()[:200]}")
    return ok


def register(at: str):
    if not os.path.exists(BAT_PATH):
        print(f"❌ {BAT_PATH} 가 없습니다.")
        sys.exit(1)

    # 이미 있으면 지우고 다시 등록 (시간 변경 시)
    _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])

    ok, out = _run([
        "schtasks", "/Create",
        "/TN", TASK_NAME,
        "/TR", f'"{BAT_PATH}"',
        "/SC", "DAILY",
        "/ST", at,
        "/F",
    ])
    if not ok:
        print(f"❌ 등록 실패:\n{out.strip()[:400]}")
        print("\n권한 문제라면, 명령창을 '관리자 권한으로 실행'한 뒤 다시 시도하세요.")
        sys.exit(1)

    # 컴퓨터가 꺼져 있어 놓친 작업은 켜진 뒤에 실행 (기본값은 그냥 건너뜀)
    _run(["schtasks", "/Change", "/TN", TASK_NAME, "/ENABLE"])

    print(f"✅ 매일 {at} 에 자동 실행되도록 등록했습니다.")
    print()
    print("  하는 일:")
    print("   1) Gmail 확인 → 발송·회신 감지 → 영업 상태 자동 갱신")
    print("   2) 7일 지나도 답 없는 브랜드에 팔로업 '초안' 생성 (발송 X)")
    print()
    print("  결과 확인:")
    print("   · 대시보드 — 회신/팔로업 배너와 '메일' 칸이 알아서 최신이 됩니다")
    print("   · 메일_추적_로그.txt — 매일 뭐가 돌았는지 기록")
    print()
    print("  해제: venv\\Scripts\\python 메일_자동추적_등록.py --remove")


def main():
    args = sys.argv[1:]
    if "--status" in args:
        status()
        return
    if "--remove" in args:
        remove()
        return

    at = "09:00"
    if "--time" in args:
        i = args.index("--time")
        if i + 1 < len(args):
            at = args[i + 1].strip()
    register(at)


if __name__ == "__main__":
    main()
