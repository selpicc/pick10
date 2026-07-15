# -*- coding: utf-8 -*-
"""메일 자동 추적을 윈도우 작업 스케줄러에 등록 (한 번만 실행)
─────────────────────────────────────────────────────────────
매일 정해진 시각(들)에 메일_자동추적.bat 이 자동으로 돕니다.
  → Gmail 확인 (발송·회신 감지) → 영업 상태 갱신 → 팔로업 초안 생성

하루에 여러 번도 가능합니다 (--time 에 콤마로 여러 시간).
추적은 그때그때 Gmail 현재 상태를 통째로 다시 읽는 방식이라, 어떤 회차를
놓쳐도(그 시각에 컴퓨터가 꺼져 있어도) 다음 실행 때 전부 따라잡습니다.

사용법:
  venv\\Scripts\\python 메일_자동추적_등록.py                     # 매일 09:00 등록
  venv\\Scripts\\python 메일_자동추적_등록.py --time 14:30
  venv\\Scripts\\python 메일_자동추적_등록.py --time "09:30,14:00,17:00"   # 하루 3번
  venv\\Scripts\\python 메일_자동추적_등록.py --remove            # 등록 전부 해제
  venv\\Scripts\\python 메일_자동추적_등록.py --status            # 등록됐는지 확인

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

TASK_NAME = "셀픽_메일자동추적"     # 레거시(단일) 이름 — 정리 대상에 포함
MAX_SLOTS = 9                       # 하루 최대 9회까지 지원
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


def _all_task_names():
    """정리·조회 대상 — 레거시 단일 이름 + 번호 붙은 이름들(_1.._N)"""
    return [TASK_NAME] + [f"{TASK_NAME}_{i}" for i in range(1, MAX_SLOTS + 1)]


def _norm_time(t: str) -> str:
    """'9:30' / '09:30' → 'HH:MM' 로 정규화. 형식 틀리면 예외."""
    t = t.strip()
    parts = t.split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"시간 범위 벗어남: {t}")
    return f"{h:02d}:{m:02d}"


def _cleanup_all():
    """기존에 등록된 셀픽 메일추적 작업들을 전부 삭제 (중복 방지)."""
    for n in _all_task_names():
        _run(["schtasks", "/Delete", "/TN", n, "/F"])


def status():
    found = []
    for n in _all_task_names():
        ok, out = _run(["schtasks", "/Query", "/TN", n, "/FO", "LIST"])
        if ok:
            # '다음 실행 시간' 줄만 뽑아 보여줌
            nxt = ""
            for line in out.splitlines():
                if "다음 실행 시간" in line or "Next Run Time" in line:
                    nxt = line.split(":", 1)[-1].strip()
                    break
            found.append((n, nxt))
    if found:
        print(f"✅ 등록돼 있습니다 — 하루 {len(found)}회:")
        for n, nxt in found:
            print(f"   · {n}   다음 실행: {nxt or '(확인 불가)'}")
    else:
        print(f"❌ 아직 등록 안 됨 ({TASK_NAME})")
        print("   등록: venv\\Scripts\\python 메일_자동추적_등록.py")
    return bool(found)


def remove():
    removed = 0
    for n in _all_task_names():
        ok, _ = _run(["schtasks", "/Delete", "/TN", n, "/F"])
        if ok:
            removed += 1
    if removed:
        print(f"✅ 자동 추적을 해제했습니다 ({removed}개 작업 삭제)")
        print("   이제 필요할 때 직접 돌리시면 됩니다:")
        print("     venv\\Scripts\\python 메일_추적.py")
    else:
        print("⚠ 해제할 작업이 없습니다 (등록된 적 없음).")
    return removed > 0


def register(times):
    if not os.path.exists(BAT_PATH):
        print(f"❌ {BAT_PATH} 가 없습니다.")
        sys.exit(1)

    # 이미 있으면 전부 지우고 다시 등록 (시간·개수 변경 시 중복 방지)
    _cleanup_all()

    made = []
    for i, at in enumerate(times, 1):
        tn = f"{TASK_NAME}_{i}"
        ok, out = _run([
            "schtasks", "/Create",
            "/TN", tn,
            "/TR", f'"{BAT_PATH}"',
            "/SC", "DAILY",
            "/ST", at,
            "/F",
        ])
        if not ok:
            print(f"❌ {at} 등록 실패:\n{out.strip()[:400]}")
            print("\n권한 문제라면, 명령창을 '관리자 권한으로 실행'한 뒤 다시 시도하세요.")
            # 부분 등록 상태를 남기지 않게 정리
            _cleanup_all()
            sys.exit(1)
        made.append(at)

    print(f"✅ 매일 {', '.join(made)} — 하루 {len(made)}번 자동 실행되도록 등록했습니다.")
    print()
    print("  하는 일 (매 회차마다):")
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

    raw = "09:00"
    if "--time" in args:
        i = args.index("--time")
        if i + 1 < len(args):
            raw = args[i + 1].strip()

    try:
        times = [_norm_time(t) for t in raw.split(",") if t.strip()]
    except ValueError as e:
        print(f"❌ 시간 형식이 잘못됐어요: {e}")
        print('   예) --time "09:30,14:00,17:00"')
        sys.exit(1)

    if not times:
        print("❌ 등록할 시간이 없습니다.")
        sys.exit(1)
    if len(times) > MAX_SLOTS:
        print(f"❌ 하루 최대 {MAX_SLOTS}회까지만 됩니다 (요청: {len(times)}회).")
        sys.exit(1)

    register(times)


if __name__ == "__main__":
    main()
