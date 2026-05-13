"""
PICK10 v4 - 일 5건 셀러 자동 큐레이션 (1주일 운영 보강)
=================================================================
v3 → v4 변경:
  - 누적 DB: 이전 CSV 모든 브랜드 자동 제외 (매일 다른 셀러 보장)
  - 카테고리당 키워드 1 → 2개 사용 (후보 풀 2배)
  - Selpic 점수 변별력 보강 (단계 세분화)

흐름:
  [1/6]    12개 카테고리 × 2개 키워드 검색 → 후보 풀
  [2/6]    Selpic Fit Score 산정 (100점 만점, 변별력 강화)
           카테고리(50) + 타깃(30) + 검색노출(20)
  [3/6]    중복 제거 + 누적 DB 자동 제외
  [3.5/6]  시장 타깃 크로스체크 (A+B+C 3중 필터)
           A. 영유아 키워드 1개+ 필수 / B. 대기업 컷 / C. 부정 키워드 차단
  [4/6]    70점+ 필터 → 점수순 정렬
  [5/6]    디테일 수집 + 대기업 자동 제외 (카페 50만+) → 5건 채울 때까지
  [6/6]    CSV 저장

실행:
  python collect_5.py

설정:
  코드 상단의 CATEGORY_PRESETS 수정 → 카테고리/키워드 변경
  SCORE_THRESHOLD 수정 → 적합도 임계치 조정
=================================================================
"""

import argparse
import math
import os
import re
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

from supabase_client import (
    get_supabase_client,
    kor_row_to_db,
    TABLE_NAME,
)


sys.stdout.reconfigure(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────
# 설정 (수정 가능)
# ─────────────────────────────────────────────────────────────────
SCORE_THRESHOLD = 70   # Selpic Fit Score 임계치 (기본값)
TARGET_COUNT = 5       # 일일 선별 건수 (기본값)

# ─────────────────────────────────────────────────────────────────
# 커맨드라인 인자 처리 (3가지 수집 모드)
# ─────────────────────────────────────────────────────────────────
# 사용법:
#   1. 자동 (전체):    python collect_5.py --count 5
#   2. 카테고리 지정:  python collect_5.py --count 3 --category "분유·유아식"
#   3. 키워드 입력:    python collect_5.py --count 5 --keywords "산양분유,유기농 기저귀"
#
# (역호환) 첫 위치 인자가 숫자면 --count로 처리:
#   python collect_5.py 3   == python collect_5.py --count 3
# ─────────────────────────────────────────────────────────────────
_parser = argparse.ArgumentParser(description="PICK10 신규 셀러 자동 큐레이션")
_parser.add_argument("--count", type=int, default=5, help="수집 건수 (1~10)")
_parser.add_argument("--category", type=str, default="", help="검색 카테고리 한정")
_parser.add_argument("--keywords", type=str, default="", help="사용자 키워드 (쉼표 구분)")
# 역호환: positional (선택)
_parser.add_argument("count_legacy", nargs="?", type=int, default=None,
                     help="(역호환) 위치 인자 = count")
_args, _unknown = _parser.parse_known_args()

# 역호환: 위치 인자가 있으면 --count로 사용
if _args.count_legacy is not None:
    _args.count = _args.count_legacy

# count 안전 제한 1~10
TARGET_COUNT = max(1, min(10, _args.count))
TARGET_CATEGORY = (_args.category or "").strip()
USER_KEYWORDS = [k.strip() for k in (_args.keywords or "").split(",") if k.strip()]

# 모드 결정
if USER_KEYWORDS:
    COLLECT_MODE = "keywords"
    SCORE_THRESHOLD = 0    # 키워드 모드 — 엄격 카테고리 매칭이 메인 필터, 점수 컷 비활성
elif TARGET_CATEGORY:
    COLLECT_MODE = "category"
    SCORE_THRESHOLD = 30   # 카테고리 모드 — 엄격 매칭으로 보완, 점수 임계치 약간 낮춤
else:
    COLLECT_MODE = "auto"
    SCORE_THRESHOLD = 70   # 자동 모드 — 전체 시장 탐색, 점수 변별력 유지

print(f"   📌 수집 모드: {COLLECT_MODE}")
print(f"   📌 수집 건수: {TARGET_COUNT}건")
if COLLECT_MODE == "category":
    print(f"   📌 카테고리: {TARGET_CATEGORY}")
elif COLLECT_MODE == "keywords":
    print(f"   📌 사용자 키워드: {', '.join(USER_KEYWORDS)}")
print(f"   📌 점수 임계치: {SCORE_THRESHOLD}점+")

# 10개 카테고리 프리셋 (영유아·산모 특화)
# 메인 표의 자동 분류 카테고리와 동일 → 단일 진실의 원천 (market_filter.py)
# 카테고리 지정 모드에서 이 키워드들로 Naver 검색 → 관련 셀러 수집
from market_filter import CATEGORY_SEARCH_KEYWORDS as CATEGORY_PRESETS

# 타깃 키워드 (상품명에 들어있으면 점수 가산)
TARGET_KEYWORDS = [
    "임산부", "산모", "신생아", "영유아", "아기",
    "유아", "베이비", "출산", "임신", "분유"
]

# 시장 타깃 크로스체크 (A+B+C) + 자동 카테고리 분류 — market_filter.py
# 키워드 명단 수정은 market_filter.py 한 곳에서만!
from market_filter import (
    MARKET_FIT_KEYWORDS,
    BIG_COMPANY_BLOCKLIST,
    NEGATIVE_KEYWORDS,
    market_fit_check,
    classify_category,
    expand_keyword,
    generate_space_variants,   # 띄어쓰기 변형 자동 생성
)


RESULTS_DIR = "results"
os.makedirs(RESULTS_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────────────
# .env API 키
# ─────────────────────────────────────────────────────────────────
load_dotenv()
CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
if not CLIENT_ID or not CLIENT_SECRET:
    print("❌ .env 파일에서 API 키를 못 찾았어요.")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────
# 공용 함수
# ─────────────────────────────────────────────────────────────────
def search_naver(category: str, query: str, display: int = 20, start: int = 1, sort: str = "sim") -> dict:
    """네이버 검색 API 일반화 (페이지네이션 + 정렬 옵션 지원)

    sort 옵션:
      - "sim": 관련성순 (기본, 베스트셀러 위주)
      - "date": 최신순 (신생 셀러 발굴)
      - "asc": 가격 낮은순
      - "dsc": 가격 높은순
    """
    api_url = f"https://openapi.naver.com/v1/search/{category}.json"
    headers = {
        "X-Naver-Client-Id": CLIENT_ID,
        "X-Naver-Client-Secret": CLIENT_SECRET,
    }
    params = {"query": query, "display": display, "sort": sort, "start": start}
    try:
        resp = requests.get(api_url, headers=headers, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return {"total": 0, "items": []}


def search_shop(query: str, display: int = 20, start: int = 1, sort: str = "sim") -> list:
    return search_naver("shop", query, display, start, sort).get("items", [])


def mine_brands_from_blog(keyword: str, max_brands: int = 15) -> list:
    """Naver 블로그에서 추천/후기 글 → Smart Store URL 추출 → mallName 식별 (속도 최적화)"""
    found_store_ids = set()
    suffixes = ["추천", "후기"]   # 4 → 2 (속도 우선)
    EXCLUDE = {"main", "search", "category", "popup"}

    for suffix in suffixes:
        query = f"{keyword} {suffix}"
        result = search_naver("blog", query, display=20)   # 30 → 20
        for item in result.get("items", []):
            text = (item.get("title", "") + " " + item.get("description", ""))
            text = clean_html_tags(text)
            for store_id in re.findall(r"smartstore\.naver\.com/([a-zA-Z0-9_\-]+)", text):
                if store_id and store_id not in EXCLUDE:
                    found_store_ids.add(store_id)
                    if len(found_store_ids) >= max_brands * 2:
                        break
            if len(found_store_ids) >= max_brands * 2:
                break
        time.sleep(0.05)

    brand_candidates = []
    for store_id in list(found_store_ids)[:max_brands]:
        items = search_shop(store_id, display=3)   # 5 → 3
        for item in items:
            if (item.get("link", "").find(f"smartstore.naver.com/{store_id}") >= 0
                or item.get("link", "").find(f"/{store_id}") >= 0):
                brand_candidates.append(item)
                break
        time.sleep(0.05)

    return brand_candidates


def clean_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def load_collected_brands() -> set:
    """Supabase sellers 테이블에서 이미 수집된 브랜드명 모음.
    누적 DB 역할 — 이미 영업 시도된 셀러는 다음 실행에서 자동 제외.

    ⚠️ strip() 필수 — DB와 candidates의 brand_name 공백 차이로
       중복 제외 실패하던 버그 수정 (어제 수집한 게 오늘 또 INSERT 되는 문제)
    """
    sb = get_supabase_client()
    if not sb:
        return set()
    try:
        result = sb.table(TABLE_NAME).select("brand_name").execute()
        return {
            row["brand_name"].strip()
            for row in result.data
            if row.get("brand_name") and row["brand_name"].strip()
        }
    except Exception:
        return set()


def build_real_product_url(store_url: str, product_id: str) -> str:
    """진짜 storeId + productId 조합 → 클릭 시 바로 열리는 상품 URL
    예) ('https://smartstore.naver.com/drchoice', '11665105278')
        → 'https://smartstore.naver.com/drchoice/products/11665105278'
    """
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
    """주력 상품명에서 가장 의미있는 상품 키워드 추출

    전략:
      1. [의료기기] / (15g) 같은 부가정보 제거
      2. 토큰화
      3. SKIP_MODIFIERS 제외 (사이즈/시장/일반 수식어)
      4. 남은 단어 중 가장 긴(고유한) 것 선택

    예: "빅사이즈 임산부 원피스 반팔 베이글주름원피스"
        → 수식어 제외 → ["원피스", "베이글주름원피스"]
        → 가장 긴 단어 → "베이글주름원피스" ✅

    예: "에디슨 유아 젓가락 셀프케어 세트"
        → 수식어 제외 → ["에디슨", "젓가락", "셀프케어"]
        → 가장 긴 단어 → "셀프케어"
    """
    if not title:
        return ""
    text = clean_html_tags(title)
    text = re.sub(r"\[[^\]]*\]", "", text)   # [의료기기] 등
    text = re.sub(r"\([^)]*\)", "", text)    # (300ml) 등
    text = " ".join(text.split())            # 공백 정리
    words = text.split()
    if not words:
        return ""

    # 스킵할 수식어 (제품 식별에 도움 안 되는 단어)
    SKIP_MODIFIERS = {
        # 사이즈
        "빅사이즈", "스몰", "미디움", "라지", "엑스라지", "프리사이즈",
        "미니", "맥시", "스탠다드", "투웨이",
        # 옷 종류 수식어
        "반팔", "긴팔", "민소매", "오버사이즈",
        # 일반 수식어
        "프리미엄", "신상", "정품", "신형", "구형", "한정", "신규",
        "오리지널", "에디션", "스페셜",
        # 묶음/세트
        "세트", "묶음", "패키지", "콤보",
        # 시장 키워드 (이미 컨텍스트로 활용됨)
        "베이비", "유아", "아기", "신생아", "영아", "영유아",
        "임산부", "산모", "임부", "수유", "출산", "임신", "산후",
        "임산부용", "유아용", "신생아용", "키즈", "주니어",
    }

    # 수식어 제외
    filtered = [w for w in words if w not in SKIP_MODIFIERS]
    if not filtered:
        # 모두 수식어면 첫 단어로 fallback
        return words[0]

    # 가장 긴 (고유한) 단어 선택 — 한국어 상품명은 보통 끝부분이 상품 본체
    filtered.sort(key=len, reverse=True)
    return filtered[0]


def resolve_real_store_url(link: str, max_retries: int = 2) -> tuple:
    """검색 API link → requests redirect 추적 → 진짜 셀러 URL
    반환: (store_url, debug_msg)
    """
    if not link:
        return "", "link 비어있음"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
    }

    EXCLUDE = {"main", "search", "category", "popup"}

    def match_store_url(url: str) -> str:
        """주어진 URL에서 smartstore/brand storeId 추출 시도"""
        if not url:
            return ""
        m = re.match(r"https?://smartstore\.naver\.com/([^/?#]+)", url)
        if m and m.group(1) not in EXCLUDE:
            return f"https://smartstore.naver.com/{m.group(1)}"
        m = re.match(r"https?://brand\.naver\.com/([^/?#]+)", url)
        if m and m.group(1) not in EXCLUDE:
            return f"https://brand.naver.com/{m.group(1)}"
        return ""

    for attempt in range(max_retries):
        try:
            resp = requests.get(
                link,
                headers=headers,
                allow_redirects=True,
                timeout=15,
            )
            final_url = resp.url

            # 1차: redirect 후 최종 URL에서 추출
            result = match_store_url(final_url)
            if result:
                return result, f"OK redirect ({final_url[:50]}...)"

            # 2차: HTML 파싱 fallback
            html = resp.text or ""

            # 2-A) <meta property="og:url" content="...">
            og_match = re.search(
                r'<meta\s+property=["\']og:url["\']\s+content=["\']([^"\']+)["\']',
                html, re.IGNORECASE
            )
            if og_match:
                result = match_store_url(og_match.group(1))
                if result:
                    return result, f"OK og:url"

            # 2-B) <link rel="canonical" href="...">
            canonical_match = re.search(
                r'<link\s+rel=["\']canonical["\']\s+href=["\']([^"\']+)["\']',
                html, re.IGNORECASE
            )
            if canonical_match:
                result = match_store_url(canonical_match.group(1))
                if result:
                    return result, f"OK canonical"

            # 2-C) HTML 본문의 smartstore/brand 링크 모두 살펴보기
            all_smartstore = re.findall(
                r'https?://smartstore\.naver\.com/([a-zA-Z0-9_\-]+)', html
            )
            for sid in all_smartstore:
                if sid not in EXCLUDE:
                    return f"https://smartstore.naver.com/{sid}", f"OK HTML link"

            all_brand = re.findall(
                r'https?://brand\.naver\.com/([a-zA-Z0-9_\-]+)', html
            )
            for bid in all_brand:
                if bid not in EXCLUDE:
                    return f"https://brand.naver.com/{bid}", f"OK brand HTML link"

            return "", f"redirect+HTML 모두 매칭 실패: {final_url[:60]}"

        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
            return "", "timeout (15초 초과)"
        except requests.exceptions.RequestException as e:
            return "", f"요청 오류: {type(e).__name__}"
        except Exception as e:
            return "", f"기타 오류: {type(e).__name__}"

    return "", "재시도 모두 실패"


def fetch_follower_count(store_url: str) -> int:
    """스마트스토어 셀러 페이지에서 관심고객수 자동 추출

    - 성공: 관심고객 수 반환 (int)
    - 실패 (네트워크 오류, 봇 차단, 로그인 redirect, 셀러 비공개 등): 0 반환

    여러 패턴 시도:
      1. "관심고객 12,345명" 형식 텍스트
      2. JSON 안의 followerCount: 12345
      3. data-fan-count 같은 HTML 속성
    """
    if not store_url or "smartstore.naver.com" not in store_url:
        return 0
    # 상품 페이지 URL (/main/products/) 이면 메인 URL로 변환 시도 X (그냥 0 반환)
    if "/main/products/" in store_url:
        return 0

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9",
    }
    try:
        resp = requests.get(store_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return 0
        html = resp.text or ""

        # 패턴 1: "관심고객 12,345" 또는 "관심고객 12,345명"
        m = re.search(r'관심고객[\s"]*?([\d,]{3,})', html)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                pass

        # 패턴 2: JSON에서 followerCount, fanCount, subscriberCount 등
        for pattern in [
            r'"followerCount"\s*:\s*(\d+)',
            r'"fanCount"\s*:\s*(\d+)',
            r'"subscriberCount"\s*:\s*(\d+)',
        ]:
            m = re.search(pattern, html)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    pass

        return 0
    except requests.exceptions.RequestException:
        return 0
    except Exception:
        return 0


def calculate_marketing_grade(brand_name: str, search_keyword: str, category: str = "", follower_count: int = 0) -> dict:
    """3채널 검색 노출량 → 상/중/하 + 규모

    노이즈 제거 핵심 — 브랜드명 + 시장 컨텍스트 조합으로 검색:
      예: "에디슨" 단독 → 토마스 에디슨(과학자) 글 다 잡혀 → 부정확
      예: "에디슨 베이비" 조합 → 그 브랜드의 베이비 시장 활동만 측정 ✅

    검색 쿼리 구성:
      - 메인: "{brand_name} {시장 컨텍스트}"
              시장 컨텍스트는 카테고리에서 자동 추론 (임산부 / 베이비)
      - SNS: "{brand_name} {시장 컨텍스트} 인스타"

    채널 구성 (3채널):
      - 블로그: Naver 블로그 검색 결과 수
      - 카페: Naver 카페 검색 결과 수 (가장 신뢰성 높음, 유기적 입소문)
      - SNS: Naver에서 "{쿼리} 인스타" 멘션 (인스타그램 프록시)
    """
    # 시장 컨텍스트 결정 (카테고리 기반)
    if any(k in category for k in ["임산부", "산모", "출산", "산후"]):
        market_context = "임산부"
    else:
        market_context = "베이비"

    # 검색 쿼리: 브랜드명 + 시장 컨텍스트
    # (search_keyword는 보조 정보로만 사용 — 노이즈 원인이 될 수 있음)
    if brand_name:
        query_main = f"{brand_name} {market_context}"
    else:
        # 브랜드명 없으면 fallback (드문 케이스)
        query_main = f"{search_keyword} {market_context}"

    blog = search_naver("blog", query_main, 1).get("total", 0)
    cafe = search_naver("cafearticle", query_main, 1).get("total", 0)
    # SNS(인스타그램) 노출 — Naver 블로그+카페에서 "인스타" 멘션 합산
    sns_blog = search_naver("blog", f"{query_main} 인스타", 1).get("total", 0)
    sns_cafe = search_naver("cafearticle", f"{query_main} 인스타", 1).get("total", 0)
    sns = sns_blog + sns_cafe

    # 실제 검색에 사용된 쿼리 (DB에 저장용)
    used_query = query_main

    # 점수 계산 — 3채널 가중치 (카페 가장 중요)
    score = (
        math.log10(blog + 1) * 1.5
        + math.log10(cafe + 1) * 2.0
        + math.log10(sns + 1) * 1.5
    )

    # 마케팅 등급 (3채널이라 임계치 조정)
    if score >= 9:
        grade = "상"
    elif score >= 4:
        grade = "중"
    else:
        grade = "하"

    # 마케팅 활동 단계 — 3단계 (대기업 컷 후)
    #   🚀 확장기 — 마케팅 활발, 신규 매출 채널 확장 적기
    #   📈 성장기 — 마케팅 시도 중, 효율 채널 도입 적기
    #   🌱 도입기 — 마케팅 미흡, 기초 컨설팅 필요
    # 대기업은 관심고객 30만+ 만으로 판별 (앞서 합의)
    if follower_count >= 300_000:
        size = "대기업"
        size_note = f"관심고객 {follower_count:,}명 (영업 비효율, 자체 마케팅팀 보유)"
    else:
        # 채널별 강세/약세 분석
        channels = {"블로그": blog, "카페": cafe, "SNS": sns}
        max_channel = max(channels, key=channels.get)
        max_value = channels[max_channel]

        # 단계 결정 (점수 기반)
        if score >= 12:
            size = "확장기"
            # 서술: 강세 채널 + 권장 영업 방향
            size_note = (
                f"{max_channel} 노출 {max_value:,}건 등 마케팅 활발 — "
                f"신규 매출 채널 확장 적기"
            )
        elif score >= 5:
            size = "성장기"
            size_note = (
                f"{max_channel} 중심 노출 형성 중 ({max_value:,}건) — "
                f"효율 채널 도입 적기"
            )
        else:
            size = "도입기"
            # 가장 부족한 정보 표시
            size_note = (
                f"전 채널 노출 미흡 (블{blog:,}/카{cafe:,}/SNS{sns:,}) — "
                f"마케팅 기초 도입 컨설팅 필요"
            )

    return {
        "grade": grade,
        "blog": blog,
        "cafe": cafe,
        "sns": sns,
        "score": round(score, 2),
        "size": size,
        "size_note": size_note,
        "query": used_query,   # 실제 검색에 사용된 쿼리
    }


def calculate_fit_score(item: dict, preset_category: str) -> tuple:
    """Selpic Fit Score 산정 (100점 만점) — v4 변별력 보강

    - 카테고리 (50): 프리셋+카테고리경로일치 50 / 프리셋만 40 / 출산/육아 25 / 영유아 관련 15 / 무관 0
    - 타깃 (30):    상품명에 타깃 키워드 3개+ 30 / 2개 25 / 1개 18 / 0개 0
    - 검색 노출 (20): 1위 20 / 2위 18 / 3위 15 / 4-5위 12 / 6-10위 8 / 11+ 4
    """
    title = clean_html_tags(item.get("title", ""))
    item_cat = " > ".join(
        filter(None, [item.get(f"category{i}", "") for i in range(1, 5)])
    )

    score = 0
    breakdown = []

    # 1. 카테고리 일치도 (50점) — v4: 단계 세분화
    # 카테고리 경로에 영유아·산모 키워드 명시 여부도 평가
    cat_keywords_in_path = any(
        kw in item_cat for kw in ["영유아", "유아", "신생아", "임산부", "산모", "베이비", "출산"]
    )
    if preset_category and cat_keywords_in_path:
        score += 50
        breakdown.append("카테고리 50/50 (프리셋+경로 일치)")
    elif preset_category:
        score += 40
        breakdown.append("카테고리 40/50 (프리셋만)")
    elif "출산/육아" in item_cat:
        score += 25
        breakdown.append("카테고리 25/50 (출산/육아)")
    elif cat_keywords_in_path:
        score += 15
        breakdown.append("카테고리 15/50 (영유아 관련)")
    else:
        breakdown.append("카테고리 0/50")

    # 2. 타깃 일치도 (30점) — v4: 4단계 세분화
    target_matches = [k for k in TARGET_KEYWORDS if k in title]
    if len(target_matches) >= 3:
        score += 30
        breakdown.append(f"타깃 30/30 ({','.join(target_matches[:3])})")
    elif len(target_matches) == 2:
        score += 25
        breakdown.append(f"타깃 25/30 ({','.join(target_matches)})")
    elif len(target_matches) == 1:
        score += 18
        breakdown.append(f"타깃 18/30 ({target_matches[0]})")
    else:
        breakdown.append("타깃 0/30")

    # 3. 검색 노출 (20점) — v4: 6단계 세분화
    rank = item.get("_rank", 99)
    if rank == 1:
        score += 20
        breakdown.append("노출 20/20 (1위)")
    elif rank == 2:
        score += 18
        breakdown.append("노출 18/20 (2위)")
    elif rank == 3:
        score += 15
        breakdown.append("노출 15/20 (3위)")
    elif rank <= 5:
        score += 12
        breakdown.append(f"노출 12/20 ({rank}위)")
    elif rank <= 10:
        score += 8
        breakdown.append(f"노출 8/20 ({rank}위)")
    else:
        score += 4
        breakdown.append(f"노출 4/20 ({rank}위)")

    return score, breakdown


# ─────────────────────────────────────────────────────────────────
# 메인 흐름 — 모드별 검색 분기 (auto / category / keywords)
# ─────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"  📦 PICK10 - 신규 셀러 자동 큐레이션 ({COLLECT_MODE} 모드)")
print(f"  임계치 {SCORE_THRESHOLD}점 · 목표 {TARGET_COUNT}건")
print("=" * 60 + "\n")


# ── [1/6] 모드별 검색 → 후보 풀 ──
candidates = []

if COLLECT_MODE == "keywords":
    # 모드 3: 사용자 키워드 직접 입력 — 동의어 자동 확장 + 페이지네이션
    # 영유아/임산부 동의어 자동 추가 (예: "아기 로션" → "베이비 로션", "신생아 로션" 등)
    expanded_keywords = []
    for kw in USER_KEYWORDS:
        expansions = expand_keyword(kw)
        expanded_keywords.extend(expansions)
        if len(expansions) > 1:
            print(f"   🔄 '{kw}' → {len(expansions)}개로 확장: {', '.join(expansions)}")

    # 중복 제거
    expanded_keywords = list(dict.fromkeys(expanded_keywords))
    print(f"\n🔍 [1/6] 사용자 키워드 {len(USER_KEYWORDS)}개 → 확장 {len(expanded_keywords)}개로 검색...")

    for keyword in expanded_keywords:
        kw_cat = classify_category(keyword)
        if kw_cat == "기타":
            kw_cat = keyword

        keyword_total = 0
        # Sort 다양화 (sim + date) + 페이지 3개 (1~150위) — 속도 vs 풀 균형
        for sort_method in ["sim", "date"]:
            for start_offset in [1, 51, 101]:   # 6 → 3 페이지 (절반)
                items = search_shop(keyword, display=50, start=start_offset, sort=sort_method)
                if not items:
                    break
                ss_items = [
                    it for it in items
                    if "smartstore.naver.com" in it.get("link", "")
                ]
                for rank, item in enumerate(ss_items, start_offset):
                    item["_keyword"] = keyword
                    item["_category_preset"] = kw_cat
                    item["_rank"] = rank
                    candidates.append(item)
                keyword_total += len(ss_items)
                time.sleep(0.05)   # 0.1 → 0.05 (대기 시간 절반)
        print(f"   ✓ '{keyword:18s}' → 스마트스토어 {keyword_total}건  (카테고리: {kw_cat})")

    # 블로그 마이닝 — 추천/후기 글에서 Smart Store 셀러 추가 발굴
    print(f"\n🔎 [1.5/6] 블로그 마이닝 — 추천/후기 글에서 셀러 추가 발굴...")
    for keyword in USER_KEYWORDS:   # 원본 키워드만 (확장 X — API 부담)
        kw_cat = classify_category(keyword)
        if kw_cat == "기타":
            kw_cat = keyword
        blog_brands = mine_brands_from_blog(keyword, max_brands=30)
        for item in blog_brands:
            item["_keyword"] = f"{keyword} (블로그)"
            item["_category_preset"] = kw_cat
            item["_rank"] = 99   # 블로그 발굴은 별도 랭크
            candidates.append(item)
        print(f"   ✓ '{keyword}' 블로그 발굴 → {len(blog_brands)}건")

elif COLLECT_MODE == "category":
    # 모드 2: 단일 카테고리 한정
    if TARGET_CATEGORY not in CATEGORY_PRESETS:
        print(f"   ❌ 알 수 없는 카테고리: '{TARGET_CATEGORY}'")
        print(f"      허용 카테고리: {', '.join(CATEGORY_PRESETS.keys())}")
        sys.exit(1)
    keywords = CATEGORY_PRESETS[TARGET_CATEGORY]
    print(f"🔍 [1/6] '{TARGET_CATEGORY}' 카테고리 키워드 {len(keywords)}개로 검색 (sort 다양화)...")
    for keyword in keywords:
        keyword_total = 0
        # Sort 다양화 + 페이지 3개 (속도 최적화)
        for sort_method in ["sim", "date"]:
            for start_offset in [1, 51, 101]:   # 6 → 3 페이지
                items = search_shop(keyword, display=50, start=start_offset, sort=sort_method)
                if not items:
                    break
                ss_items = [
                    it for it in items
                    if "smartstore.naver.com" in it.get("link", "")
                ]
                for rank, item in enumerate(ss_items, start_offset):
                    item["_keyword"] = keyword
                    item["_category_preset"] = TARGET_CATEGORY
                    item["_rank"] = rank
                    candidates.append(item)
                keyword_total += len(ss_items)
                time.sleep(0.05)
        print(f"   ✓ '{keyword:18s}' → 스마트스토어 {keyword_total}건")

    # 카테고리 모드도 블로그 마이닝 (단, 첫 키워드만 — 너무 많아질 수 있음)
    if keywords:
        print(f"\n🔎 [1.5/6] 블로그 마이닝 (대표 키워드 1개)...")
        blog_brands = mine_brands_from_blog(keywords[0], max_brands=20)
        for item in blog_brands:
            item["_keyword"] = f"{keywords[0]} (블로그)"
            item["_category_preset"] = TARGET_CATEGORY
            item["_rank"] = 99
            candidates.append(item)
        print(f"   ✓ '{keywords[0]}' 블로그 발굴 → {len(blog_brands)}건")

else:
    # 모드 1 (기본): 12개 카테고리 전체
    print(f"🔍 [1/6] {len(CATEGORY_PRESETS)}개 카테고리 × 2개 키워드로 검색 → 후보 풀 모음...")
    for cat_name, keywords in CATEGORY_PRESETS.items():
        cat_total = 0
        for keyword in keywords[:2]:
            items = search_shop(keyword, display=10)
            ss_items = [
                it for it in items
                if "smartstore.naver.com" in it.get("link", "")
            ]
            for rank, item in enumerate(ss_items, 1):
                item["_keyword"] = keyword
                item["_category_preset"] = cat_name
                item["_rank"] = rank
                candidates.append(item)
            cat_total += len(ss_items)
            time.sleep(0.15)
        print(f"   ✓ {cat_name:18s} → 키워드 {len(keywords[:2])}개 → 스마트스토어 {cat_total}건")

print(f"\n   ✅ 총 후보 풀: {len(candidates)}건\n")


# ── [2/6] Selpic Fit Score 산정 ──
print(f"📊 [2/6] Selpic Fit Score 산정 (각 후보 100점 만점)...")
for c in candidates:
    score, breakdown = calculate_fit_score(c, c["_category_preset"])
    c["_score"] = score
    c["_breakdown"] = breakdown
print(f"   ✅ {len(candidates)}건 점수 산정 완료\n")


# ── [3/6] 중복 제거 + 누적 DB 자동 제외 ──
print(f"🔁 [3/6] 중복 제거 + 이전 수집 브랜드 자동 제외...")

# 이전 CSV들에서 이미 수집한 브랜드 로드
already_collected = load_collected_brands()
print(f"   ℹ️ 이전에 수집한 브랜드: {len(already_collected)}건 (자동 제외 대상)")

seen_brands = set()
unique_candidates = []
already_collected_skipped = 0
for c in candidates:
    brand = c.get("mallName", "").strip()
    if not brand:
        continue
    # 이번 실행 내 중복
    if brand in seen_brands:
        continue
    # 이전 실행에서 이미 수집됨
    if brand in already_collected:
        already_collected_skipped += 1
        continue
    seen_brands.add(brand)
    unique_candidates.append(c)

dup_removed = len(candidates) - len(unique_candidates) - already_collected_skipped
print(f"   ✅ {len(candidates)}건 → {len(unique_candidates)}건")
print(f"      (이번 실행 중복 {dup_removed}건 + 이전 수집 {already_collected_skipped}건 제외)\n")


# ── [3.5/6] 시장 타깃 크로스체크 (B+C 필터, A는 keyword/category 모드에서 건너뜀) ──
# 자동 모드: A+B+C 모두 적용 (시장 좁히기용)
# 키워드/카테고리 모드: B+C만 적용 (사용자가 이미 시장 의도 명시)
#   → "튼살크림" 검색은 이미 임산부 시장이라 A 영유아 키워드 강제 불필요
if COLLECT_MODE in ("keywords", "category"):
    print(f"🎯 [3.5/6] 시장 타깃 크로스체크 (B 대기업 컷 / C 다른시장 차단 — A 건너뜀: 사용자 의도 명확)...")
else:
    print(f"🎯 [3.5/6] 시장 타깃 크로스체크 (A 영유아 필수 / B 대기업 컷 / C 다른시장 차단)...")
fit_candidates = []
fail_log = {"a": [], "b": [], "c": []}

for c in unique_candidates:
    brand = c.get("mallName", "").strip()
    title = clean_html_tags(c.get("title", ""))
    result, reason = market_fit_check(brand, title)

    # 키워드/카테고리 모드: A 탈락은 무시 (시장 의도 이미 명확)
    if result == "a" and COLLECT_MODE in ("keywords", "category"):
        fit_candidates.append(c)   # A 통과로 처리
    elif result == "ok":
        fit_candidates.append(c)
    else:
        fail_log[result].append(f"{brand} ({reason})")

print(f"   ✅ 시장 타깃 통과: {len(fit_candidates)}건")
print(f"      ❌ A 탈락 (영유아 시장 X): {len(fail_log['a'])}건")
print(f"      ❌ B 탈락 (대기업): {len(fail_log['b'])}건")
print(f"      ❌ C 탈락 (다른 시장): {len(fail_log['c'])}건")

# 디버그 출력 (각 카테고리 최대 3개씩만)
for tag, label in [("a", "A 영유아 시장 X"), ("b", "B 대기업"), ("c", "C 다른 시장")]:
    if fail_log[tag]:
        print(f"\n   [{label} 샘플]")
        for item in fail_log[tag][:3]:
            print(f"      - {item}")
        if len(fail_log[tag]) > 3:
            print(f"      ... 그 외 {len(fail_log[tag]) - 3}건")
print()

unique_candidates = fit_candidates


# ── [4/6] 임계치 + 정렬 (5건 선별은 [5/6]에서 대기업 컷 후) ──
print(f"🎯 [4/6] {SCORE_THRESHOLD}점+ 필터 → 점수순 정렬...")
passed = [c for c in unique_candidates if c["_score"] >= SCORE_THRESHOLD]
passed.sort(key=lambda x: x["_score"], reverse=True)

print(f"   ✓ {SCORE_THRESHOLD}점+ 통과: {len(passed)}건 (대기업 컷 후 상위 {TARGET_COUNT}건 선별)")
if len(passed) == 0:
    print(f"\n   ❌ 통과 셀러 없음. 임계치 조정 또는 카테고리 변경 필요")
    sys.exit(0)
print()


# ── [5/6] 디테일 수집 + 대기업 자동 제외 (카페 50만+ 컷) ──
# 기존 selected는 점수순 5건만 잡았지만, 대기업 제외하면 5건 부족할 수 있어서
# passed (70점+ 통과 전체)에서 시작해서 5건 채울 때까지 진행
print(f"🔬 [5/6] 디테일 수집 + 대기업 자동 제외 (카페 50만+ 자동 컷)...")

results = []
big_company_skipped = []   # 대기업으로 제외된 셀러 기록
processed_brands = set()   # 같은 브랜드 중복 처리 방지

for sel in passed:
    if len(results) >= TARGET_COUNT:
        break   # 5건 다 채움

    brand_name = sel.get("mallName", "").strip()
    if not brand_name or brand_name in processed_brands:
        continue
    processed_brands.add(brand_name)

    print(f"\n   ▶ {brand_name}  (Selpic 점수 {sel['_score']})")
    print(f"        근거: {' · '.join(sel['_breakdown'])}")

    # 5-1) 브랜드명 재검색 → 주력 상품 식별 (display 20개로 늘려 종합몰 판별에도 활용)
    brand_items = search_shop(brand_name, display=20)
    own_items = [
        it for it in brand_items
        if it.get("mallName", "").strip() == brand_name
        and "smartstore.naver.com" in it.get("link", "")
    ]

    # 5-1.3) 종합몰 자동 제외 — 모드별 다른 기준
    # 자동 모드:    Naver 카테고리1 영유아 50% 미만 OR 카테고리 8종+
    # 키워드/카테고리: 상품 제목에 영유아/산모 타깃 키워드 30% 미만 포함
    #   (Naver 카테고리1보다 정확 — 산모 전문 브랜드도 카테고리는 다양할 수 있어서)
    own_brand_items = [
        it for it in brand_items
        if it.get("mallName", "").strip() == brand_name
    ]
    if len(own_brand_items) >= 5:
        is_general_mall = False

        if COLLECT_MODE == "auto":
            # 자동 모드: Naver 카테고리1 기반
            cat1_counts = {}
            for it in own_brand_items:
                cat1 = it.get("category1", "").strip()
                if cat1:
                    cat1_counts[cat1] = cat1_counts.get(cat1, 0) + 1

            if cat1_counts:
                total_items = sum(cat1_counts.values())
                BABY_CATS = {"출산/육아"}
                baby_count = sum(cnt for cat, cnt in cat1_counts.items() if cat in BABY_CATS)
                baby_ratio = baby_count / total_items
                diversity = len(cat1_counts)

                if baby_ratio < 0.5 or diversity >= 8:
                    is_general_mall = True
                    print(f"        🚫 종합몰 자동 제외 "
                          f"(영유아 {baby_ratio:.0%}, 카테고리 {diversity}종) → 다음 후보로")
        else:
            # 키워드/카테고리 모드: 타깃 키워드 포함율 기반
            # 상품 제목에 영유아/산모 키워드(MARKET_FIT_KEYWORDS) 30% 이상 포함되어야
            target_count = 0
            for it in own_brand_items:
                title = clean_html_tags(it.get("title", "")).lower()
                if any(kw.lower() in title for kw in MARKET_FIT_KEYWORDS):
                    target_count += 1
            target_ratio = target_count / len(own_brand_items)

            if target_ratio < 0.3:
                is_general_mall = True
                print(f"        🚫 종합몰 자동 제외 "
                      f"(타깃 키워드 포함율 {target_ratio:.0%}, "
                      f"{target_count}/{len(own_brand_items)}개) → 다음 후보로")

        if is_general_mall:
            time.sleep(0.3)
            continue
    flagship = own_items[0] if own_items else sel
    flagship_title = clean_html_tags(flagship.get("title", ""))
    flagship_url = flagship.get("link", "")
    flagship_category = " > ".join(
        filter(None, [flagship.get(f"category{i}", "") for i in range(1, 5)])
    )

    # 5-1.5) 주력상품 발견 후 B+C 재검사 (대기업/부정 키워드 누락 방지)
    # 자동 모드: A+B+C 모두 적용
    # 키워드/카테고리 모드: B+C만 적용 (A 건너뜀 — 사용자 의도 명확)
    flagship_check, flagship_reason = market_fit_check(brand_name, flagship_title)
    # 키워드/카테고리 모드에서 A 탈락은 무시
    if flagship_check == "a" and COLLECT_MODE in ("keywords", "category"):
        pass   # 통과 처리
    elif flagship_check != "ok":
        print(f"        🚫 주력상품 재검사 탈락: {flagship_reason} → 다음 후보로")
        time.sleep(0.3)
        continue   # 5건에 안 포함

    # 5-1.6) 키워드/카테고리 모드: 브랜드 top 10 상품 중 1개라도 검색 키워드 부분 매칭 필수
    # 이유: 키워드 모드도 무관 브랜드(바퀴벌레약 등) 차단 필요
    # 매칭 방식: 부분 키워드 매칭 + 시장 컨텍스트 + 띄어쓰기 변형
    # ⭐ 2026-05-13 강화 (3차):
    #   - top 5 → top 10 (메인 라인 범위 확대)
    #   - 띄어쓰기 변형 자동 생성 ("튼살크림" ↔ "튼살 크림" 양쪽 매칭)
    #   - 확장 키워드 토큰화 + MARKET_FIT_KEYWORDS 통합
    if COLLECT_MODE in ("keywords", "category"):
        if COLLECT_MODE == "keywords":
            # 사용자 입력 + 확장 동의어 + 띄어쓰기 변형 + 토큰화
            search_kw_pool = set()
            for kw in USER_KEYWORDS:
                # 원본 + 띄어쓰기 변형 ("튼살크림" → "튼살크림", "튼살 크림", ...)
                for variant in generate_space_variants(kw):
                    search_kw_pool.add(variant.lower())
                # 원본 토큰 (예: "임산부 크림" → "임산부", "크림")
                for token in kw.split():
                    if len(token) >= 2:
                        search_kw_pool.add(token.lower())
                # 확장 동의어 + 확장 동의어의 띄어쓰기 변형 + 토큰
                for ex in expand_keyword(kw):
                    for variant in generate_space_variants(ex):
                        search_kw_pool.add(variant.lower())
                    for token in ex.split():
                        if len(token) >= 2:
                            search_kw_pool.add(token.lower())
        else:
            cat_kws = CATEGORY_PRESETS.get(TARGET_CATEGORY, [])
            search_kw_pool = set()
            for kw in cat_kws:
                # 카테고리 키워드도 띄어쓰기 변형 적용
                for variant in generate_space_variants(kw):
                    search_kw_pool.add(variant.lower())
                for token in kw.split():
                    if len(token) >= 2:
                        search_kw_pool.add(token.lower())

        # ⭐ 시장 컨텍스트 키워드 통합 (MARKET_FIT_KEYWORDS — 자동 확장된 세트)
        # 이유: 사용자가 "튼살크림" 검색했을 때, 그 브랜드가 "임산부용 바디 로션",
        #       "산모 마사지오일" 같은 변형 상품도 메인 라인에 있을 수 있음.
        # 안전: 다른 시장 키워드는 MARKET_FIT_KEYWORDS에 없으므로 false positive 낮음
        for mkt_kw in MARKET_FIT_KEYWORDS:
            if len(mkt_kw) >= 2:
                search_kw_pool.add(mkt_kw.lower())

        # 브랜드 top 10 상품(메인 라인) 중 1개라도 검색 키워드 부분 매칭
        # Top 10 → 메인 라인 검사 범위 확대 (top 5는 너무 좁아 미달 발생)
        # 비교 시 양쪽 정규화 (공백 차이 무시) — 더 robust한 매칭
        has_matching_product = False
        matched_product = ""
        matched_keyword = ""
        for item in brand_items[:10]:   # top 5 → top 10 확대
            title_raw = clean_html_tags(item.get("title", "")).lower()
            title_no_space = title_raw.replace(" ", "")
            for kw in search_kw_pool:
                kw_no_space = kw.replace(" ", "")
                # 양쪽 정규화 비교 — "튼살크림" vs "튼살 크림" 양쪽 매칭
                if kw in title_raw or (kw_no_space and kw_no_space in title_no_space):
                    has_matching_product = True
                    matched_product = item.get("title", "")[:40]
                    matched_keyword = kw
                    break
            if has_matching_product:
                break

        if not has_matching_product:
            print(f"        🚫 메인 라인(top 10)에 검색 키워드/시장 키워드 매칭 X → 다음 후보로")
            time.sleep(0.3)
            continue
        else:
            print(f"        ✓ 메인 라인 매칭: '{matched_keyword}' in {matched_product}")

    flagship_price = int(flagship.get("lprice", 0))

    # 5-2) 진짜 스토어 URL — 3중 fallback 전략 (검색 페이지로는 절대 안 보냄)
    # ⚠️ 영구 보장 패턴 (학습된 규칙):
    #   1순위: redirect 추적 성공 → 셀러 메인 URL (https://smartstore.naver.com/{storeId})
    #   2순위: API 원본 link (상품 상세 페이지) — 진짜 스마트스토어 페이지, 셀러명 클릭 가능
    #   3순위 (최후): 검색 페이지 — 절대 안 씀. 영업 흐름 끊김.
    # 핵심: API 원본 link가 있으면 무조건 그걸 보존. 검색 fallback X.
    store_url, store_debug = resolve_real_store_url(flagship_url)
    if store_url:
        print(f"        스토어 URL: {store_url} (셀러 메인)")
    elif flagship_url and "smartstore.naver.com" in flagship_url:
        # API 원본 link 보존 (상품 페이지지만 진짜 스마트스토어)
        store_url = flagship_url
        print(f"        스토어 URL: 상품 페이지 fallback (셀러명 클릭으로 메인 이동)")
    elif flagship_url and "brand.naver.com" in flagship_url:
        store_url = flagship_url
        print(f"        스토어 URL: brand.naver.com 상품 페이지 fallback")
    else:
        # 마지막 안전망 — 여기 도달하면 API link도 비어있는 비정상 케이스
        store_url = (
            f"https://search.shopping.naver.com/search/all?"
            f"query={urllib.parse.quote(brand_name)}"
        )
        print(f"        스토어 URL: 비정상 — 검색 페이지 최후 fallback ({store_debug})")

    # 5-3) 주력 상품명에서 검색용 핵심 키워드 추출
    product_keyword = extract_product_keyword(flagship_title)

    # 5-3.5) 스마트스토어 관심고객수 자동 수집 (대기업 판별용)
    # 실패 시 0 → 대기업 판별 skip (명단 매칭에 의존)
    follower_count = fetch_follower_count(store_url)
    if follower_count > 0:
        print(f"        관심고객수: {follower_count:,}명")
    else:
        print(f"        관심고객수: 자동 수집 실패 (페이지 차단 또는 비공개)")

    # 5-4) 마케팅 등급 + 활동 단계
    # 브랜드명 + 시장 컨텍스트로 검색 → "에디슨" 같은 브랜드명 노이즈 제거
    # 자동 카테고리 분류 먼저 (마케팅 검색의 시장 컨텍스트 결정에 사용)
    auto_cat = classify_category(flagship_title)
    mgrade = calculate_marketing_grade(brand_name, product_keyword, auto_cat, follower_count)
    print(f"        주력: {flagship_title[:50]}")
    print(f"        카테고리: {auto_cat}  (자동 분류)")
    print(f"        마케팅 검색 쿼리: '{brand_name} {('임산부' if any(k in auto_cat for k in ['임산부','산모','출산','산후']) else '베이비')}'")
    print(f"        마케팅: {mgrade['grade']} (블{mgrade['blog']}/카{mgrade['cafe']}/SNS{mgrade['sns']})")
    print(f"        마케팅 활동 단계: {mgrade['size']} — {mgrade['size_note']}")

    # 5-5) 대기업 자동 제외 (관심고객수 30만+)
    if mgrade["size"] == "대기업":
        print(f"        🚫 대기업 자동 제외 (관심고객 {follower_count:,}명) → 다음 후보로")
        big_company_skipped.append({
            "브랜드명": brand_name,
            "관심고객수": follower_count,
            "Selpic 점수": sel["_score"],
        })
        time.sleep(0.3)
        continue   # 5건에 안 포함

    # 5-6) 정상 결과 추가
    results.append({
        "수집일":               datetime.now().strftime("%Y-%m-%d"),
        "Selpic 점수":          sel["_score"],
        "발견 카테고리":        sel["_category_preset"],
        "발견 키워드":          sel["_keyword"],
        "수집 모드":            COLLECT_MODE,   # auto / category / keywords
        "브랜드명":             brand_name,
        "스마트스토어 주소":    store_url,
        "주력상품명":           flagship_title,
        "상품 카테고리":        flagship_category,
        "가격":                 f"{flagship_price:,}원" if flagship_price else "",
        "점수 근거":            " · ".join(sel["_breakdown"]),
        "마케팅 검색 키워드 (자동)": mgrade.get("query", product_keyword),
        "마케팅 등급 (자동)":   mgrade["grade"],
        "마케팅 점수 (자동)":   mgrade["score"],
        "마케팅 채널별 노출 (자동)": (
            f"블로그 {mgrade['blog']:,} · "
            f"카페 {mgrade['cafe']:,} · "
            f"SNS {mgrade['sns']:,}"
        ),
        "마케팅 활동 단계 (자동)": f"{mgrade['size']} — {mgrade['size_note']}",
        "관심고객수 (자동)":    follower_count,   # 스마트스토어 자동 수집 (0이면 실패)
        # 수기 입력 컬럼
        "관심고객수 (수기)":         "",
        "리뷰수 (수기)":             "",
        "상호 (수기)":               "",
        "대표 (수기)":               "",
        "이메일 (수기)":             "",
        "전화 (수기)":               "",
        "마케팅 분석 메모 (수기)":   "",
    })
    time.sleep(0.3)

# 최종 요약
print()
print(f"   ✅ 최종 선별: {len(results)}건")
if big_company_skipped:
    print(f"   🚫 대기업 자동 제외: {len(big_company_skipped)}건")
    for bc in big_company_skipped:
        print(f"      - {bc['브랜드명']} (관심고객 {bc['관심고객수']:,}명, 점수 {bc['Selpic 점수']})")
if len(results) < TARGET_COUNT:
    print(f"   ⚠️ 목표 {TARGET_COUNT}건 미달. 카테고리 추가 또는 임계치 조정 검토")
print()


# ── [6/6] Supabase 저장 ──
print(f"💾 [6/6] Supabase에 저장...")

sb = get_supabase_client()
if not sb:
    print("   ❌ Supabase 연결 실패. .env 확인.")
    sys.exit(1)

saved_count = 0
for r in results:
    db_row = kor_row_to_db(r)
    try:
        sb.table(TABLE_NAME).upsert(db_row, on_conflict="brand_name").execute()
        saved_count += 1
    except Exception as e:
        print(f"   ⚠️ {r.get('브랜드명')} 저장 실패: {str(e)[:80]}")

print(f"   ✅ 저장 완료: {saved_count}/{len(results)}건 (Supabase sellers 테이블)\n")


# ── 결과 미리보기 ──
print("=" * 60)
print(f"📌 오늘의 {len(results)}건 큐레이션 결과")
print("=" * 60)
for idx, r in enumerate(results, 1):
    score = r["Selpic 점수"]
    mark = "🔥" if score >= 95 else "✨" if score >= 80 else "⭐"
    print(f"\n[{idx}] {mark} {r['브랜드명']}  Selpic {score}점 · 마케팅 {r['마케팅 등급 (자동)']}")
    print(f"    카테고리: {r['발견 카테고리']}  /  키워드: {r['발견 키워드']}")
    print(f"    주력: {r['주력상품명'][:60]}")
    if r["스마트스토어 주소"]:
        print(f"    URL : {r['스마트스토어 주소']}")
print()
print("=" * 60)
print(f"\n👉 다음 액션:")
print(f"   1) 대시보드(streamlit run dashboard.py)에서 새 셀러 확인")
print(f"   2) ✏️ 수기 컬럼 (영업상태·이메일·연락처·관심고객수·메모) 채우기")
print(f"   3) Selpic 점수 ↑ 또는 마케팅 등급 '상' 셀러부터 영업 우선순위")
print()
