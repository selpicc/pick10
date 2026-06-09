# -*- coding: utf-8 -*-
"""
수집 진단 — 속도 + 연락처(전화/이메일) 수집률 측정
─────────────────────────────────────────────────────────────
대표 브랜드 몇 개로 collect_business_info를 돌려서:
  · 브랜드별 소요 시간 (어디가 느린지)
  · 전화 / 이메일 수집 여부 (수집률)
  · 신뢰도
를 한눈에 보여주고, 상세 로그는 '진단_로그.txt'에 저장합니다.

실행:  venv\\Scripts\\python 진단_수집측정.py
결과:  화면에 요약표 + 같은 폴더에 진단_로그.txt
"""
import sys
import io
import time

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

import business_info_collector as b

# ── 측정할 대표 브랜드 (카테고리 골고루: 이유식/카시트/스킨케어/사진서비스) ──
#   필요하면 이 목록만 바꿔서 다시 돌리면 돼요.
BRANDS = [
    "엘빈즈",          # 이유식 (공식몰 있음 — 기준)
    "큐어베이비",       # 베이비 스킨케어
    "베이비유",         # 만삭/돌사진 스튜디오 (서비스)
]

real_out = sys.stdout
log_buf = io.StringIO()
results = []

print("=" * 60)
print("  수집 진단 시작 — 브랜드별 시간 + 연락처 수집 측정")
print("=" * 60)

t_all = time.time()
for name in BRANDS:
    print(f"\n▶ '{name}' 측정 중... (시간이 좀 걸려요)", flush=True)
    # 상세 로그는 버퍼로 캡처 (화면엔 요약만)
    sys.stdout = log_buf
    print(f"\n\n##################  {name}  ##################")
    t0 = time.time()
    try:
        info = b.collect_business_info(name, "")
    except Exception as e:
        info = {"_error": f"{type(e).__name__}: {e}"}
    dt = time.time() - t0
    sys.stdout = real_out

    phone = (info.get("phone") or "").strip()
    email = (info.get("email") or "").strip()
    conf = info.get("confidence", "")
    src = "; ".join(info.get("sources", []))[:40]
    results.append((name, dt, phone, email, conf, src, info.get("_error")))
    print(f"   완료: {dt:5.1f}초   전화:{'O' if phone else 'X'}  "
          f"이메일:{'O' if email else 'X'}  신뢰도:{conf}")
    if info.get("_error"):
        print(f"   ⚠ 오류: {info['_error']}")

try:
    b.close_browser()
except Exception:
    pass

total = time.time() - t_all

# ── 상세 로그 파일 저장 ──
with open("진단_로그.txt", "w", encoding="utf-8") as f:
    f.write(log_buf.getvalue())

# ── 요약표 ──
print("\n\n" + "=" * 60)
print("  진단 요약")
print("=" * 60)
print(f"  {'브랜드':12s} {'시간':>7s}  전화  이메일  신뢰도")
print("  " + "-" * 50)
n_phone = n_email = 0
for name, dt, phone, email, conf, src, err in results:
    if phone:
        n_phone += 1
    if email:
        n_email += 1
    print(f"  {name:12s} {dt:6.1f}초   "
          f"{'O' if phone else 'X':^3s}   {'O' if email else 'X':^4s}   {conf}")
print("  " + "-" * 50)
n = len(results)
print(f"  합계 {n}건 · 총 {total:.1f}초 (평균 {total/max(n,1):.1f}초/건)")
print(f"  전화 수집 {n_phone}/{n}   이메일 수집 {n_email}/{n}")
print("=" * 60)
print("  상세 로그 → 진단_로그.txt (이 파일을 클로드에게 보여주세요)")
print("=" * 60)
