# -*- coding: utf-8 -*-
"""
이메일 영업용 리드 추출
─────────────────────────────────────────────────────────────
Supabase sellers 테이블에서 '이메일이 있는 브랜드'만 골라
초안 작성에 필요한 정보(브랜드명·이메일·카테고리·주력상품·영업상태)를
email_leads.json 으로 저장합니다.

실행:  venv\\Scripts\\python export_email_leads.py
결과:  email_leads.json  (같은 폴더에 생성)
"""
import sys
import io
import json

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

from supabase_client import get_supabase_client, TABLE_NAME

cli = get_supabase_client()
if cli is None:
    print("❌ Supabase 연결 실패 — .env의 SUPABASE_URL/KEY 확인")
    sys.exit(1)

# 전체 행 가져오기 (페이지네이션)
rows = []
page = 0
PAGE = 1000
while True:
    res = (
        cli.table(TABLE_NAME)
        .select("*")
        .range(page * PAGE, page * PAGE + PAGE - 1)
        .execute()
    )
    batch = res.data or []
    rows.extend(batch)
    if len(batch) < PAGE:
        break
    page += 1

print(f"전체 {len(rows)}행 조회")

leads = []
for r in rows:
    # 이메일: 자동 우선, 없으면 수기
    email = (r.get("auto_email") or r.get("manual_email") or "").strip()
    if not email or "@" not in email:
        continue

    brand = (r.get("brand_name") or "").strip()
    # 카테고리: 발견 카테고리(출산 서비스 판별용) + 상품 카테고리(맞춤 문구용)
    found_cat = (r.get("category") or "").strip()
    prod_cat = (r.get("product_category") or "").strip()
    flagship = (r.get("flagship_product") or "").strip()
    sales_status = (r.get("sales_status") or "").strip()

    # 제품형 / 서비스형 판별
    is_service = ("서비스" in found_cat) or ("서비스" in prod_cat)
    template = "service" if is_service else "product"

    leads.append({
        "brand_name": brand,
        "email": email,
        "email_source": "자동" if r.get("auto_email") else "수기",
        "found_category": found_cat,
        "product_category": prod_cat,
        "flagship_product": flagship,
        "sales_status": sales_status,
        "template": template,
        "biz_confidence": (r.get("auto_biz_confidence") or "").strip(),
    })

# 이미 영업상태가 채워진 곳(발송/컨택 등)은 따로 표시
already = [x for x in leads if x["sales_status"]]
fresh = [x for x in leads if not x["sales_status"]]

out = {
    "total_with_email": len(leads),
    "fresh_count": len(fresh),          # 영업상태 비어있음 = 신규 발송 대상
    "already_contacted_count": len(already),
    "leads": leads,
}

with open("email_leads.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print("\n" + "=" * 55)
print(f"  이메일 보유 브랜드: {len(leads)}건")
print(f"   - 신규 발송 대상(영업상태 빈칸): {len(fresh)}건")
print(f"   - 이미 영업상태 기록됨: {len(already)}건")
print("-" * 55)
# 미리보기
for x in leads[:30]:
    t = "서비스" if x["template"] == "service" else "제품"
    mark = "·이미기록" if x["sales_status"] else ""
    print(f"  [{t}] {x['brand_name']}  <{x['email']}>  "
          f"주력:{x['flagship_product'] or '-'}{mark}")
if len(leads) > 30:
    print(f"  ... 외 {len(leads) - 30}건")
print("=" * 55)
print("✅ email_leads.json 저장 완료 — 이 파일을 클로드가 읽습니다")
