"""
PICK10 - 셀러 1건 자동 수집 (메인 스크립트 v2)
=================================================================
v2 변경:
  - 자동화 크롬(Playwright) 부분 제거 (봇 차단 우회 안정성 ↓ + 어차피 막힘)
  - 주력상품 선정 로직 개선:
      v1: 키워드 검색의 1위 상품 = 주력 (키워드 의존)
      v2: 셀러 mallName으로 재검색 → 그 셀러 자체의 노출 1위 상품
          → 그 셀러의 진짜 주력 상품에 가장 가깝다 (네이버 검색이 종합 평가)
  - CSV 컬럼:
      ✅ 자동: 검색 API로 받는 정보들
      ✏️ 수기: 관심고객수, 리뷰수, 상호, 대표, 이메일, 전화, 마케팅 분석

실행:
  python collect_one.py                ← 기본 키워드 '튼살크림'
  python collect_one.py 산양분유       ← 다른 키워드
=================================================================
"""

import csv
import math
import os
import re
import sys
import urllib.parse
from datetime import datetime

import requests
from dotenv import load_dotenv


sys.stdout.reconfigure(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────────
KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "튼살크림"
RESULTS_DIR = "results"
os.makedirs(RESULTS_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────────────
# .env API 키 불러오기
# ─────────────────────────────────────────────────────────────────
load_dotenv()
CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    print("❌ .env 파일에서 API 키를 못 찾았어요.")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────
# 공용 함수: 네이버 쇼핑 검색 API 호출
# ─────────────────────────────────────────────────────────────────
def search_shop(query: str, display: int = 20, sort: str = "sim") -> list:
    """네이버 쇼핑 검색 API 호출 → items 리스트 반환"""
    api_url = "https://openapi.naver.com/v1/search/shop.json"
    headers = {
        "X-Naver-Client-Id": CLIENT_ID,
        "X-Naver-Client-Secret": CLIENT_SECRET,
    }
    params = {"query": query, "display": display, "sort": sort}

    try:
        resp = requests.get(api_url, headers=headers, params=params, timeout=10)
    except requests.exceptions.RequestException as e:
        print(f"❌ 네트워크 오류: {e}")
        return []

    if resp.status_code != 200:
        print(f"❌ API 호출 실패 (HTTP {resp.status_code}): {resp.text[:200]}")
        return []

    return resp.json().get("items", [])


def extract_store_id(link: str) -> str:
    """검색 API의 link에서 store id 추출 시도 (확실하지 않음 → 다중 fallback)"""
    if not link:
        return ""
    # 일반 패턴: smartstore.naver.com/{storeId}/products/...
    m = re.match(r"https?://smartstore\.naver\.com/([^/?#]+)", link)
    if m and m.group(1) not in ("main", "search", "category"):
        return m.group(1)
    return ""


def resolve_real_store_url(link: str) -> str:
    """
    검색 API link가 /main/products/... 같은 통합 URL이면
    requests로 가볍게 redirect 따라가서 진짜 셀러 URL 추출 시도.
    실패하면 빈 문자열 반환 (수기 보완).
    """
    if not link:
        return ""
    try:
        resp = requests.get(
            link,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "ko-KR,ko;q=0.9",
            },
            allow_redirects=True,
            timeout=10,
        )
        final_url = resp.url

        # 최종 URL에서 진짜 storeId 추출
        m = re.match(r"https?://smartstore\.naver\.com/([^/?#]+)", final_url)
        if m and m.group(1) not in ("main", "search", "category"):
            return f"https://smartstore.naver.com/{m.group(1)}"

        # brand.naver.com 으로 redirect되는 경우도 잡기
        m = re.match(r"https?://brand\.naver\.com/([^/?#]+)", final_url)
        if m:
            return f"https://brand.naver.com/{m.group(1)}"

    except Exception:
        pass
    return ""


def clean_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def build_real_product_url(store_url: str, product_id: str) -> str:
    """진짜 storeId + productId 조합 → 클릭 시 바로 열리는 상품 URL"""
    if not store_url or not product_id:
        return ""
    m = re.match(r"https?://smartstore\.naver\.com/([^/?#]+)", store_url)
    if m:
        return f"https://smartstore.naver.com/{m.group(1)}/products/{product_id}"
    m = re.match(r"https?://brand\.naver\.com/([^/?#]+)", store_url)
    if m:
        return f"https://brand.naver.com/{m.group(1)}/products/{product_id}"
    return ""


def extract_product_keyword(title: str) -> str:
    """주력 상품명에서 검색용 핵심 키워드 추출
    - [의료기기] / (15g) 같은 부가정보 제거
    - 첫 단어가 specific하면 그것만, 일반 단어면 첫 2단어
    """
    if not title:
        return ""
    text = clean_html_tags(title)
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = re.sub(r"\([^)]*\)", "", text)
    text = " ".join(text.split())
    words = text.split()
    if not words:
        return ""

    common_words = {
        "베이비", "유아", "아기", "신생아", "임산부", "산모",
        "임산부용", "유아용", "신생아용", "키즈", "주니어",
        "프리미엄", "신상", "정품", "세트", "출산", "임신",
    }
    if words[0] in common_words and len(words) >= 2:
        return f"{words[0]} {words[1]}"
    return words[0]


# ─────────────────────────────────────────────────────────────────
# 네이버 검색 API 일반화 + 마케팅 등급 산정
# ─────────────────────────────────────────────────────────────────
def search_naver(category: str, query: str, display: int = 1) -> dict:
    """네이버 검색 API 일반화 (블로그/카페/뉴스/지식인 등)
    응답: {'total': int, 'items': [...]}"""
    api_url = f"https://openapi.naver.com/v1/search/{category}.json"
    headers = {
        "X-Naver-Client-Id": CLIENT_ID,
        "X-Naver-Client-Secret": CLIENT_SECRET,
    }
    params = {"query": query, "display": display}
    try:
        resp = requests.get(api_url, headers=headers, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return {"total": 0, "items": []}


def calculate_marketing_grade(brand_name: str) -> dict:
    """브랜드 기반 4채널 검색 노출량 → 상/중/하 등급 + 세부 해석

    측정 채널:
      - 블로그 (바이럴·후기·체험)
      - 카페   (맘카페 + 일반 카페 입소문)  ← 가중치 ↑
      - 뉴스   (PR/홍보 활동)
      - 지식인 (소비자 관심·검색 의도)
    """
    blog = search_naver("blog", brand_name).get("total", 0)
    cafe = search_naver("cafearticle", brand_name).get("total", 0)
    news = search_naver("news", brand_name).get("total", 0)
    kin = search_naver("kin", brand_name).get("total", 0)

    # 가중 점수 (log10 + 가중치)
    # 카페는 맘카페 등 실제 구매자 의견이라 가중치 ↑
    score = (
        math.log10(blog + 1) * 1.5
        + math.log10(cafe + 1) * 2.0
        + math.log10(news + 1) * 1.0
        + math.log10(kin + 1) * 1.0
    )

    # 3단계 등급 (상/중/하)
    if score >= 9:
        grade = "상"
    elif score >= 4:
        grade = "중"
    else:
        grade = "하"

    # ── 세부 해석 만들기 ──
    interpretation_parts = []

    # 1) 종합 진단
    if grade == "상":
        interpretation_parts.append(
            "📈 [종합 진단] 온라인 마케팅이 활발히 작동 중. "
            "이미 일정 규모 광고비를 집행 중이거나, 자연 바이럴 자산이 충분히 쌓인 브랜드."
        )
    elif grade == "중":
        interpretation_parts.append(
            "📊 [종합 진단] 일정 수준의 노출은 확보된 상태. "
            "안정기 진입 직전의 성장 브랜드, 또는 특정 채널 의존형."
        )
    else:
        interpretation_parts.append(
            "📉 [종합 진단] 온라인 노출이 전반적으로 부족. "
            "초기 또는 마케팅 의지 약함 — 셀픽 광고가 즉각적 효과 낼 수 있는 블루오션."
        )

    # 2) 채널 강점 분석 (가장 두드러진 채널 찾기)
    channels = [("블로그", blog), ("카페", cafe), ("뉴스", news), ("지식인", kin)]
    channels.sort(key=lambda x: x[1], reverse=True)
    top_name, top_count = channels[0]

    if top_count > 0:
        if top_name == "카페" and cafe >= 50:
            interpretation_parts.append(
                f"☕ [강점 채널] 카페 {cafe:,}건 — 맘카페·커뮤니티 입소문이 핵심. "
                "실제 구매자 신뢰도 높은 자연 바이럴 보유."
            )
        elif top_name == "블로그" and blog >= 100:
            interpretation_parts.append(
                f"✍️ [강점 채널] 블로그 {blog:,}건 — 후기·체험 마케팅 활발. "
                "인플루언서 협업 또는 자연 후기형 콘텐츠 다수."
            )
        elif top_name == "뉴스" and news >= 10:
            interpretation_parts.append(
                f"📰 [강점 채널] 뉴스 {news:,}건 — PR·기사형 광고 의존. "
                "미디어 활동 활발하지만 직접 채널 노출은 보강 필요."
            )
        elif top_name == "지식인" and kin >= 30:
            interpretation_parts.append(
                f"❓ [강점 채널] 지식인 {kin:,}건 — 소비자 검색·관심 의도 높음. "
                "수요는 있지만 마케팅 콘텐츠가 못 따라가는 상태 → 광고 효과 매우 클 수 있음."
            )
        else:
            interpretation_parts.append(
                f"🔍 [강점 채널] {top_name} {top_count:,}건 — 두드러진 채널 없이 분산형."
            )

    # 3) 약점 채널 지적
    weak_channels = []
    if cafe < 30:
        weak_channels.append("카페(맘카페 입소문 약함)")
    if blog < 50:
        weak_channels.append("블로그(후기·체험단 미흡)")
    if news < 5 and grade != "하":
        weak_channels.append("뉴스(PR 활동 부족)")

    if weak_channels:
        interpretation_parts.append(
            f"⚠️ [약점 채널] {', '.join(weak_channels)}"
        )

    # 4) 영업 어프로치 가이드
    if grade == "상":
        interpretation_parts.append(
            "💡 [영업 어프로치] '광고 채널 다변화' 또는 '광고 효율 개선' 메시지로 접근. "
            "이미 광고비 집행 의지 있는 셀러 — 셀픽이 추가 채널로 들어가는 포지션."
        )
    elif grade == "중":
        interpretation_parts.append(
            "💡 [영업 어프로치] '신규 채널로 도달 확장' 메시지로 접근. "
            "성장기 브랜드 — 새로운 광고 채널 검토 의지가 가장 큰 시점."
        )
    else:
        interpretation_parts.append(
            "💡 [영업 어프로치] '온라인 진입·첫 광고 효과 극대화' 메시지로 접근. "
            "노출 부족 브랜드 — 셀픽 광고만으로도 검색량 대비 큰 도달 효과 기대 가능."
        )

    # 콘솔용 (여러 줄) / CSV용 (한 줄, ' | '로 구분)
    interpretation_console = "\n      ".join(interpretation_parts)
    interpretation_csv = " | ".join(interpretation_parts)

    return {
        "grade": grade,
        "blog": blog,
        "cafe": cafe,
        "news": news,
        "kin": kin,
        "score": round(score, 2),
        "interpretation_console": interpretation_console,
        "interpretation_csv": interpretation_csv,
    }


# ─────────────────────────────────────────────────────────────────
# 메인 흐름
# ─────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  📦 PICK10 - 셀러 1건 자동 수집 (v2)")
print(f"  키워드: '{KEYWORD}'")
print("=" * 60 + "\n")


# ── Step 1: 키워드 검색 → 첫 스마트스토어 셀러 식별 ──
print(f"🔍 [1/4] '{KEYWORD}' 검색 → 셀러 식별...")
items = search_shop(KEYWORD, display=20)
ss_items = [it for it in items if "smartstore.naver.com" in it.get("link", "")]

if not ss_items:
    print("❌ 스마트스토어 셀러를 못 찾았어요.")
    sys.exit(1)

first_seller_item = ss_items[0]
brand_name = first_seller_item.get("mallName", "")
search_rank_in_keyword = items.index(first_seller_item) + 1

print(f"   ✅ '{KEYWORD}' 1번째 스마트스토어 셀러: {brand_name}")
print(f"   📍 키워드 노출 순위: 전체 {search_rank_in_keyword}위 / 스마트스토어 {ss_items.index(first_seller_item)+1}위\n")


# ── Step 2: 셀러 자체의 주력 상품 식별 (브랜드명으로 재검색) ──
print(f"🔍 [2/4] '{brand_name}' 으로 재검색 → 셀러 주력 상품 식별...")
brand_items = search_shop(brand_name, display=20)
# 같은 셀러 상품만 필터 (다른 셀러가 같은 키워드로 나올 수 있음)
own_items = [
    it for it in brand_items
    if it.get("mallName", "").strip() == brand_name.strip()
    and "smartstore.naver.com" in it.get("link", "")
]

if not own_items:
    print(f"   ⚠️ 브랜드명 재검색에서 같은 셀러 상품을 못 찾음")
    print(f"   → 키워드 1위 상품을 주력으로 사용 (fallback)")
    flagship = first_seller_item
    selection_basis = f"'{KEYWORD}' 키워드 검색 1위 (셀러 재검색 실패 fallback)"
else:
    flagship = own_items[0]
    own_count = len(own_items)
    print(f"   ✅ '{brand_name}' 셀러 자체 상품 {own_count}건 발견")
    print(f"   ✅ 노출 1위 상품 = 주력 상품으로 선정")
    selection_basis = f"'{brand_name}' 브랜드명 검색 시 노출 1위 상품 (네이버 종합 평가)"

flagship_title = clean_html_tags(flagship.get("title", ""))
flagship_url = flagship.get("link", "")
flagship_category = " > ".join(
    filter(None, [flagship.get(f"category{i}", "") for i in range(1, 5)])
)
flagship_price = int(flagship.get("lprice", 0))
flagship_product_id = flagship.get("productId", "")

print(f"      → 주력 상품: {flagship_title[:50]}...")
print(f"      → 카테고리 : {flagship_category}")
print(f"      → 가격     : {flagship_price:,}원\n")


# ── Step 3: 마케팅 등급 산정 (주력 상품 핵심 키워드로 4채널 검색) ──
product_keyword = extract_product_keyword(flagship_title)
print(f"📊 [3/4] '{product_keyword}' 마케팅 노출량 분석 (블로그/카페/뉴스/지식인)...")
print(f"   ℹ️ 주력 상품 핵심 키워드 (검색 기준): '{product_keyword}'")
mgrade = calculate_marketing_grade(product_keyword)
print(f"   ✅ 종합 등급: {mgrade['grade']}  (점수 {mgrade['score']})")
print(f"      • 블로그   : {mgrade['blog']:,}건")
print(f"      • 카페     : {mgrade['cafe']:,}건  (맘카페 포함, 가중치 ↑)")
print(f"      • 뉴스     : {mgrade['news']:,}건")
print(f"      • 지식인   : {mgrade['kin']:,}건")
print(f"\n   💡 마케팅 활동 세부 해석:")
print(f"      {mgrade['interpretation_console']}")
print()

# 검색 노출 시그널 (보조)
own_count = len(own_items) if own_items else 0
search_signal = (
    f"'{KEYWORD}' 키워드 {search_rank_in_keyword}위 · "
    f"브랜드명 검색 시 노출 상품 {own_count}건"
)


# ── 스마트스토어 진짜 URL 알아내기 ──
# 1차: link에서 직접 추출 시도 (보통 실패 — link가 통합 URL이라)
# 2차: requests로 redirect 따라가기 (가벼운 자동화)
store_url_guess = ""
sid = extract_store_id(flagship_url)
if sid:
    store_url_guess = f"https://smartstore.naver.com/{sid}"
else:
    print("   🔄 redirect 따라가서 진짜 스토어 URL 찾는 중...")
    store_url_guess = resolve_real_store_url(flagship_url)
    if store_url_guess:
        print(f"   ✅ 진짜 스토어 URL: {store_url_guess}")
    else:
        print(f"   ⚠️ redirect 추적 실패 → 수기 보완 필요")



# ── 결과 정리 ──
collected = {
    # 자동 (메타)
    "수집일": datetime.now().strftime("%Y-%m-%d"),
    "키워드": KEYWORD,
    # 자동 (검색 API - 셀러/상품 정보)
    "브랜드명": brand_name,
    "스마트스토어 주소": store_url_guess,
    "주력상품명": flagship_title,
    "상품 카테고리": flagship_category,
    "가격": f"{flagship_price:,}원" if flagship_price else "",
    "주력상품 선정 근거": selection_basis,
    # 자동 (마케팅 등급 - 4채널 분석, 주력 상품 키워드 기준)
    "마케팅 검색 키워드 (자동)": product_keyword,
    "마케팅 등급 (자동)": mgrade["grade"],
    "마케팅 점수 (자동)": mgrade["score"],
    "마케팅 채널별 노출 (자동)": (
        f"블로그 {mgrade['blog']:,} · "
        f"카페 {mgrade['cafe']:,} · "
        f"뉴스 {mgrade['news']:,} · "
        f"지식인 {mgrade['kin']:,}"
    ),
    "마케팅 활동 세부 해석 (자동)": mgrade["interpretation_csv"],
    "검색 노출 시그널 (자동)": search_signal,
    # 수기 입력 (CSV에 빈 컬럼 — 영업 활동에서 직접 채움)
    "관심고객수 (수기)": "",
    "리뷰수 (수기)": "",
    "상호 (수기)": "",
    "대표 (수기)": "",
    "이메일 (수기)": "",
    "전화 (수기)": "",
    "마케팅 분석 추가메모 (수기)": "",
}


# ── Step 4: CSV 저장 (누적) ──
print("💾 [4/4] CSV 파일 저장...")
today = datetime.now().strftime("%Y-%m-%d")
csv_path = f"{RESULTS_DIR}/PICK10_{today}.csv"

file_exists = os.path.exists(csv_path)

# 기존 파일이 있으면 컬럼 구조가 같은지 확인 (다르면 백업 후 새 파일 시작)
if file_exists:
    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            existing_fields = reader.fieldnames or []
        if set(existing_fields) != set(collected.keys()):
            backup_path = csv_path.replace(
                ".csv", f"_backup_{datetime.now().strftime('%H%M%S')}.csv"
            )
            os.rename(csv_path, backup_path)
            print(f"   📦 컬럼 구조 변경 감지 → 기존 파일 백업: {backup_path}")
            file_exists = False
    except Exception as e:
        print(f"   ⚠️ 기존 CSV 읽기 실패 (무시하고 진행): {e}")

try:
    with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=collected.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(collected)
    print(f"   ✅ 저장 완료: {csv_path}\n")
except PermissionError:
    print(f"   ❌ CSV 파일 저장 실패 (PermissionError)")
    print(f"      가장 흔한 원인: 엑셀에서 '{csv_path}' 를 열어둔 상태")
    print(f"      해결: 엑셀에서 그 파일 닫고 다시 실행하세요.\n")
    sys.exit(1)


# ── 결과 미리보기 ──
print("─" * 60)
print("📌 수집 결과")
print("─" * 60)
for key, value in collected.items():
    if "(수기)" in key:
        mark = "✏️ 수기" if not value else "✅"
    elif value:
        mark = "✅"
    else:
        mark = "⚠️ 빈칸"
    print(f"  {mark} {key:22s}: {value or '-'}")
print("─" * 60)

print(f"\n👉 다음 액션:")
print(f"   1) {csv_path} 를 엑셀로 열기")
print(f"   2) ✏️ 수기 컬럼 직접 채우기 (스마트스토어 페이지 보고)")
print(f"   3) 다른 키워드로 다시 실행하면 같은 CSV에 행 추가됨")
print()
