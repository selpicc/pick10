# -*- coding: utf-8 -*-
"""
연락처 수집 테스트 — 지정 브랜드만 collect_business_info로 돌려 결과 확인.
실행: venv\\Scripts\\python 연락처테스트.py
상세 로그는 연락처테스트_로그.txt 에 저장, 화면엔 요약표만.
"""
import sys
import io
import time

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

import business_info_collector as b

BRANDS = ["비쥬앤허그", "프라젠트라"]

real_out = sys.stdout
log_buf = io.StringIO()
results = []

print("=" * 60)
print("  연락처 수집 테스트 — 비쥬앤허그 · 프라젠트라")
print("=" * 60)

for name in BRANDS:
    print(f"\n▶ '{name}' 수집 중... (시간이 좀 걸려요)", flush=True)
    sys.stdout = log_buf
    print(f"\n\n##################  {name}  ##################")
    t0 = time.time()
    try:
        info = b.collect_business_info(name, "")
    except Exception as e:
        info = {"_error": f"{type(e).__name__}: {e}"}
    dt = time.time() - t0
    sys.stdout = real_out
    info["_dt"] = dt
    info["_name"] = name
    results.append(info)
    print(f"   완료: {dt:5.1f}초   "
          f"전화:{'O' if (info.get('phone') or '').strip() else 'X'}  "
          f"이메일:{'O' if (info.get('email') or '').strip() else 'X'}  "
          f"신뢰도:{info.get('confidence','')}")
    if info.get("_error"):
        print(f"   ⚠ 오류: {info['_error']}")

try:
    b.close_browser()
except Exception:
    pass

with open("연락처테스트_로그.txt", "w", encoding="utf-8") as f:
    f.write(log_buf.getvalue())

# ── 상세 결과표 ──
print("\n\n" + "=" * 60)
print("  결과 상세")
print("=" * 60)
for info in results:
    print(f"\n[ {info.get('_name')} ]  ({info.get('_dt',0):.1f}초)")
    print(f"   회사명     : {info.get('company_name') or '-'}")
    print(f"   대표       : {info.get('ceo') or '-'}")
    print(f"   사업자번호 : {info.get('business_number') or '-'}")
    print(f"   전화       : {info.get('phone') or '-'}")
    print(f"   이메일     : {info.get('email') or '-'}")
    print(f"   신뢰도     : {info.get('confidence') or '-'}")
    print(f"   소스       : {'; '.join(info.get('sources', [])) or '-'}")
print("\n" + "=" * 60)
print("  상세 로그 → 연락처테스트_로그.txt")
print("=" * 60)
