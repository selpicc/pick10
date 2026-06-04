# -*- coding: utf-8 -*-
"""pnsls.co.kr 진단 — 오아센 공식몰 판정 실패 원인 확인용 (1회용)"""
import re
import requests
import business_info_collector as b

BRAND = "아토피 연구소 오아센"
URL = "https://www.pnsls.co.kr/"

h = ""
try:
    r = requests.get(URL, headers=b.HTTP_HEADERS, timeout=10)
    h = r.text if r.status_code == 200 else ""
    print("HTTP 상태:", r.status_code)
except Exception as e:
    print("requests 실패:", e)

print("HTML 길이:", len(h))

t = re.search(r"<title[^>]*>([^<]+)</title>", h, re.I)
print("title:", t.group(1).strip() if t else "(없음)")

og = re.search(
    r'property=["\']og:site_name["\'][^>]*content=["\']([^"\']+)', h, re.I
)
print("og:site_name:", og.group(1) if og else "(없음)")

ogt = re.search(
    r'property=["\']og:title["\'][^>]*content=["\']([^"\']+)', h, re.I
)
print("og:title:", ogt.group(1) if ogt else "(없음)")

tail = re.sub(r"<[^>]+>", " ", h[-15000:])
tail = re.sub(r"\s+", " ", tail).strip()
print("footer 미리보기(끝 500자):", tail[-500:])

m = re.search(r"\d{3}-\d{2}-\d{5}", tail)
print("footer 사업자번호 패턴:", m.group(0) if m else "(없음)")

vis = re.sub(r"<[^>]+>", " ", h)
print("'오아센' 등장 횟수(보이는 텍스트):", vis.count("오아센"))

print("own_site 판정:", b._is_brand_own_site(h, BRAND))
print("brand_presence 점수:", b._brand_presence_score(h, BRAND))
