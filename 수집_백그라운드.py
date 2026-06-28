# -*- coding: utf-8 -*-
"""
수집_백그라운드.py — 창이 꺼져도 안 끊기는 수집 런처

배경: 명령창(conhost)이 수집 도중 충돌해 닫히면, 그 안에서 돌던 수집도 같이
죽어 데이터가 날아갔다. 이 런처는 collect_5.py를 '완전히 분리된(detached)'
백그라운드 프로세스로 띄운다. 부모 창이 죽어도 수집은 혼자 끝까지 돌고
Supabase에 저장된다. 진행 상황은 로그 파일에, 상태는 JSON에 남는다.

사용법 (collect_5.py와 인자 동일):
  venv\\Scripts\\python 수집_백그라운드.py --keywords "튼살크림" --count 3
  venv\\Scripts\\python 수집_백그라운드.py --category "베이비 스킨케어" --count 5
  venv\\Scripts\\python 수집_백그라운드.py --count 5

띄운 뒤 바로 종료하며, 아래 두 파일 경로를 출력한다:
  - 수집로그_최근.log   : 수집 진행/결과 로그 (실시간 기록)
  - 수집상태.json       : pid / 시작시각 / 인자 / 로그경로
이 두 파일만 보면 창이 꺼졌어도 무슨 일이 있었는지 알 수 있다.
"""
import os
import sys
import json
import time
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(SCRIPT_DIR, "수집로그_최근.log")
STATUS_PATH = os.path.join(SCRIPT_DIR, "수집상태.json")

# Windows 분리 실행 플래그
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def main():
    # 사용자가 준 인자를 그대로 collect_5.py 로 넘긴다
    passthrough = sys.argv[1:]
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, "collect_5.py")] + passthrough

    # 자식이 항상 UTF-8로 출력 (한글 깨짐 방지)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

    logf = open(LOG_PATH, "w", encoding="utf-8", errors="replace")
    logf.write(f"[런처] 시작: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    logf.write(f"[런처] 명령: {' '.join(passthrough) if passthrough else '(전체 자동수집)'}\n")
    logf.flush()

    # 부모 창(터미널)이 죽어도 살아남도록 job에서 분리 시도.
    # 일부 환경은 breakaway를 막아 에러가 나므로 단계적으로 폴백한다.
    flag_attempts = [
        DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB,
        DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        CREATE_NEW_PROCESS_GROUP,
    ]
    proc = None
    last_err = None
    for flags in flag_attempts:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=logf,
                stderr=subprocess.STDOUT,
                cwd=SCRIPT_DIR,
                env=env,
                creationflags=flags,
                close_fds=True,
            )
            break
        except OSError as e:
            last_err = e
            continue

    if proc is None:
        logf.write(f"[런처] 실행 실패: {last_err}\n")
        logf.flush()
        print(f"수집 시작 실패: {last_err}")
        sys.exit(1)

    status = {
        "pid": proc.pid,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "started_epoch": int(time.time()),
        "args": passthrough,
        "log_path": LOG_PATH,
        "status": "running",
    }
    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

    print("수집을 백그라운드로 시작했어요. (이 창이 꺼져도 수집은 계속 돌아갑니다)")
    print(f"  - 진행 로그: {LOG_PATH}")
    print(f"  - 상태 파일: {STATUS_PATH}")
    print(f"  - 프로세스 번호(pid): {proc.pid}")
    # 런처는 여기서 즉시 끝난다. 자식(수집)은 계속 돌아간다.


if __name__ == "__main__":
    main()
