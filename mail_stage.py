# -*- coding: utf-8 -*-
"""메일 '단계' 계산 — 단일 진실의 원천
─────────────────────────────────────────────────────────────
대시보드 표시와 메일_추적.py 판단이 어긋나면 안 되므로, 단계 규칙은 여기 하나만 둔다.

  초안 → 메일 발송 → (7일) 1차 팔로업 생성 → 1차 팔로업 송신
       → (14일) 2차 팔로업 생성 → 2차 팔로업 송신
  ⚡ 어느 단계든 답이 오면 '답신 감지'가 최우선

⭐ 모든 n일은 '첫 메일 발송일' 기준이다.
   1차 팔로업을 늦게 보내도 2차 시점이 밀리지 않는다 (시계가 고정).

⭐ '생성'과 '송신'을 나누는 근거 — 이 둘은 다른 숫자다:
     팔로업 횟수(followup_count) = 초안을 만든 횟수  (메일_추적.py가 만들 때 +1)
     메일 발송 수(sent_count)    = 실제로 나간 메일 수 (Gmail 보낸편지함에서 셈)
   프로그램은 초안까지만 만들고 발송은 사람이 하므로, 둘 사이에 시차가 생긴다.
   followup_count >= 다음회차 이면 → 초안이 임시보관함에서 대기 중(= 밍 차례).
"""

FOLLOWUP_WAIT_DAYS = 7      # 1차 = 첫 발송 + 7일, 2차 = 첫 발송 + 14일
FOLLOWUP_MAX = 2            # 팔로업 최대 회차


def normalize_sent_count(sent_count, followup_count, has_sent_date) -> int:
    """실제로 나간 메일 수. 옛 데이터(발송 수 컬럼 없음)는 근사로 보완한다.

    발송일이 있으면 최소 1통은 나갔고, 팔로업 초안을 만든 만큼은 보냈다고 본다.
    (근사치다 — 메일_추적.py가 한 번 돌면 Gmail 실측값으로 교정된다)
    """
    try:
        n = int(sent_count or 0)
    except (TypeError, ValueError):
        n = 0
    if n:
        return n
    if not has_sent_date:
        return 0
    try:
        fu = int(followup_count or 0)
    except (TypeError, ValueError):
        fu = 0
    return 1 + min(max(fu, 0), FOLLOWUP_MAX)


def next_round(sent_count: int) -> int:
    """다음에 만들 팔로업 회차. 0이면 더 할 게 없다.

    1통 나갔으면 다음은 1차, 2통이면 2차, 3통이면 끝.
    """
    if sent_count <= 0 or sent_count > FOLLOWUP_MAX:
        return 0
    return sent_count


def due_days(round_no: int) -> int:
    """그 회차가 나가야 할 시점 (첫 발송일로부터 며칠 뒤)."""
    return FOLLOWUP_WAIT_DAYS * round_no


def stage(sent_count: int, followup_count: int, days_since_first_sent: int,
          replied: bool, has_thread: bool) -> str:
    """지금 단계를 한 줄로. days_since_first_sent 는 첫 발송 기준(모르면 -1).

    반환 예: "📝 초안" / "✉️ 메일 발송 · 3일" / "📄 1차 팔로업 생성 · 8일"
    """
    if not has_thread:
        return ""                                   # 초안을 만든 적 없음
    if replied:
        return "💬 답신 감지"                        # ← 최우선
    if sent_count <= 0:
        return "📝 초안"                             # 만들었지만 아직 안 보냄

    ago = f" · {days_since_first_sent}일" if days_since_first_sent >= 0 else ""
    if sent_count > FOLLOWUP_MAX:
        return f"✅ 2차 팔로업 송신{ago}"             # 끝까지 나감

    nth = next_round(sent_count)
    try:
        fu = int(followup_count or 0)
    except (TypeError, ValueError):
        fu = 0
    if fu >= nth:
        return f"📄 {nth}차 팔로업 생성{ago}"        # 초안 대기 = 밍이 보낼 차례
    if days_since_first_sent >= due_days(nth):
        return f"🔔 {nth}차 팔로업 때{ago}"          # 곧 자동추적이 초안을 만든다
    if sent_count == 1:
        return f"✉️ 메일 발송{ago}"
    return f"📮 1차 팔로업 송신{ago}"
