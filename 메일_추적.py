# -*- coding: utf-8 -*-
"""메일 발송·회신 추적 + 팔로업 초안 생성
─────────────────────────────────────────────────────────────
초안까지만 만들고 끝나던 흐름에 '그 다음'을 붙인다.

  ① 보냈는지 확인   — 초안이 임시보관함에서 사라지고 보낸편지함에 있으면 발송됨
  ② 회신 왔는지 확인 — 같은 대화에 상대방 메시지가 있으면 회신 (자동응답은 제외)
  ③ 영업 상태 자동  — 발송 → '메일 발송' / 회신 → '컨택중'
                     ⚠ 사람이 바꿔놓은 값(계약 완료·거절·패싱·컨택중)은 절대 안 건드림
  ④ 팔로업          — 첫 발송 + 7일 → 1차 / 첫 발송 + 14일 → 2차 리마인드 초안

콜드메일 회신의 상당수는 첫 메일이 아니라 팔로업에서 나온다.
다만 재촉은 역효과다 — 최대 2회.

⭐ 시계는 '첫 메일 발송일'에 고정된다 (1차를 늦게 보내도 2차 시점이 안 밀림).
   단 1차를 실제로 보낸 지 5일은 지나야 2차를 만든다 — 연달아 나가면 재촉이다.

사용법:
  venv\\Scripts\\python 메일_추적.py                # 현황만 보기 (아무것도 안 만듦)
  venv\\Scripts\\python 메일_추적.py --followup     # 팔로업 대상에게 초안 생성
  venv\\Scripts\\python 메일_추적.py --followup --days 5   # 5일 지난 건부터
  venv\\Scripts\\python 메일_추적.py --brand 부담제로 --followup

전제:
  - Gmail 읽기 권한 필요 → auth_gmail.py 재실행 (한 번만)
  - Supabase 컬럼 필요 (mail_draft_id / mail_thread_id / mail_sent_at /
    mail_replied_at / mail_followup_count / mail_last_followup_at)

⚠ 발송은 하지 않는다. 팔로업도 '초안'까지만.
"""
import sys
import io
import os
from datetime import datetime, timezone, timedelta

def _force_utf8_stdout():
    """콘솔(cp949)에서 한글·이모지가 깨지지 않게. 스크립트로 실행할 때만 호출한다.
    import 될 때 하면 부르는 쪽(self_check 등)의 stdout까지 갈아엎어 출력이 깨진다.
    """
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass


from supabase_client import get_supabase_client, TABLE_NAME
from email_templates import build_followup
import gmail_drafts
import mail_stage           # 단계·시점 규칙 (대시보드와 공유 — 단일 진실의 원천)

FOLLOWUP_WAIT_DAYS = mail_stage.FOLLOWUP_WAIT_DAYS   # 1차 7일 / 2차 14일의 단위
FOLLOWUP_MAX = mail_stage.FOLLOWUP_MAX               # 최대 팔로업 횟수 (그 이상은 스팸)

# ─────────────────────────────────────────────────────────
# ⭐ 2026-07 변경: 팔로업 시점은 '첫 메일 발송일' 하나로 계산한다.
#   1차 = 첫 발송 + 7일 / 2차 = 첫 발송 + 14일
#   예전엔 '마지막 연락' 기준이라, 1차 팔로업을 늦게 보내면 2차도 통째로 밀렸다.
#   이제 시계는 첫 발송일에 고정 → 언제 무엇이 나갈지 예측 가능.
#
#   단 하나의 예외(MIN_GAP_AFTER_SEND): 밍이 1차 팔로업 초안을 늦게 보냈다면
#   14일이 됐어도 1차를 보낸 지 5일은 지나야 2차를 만든다.
#   (7/12에 1차를 보냈는데 7/15에 2차가 나가면 재촉으로 읽힌다)
# ─────────────────────────────────────────────────────────
MIN_GAP_AFTER_SEND = 5      # 직전 팔로업을 '실제로 보낸 날'로부터 최소 이만큼

# 팔로업을 보내면 안 되는 상태 (사람이 이미 판단을 내린 브랜드)
STOP_STATUSES = {"계약 완료", "거절", "기타) 패싱"}

# ─────────────────────────────────────────────────────────
# 영업 상태 자동 변경 — 사람이 내린 판단은 절대 덮어쓰지 않는다
#   발송 감지 → "메일 발송" / 회신 감지 → "컨택중"
#   단, 아래 두 경우에만 바꾼다:
#     ① 상태가 비어 있다
#     ② 상태가 '프로그램이 넣었을 법한 값'(메일 발송) 그대로다
#   밍이 계약 완료·거절·패싱·컨택중으로 바꿔놨으면 손대지 않는다.
#   (수기값을 조용히 되돌리면 "내가 바꾼 게 왜 사라졌지?"가 된다)
# ─────────────────────────────────────────────────────────
AUTO_SET_SENT = "메일 발송"
AUTO_SET_REPLIED = "컨택중"
# 이 값들만 자동 변경 대상. 그 외(계약 완료/거절/패싱/컨택중)는 사람 판단 → 보호.
OVERWRITABLE = {"", AUTO_SET_SENT}


def _auto_status(current: str, sent: bool, replied: bool) -> str:
    """자동으로 바꿔야 할 새 상태. 바꾸면 안 되면 빈 문자열."""
    cur = (current or "").strip()
    if cur not in OVERWRITABLE:
        return ""                       # 사람이 정한 값 → 보호
    if replied and cur != AUTO_SET_REPLIED:
        return AUTO_SET_REPLIED
    if sent and cur != AUTO_SET_SENT and not replied:
        return AUTO_SET_SENT
    return ""


# 새 컬럼이 없다는 안내는 브랜드마다 반복하지 말고 한 번만
_COL_WARNED = [False]


def _now():
    return datetime.now(timezone.utc)


def _parse(ts: str):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _days_since(ts: str) -> float:
    d = _parse(ts)
    if not d:
        return -1
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (_now() - d).total_seconds() / 86400


def main():
    args = sys.argv[1:]
    do_followup = "--followup" in args
    brand_filter = ""
    wait_days = FOLLOWUP_WAIT_DAYS
    if "--brand" in args:
        i = args.index("--brand")
        if i + 1 < len(args):
            brand_filter = args[i + 1].strip()
    if "--days" in args:
        i = args.index("--days")
        if i + 1 < len(args):
            try:
                wait_days = int(args[i + 1])
            except ValueError:
                pass

    try:
        service = gmail_drafts.get_service()
    except RuntimeError as e:
        print(f"❌ {e}")
        sys.exit(1)
    if service is None:
        print("❌ Gmail 연동이 안 됐어요 (token.json 없음).")
        print("   venv\\Scripts\\python auth_gmail.py 먼저 실행하세요.")
        sys.exit(1)

    sb = get_supabase_client()
    if sb is None:
        print("❌ Supabase 연결 실패 — .env의 SUPABASE_URL/KEY 확인")
        sys.exit(1)

    try:
        rows = (sb.table(TABLE_NAME).select("*").execute().data) or []
    except Exception as e:
        print(f"❌ DB 읽기 실패: {e}")
        sys.exit(1)

    # 메일을 만든 적 있는 브랜드만 대상
    targets = [
        r for r in rows
        if (r.get("mail_thread_id") or "").strip()
        and (not brand_filter or (r.get("brand_name") or "").strip() == brand_filter)
    ]
    if not targets:
        print("추적할 메일이 없습니다.")
        print("  (초안을 만들면 자동으로 추적 대상이 됩니다.")
        print("   이미 만든 초안이 있는데 안 잡히면, Supabase 컬럼 추가 SQL을")
        print("   실행했는지 확인하세요 — 그 전에 만든 초안은 ID가 안 남았습니다.)")
        return

    waiting, replied_list, todo, not_sent, pending = [], [], [], [], []

    print(f"▶ {len(targets)}건 확인 중...\n")
    for r in targets:
        bn = (r.get("brand_name") or "").strip()
        st = gmail_drafts.lookup_status(
            service,
            (r.get("mail_draft_id") or "").strip(),
            (r.get("mail_thread_id") or "").strip(),
        )

        # 실제로 나간 메일 수 (0=미발송 / 1=첫메일 / 2=1차팔로업까지 / 3=2차까지)
        sent_count = int(st.get("sent_count") or 0)
        sent_times = st.get("sent_times") or []
        # 1차 팔로업을 '실제로 보낸' 시각 = 두 번째 발송 (2차 간격 확보에 사용)
        fu1_sent_at = sent_times[1] if len(sent_times) > 1 else ""

        upd = {}
        if st["sent"] and not r.get("mail_sent_at"):
            upd["mail_sent_at"] = st["sent_at"]
        if st["replied"] and not r.get("mail_replied_at"):
            upd["mail_replied_at"] = st["replied_at"]
        # 대시보드가 '메일 단계'를 그리는 근거 — Gmail을 못 보는 클라우드에서도
        # 단계를 알아야 하므로 DB에 남긴다
        if sent_count and sent_count != int(r.get("mail_sent_count") or 0):
            upd["mail_sent_count"] = sent_count
        if fu1_sent_at and fu1_sent_at != (r.get("mail_followup1_sent_at") or ""):
            upd["mail_followup1_sent_at"] = fu1_sent_at

        # 영업 상태 자동 변경 (사람이 정한 값은 보호 — _auto_status 참고)
        #   발송(첫 메일이든 팔로업이든) → '메일 발송' / 답신 감지 → '컨택중'
        new_status = _auto_status(
            r.get("sales_status") or "",
            bool(sent_count or st["sent"] or r.get("mail_sent_at")),
            bool(st["replied"] or r.get("mail_replied_at")),
        )
        if new_status:
            upd["sales_status"] = new_status

        if upd:
            try:
                sb.table(TABLE_NAME).update(upd).eq("brand_name", bn).execute()
                r.update(upd)
                if new_status:
                    print(f"  · {bn} — 영업 상태 → '{new_status}' (자동)")
            except Exception:
                # 새 컬럼(mail_sent_count / mail_followup1_sent_at)이 아직 DB에
                # 없으면 그 필드만 빼고 재시도 — 나머지 추적은 계속 굴러가야 한다
                _retry = {k: v for k, v in upd.items()
                          if k not in ("mail_sent_count", "mail_followup1_sent_at")}
                try:
                    if _retry:
                        sb.table(TABLE_NAME).update(_retry).eq("brand_name", bn).execute()
                    r.update(upd)     # 메모리 상으로는 반영 (이번 실행의 판단에 사용)
                    if not _COL_WARNED[0]:
                        _COL_WARNED[0] = True
                        print("  ⚠ DB에 mail_sent_count / mail_followup1_sent_at 컬럼이"
                              " 없습니다 → 대시보드 단계 표시가 옛 방식으로 보입니다.")
                        print("     (Supabase SQL 편집기에서 컬럼 추가 SQL 1회 실행 필요)")
                except Exception as e:
                    print(f"  ⚠ {bn} DB 갱신 실패: {e}")

        sent_at = r.get("mail_sent_at") or st["sent_at"]
        replied_at = r.get("mail_replied_at") or st["replied_at"]
        status = (r.get("sales_status") or "").strip()
        fu = int(r.get("mail_followup_count") or 0)

        if not (sent_count or st["sent"] or sent_at):
            not_sent.append(bn)
            continue
        if replied_at:
            replied_list.append((bn, st["reply_from"] or "", replied_at))
            continue

        # ⭐ 모든 n일은 '첫 메일 발송일' 기준 — 화면 표시도 판단도 이 값 하나
        days = _days_since(sent_at)
        if status in STOP_STATUSES:
            continue

        # 다음 회차 = 지금까지 실제로 나간 메일 수
        #   1통 나갔으면 다음은 1차 팔로업, 2통이면 2차, 3통이면 끝
        nth = mail_stage.next_round(sent_count or 1)
        if not nth:
            continue                        # 2차까지 다 나감 → 종료

        if fu >= nth:
            # 이 회차 초안은 이미 만들어져 임시보관함에서 발송 대기 중
            pending.append((bn, days, nth))
            continue

        # 1차 = 7일 / 2차 = 14일 (첫 발송 기준). --days 로 단위를 바꾸면 그에 비례
        due = (mail_stage.due_days(nth) if wait_days == FOLLOWUP_WAIT_DAYS
               else wait_days * nth)
        ready = days >= due
        # 2차는 1차를 '실제로 보낸 날'로부터 최소 5일 확보 (늦게 보냈으면 밀어준다)
        if ready and nth == 2:
            _fu1 = fu1_sent_at or r.get("mail_followup1_sent_at") or ""
            if _fu1 and _days_since(_fu1) < MIN_GAP_AFTER_SEND:
                ready = False
        if ready:
            todo.append((bn, r, nth, days))
        else:
            waiting.append((bn, days, nth, due))

    # ── 현황 ──
    print("=" * 60)
    if not_sent:
        print(f"📝 아직 미발송 (초안 상태): {len(not_sent)}건")
        print("   " + ", ".join(not_sent))
    if pending:
        print(f"\n📄 팔로업 초안 대기 중 (밍이 보내야 함): {len(pending)}건")
        for bn, d, nth in pending:
            print(f"   · {bn} — {nth}차 팔로업 초안 · 첫 발송 {d:.0f}일 전")
    if waiting:
        print(f"\n⏳ 발송함 · 대기 중: {len(waiting)}건")
        for bn, d, nth, due in waiting:
            print(f"   · {bn} — 첫 발송 {d:.0f}일 전 · "
                  f"{nth}차 팔로업까지 {max(0, due - d):.0f}일 남음")
    if replied_list:
        print(f"\n💬 회신 옴: {len(replied_list)}건  ← 여기부터 챙기세요")
        for bn, frm, at in replied_list:
            print(f"   ★ {bn} — {frm}")
    if todo:
        print(f"\n🔔 팔로업 대상 (첫 발송 기준 경과 · 무응답): {len(todo)}건")
        for bn, _, nth, d in todo:
            print(f"   · {bn} — 첫 발송 {d:.0f}일 전 · {nth}차 팔로업")
    print("=" * 60)

    if not do_followup:
        if todo:
            print("\n팔로업 초안을 만들려면:")
            print("  venv\\Scripts\\python 메일_추적.py --followup")
        return

    if not todo:
        print("\n팔로업할 대상이 없습니다.")
        return

    # ── 팔로업 초안 생성 ──
    print(f"\n▶ 팔로업 초안 생성 ({len(todo)}건)...")
    made = 0
    for bn, r, nth, _ in todo:
        built = build_followup(r, round_no=nth)
        if built["skip"]:
            print(f"  · {bn} 건너뜀 — {built['skip_reason']}")
            continue
        try:
            ids = gmail_drafts.create_followup_draft(
                service, built["to"], built["subject"],
                (r.get("mail_thread_id") or "").strip(),
                built["html"], built["plain"],
            )
            _now_iso = _now().isoformat()
            _upd = {
                "mail_draft_id": ids.get("draft_id", ""),
                "mail_followup_count": nth,
                "mail_last_followup_at": _now_iso,
            }
            # 회차별 날짜도 개별 보관 → 히스토리에 1차/2차 날짜가 정확히 남는다
            if nth == 1:
                _upd["mail_followup1_at"] = _now_iso
            elif nth == 2:
                _upd["mail_followup2_at"] = _now_iso
            try:
                sb.table(TABLE_NAME).update(_upd).eq("brand_name", bn).execute()
            except Exception:
                # 새 컬럼(mail_followup1_at/2_at)이 아직 DB에 없으면 그 필드 빼고 재시도
                _upd.pop("mail_followup1_at", None)
                _upd.pop("mail_followup2_at", None)
                sb.table(TABLE_NAME).update(_upd).eq("brand_name", bn).execute()
            made += 1
            print(f"  ✅ {bn} → {built['to']} ({nth}차 팔로업)")
        except Exception as e:
            print(f"  ⚠ {bn} 실패: {e}")

    print("\n" + "=" * 60)
    print(f"  ✉️ 팔로업 초안 {made}건 (Gmail 임시보관함)")
    print("  → 원래 대화에 '답장'으로 붙어 있어요. 확인 후 보내세요.")
    print("=" * 60)


if __name__ == "__main__":
    _force_utf8_stdout()
    main()
