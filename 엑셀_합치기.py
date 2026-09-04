# -*- coding: utf-8 -*-
"""
엑셀 두 개를 브랜드명·홈페이지 기준으로 중복 제거하며 합침.
실행: venv\\Scripts\\python 엑셀_합치기.py 원본.xlsx 추가.xlsx [--out 원본.xlsx]
기본은 원본 파일에 덮어써서 '업데이트' 한다.
"""
import argparse
import sys
import io
from urllib.parse import urlparse

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("base")
ap.add_argument("add")
ap.add_argument("--out", default="")
args = ap.parse_args()
out = args.out or args.base

base = pd.read_excel(args.base).fillna("")
add = pd.read_excel(args.add).fillna("")

# 홈페이지 컬럼명 찾기 (원본/추가 동일 스키마 가정)
hp_col = "공식 홈페이지" if "공식 홈페이지" in base.columns else None


def _dom(u):
    try:
        return urlparse(str(u)).netloc.replace("www.", "").lower()
    except Exception:
        return ""


seen_names = {str(n).strip() for n in base["브랜드명"]}
seen_doms = {_dom(u) for u in base[hp_col]} if hp_col else set()

new_rows = []
skipped = 0
for _, r in add.iterrows():
    nm = str(r["브랜드명"]).strip()
    dm = _dom(r[hp_col]) if hp_col else ""
    if nm in seen_names or (dm and dm in seen_doms):
        skipped += 1
        continue
    seen_names.add(nm)
    if dm:
        seen_doms.add(dm)
    new_rows.append(r)

merged = pd.concat([base, pd.DataFrame(new_rows)], ignore_index=True) if new_rows else base

with pd.ExcelWriter(out, engine="xlsxwriter") as xw:
    merged.to_excel(xw, index=False, sheet_name="영업처")
    wb, ws = xw.book, xw.sheets["영업처"]
    hf = wb.add_format({"bold": True, "bg_color": "#DCE6F1",
                        "border": 1, "align": "center", "valign": "vcenter"})
    for c, col in enumerate(merged.columns):
        ws.write(0, c, col, hf)
        w = max([len(str(col))] + [len(str(v)) for v in merged[col].astype(str).tolist()])
        ws.set_column(c, c, min(max(w + 2, 10), 45))
    ws.freeze_panes(1, 0)

print(f"✅ 합치기 완료: 기존 {len(base)} + 신규 {len(new_rows)}건 "
      f"(중복 {skipped} 제외) = 총 {len(merged)}건 → {out}")
for i, r in enumerate(new_rows, 1):
    print(f"  + {r['브랜드명']}")
