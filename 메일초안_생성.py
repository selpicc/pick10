# -*- coding: utf-8 -*-
"""
영업메일 초안 생성 (명령 한 줄)
─────────────────────────────────────────────────────────────
Supabase의 브랜드 중 '이메일이 있는' 브랜드별로 맞춤 영업메일을 만들어
Gmail '초안(임시보관함)'으로 저장합니다. (발송 X — 첨부·전송은 사람이 직접)

사용법:
  venv\\Scripts\\python 메일초안_생성.py              # 이메일 있는 신규 브랜드 전부
  venv\\Scripts\\python 메일초안_생성.py --all          # 이미 만든 것도 다시 (중복 무시)
  venv\\Scripts\\python 메일초안_생성.py --brand 엘빈즈   # 특정 브랜드 1개만
  venv\\Scripts\\python 메일초안_생성.py --brands "엘빈즈,비쥬앤허그"  # 지정한 브랜드들만 (방금 수집한 N개)
  venv\\Scripts\\python 메일초안_생성.py --limit 5       # 최대 5건만
  venv\\Scripts\\python 메일초안_생성.py --no-ai        # AI 맞춤 도입부 끄기

도입부는 기본적으로 AI(Gemini)가 브랜드 홈페이지를 읽고 1~2줄 맞춤으로 씁니다.
키가 없거나 생성이 미덥지 않으면(숫자·수상 등 검증 불가 표현) 자동으로
기존 카테고리 문구로 되돌아갑니다 — 메일은 어떤 경우에도 정상 생성됩니다.

전제: Gmail 1회 연동(token.json) 필요 — Gmail_연동_설정가이드.md 참고.
"""
import sys
import io
import json
import os

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

from supabase_client import get_supabase_client, TABLE_NAME
from email_templates import build_email
from brand_intro import make_opener

DRAFTED_PATH = "drafted_brands.json"


def _load_drafted() -> set:
    if os.path.exists(DRAFTED_PATH):
        try:
            with open(DRAFTED_PATH, encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def _save_drafted(s: set):
    try:
        with open(DRAFTED_PATH, "w", encoding="utf-8") as f:
            json.dump(sorted(s), f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _save_mail_ids(sb, brand: str, ids: dict):
    """초안ID·스레드ID를 sellers 테이블에 기록 (발송·회신 추적용).

    새 초안을 만들면 이전 추적 기록(발송일·회신일·팔로업 횟수)은 초기화한다.
    같은 브랜드에 메일을 새로 쓰는 것이므로 옛 상태를 끌고 가면 안 된다.
    컬럼이 아직 없으면(SQL 미실행) 조용히 넘어간다 — 초안 생성은 성공했으므로
    그걸 실패로 만들지 않는다.
    """
    base = {
        "mail_draft_id": ids.get("draft_id", ""),
        "mail_thread_id": ids.get("thread_id", ""),
        "mail_sent_at": None,
        "mail_replied_at": None,
        "mail_followup_count": 0,
        "mail_last_followup_at": None,
    }
    # 단계 표시용 컬럼도 함께 초기화 (없는 DB면 아래 except에서 빼고 재시도)
    full = dict(base, mail_sent_count=0, mail_followup1_sent_at=None)
    try:
        sb.table(TABLE_NAME).update(full).eq("brand_name", brand).execute()
    except Exception:
        try:
            sb.table(TABLE_NAME).update(base).eq("brand_name", brand).execute()
        except Exception as e:
            print(f"     (추적 정보 저장 못 함 — {e})")


def main():
    args = sys.argv[1:]
    only_new = "--all" not in args
    use_ai = "--no-ai" not in args   # 기본 켜짐. 끄면 카테고리 문구만 사용
    ai_used = []
    brand_filter = None
    brand_set = None  # --brands 로 지정한 여러 브랜드 (방금 수집한 N개만)
    limit = None
    if "--brand" in args:
        i = args.index("--brand")
        if i + 1 < len(args):
            brand_filter = args[i + 1].strip()
    if "--brands" in args:
        i = args.index("--brands")
        if i + 1 < len(args):
            brand_set = {
                b.strip() for b in args[i + 1].split(",") if b.strip()
            } or None
    if "--limit" in args:
        i = args.index("--limit")
        if i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                limit = None

    # 1) Gmail 인증 확인
    try:
        import gmail_drafts
        service = gmail_drafts.get_service()
    except RuntimeError as e:
        print(f"❌ {e}")
        sys.exit(1)
    if service is None:
        print("❌ Gmail 인증이 안 됐어요 (token.json 없음).")
        print("   Gmail_연동_설정가이드.md 따라 1회 설정 후 다시 실행하세요.")
        sys.exit(1)

    # 2) Supabase에서 브랜드 읽기
    sb = get_supabase_client()
    if sb is None:
        print("❌ Supabase 연결 실패 — .env의 SUPABASE_URL/KEY 확인")
        sys.exit(1)

    rows, page = [], 0
    while True:
        res = (
            sb.table(TABLE_NAME).select("*")
            .range(page * 1000, page * 1000 + 999).execute()
        )
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < 1000:
            break
        page += 1

    drafted = _load_drafted()
    created, skipped = [], []

    for r in rows:
        bn = (r.get("brand_name") or "").strip()
        if brand_filter and bn != brand_filter:
            continue
        if brand_set is not None and bn not in brand_set:
            continue

        # 브랜드 맞춤 도입부 (홈페이지를 읽고 AI가 1~2줄 작성).
        # 실패하면 빈 문자열 → 템플릿이 기존 카테고리 문구로 자동 복귀한다.
        if use_ai:
            try:
                opener = make_opener(r)
            except Exception as e:
                opener = ""
                print(f"  ⚠ {bn} — 도입부 생성 실패({e}). 카테고리 문구로 진행합니다.")
            if opener:
                r["ai_opener"] = opener
                ai_used.append(bn)
            else:
                print(f"  · {bn} — 맞춤 도입부 미생성 → 카테고리 문구 사용")

        built = build_email(r)
        if built["skip"]:
            skipped.append((bn, built["skip_reason"]))
            continue
        # 브랜드를 명시(--brand/--brands)하면 의도적 지정이므로 중복 스킵 안 함
        explicit = bool(brand_filter) or brand_set is not None
        if only_new and not explicit and bn in drafted:
            skipped.append((bn, "이미 초안 생성됨"))
            continue
        try:
            ids = gmail_drafts.create_draft(
                service, built["to"], built["subject"],
                built["html"], built["plain"],
            )
            # ⭐ 초안ID·스레드ID를 DB에 남긴다 — 나중에 '보냈는지 / 회신 왔는지'를
            #   Gmail에 물어보려면 이 두 개가 필요하다. (메일_추적.py)
            _save_mail_ids(sb, bn, ids)
            created.append((bn, built["to"], built["template"]))
            drafted.add(bn)
            print(f"  ✅ {bn} → {built['to']} ({built['template']}형)")
        except Exception as e:
            skipped.append((bn, f"초안 생성 오류: {e}"))
            print(f"  ⚠ {bn} → 오류: {e}")
        if limit and len(created) >= limit:
            break

    _save_drafted(drafted)

    print("\n" + "=" * 50)
    print(f"  ✉️ 초안 생성: {len(created)}건 (Gmail 임시보관함)")
    if created:
        p = sum(1 for _, _, t in created if t == "product")
        s = sum(1 for _, _, t in created if t == "service")
        print(f"     제품형 {p}건 · 서비스형 {s}건")
    if use_ai:
        made = {bn for bn, _, _ in created}
        n_ai = len([b for b in ai_used if b in made])
        print(f"     브랜드 맞춤 도입부(AI): {n_ai}건 / 나머지는 카테고리 문구")
    if skipped:
        print(f"  건너뜀: {len(skipped)}건 (이메일 없음·중복 등)")
    # --brands 로 지정했는데 초안이 안 만들어진 브랜드 안내 (DB에 없거나 이메일 없음)
    if brand_set is not None:
        made = {bn for bn, _, _ in created}
        seen = made | {bn for bn, _ in skipped}
        missing = sorted(brand_set - seen)
        if missing:
            print(f"  ⚠ 지정했지만 초안 못 만든 브랜드: {', '.join(missing)}")
            print(f"     (DB에 없거나 이메일 미수집 — 이메일 칸 확인 필요)")
    print("=" * 50)
    print("  → Gmail 임시보관함에서 셀픽 소개서 첨부 후 발송하세요.")
    print("     (본문에 '소개서 함께 첨부드립니다'라고 적혀 있으니 첨부 필수)")


if __name__ == "__main__":
    main()
