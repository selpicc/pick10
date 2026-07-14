# -*- coding: utf-8 -*-
"""메일 발송·회신 추적 + 팔로업 초안 생성
─────────────────────────────────────────────────────────────
초안까지만 만들고 끝나던 흐름에 '그 다음'을 붙인다.

  ① 보냈는지 확인   — 초안이 임시보관함에서 사라지고 보낸편지함에 있으면 발송됨
  ② 회신 왔는지 확인 — 같은 대화에 상대방 메시지가 있으면 회신 (자동응답은 제외)
  ③ 팔로업          — 보낸 지 N일 지났는데 답이 없으면 같은 대화에 리마인드 초안

콜드메일 회신의 상당수는 첫 메일이 아니라 팔로업에서 나온다.
다만 재촉은 역효과다 — 최대 2회, 7일 간격이 기본값.

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

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

from supabase_client import get_supabase_client, TABLE_NAME
from email_templates import build_followup
import gmail_drafts

FOLLOWUP_WAIT_DAYS = 7      # 발송 후 이만큼 지나도 답 없으면 팔로업
FOLLOWUP_MAX = 2            # 최대 팔로업 횟수 (그 이상은 스팸)

# 팔로업을 보내면 안 되는 상태 (사람이 이미 판단을 내린 브랜드)
STOP_STATUSES = {"계약 완료", "거절", "기타) 패싱"}


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

    waiting, replied_list, todo, not_sent = [], [], [], []

    print(f"▶ {len(targets)}건 확인 중...\n")
    for r in targets:
        bn = (r.get("brand_name") or "").strip()
        st = gmail_drafts.lookup_status(
            service,
            (r.get("mail_draft_id") or "").strip(),
            (r.get("mail_thread_id") or "").strip(),
        )

        upd = {}
        if st["sent"] and not r.get("mail_sent_at"):
            upd["mail_sent_at"] = st["sent_at"]
        if st["replied"] and not r.get("mail_replied_at"):
            upd["mail_replied_at"] = st["replied_at"]
        if upd:
            try:
                sb.table(TABLE_NAME).update(upd).eq("brand_name", bn).execute()
                r.update(upd)
            except Exception as e:
                print(f"  ⚠ {bn} DB 갱신 실패: {e}")

        sent_at = r.get("mail_sent_at") or st["sent_at"]
        replied_at = r.get("mail_replied_at") or st["replied_at"]
        status = (r.get("sales_status") or "").strip()
        fu = int(r.get("mail_followup_count") or 0)

        if not (st["sent"] or sent_at):
            not_sent.append(bn)
            continue
        if replied_at:
            replied_list.append((bn, st["reply_from"] or "", replied_at))
            continue

        days = _days_since(sent_at)
        if status in STOP_STATUSES:
            continue
        if fu >= FOLLOWUP_MAX:
            continue
        # 팔로업 간격은 '마지막 연락' 기준 (발송이든 이전 팔로업이든)
        last = r.get("mail_last_followup_at") or sent_at
        if _days_since(last) >= wait_days:
            todo.append((bn, r, fu + 1, days))
        else:
            waiting.append((bn, days, fu))

    # ── 현황 ──
    print("=" * 60)
    if not_sent:
        print(f"📝 아직 미발송 (초안 상태): {len(not_sent)}건")
        print("   " + ", ".join(not_sent))
    if waiting:
        print(f"\n⏳ 발송함 · 대기 중: {len(waiting)}건")
        for bn, d, fu in waiting:
            print(f"   · {bn} — {d:.0f}일 전 발송"
                  + (f" · 팔로업 {fu}회" if fu else ""))
    if replied_list:
        print(f"\n💬 회신 옴: {len(replied_list)}건  ← 여기부터 챙기세요")
        for bn, frm, at in replied_list:
            print(f"   ★ {bn} — {frm}")
    if todo:
        print(f"\n🔔 팔로업 대상 ({wait_days}일 경과 · 무응답): {len(todo)}건")
        for bn, _, nth, d in todo:
            print(f"   · {bn} — {d:.0f}일 전 발송 · {nth}차 팔로업")
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
            sb.table(TABLE_NAME).update({
                "mail_draft_id": ids.get("draft_id", ""),
                "mail_followup_count": nth,
                "mail_last_followup_at": _now().isoformat(),
            }).eq("brand_name", bn).execute()
            made += 1
            print(f"  ✅ {bn} → {built['to']} ({nth}차 팔로업)")
        except Exception as e:
            print(f"  ⚠ {bn} 실패: {e}")

    print("\n" + "=" * 60)
    print(f"  ✉️ 팔로업 초안 {made}건 (Gmail 임시보관함)")
    print("  → 원래 대화에 '답장'으로 붙어 있어요. 확인 후 보내세요.")
    print("=" * 60)


if __name__ == "__main__":
    main()
