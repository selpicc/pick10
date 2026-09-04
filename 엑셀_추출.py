# -*- coding: utf-8 -*-
"""
Supabase sellers → 엑셀(.xlsx) 추출
─────────────────────────────────────────────────────────────
브랜드 목록을 보기 좋은 영업용 엑셀로 뽑습니다.
연락처/이메일은 '수기 우선(사람이 고친 값이 자동을 이긴다)'로 합칩니다.

실행 예:
  # 특정 브랜드만 (콤마 구분)
  venv\\Scripts\\python 엑셀_추출.py --brands "엘빈즈,비쥬앤허그" --out 결과.xlsx
  # 주력상품/카테고리에 특정 단어가 든 브랜드만
  venv\\Scripts\\python 엑셀_추출.py --contains "샴푸,치약,칫솔" --out 결과.xlsx
  # 전체
  venv\\Scripts\\python 엑셀_추출.py --out 전체.xlsx
"""
import argparse
import io
import sys

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

import pandas as pd
from supabase_client import get_supabase_client, TABLE_NAME

p = argparse.ArgumentParser(description="Supabase → 엑셀 추출")
p.add_argument("--brands", default="", help="브랜드명 콤마 구분 (지정 시 그 브랜드만)")
p.add_argument("--contains", default="", help="주력상품/카테고리에 포함된 단어 콤마 구분")
p.add_argument("--out", default="영업처_추출.xlsx", help="저장할 xlsx 파일명")
args = p.parse_args()

cli = get_supabase_client()
if cli is None:
    print("❌ Supabase 연결 실패 — .env의 SUPABASE_URL/KEY 확인")
    sys.exit(1)

# 전체 행 (페이지네이션)
rows, page, PAGE = [], 0, 1000
while True:
    res = (cli.table(TABLE_NAME).select("*")
           .range(page * PAGE, page * PAGE + PAGE - 1).execute())
    batch = res.data or []
    rows.extend(batch)
    if len(batch) < PAGE:
        break
    page += 1

# 필터
brand_set = {b.strip() for b in args.brands.split(",") if b.strip()}
words = [w.strip() for w in args.contains.split(",") if w.strip()]


def keep(r):
    if brand_set:
        return (r.get("brand_name") or "").strip() in brand_set
    if words:
        hay = ((r.get("flagship_product") or "") + " " +
               (r.get("product_category") or "") + " " +
               (r.get("keyword") or "") + " " +
               (r.get("category") or ""))
        return any(w in hay for w in words)
    return True


picked = [r for r in rows if keep(r)]

# 수기 우선 병합
def pick(r, manual, auto):
    return ((r.get(manual) or "").strip() or (r.get(auto) or "").strip())

out_rows = []
for r in picked:
    out_rows.append({
        "브랜드명":        (r.get("brand_name") or "").strip(),
        "주력상품명":      (r.get("flagship_product") or "").strip(),
        "상품 카테고리":   (r.get("product_category") or "").strip(),
        "발견 카테고리":   (r.get("category") or "").strip(),
        "발견 키워드":     (r.get("keyword") or "").strip(),
        "Selpic 점수":     r.get("selpic_score") or "",
        "전화":            pick(r, "manual_phone", "auto_phone"),
        "이메일":          pick(r, "manual_email", "auto_email"),
        "상호":            pick(r, "manual_company_name", "auto_company_name"),
        "대표":            pick(r, "manual_ceo", "auto_ceo"),
        "주소":            (r.get("auto_address") or "").strip(),
        "사업자정보 신뢰도": (r.get("auto_biz_confidence") or "").strip(),
        "스마트스토어 주소": (r.get("smartstore_url") or "").strip(),
        "영업 상태":       (r.get("sales_status") or "").strip(),
        "수집일":          (r.get("collected_at") or "").strip(),
    })

# 브랜드명 지정 순서 유지 (있으면)
if brand_set and args.brands:
    order = [b.strip() for b in args.brands.split(",") if b.strip()]
    out_rows.sort(key=lambda x: order.index(x["브랜드명"]) if x["브랜드명"] in order else 999)

df = pd.DataFrame(out_rows)

with pd.ExcelWriter(args.out, engine="xlsxwriter") as xw:
    df.to_excel(xw, index=False, sheet_name="영업처")
    wb, ws = xw.book, xw.sheets["영업처"]
    header_fmt = wb.add_format({"bold": True, "bg_color": "#DCE6F1",
                                "border": 1, "align": "center", "valign": "vcenter"})
    for c, col in enumerate(df.columns):
        ws.write(0, c, col, header_fmt)
        # 열 너비 자동 (한글 폭 고려)
        maxlen = max([len(str(col))] + [len(str(v)) for v in df[col].tolist()])
        ws.set_column(c, c, min(max(maxlen + 2, 10), 45))
    ws.freeze_panes(1, 0)

print(f"✅ {len(df)}건 저장 완료 → {args.out}")
for x in out_rows:
    print(f"  · {x['브랜드명']}  |  주력:{x['주력상품명'] or '-'}  |  "
          f"전화:{x['전화'] or '-'}  |  이메일:{x['이메일'] or '-'}")
