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


def main():
    args = sys.argv[1:]
    only_new = "--all" not in args
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
            gmail_drafts.create_draft(
                service, built["to"], built["subject"],
                built["html"], built["plain"],
            )
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
    print("  → Gmail 임시보관함에서 회사소개서 첨부 후 발송하세요.")


if __name__ == "__main__":
    main()
