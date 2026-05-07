"""
PICK10 - Step 4: 네이버 쇼핑 API로 스마트스토어 셀러 1건 찾기
=================================================================
실행 방법:
    1) 명령 프롬프트 열기
    2) cd "C:\\Users\\PC\\Documents\\Claude\\Projects\\셀픽 영업처 수집"
    3) venv\\Scripts\\activate     ← (venv) 표시 확인
    4) python step4_search.py

결과: 화면에 셀러 1명의 스토어명, 메인 URL 등이 출력됨
=================================================================
"""

import os
import sys
import re
import urllib.parse

import requests
from dotenv import load_dotenv


# 한글 출력이 윈도우 cmd에서 깨지지 않도록 강제 UTF-8 설정
# (Python 3.7+ 에서만 동작 — 우리는 3.12라 OK)
sys.stdout.reconfigure(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────
# 1) .env 파일에서 API 키 불러오기
# ─────────────────────────────────────────────────────────────────
load_dotenv()
CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

# 키가 비어있는지 검증 (가장 흔한 실수)
if not CLIENT_ID or not CLIENT_SECRET:
    print("❌ 오류: .env 파일에서 API 키를 못 찾았어요.")
    print("   .env 파일이 작업 폴더 안에 있는지,")
    print("   NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 두 줄이 들어있는지 확인해주세요.")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────
# 2) 검색 설정
# ─────────────────────────────────────────────────────────────────
KEYWORD = "튼살크림"   # 첫 주 테스트용 키워드 (나중에 바꿀 수 있음)
DISPLAY = 20           # 한 번에 받을 상품 개수 (최대 100)

print("\n" + "=" * 60)
print(f"  🔍 PICK10 / Step 4 — 네이버 쇼핑 검색")
print(f"  키워드: '{KEYWORD}'")
print("=" * 60 + "\n")


# ─────────────────────────────────────────────────────────────────
# 3) 네이버 쇼핑 검색 API 호출
# ─────────────────────────────────────────────────────────────────
api_url = "https://openapi.naver.com/v1/search/shop.json"
headers = {
    "X-Naver-Client-Id": CLIENT_ID,
    "X-Naver-Client-Secret": CLIENT_SECRET,
}
params = {
    "query": KEYWORD,
    "display": DISPLAY,
    "sort": "sim",   # sim=정확도순 / date=최신순 / asc=가격↑ / dsc=가격↓
}

try:
    response = requests.get(api_url, headers=headers, params=params, timeout=10)
except requests.exceptions.RequestException as e:
    print(f"❌ 네트워크 오류: {e}")
    sys.exit(1)

if response.status_code != 200:
    print(f"❌ API 호출 실패 (HTTP {response.status_code})")
    print("   응답 본문:", response.text[:300])
    print("\n   가능한 원인:")
    print("   - Client ID/Secret 값에 공백이나 잘못된 문자가 섞임")
    print("   - 네이버 개발자센터에서 '검색' API 사용 신청 누락")
    sys.exit(1)

data = response.json()
items = data.get("items", [])
print(f"✅ 응답 OK — 총 {len(items)}개 상품 받음\n")


# ─────────────────────────────────────────────────────────────────
# 4) 스마트스토어 셀러만 필터링
#    (네이버 쇼핑 결과엔 11번가, G마켓 등 다른 몰도 섞여 있음)
# ─────────────────────────────────────────────────────────────────
smartstore_items = [
    item for item in items
    if "smartstore.naver.com" in item.get("link", "")
]
print(f"   - 그 중 스마트스토어 셀러: {len(smartstore_items)}건")
print(f"   - 그 외 다른 몰: {len(items) - len(smartstore_items)}건\n")

if not smartstore_items:
    print("❌ 스마트스토어 셀러를 못 찾았어요.")
    print("   키워드를 바꾸거나, sort 옵션을 'date'로 변경해보세요.")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────
# 5) 첫 번째 셀러 정보 정리해서 출력
# ─────────────────────────────────────────────────────────────────
first = smartstore_items[0]

# 네이버 API 응답의 title에는 <b>...</b> 같은 HTML 태그가 들어있어서 제거
clean_title = re.sub(r"<[^>]+>", "", first.get("title", ""))

# 상품 URL에서 스토어 ID 추출
# 예: https://smartstore.naver.com/abc_store/products/12345 → "abc_store"
parsed = urllib.parse.urlparse(first.get("link", ""))
path_parts = parsed.path.strip("/").split("/")
store_id = path_parts[0] if path_parts else ""
store_main_url = f"https://smartstore.naver.com/{store_id}"

print("─" * 60)
print("📌 1번째 후보 셀러")
print("─" * 60)
print(f"  스토어명 (mallName) : {first.get('mallName', '(없음)')}")
print(f"  스토어 ID           : {store_id}")
print(f"  대표 상품           : {clean_title}")
print(f"  카테고리            : "
      f"{first.get('category1', '')} > "
      f"{first.get('category2', '')} > "
      f"{first.get('category3', '')}")
print(f"  최저가              : {int(first.get('lprice', 0)):,}원")
print(f"  상품 페이지         : {first.get('link', '')}")
print(f"  스토어 메인 페이지  : {store_main_url}")
print("─" * 60)

print("\n👉 다음 단계: 위 '스토어 메인 페이지'에 접속해서 판매자정보를 자동 추출.")
print("   (Step 5 코드에서 이 URL을 그대로 받아 처리할 예정)\n")
