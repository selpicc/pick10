# -*- coding: utf-8 -*-
"""
브랜드 DB 초기화 (처음부터 다시 쌓기)
─────────────────────────────────────────────────────────────
① 현재 sellers 테이블 전체를 backup_sellers_날짜시간.json 으로 백업
② 화면에서 RESET 을 입력해야만 전체 삭제 실행 (안전장치)
③ 삭제 후 비었는지 검증

실행:  venv\\Scripts\\python reset_db.py
※ 삭제는 되돌릴 수 없습니다. 백업 파일은 남으니 필요하면 복구 가능.
"""
import sys
import io
import json
from datetime import datetime

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

from supabase_client import get_supabase_client, TABLE_NAME

cli = get_supabase_client()
if cli is None:
    print("❌ Supabase 연결 실패 — .env의 SUPABASE_URL/KEY 확인")
    sys.exit(1)

# ① 전체 행 조회 (페이지네이션)
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

total = len(rows)
print(f"현재 sellers 테이블: {total}건")

if total == 0:
    print("이미 비어 있습니다. 종료합니다.")
    sys.exit(0)

# ② 백업
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_path = f"backup_sellers_{stamp}.json"
with open(backup_path, "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, indent=2)
print(f"✅ 백업 완료: {backup_path} ({total}건)")

# ③ 확인 입력
print("\n" + "!" * 55)
print(f"  {total}건을 '전부' 삭제합니다. 되돌릴 수 없습니다.")
print(f"  (백업은 {backup_path} 에 남아 있습니다)")
print("!" * 55)
answer = input("\n정말 삭제하려면 대문자로 RESET 입력 후 Enter: ").strip()
if answer != "RESET":
    print("입력이 'RESET'이 아니므로 취소했습니다. 데이터는 그대로입니다.")
    sys.exit(0)

# ④ 삭제 — brand_name 기준 (테이블의 실제 키)
brand_names = []
seen = set()
for r in rows:
    bn = r.get("brand_name")
    if bn is None:
        continue
    if bn in seen:
        continue
    seen.add(bn)
    brand_names.append(bn)

deleted = 0
for bn in brand_names:
    try:
        cli.table(TABLE_NAME).delete().eq("brand_name", bn).execute()
        deleted += 1
    except Exception as e:
        print(f"   ⚠ 삭제 실패: {bn} → {e}")

# ⑤ 검증
res2 = cli.table(TABLE_NAME).select("brand_name").limit(5).execute()
remain = len(res2.data or [])
print("\n" + "=" * 55)
print(f"  삭제 처리한 브랜드: {deleted}건")
if remain == 0:
    print("  ✅ 테이블이 비었습니다. 이제 처음부터 다시 수집하세요.")
else:
    print(f"  ⚠ 아직 {remain}건 이상 남아 있습니다. (brand_name 없는 행일 수 있음)")
    print("     남은 행이 있으면 알려주세요. 별도로 처리하겠습니다.")
print("=" * 55)
