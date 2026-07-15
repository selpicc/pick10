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
# ⭐ 2026-05-13 정책 통일: 3가지 모드 모두 점수 컷 0점 (전부 통과)
#    점수 컷보다 종합몰/시장 필터/매칭으로 정밀 컷
if USER_KEYWORDS:
    COLLECT_MODE = "keywords"
    SCORE_THRESHOLD = 0
elif TARGET_CATEGORY:
    COLLECT_MODE = "category"
    SCORE_THRESHOLD = 0
else:
    COLLECT_MODE = "auto"
    SCORE_THRESHOLD = 0

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
    GENERIC_TOKENS,            # 일반어 (단독 매칭 제외용)
    CATEGORY_KEYWORDS,         # 카테고리별 핵심 키워드 (extract_product_keyword 개선용)
    market_fit_check,
    classify_category,
    expand_keyword,
    expand_keyword_synonym_only,   # 매칭 풀 구성용 (PRODUCT_SYNONYMS 제외)
    generate_space_variants,   # 띄어쓰기 변형 자동 생성
    is_excluded_brand,         # ⭐ 수동 제외 목록 (종합몰 등)
)

# 사업자 정보 자동 수집 (Phase 1+2+3)
# 신규 셀러 수집 시 상호·대표·사업자번호·전화·이메일 자동 추출
from business_info_collector import (
    collect_business_info,
    find_service_business_homepages,   # ⭐ 서비스 업체(웹검색) 발굴
    find_powerlink_businesses,         # ⭐ 서비스 업체(파워링크 광고) 발굴
    find_business_info_from_homepage,  # ⭐ 서비스 업체 홈페이지 연락처 수집
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
    return _mine_brands_from_source(keyword, source="blog", max_brands=max_brands)


def mine_brands_from_cafe(keyword: str, max_brands: int = 15) -> list:
    """Naver 카페에서 추천/후기 글 → Smart Store URL 추출 → mallName 식별

    영유아/임산부 시장 핵심 풀:
      맘카페 입소문이 가장 신뢰성 높은 유기적 추천 소스
      블로그(광고 가능성)보다 더 진짜 사용자 후기
    """
    return _mine_brands_from_source(keyword, source="cafearticle", max_brands=max_brands)


def _mine_brands_from_source(keyword: str, source: str, max_brands: int = 15) -> list:
    """Naver 검색 소스(blog/cafearticle)에서 셀러 발굴 — 통합 헬퍼.

    Args:
        keyword: 검색 키워드 (예: "튼살크림")
        source: "blog" 또는 "cafearticle"
        max_brands: 최대 발굴 브랜드 수

    동작:
      1. "{keyword} 추천", "{keyword} 후기" 검색 (해당 소스)
      2. 글 본문에서 smartstore.naver.com/{storeId} 정규식 추출
      3. 각 storeId로 쇼핑 API 재검색 → 상품 정보 확보
    """
    found_store_ids = set()
    suffixes = ["추천", "후기"]
    EXCLUDE = {"main", "search", "category", "popup"}

    for suffix in suffixes:
        query = f"{keyword} {suffix}"
        result = search_naver(source, query, display=20)
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
        items = search_shop(store_id, display=3)
        for item in items:
            if (item.get("link", "").find(f"smartstore.naver.com/{store_id}") >= 0
                or item.get("link", "").find(f"/{store_id}") >= 0):
                brand_candidates.append(item)
                break
        time.sleep(0.05)

    return brand_candidates


def clean_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


# ─────────────────────────────────────────────────────────────────
# 키워드 매칭 컨텍스트 (5-1.3 종합몰 컷 + 5-1.6 메인 라인 매칭 공용)
# 사용자 키워드 직접 관련만 인정 — MARKET_FIT_KEYWORDS 통합 X
#
# 매칭 전략:
#   1. Substring 매칭: search_kw_pool 안의 키워드가 제목에 포함 → OK
#      (사용자 키워드 + 확장 동의어 + 띄어쓰기 변형, GENERIC 단독 토큰 제외)
#   2. AND 토큰 매칭: 사용자 키워드 분리 토큰 모두 제목에 포함 → OK
#      예: "임산부 로션" → ("임산부", "로션") 둘 다 있으면 매칭
# ─────────────────────────────────────────────────────────────────
def _build_keyword_match_context(
    mode: str,
    user_keywords: list,
    target_category: str,
    category_presets: dict,
) -> tuple:
    """매칭 컨텍스트 빌드 — 한 번만 구성하고 5-1.3, 5-1.6 모두에서 재사용.

    반환:
      (search_kw_pool: set, user_kw_tokens: list[list[str]])

    ⭐ 2026-05-13 정책 (사용자 명시):
      "내가 입력한 키워드가 있는 브랜드만 발굴"
      → 확장 동의어(스트레치마크/임부 크림/산모 크림 등) 매칭 제거
      → 사용자가 직접 입력한 키워드와 그 띄어쓰기 변형만 인정

    search_kw_pool 구성 (substring 매칭):
      - 사용자 키워드 원본 (예: "튼살크림")
      - 띄어쓰기 변형 (예: "튼살 크림")
      - GENERIC 아닌 토큰 (예: "튼살"은 OK / "크림"은 X — 단독 매칭 차단)

    user_kw_tokens 구성 (AND 매칭):
      - 사용자 키워드 모든 변형의 토큰 분리
      - 예: "튼살크림" → ("튼살", "크림") AND 매칭
      - 효과: "튼살 케어 보디 크림" 같은 변형도 매칭
    """
    search_kw_pool = set()
    user_kw_tokens = []
    seen_tokens = set()

    def _process_keyword(kw: str):
        """단일 키워드 처리 — search_kw_pool + user_kw_tokens 동시 채움.

        ⭐ 2026-05-13 강화 (baby/mom 동의어 그룹 통합):
          "신생아 로션" 입력 시 → "아기 로션", "베이비 로션", "유아 로션" 등
          자연스러운 단어 동의어도 매칭 풀에 포함 (사용자 요청).

          단, PRODUCT_SYNONYMS의 상품 동의어는 제외 (사용자 명시 키워드와 의미 차이):
          - "튼살크림" → "스트레치마크" 매칭 X
          - "신생아" ↔ "아기" 매칭 O

        정책 (단어 동의어):
          - keywords 모드: expand_keyword_synonym_only로 baby/mom 그룹 치환
          - category 모드: 카테고리 키워드는 그대로 (이미 명시적 키워드 셋)
        """
        # 1. 매칭 대상 키워드 변형 모음 — 원본 + baby/mom 동의어 치환
        if mode == "keywords":
            keyword_variants_set = set(expand_keyword_synonym_only(kw))
        else:
            keyword_variants_set = {kw}
        # 원본은 항상 포함
        keyword_variants_set.add(kw)

        # 2. Substring 매칭 풀 — 각 변형 + 그 변형의 띄어쓰기 변형
        all_space_variants = set()
        for syn_kw in keyword_variants_set:
            for variant in generate_space_variants(syn_kw):
                search_kw_pool.add(variant.lower())
                all_space_variants.add(variant)

        # 3. 원본 키워드의 토큰만 search_kw_pool에 단독 추가
        #    GENERIC 단독 차단 (예: "신생아", "로션" 단독 매칭 X)
        for token in kw.split():
            tok = token.strip().lower()
            if len(tok) >= 2 and tok not in GENERIC_TOKENS:
                search_kw_pool.add(tok)

        # 4. AND 토큰 매칭용 — 각 동의어 변형의 토큰 페어
        # 예: "신생아 로션" → ("신생아", "로션"), ("아기", "로션"),
        #     ("베이비", "로션"), ("유아", "로션") 등 모두 AND 매칭 후보
        for syn_kw in keyword_variants_set:
            syn_tokens = tuple(
                t.strip().lower() for t in syn_kw.split() if len(t.strip()) >= 2
            )
            if len(syn_tokens) >= 2:
                if syn_tokens not in seen_tokens:
                    user_kw_tokens.append(list(syn_tokens))
                    seen_tokens.add(syn_tokens)
            else:
                # 공백 없는 합성어 ("튼살크림") → 띄어쓰기 변형의 토큰 페어
                for variant in generate_space_variants(syn_kw):
                    if " " not in variant:
                        continue
                    vtokens = tuple(
                        t.strip().lower() for t in variant.split() if len(t.strip()) >= 2
                    )
                    if len(vtokens) >= 2 and vtokens not in seen_tokens:
                        user_kw_tokens.append(list(vtokens))
                        seen_tokens.add(vtokens)

        # 5. ⭐ 2026-06-09: 검색에 쓴 '확장 키워드'(스트레치마크/산모 크림/임산부 크림 등)도
        #    매칭 풀에 '구절 통째로' 추가.
        #    문제: 검색은 expand_keyword(상품 동의어 포함)로 넓게 하면서 매칭은
        #    expand_keyword_synonym_only(상품 동의어 제외)로 좁게 해서, 같은 제품을
        #    다른 이름으로 파는 진짜 브랜드(파더마 등 임산부 브랜드)가 '키워드 관련 0%'로
        #    거절되던 불균형. → 검색=매칭 키워드 일치시킴.
        #    단, 구절을 쪼갠 '단독 토큰'(산모/크림)은 추가 X — 예전 false positive 원인이
        #    단독 generic 토큰이었으므로, 구절 전체(substring)와 AND 토큰만 인정.
        if mode == "keywords":
            for exp_kw in expand_keyword(kw):
                for variant in generate_space_variants(exp_kw):
                    v = variant.lower().strip()
                    if v:
                        search_kw_pool.add(v)
                exp_tokens = tuple(
                    t.strip().lower() for t in exp_kw.split() if len(t.strip()) >= 2
                )
                if len(exp_tokens) >= 2 and exp_tokens not in seen_tokens:
                    user_kw_tokens.append(list(exp_tokens))
                    seen_tokens.add(exp_tokens)

    if mode == "keywords":
        for kw in user_keywords:
            _process_keyword(kw)
    else:   # category 모드
        cat_kws = category_presets.get(target_category, [])
        for kw in cat_kws:
            _process_keyword(kw)

    return search_kw_pool, user_kw_tokens


def _is_keyword_match(title: str, search_kw_pool: set, user_kw_tokens: list) -> tuple:
    """제목이 사용자 키워드/토큰과 매칭되는지 검사.

    반환: (matched: bool, matched_keyword: str)

    매칭 우선순위:
      1. Substring 직접 매칭 (정규화 양쪽 적용 — 공백 차이 무시)
      2. 다중 토큰 AND 매칭 (사용자 키워드 토큰 모두 포함)
    """
    title_raw = title.lower()
    title_no_space = title_raw.replace(" ", "")

    # 1) Substring 직접 매칭
    for kw in search_kw_pool:
        kw_no_space = kw.replace(" ", "")
        if kw in title_raw or (kw_no_space and kw_no_space in title_no_space):
            return True, kw

    # 2) AND 토큰 매칭
    for tokens in user_kw_tokens:
        if all(tok in title_raw or tok in title_no_space for tok in tokens):
            return True, f"[{' + '.join(tokens)}] 토큰분리"

    return False, ""


# ─────────────────────────────────────────────────────────────────
# 한 후보 처리 — 디테일 수집 + 모든 필터 (5-1 ~ 5-6 통합)
# 메인 [5/6] + 확장 라운드 양쪽에서 재사용
# ─────────────────────────────────────────────────────────────────
def _process_one_candidate(
    sel,
    processed_brands,
    results,
    big_company_skipped,
    search_kw_pool_strict,
    user_kw_tokens,
    auto_mode_contexts,
    is_expansion=False,
):
    """한 후보의 디테일 수집 + 모든 필터 통과 시 results.append.

    반환: True if added, False otherwise.
    """
    import urllib.parse as _urlparse
    brand_name = sel.get("mallName", "").strip()
    if not brand_name or brand_name in processed_brands:
        return False
    processed_brands.add(brand_name)

    # ⭐ 2026-06-01: 수동 제외 목록(종합몰 등) 즉시 차단
    if is_excluded_brand(brand_name):
        print(f"        🚫 수동 제외 목록 차단: {brand_name} → 다음 후보로")
        return False

    mark = " [확장]" if is_expansion else ""
    print(f"\n   ▶ {brand_name}{mark}  (Selpic 점수 {sel['_score']})")
    print(f"        근거: {' · '.join(sel['_breakdown'])}")

    # ⭐ 5-1.0) 리셀러/잡동사니 몰 '이름' 휴리스틱 (빠른 컷, 2026-05-30)
    #   '커머스/딜/가성비/잇템/쇼핑/도매/유통/특가' 등은 만물상 리셀러 작명 패턴.
    #   (브랜드형 이름은 안 걸림 — 진짜 브랜드 보호. 너무 많이 걸리면 토큰 빼면 됨)
    RESELLER_NAME_TOKENS = (
        "커머스", "가성비", "잇템", "빅딜", "딜몰", "특가",
        "도매", "유통", "트레이딩", "트레이드", "쇼핑몰", "쇼핑",
        "만물", "종합몰", "마켓플레이스",
        # ⭐ 2026-06-01: '마켓' 작명 만물상 추가 (하나뿐 마켓 등)
        "마켓",
    )
    _bn = brand_name.replace(" ", "")
    if any(tk in _bn for tk in RESELLER_NAME_TOKENS):
        print(f"        🚫 리셀러/잡동사니 몰 이름 패턴 제외: {brand_name} → 다음 후보로")
        time.sleep(0.2)
        return False

    # 5-1) 브랜드 재검색
    # ⭐ 2026-06-01: display 20→100(최대). 셀러 카탈로그를 최대한 넓게 봐야
    #   만물상(SOOAPAPA·뉴몰3·케이유플러스 등 — 타깃 몇 개 + 비타깃 다수)의
    #   '진짜 낮은 타깃 비율'이 드러나 걸러짐. (이름 검색은 타깃 상품 위주로
    #   잡혀 비율이 실제보다 높게 보이는 문제 → 표본을 키워 완화)
    brand_items = search_shop(brand_name, display=100)
    own_items = [
        it for it in brand_items
        if it.get("mallName", "").strip() == brand_name
        and "smartstore.naver.com" in it.get("link", "")
    ]

    # 5-1.3) 종합몰/무관 셀러 자동 제외
    own_brand_items = [
        it for it in brand_items
        if it.get("mallName", "").strip() == brand_name
    ]

    # ⭐ 5-1.3a) 잡동사니 몰 제외 (2026-05-30, 사용자 요청)
    #   확장 검색이 끌어오는 '만물상'(상품 수천개 중 타깃 1개) 차단.
    #   셀러 자기 상품 중 '명확한 영유아/임산부 상품' 비율이 절반 미만이면 제외.
    #   판별: 영유아 키워드(ok) OR 출산/육아 등 카테고리 → 진짜 타깃 상품으로 카운트.
    #   (FOCUS_MIN 숫자 낮추면 관대, 높이면 엄격)
    FOCUS_MIN = 0.5
    CAT_TARGET_TOKENS = (
        "출산", "육아", "유아", "기저귀", "분유", "이유식",
        "수유", "임부", "임산부", "신생아", "베이비", "젖병",
    )
    # ⭐ 2026-06-01: 네이버 카테고리 기준 '명백히 영유아 아님' (종합몰 판별 핵심)
    #   이런 카테고리 상품을 여러 개 팔면 = 만물상 (포커스 영유아 브랜드는 0개).
    #   키워드 추측이 아닌 네이버 공식 카테고리라 안정적.
    CAT_NONBABY_TOKENS = (
        "디지털", "가전", "컴퓨터", "노트북", "휴대폰",
        "가구", "인테리어", "주방", "공구", "철물",
        "자동차", "타이어", "스포츠", "레저", "골프", "등산", "낚시",
        "반려", "펫", "강아지", "고양이",
        "도서", "음반", "악기", "문구", "사무",
        "성인", "주류",
    )
    if len(own_brand_items) >= 3:
        target_hits = 0
        nontarget_hits = 0   # 명백히 다른 시장(c) — 만물상 강한 신호
        nonbaby_cat_hits = 0   # ⭐ 네이버 카테고리상 명백히 영유아 아님
        for it in own_brand_items:
            title = clean_html_tags(it.get("title", ""))
            fit, _ = market_fit_check(brand_name, title)
            cat_path = " ".join(
                str(it.get(f"category{i}", "")) for i in range(1, 5)
            )
            is_target = fit == "ok" or any(tk in cat_path for tk in CAT_TARGET_TOKENS)
            if is_target:
                target_hits += 1
            else:
                if fit == "c":   # 시니어/펫/공구/가전 등 명백히 다른 시장(키워드)
                    nontarget_hits += 1
                # 카테고리상 명백히 영유아 아님 (타깃 카테고리도 아닐 때만)
                if any(tk in cat_path for tk in CAT_NONBABY_TOKENS):
                    nonbaby_cat_hits += 1
        focus_ratio = target_hits / len(own_brand_items)
        # 잡몰 판정: ① 타깃 집중도 절반 미만  OR
        #           ② 명백히 다른 시장(키워드) 2개+  OR
        #           ③ 영유아 아닌 카테고리 상품 2개+ (가전·가구·자동차 등)
        if focus_ratio < FOCUS_MIN or nontarget_hits >= 2 or nonbaby_cat_hits >= 2:
            print(f"        🚫 잡동사니 몰 제외 "
                  f"(타깃 집중도 {focus_ratio:.0%} — "
                  f"{target_hits}/{len(own_brand_items)}개 타깃, "
                  f"다른시장 {nontarget_hits}개, 비영유아카테고리 {nonbaby_cat_hits}개) "
                  f"→ 다음 후보로")
            time.sleep(0.3)
            return False

    if len(own_brand_items) >= 5:
        if COLLECT_MODE == "auto":
            cat_preset = sel.get("_category_preset", "")
            pool, tokens = auto_mode_contexts.get(cat_preset, (set(), []))
        else:
            pool, tokens = search_kw_pool_strict, user_kw_tokens
        match_count = 0
        for it in own_brand_items:
            title = clean_html_tags(it.get("title", ""))
            matched, _ = _is_keyword_match(title, pool, tokens)
            if matched:
                match_count += 1
        target_ratio = match_count / len(own_brand_items)
        if target_ratio < 0.3:
            print(f"        🚫 종합몰/무관 셀러 자동 제외 "
                  f"(사용자 키워드 관련 {target_ratio:.0%}, "
                  f"{match_count}/{len(own_brand_items)}개) → 다음 후보로")
            time.sleep(0.3)
            return False

    flagship = own_items[0] if own_items else sel
    flagship_title = clean_html_tags(flagship.get("title", ""))
    flagship_url = flagship.get("link", "")
    flagship_category = " > ".join(
        filter(None, [flagship.get(f"category{i}", "") for i in range(1, 5)])
    )

    # 5-1.5) 주력상품 B+C 재검사
    flagship_check, flagship_reason = market_fit_check(brand_name, flagship_title)
    if flagship_check == "a":
        pass   # 모든 모드 A 건너뜀
    elif flagship_check != "ok":
        print(f"        🚫 주력상품 재검사 탈락: {flagship_reason} → 다음 후보로")
        time.sleep(0.3)
        return False

    # 5-1.6) 메인 라인 매칭 (키워드 모드만)
    if COLLECT_MODE == "keywords":
        has_matching_product = False
        matched_product = ""
        matched_keyword = ""
        for item in brand_items[:10]:
            title = clean_html_tags(item.get("title", ""))
            matched, kw = _is_keyword_match(title, search_kw_pool_strict, user_kw_tokens)
            if matched:
                has_matching_product = True
                matched_product = item.get("title", "")[:40]
                matched_keyword = kw
                break
        if not has_matching_product:
            print(f"        🚫 메인 라인(top 10)에 사용자 키워드 매칭 X → 다음 후보로")
            time.sleep(0.3)
            return False
        else:
            print(f"        ✓ 메인 라인 매칭: '{matched_keyword}' in {matched_product}")

    flagship_price = int(flagship.get("lprice", 0))

    # 5-2) 진짜 스토어 URL
    store_url, store_debug = resolve_real_store_url(flagship_url)
    if store_url:
        print(f"        스토어 URL: {store_url} (셀러 메인)")
    elif flagship_url and "smartstore.naver.com" in flagship_url:
        store_url = flagship_url
        print(f"        스토어 URL: 상품 페이지 fallback")
    elif flagship_url and "brand.naver.com" in flagship_url:
        store_url = flagship_url
        print(f"        스토어 URL: brand.naver.com 상품 페이지 fallback")
    else:
        store_url = (
            f"https://search.shopping.naver.com/search/all?"
            f"query={_urlparse.quote(brand_name)}"
        )
        print(f"        스토어 URL: 검색 페이지 최후 fallback ({store_debug})")

    # 5-3) 상품 키워드 — ⭐ 브랜드명 전달 (자기 자신 추출 방지)
    product_keyword = extract_product_keyword(flagship_title, brand_name=brand_name)

    # 5-3.5) 관심고객수
    follower_count = fetch_follower_count(store_url)
    if follower_count > 0:
        print(f"        관심고객수: {follower_count:,}명")
    else:
        print(f"        관심고객수: 자동 수집 실패")

    # 5-4) 마케팅 등급
    auto_cat = classify_category(flagship_title)
    mgrade = calculate_marketing_grade(brand_name, product_keyword, auto_cat, follower_count)
    print(f"        주력: {flagship_title[:50]}")
    print(f"        카테고리: {auto_cat}")
    print(f"        마케팅: {mgrade['grade']} (블{mgrade['blog']}/카{mgrade['cafe']})")
    print(f"        마케팅 활동 단계: {mgrade['size']} — {mgrade['size_note']}")

    # 5-5) 대기업 컷
    if mgrade["size"] == "대기업":
        print(f"        🚫 대기업 자동 제외 (관심고객 {follower_count:,}명) → 다음 후보로")
        big_company_skipped.append({
            "브랜드명": brand_name,
            "관심고객수": follower_count,
            "Selpic 점수": sel["_score"],
        })
        time.sleep(0.3)
        return False

    # 5-5.5) 사업자 정보 자동 수집 (Phase 1+2+3)
    # 신규 셀러 수집 시점부터 상호·대표·전화·이메일 자동 추출
    # 무료 (스마트스토어 페이지 + 공정위 DB + 홈페이지/검색)
    biz_info = collect_business_info(brand_name, store_url)

    # 5-6) 결과 추가 — 자동 수집된 사업자 정보 포함
    results.append({
        "수집일":               datetime.now().strftime("%Y-%m-%d"),
        "Selpic 점수":          sel["_score"],
        "발견 카테고리":        sel["_category_preset"],
        "발견 키워드":          sel["_keyword"],
        "수집 모드":            COLLECT_MODE,
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
            f"카페 {mgrade['cafe']:,}"
        ),
        "마케팅 활동 단계 (자동)": f"{mgrade['size']} — {mgrade['size_note']}",
        "관심고객수 (자동)":    follower_count,
        # ⭐ 자동 수집 사업자 정보 (Phase 1+2+3) — 주소 제외 (2026-05-26)
        "상호 (자동)":               biz_info.get("company_name", ""),
        "대표 (자동)":               biz_info.get("ceo", ""),
        "사업자번호 (자동)":         biz_info.get("business_number", ""),
        "전화 (자동)":               biz_info.get("phone", ""),
        "이메일 (자동)":             biz_info.get("email", ""),
        "사업자정보 출처 (자동)":    ", ".join(biz_info.get("sources", [])),
        "사업자정보 신뢰도 (자동)":  biz_info.get("confidence", "낮음"),
        # 수기 입력 컬럼 (사용자 검증·수정용)
        "관심고객수 (수기)":         "",
        "리뷰수 (수기)":             "",
        "상호 (수기)":               "",
        "대표 (수기)":               "",
        "이메일 (수기)":             "",
        "전화 (수기)":               "",
        "마케팅 분석 메모 (수기)":   "",
    })
    time.sleep(0.3)
    return True


# ─────────────────────────────────────────────────────────────────
# 확장 라운드 검색 — 부족 시 자동 확장
# ─────────────────────────────────────────────────────────────────
def _perform_expansion_search(
    expansion_round,
    expanded_keywords,
    processed_brands,
    already_collected,
):
    """확장 라운드별 다른 검색 전략으로 추가 후보 발굴.

    라운드 1: asc + dsc sort, 페이지 1~150 (가격 정렬로 다른 셀러)
    라운드 2: sort 4종, 페이지 151~300 (깊은 페이지로 더 다양)

    반환: 점수순으로 정렬된 추가 passed 리스트
    """
    if expansion_round == 1:
        sorts = ["asc", "dsc"]
        offsets = [1, 51, 101]
    else:
        sorts = ["sim", "date", "asc", "dsc"]
        offsets = [151, 201, 251]

    new_candidates = []
    seen_in_round = set()

    for keyword in expanded_keywords:
        for sort_method in sorts:
            for start_offset in offsets:
                items = search_shop(keyword, display=50, start=start_offset, sort=sort_method)
                if not items:
                    break
                ss_items = [
                    it for it in items
                    if "smartstore.naver.com" in it.get("link", "")
                ]
                for rank, item in enumerate(ss_items, start_offset + expansion_round * 10000):
                    brand = item.get("mallName", "").strip()
                    if (not brand or brand in processed_brands
                            or brand in already_collected
                            or brand in seen_in_round):
                        continue
                    seen_in_round.add(brand)

                    if COLLECT_MODE == "keywords":
                        kw_cat = classify_category(keyword)
                        item["_category_preset"] = kw_cat if kw_cat != "기타" else keyword
                    elif COLLECT_MODE == "category":
                        item["_category_preset"] = TARGET_CATEGORY
                    else:
                        # ⭐ 2026-05-30: 자동 모드 확장 — 키워드로 카테고리 자동 분류
                        #   ("" 두면 점수 산정/표시가 비어서 누락됨)
                        kw_cat = classify_category(keyword)
                        item["_category_preset"] = kw_cat if kw_cat != "기타" else keyword
                    item["_keyword"] = f"{keyword} (확장{expansion_round})"
                    item["_rank"] = rank

                    # B+C 필터 (A 모든 모드 건너뜀)
                    title_text = clean_html_tags(item.get("title", ""))
                    fit_result, _ = market_fit_check(brand, title_text)
                    if fit_result not in ("a", "ok"):
                        continue

                    # 점수 산정
                    score, breakdown = calculate_fit_score(item, item["_category_preset"])
                    item["_score"] = score
                    item["_breakdown"] = breakdown
                    new_candidates.append(item)
                time.sleep(0.05)

    new_candidates.sort(key=lambda x: x["_score"], reverse=True)
    return new_candidates


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


def extract_product_keyword(title: str, brand_name: str = "") -> str:
    """주력 상품명에서 마케팅 검색용 키워드 추출.

    ⭐ 2026-05-13 개선 (정확도 ↑):
      1. 브랜드명 임시 제거 (자기 자신 추출 방지)
         예: "프라젠트라 아토프라덤..." → 브랜드 빼고 처리
              결과 키워드에 "프라젠트라" 안 들어감 (최종 검색 시 다시 합침)
      2. 부가정보 제거 (대괄호·괄호·용량)
      3. 카테고리 핵심 키워드 우선 매칭 (크림/로션/유모차 등)
      4. + Specific 명사 (가장 긴 고유 명사)
      5. 조합 반환: "아토프라덤 크림" 형태

    예시:
      "프라젠트라 아토프라덤 베이비 케어 크림 200ml" + brand="프라젠트라"
        → 브랜드 제거: "아토프라덤 베이비 케어 크림 200ml"
        → 수식어 제외: ["아토프라덤", "크림"]
        → 카테고리 매칭: "크림" (베이비 스킨케어 카테고리)
        → Specific: "아토프라덤"
        → 결과: "아토프라덤 크림"

      "에디슨 유아 젓가락 셀프케어 세트" + brand="에디슨"
        → 브랜드 제거: "유아 젓가락 셀프케어 세트"
        → 수식어 제외: ["젓가락", "셀프케어"]
        → 카테고리 매칭: "젓가락" (수유용품)
        → Specific: "셀프케어"
        → 결과: "셀프케어 젓가락"

    최종 검색 쿼리 형태 (calculate_marketing_grade에서):
        "{brand_name} {추출된 키워드}"
        예: "프라젠트라 아토프라덤 크림"
    """
    if not title:
        return ""
    text = clean_html_tags(title)

    # ⭐ Step 1: 브랜드명 임시 제거 (자기 자신 추출 방지)
    # 최종 검색 쿼리는 calculate_marketing_grade에서 다시 brand_name + 결과 합침
    if brand_name:
        text = text.replace(brand_name, "")

    # Step 2: 부가정보 제거
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
        # ⭐ 추가 일반 수식어 (2026-05-13)
        "케어", "관리", "보습", "수분", "진정",
        "부드러운", "촉촉한", "산뜻한", "순한", "안전한",
        "무자극", "민감", "건성", "지성", "복합",
        "전용", "고급",
    }

    # Step 3: 토큰화 + 수식어/용량 패턴 제외
    filtered = []
    for w in words:
        if w in SKIP_MODIFIERS:
            continue
        if re.match(r"^\d+(\.\d+)?[a-zA-Z가-힣]*$", w):   # 200ml, 5kg, 100 등 용량
            continue
        if len(w) < 2:
            continue
        filtered.append(w)

    if not filtered:
        return words[0]

    # ⭐ Step 4: 카테고리 핵심 키워드 매칭 우선 (단일 단어만)
    # 원본 title 기준으로 카테고리 분류 후, 그 카테고리의 단일 단어 키워드 매칭
    category = classify_category(title)
    cat_keywords = CATEGORY_KEYWORDS.get(category, [])

    cat_match = None
    for kw in cat_keywords:
        # 단일 단어 키워드만 (예: "크림", "로션", "유모차")
        # 복합 키워드 (예: "임산부 크림")는 제외
        if " " not in kw and kw in filtered:
            cat_match = kw
            break

    # Step 5: Specific 명사 추출 (가장 긴 단어, 카테고리 매칭 제외)
    specific_candidates = [w for w in filtered if w != cat_match]
    specific_candidates.sort(key=len, reverse=True)
    specific = specific_candidates[0] if specific_candidates else None

    # Step 6: 조합
    if specific and cat_match:
        return f"{specific} {cat_match}"   # "아토프라덤 크림"
    elif specific:
        return specific   # "아토프라덤"
    elif cat_match:
        return cat_match   # "크림"
    else:
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


# '공식/오피셜' 계열 접미 — 실제 상호가 아니라 판매채널 표기라 항상 제거해도 안전
_BRAND_NOISE_SUFFIX = [
    "공식스토어", "공식 스토어", "공식몰", "공식샵", "공식 샵",
    "오피셜스토어", "오피셜 스토어", "공식", "오피셜",
]
# 일반 접미 — 실제 상호의 일부일 수도 있어 '풀네임이 0건일 때만' 벗긴다
_BRAND_BARE_SUFFIX = ["스토어", "샵", "shop", "몰", "브랜드", "official"]
# 업종/카테고리 수식어 — 상호 뒤에 붙는 제품군 표기(더마·코스메틱 등).
#   마케팅 검색 시 이걸 붙인 정식 표기를 통째로 정확일치하면 노출이 과소집계된다.
#   예: '닥터바이오 더마 코스메틱' 정확일치 → 74건뿐. 핵심 상호 '닥터바이오'로 줄이면
#   +주력상품키워드와 함께 관련 글만 제대로 잡힌다. (핵심어가 2자+ 남을 때만 벗김)
_BRAND_CATEGORY_SUFFIX = [
    "더마코스메틱", "코스메틱스", "코스메틱", "더마", "화장품",
    "dermacosmetic", "cosmetics", "cosmetic", "derma",
]
# 업종어(사진관·조리원 등) — 합성 이름을 자를 때 이 꼬리표는 살려 정확도를 높인다.
#   예: '밀크비&포시즌김해 스튜디오' → '밀크비'(과다) 대신 '밀크비 스튜디오'(정확)
_BRAND_BIZ_TYPE = [
    "산후조리원", "조리원", "산후도우미", "산후관리", "스튜디오", "사진관",
    "포토", "마사지", "에스테틱", "클리닉", "산부인과", "케어",
]


def _clean_brand_for_search(brand_name: str) -> str:
    """마케팅 검색용 브랜드명 정규화 (항상 적용되는 안전 정리).

    '밀크비&포시즌김해 스튜디오'처럼 두 상호가 &로 붙거나 '(김해점)'·'공식 스토어'
    같은 부가어가 달린 이름은 따옴표 정확일치가 0건을 만든다(그 문자열 그대로인
    글이 없으니). 실제로는 '밀크비'로 검색해야 노출이 잡힌다. → 다음을 제거:
      1) 괄호 부가설명  2) 구분자(& / + , | ·) 뒤 합성 상호  3) '공식/오피셜' 접미
    멀쩡한 이름(구분자·괄호·공식표기 없음)은 그대로 둔다.
    """
    if not brand_name:
        return brand_name
    core = brand_name.strip()
    core = re.sub(r"\s*[\(\[（【].*?[\)\]）】]", "", core).strip()   # 괄호 부가설명 제거
    # 원래 이름 끝의 업종어(스튜디오·조리원 등) 기억 — 합성으로 잘리면 다시 붙인다
    _biz_tail = next((w for w in _BRAND_BIZ_TYPE if core.endswith(w)), "")
    for sep in ["&", "/", "+", ",", "|", "·"]:
        if sep in core:
            first = core.split(sep)[0].strip()
            if len(first) >= 2:          # 1글자로 깎이면 과도 → 원래 유지 (예: 'A&B키즈')
                core = first
    # 업종어가 있었는데 잘려나갔으면 되살려 정확도 유지 ('밀크비' → '밀크비 스튜디오')
    if _biz_tail and _biz_tail not in core:
        core = f"{core} {_biz_tail}"
    changed = True
    while changed:
        changed = False
        for w in _BRAND_NOISE_SUFFIX:
            if core.endswith(w) and len(core) > len(w) + 1:
                core = core[:-len(w)].strip()
                changed = True
    # ⭐ 2026-07 (닥터바이오 케이스): 끝에 붙은 업종/카테고리 수식어(더마·코스메틱 등)를
    #   벗겨 핵심 상호로 줄인다. 단 벗긴 뒤 핵심어가 한글 2자(영문 4자)+ 남을 때만 —
    #   너무 짧아지면 오히려 무관 글 과대집계 위험이 있어 원래 이름을 유지한다.
    changed = True
    while changed:
        changed = False
        for w in _BRAND_CATEGORY_SUFFIX:
            if core.lower().endswith(w):
                cand = core[:-len(w)].strip()
                bare = cand.replace(" ", "")
                has_hangul = any("가" <= ch <= "힣" for ch in bare)
                if (has_hangul and len(bare) >= 2) or len(bare) >= 4:
                    core = cand
                    changed = True
                    break
    return core or brand_name.strip()


def _brand_core_bare(brand_name: str) -> str:
    """위 정리 후에도 남은 '스토어/샵/몰' 등 일반 접미까지 벗긴 최소 상호.
    오탈락 위험이 있어 '풀네임이 0건일 때만' 재검색용으로 쓴다.
    """
    core = _clean_brand_for_search(brand_name)
    changed = True
    while changed:
        changed = False
        for w in _BRAND_BARE_SUFFIX:
            if core.endswith(w) and len(core) > len(w) + 1:
                core = core[:-len(w)].strip()
                changed = True
    return core


def calculate_marketing_grade(brand_name: str, search_keyword: str, category: str = "", follower_count: int = 0) -> dict:
    """3채널 검색 노출량 → 상/중/하 + 규모

    ⭐ 2026-05-13 개선:
      검색 쿼리: "{brand_name} {search_keyword}" (메인 쿼리 1개)
        예: "프라젠트라 아토프라덤 크림"

      - search_keyword = extract_product_keyword 결과 (브랜드명 제외된 키워드)
      - Naver 자동 처리: 띄어쓰기·순서·사이단어 변형 모두 자동 매칭
      - 단일 쿼리 1회 검색 (블로그·카페 각 1회) — 중복 카운트 X
      - Fallback: 데이터 부족 시 시장 컨텍스트로 재검색

    노이즈 제거 핵심 — 브랜드명 + 주력상품 조합으로 검색:
      예: "에디슨" 단독 → 토마스 에디슨(과학자) 글 다 잡혀 → 부정확
      예: "에디슨 셀프케어 젓가락" → 그 브랜드 상품 마케팅만 정확 측정 ✅

    채널 구성 (3채널):
      - 블로그: Naver 블로그 검색 결과 수
      - 카페: Naver 카페 검색 결과 수 (가장 신뢰성 높음, 유기적 입소문)
      - SNS: Naver에서 "{쿼리} 인스타" 멘션 (인스타그램 프록시)
    """
    # 시장 컨텍스트 결정 (카테고리 기반) — Fallback용
    if any(k in category for k in ["임산부", "산모", "출산", "산후"]):
        market_context = "임산부"
    else:
        market_context = "베이비"

    # ⭐ 메인 쿼리: 브랜드명 + 추출된 주력상품 키워드
    # search_keyword는 extract_product_keyword 결과 (브랜드명 제외됨)
    # ⭐ 2026-06-01: 브랜드명을 따옴표로 감싸 '정확 일치' 검색.
    #   "유아러브"처럼 일반어('유아')가 든 이름은 Naver가 '유아'만 매칭해
    #   무관한 글을 잔뜩 세서 마케팅 점수가 부풀려짐 → 신생 브랜드가 확장기 오분류.
    #   따옴표로 묶으면 '유아러브'가 통째로 든 글만 카운트 → 진짜 브랜드 언급만.
    # ⭐ 2026-07: 서비스형(상품 없음)은 주력상품이 없으니 브랜드명만으로 노출 취합.
    #   (산후도우미·마사지 등은 '주력상품 키워드'가 존재하지 않는다)
    is_service = "서비스" in (category or "")
    # ⭐ 2026-07: 합성/부가어 붙은 이름(밀크비&포시즌김해 스튜디오)은 핵심 상호만으로
    #   검색해야 노출이 잡힌다. (전체 이름 정확일치 → 0건 버그)
    _bn_core = _clean_brand_for_search(brand_name)
    bn_q = f'"{_bn_core}"' if _bn_core else ""
    if is_service and brand_name:
        # 서비스형: 브랜드명 단독 검색 (시장 컨텍스트도 안 붙임)
        query_main = bn_q
    elif brand_name and search_keyword:
        query_main = f"{bn_q} {search_keyword}"
        # 예: '"프라젠트라" 아토프라덤 크림'
    elif brand_name:
        # 주력상품 키워드 추출 실패 시 시장 컨텍스트로 fallback
        query_main = f"{bn_q} {market_context}"
    else:
        # 브랜드명 없으면 (드문 케이스)
        query_main = f"{search_keyword} {market_context}"

    # 메인 검색 (블로그·카페 각 1회씩 — Naver 자동 처리에 신뢰)
    blog = search_naver("blog", query_main, 1).get("total", 0)
    cafe = search_naver("cafearticle", query_main, 1).get("total", 0)
    used_query = query_main

    # ⭐ 2026-07: 그래도 0건이면 이름이 지저분한 것(스토어/샵/몰 등 접미)일 수 있으니
    #   일반 접미까지 벗긴 최소 상호로 한 번 더 시도. (멀쩡한 이름은 0건이 아니라 안 탐)
    if blog + cafe == 0 and brand_name:
        _bare = _brand_core_bare(brand_name)
        if _bare and _bare != _bn_core:
            bare_q = f'"{_bare}"' if (is_service or not search_keyword) \
                else f'"{_bare}" {search_keyword}'
            blog_b = search_naver("blog", bare_q, 1).get("total", 0)
            cafe_b = search_naver("cafearticle", bare_q, 1).get("total", 0)
            if blog_b + cafe_b > 0:
                blog, cafe, used_query = blog_b, cafe_b, bare_q

    # ⭐ Fallback: 메인 쿼리 결과가 너무 적을 시 시장 컨텍스트로 재검색
    # 작은 브랜드·신규 상품은 specific 매칭 부족할 수 있음
    if blog + cafe < 200 and brand_name and search_keyword:
        fallback_query = f"{bn_q} {market_context}"
        blog_fb = search_naver("blog", fallback_query, 1).get("total", 0)
        cafe_fb = search_naver("cafearticle", fallback_query, 1).get("total", 0)
        if blog_fb + cafe_fb > blog + cafe:
            blog = blog_fb
            cafe = cafe_fb
            used_query = fallback_query

    # ⭐ 2026-07: SNS(인스타 프록시) 제거.
    #   기존 SNS = 네이버에서 "{쿼리} 인스타" 언급 수였는데, 실제 인스타그램 활동
    #   (팔로워·게시물·해시태그)과 무관한 의미 없는 수치라 사용자 판단으로 제외.
    #   블로그·카페 2채널만 사용. (SNS 검색 2회 제거 → 수집도 소폭 빨라짐)
    sns = 0

    # 점수 계산 — 2채널 가중치 (카페 가장 중요)
    score = (
        math.log10(blog + 1) * 1.5
        + math.log10(cafe + 1) * 2.0
    )

    # 마케팅 등급 — SNS 제거분 반영해 임계치 재조정(실측으로 기존 등급 분포 유지)
    if score >= 7:
        grade = "상"
    elif score >= 3:
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
        channels = {"블로그": blog, "카페": cafe}
        max_channel = max(channels, key=channels.get)
        max_value = channels[max_channel]

        # 단계 결정 (점수 기반 — SNS 제거분 반영해 임계치 재조정)
        if score >= 9:
            size = "확장기"
            # 서술: 강세 채널 + 권장 영업 방향
            size_note = (
                f"{max_channel} 노출 {max_value:,}건 등 마케팅 활발 — "
                f"신규 매출 채널 확장 적기"
            )
        elif score >= 4.5:
            size = "성장기"
            size_note = (
                f"{max_channel} 중심 노출 형성 중 ({max_value:,}건) — "
                f"효율 채널 도입 적기"
            )
        else:
            size = "도입기"
            # 가장 부족한 정보 표시
            size_note = (
                f"전 채널 노출 미흡 (블{blog:,}/카{cafe:,}) — "
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

    # 블로그 + 카페 마이닝 — 추천/후기 글에서 Smart Store 셀러 추가 발굴
    # ⭐ 카페 마이닝 추가 (2026-05-13): 영유아/임산부 시장 핵심 풀 (맘카페 입소문)
    print(f"\n🔎 [1.5/6] 블로그 + 카페 마이닝 — 추천/후기 글에서 셀러 추가 발굴...")
    for keyword in USER_KEYWORDS:   # 원본 키워드만 (확장 X — API 부담)
        kw_cat = classify_category(keyword)
        if kw_cat == "기타":
            kw_cat = keyword
        # 블로그 마이닝
        blog_brands = mine_brands_from_blog(keyword, max_brands=15)
        for item in blog_brands:
            item["_keyword"] = f"{keyword} (블로그)"
            item["_category_preset"] = kw_cat
            item["_rank"] = 99   # 블로그 발굴은 별도 랭크
            candidates.append(item)
        # 카페 마이닝 (맘카페 입소문 — 가장 신뢰성 높음)
        cafe_brands = mine_brands_from_cafe(keyword, max_brands=15)
        for item in cafe_brands:
            item["_keyword"] = f"{keyword} (카페)"
            item["_category_preset"] = kw_cat
            item["_rank"] = 99
            candidates.append(item)
        print(f"   ✓ '{keyword}' → 블로그 {len(blog_brands)}건 · 카페 {len(cafe_brands)}건")

elif COLLECT_MODE == "category":
    # 모드 2: 단일 카테고리 한정 + 동의어 자동 확장
    # ⭐ 2026-05-13 강화: 카테고리 키워드도 동의어 확장 적용
    if TARGET_CATEGORY not in CATEGORY_PRESETS:
        print(f"   ❌ 알 수 없는 카테고리: '{TARGET_CATEGORY}'")
        print(f"      허용 카테고리: {', '.join(CATEGORY_PRESETS.keys())}")
        sys.exit(1)
    base_keywords = CATEGORY_PRESETS[TARGET_CATEGORY]

    # 동의어 자동 확장 (예: "아기 로션" → "베이비 로션", "신생아 로션")
    expanded_keywords = []
    for kw in base_keywords:
        if kw not in expanded_keywords:
            expanded_keywords.append(kw)
        for ex in expand_keyword(kw):
            if ex not in expanded_keywords:
                expanded_keywords.append(ex)

    print(f"🔍 [1/6] '{TARGET_CATEGORY}' 카테고리 {len(base_keywords)}개 키워드 → 동의어 확장 {len(expanded_keywords)}개 × 2 sort × 3 페이지...")
    for keyword in expanded_keywords:
        keyword_total = 0
        for sort_method in ["sim", "date"]:
            for start_offset in [1, 51, 101]:
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

    # 카테고리 모드도 블로그 + 카페 마이닝 (단, 첫 키워드만 — 너무 많아질 수 있음)
    # ⭐ 카페 마이닝 추가 (2026-05-13): 영유아/임산부 시장 핵심 풀 (맘카페 입소문)
    # ⭐ 2026-06-01 버그수정: 카테고리 모드 변수는 base_keywords (keywords 아님)
    #   → NameError로 카테고리 수집이 통째로 실패하던 문제 해결
    if base_keywords:
        print(f"\n🔎 [1.5/6] 블로그 + 카페 마이닝 (대표 키워드 1개)...")
        # 블로그 마이닝
        blog_brands = mine_brands_from_blog(base_keywords[0], max_brands=15)
        for item in blog_brands:
            item["_keyword"] = f"{base_keywords[0]} (블로그)"
            item["_category_preset"] = TARGET_CATEGORY
            item["_rank"] = 99
            candidates.append(item)
        # 카페 마이닝
        cafe_brands = mine_brands_from_cafe(base_keywords[0], max_brands=15)
        for item in cafe_brands:
            item["_keyword"] = f"{base_keywords[0]} (카페)"
            item["_category_preset"] = TARGET_CATEGORY
            item["_rank"] = 99
            candidates.append(item)
        print(f"   ✓ '{base_keywords[0]}' → 블로그 {len(blog_brands)}건 · 카페 {len(cafe_brands)}건")

else:
    # 모드 1 (기본): 자동 — 10개 카테고리 전체 시장 발굴
    # ⭐ 2026-05-13 강화 (정책 통일):
    #   - 카테고리당 2개 키워드 → 동의어 자동 확장으로 4-6개 키워드
    #   - sort sim+date 다양화 + 페이지 1~3 확대
    #   - 블로그/카페 마이닝 [1.5/6]에서 추가

    # ⭐ 2026-05-30: 자동 모드 확장 검색용 키워드 풀 (카테고리당 대표 1개)
    #   확장은 깊은 페이지(151~300위)까지 뒤져 '덜 유명한 새 브랜드' 발굴.
    #   전체 키워드(약 72개)면 너무 느려서 카테고리당 대표 1개만 사용.
    expanded_keywords = [kws[0] for kws in CATEGORY_PRESETS.values() if kws]

    print(f"🔍 [1/6] {len(CATEGORY_PRESETS)}개 카테고리 × 키워드 (동의어 확장) × 2 sort × 3 페이지...")
    for cat_name, keywords in CATEGORY_PRESETS.items():
        cat_total = 0
        # 카테고리당 첫 2개 키워드 + 각 키워드의 동의어 확장
        # (전체 키워드는 너무 많음 → 대표 2개만 + 동의어로 폭 확보)
        selected_keywords = []
        for kw in keywords[:2]:
            selected_keywords.append(kw)
            # 동의어 확장 (예: "아기 로션" → "베이비 로션", "신생아 로션")
            for ex in expand_keyword(kw):
                if ex != kw and ex not in selected_keywords:
                    selected_keywords.append(ex)
                    if len(selected_keywords) >= 6:   # 카테고리당 최대 6개
                        break
            if len(selected_keywords) >= 6:
                break

        for keyword in selected_keywords:
            # sort 다양화 + 페이지 확대
            for sort_method in ["sim", "date"]:
                for start_offset in [1, 51, 101]:
                    items = search_shop(keyword, display=50, start=start_offset, sort=sort_method)
                    if not items:
                        break
                    ss_items = [
                        it for it in items
                        if "smartstore.naver.com" in it.get("link", "")
                    ]
                    for rank, item in enumerate(ss_items, start_offset):
                        item["_keyword"] = keyword
                        item["_category_preset"] = cat_name
                        item["_rank"] = rank
                        candidates.append(item)
                    cat_total += len(ss_items)
                    time.sleep(0.05)
        print(f"   ✓ {cat_name:18s} → 키워드 {len(selected_keywords)}개(확장 포함) → {cat_total}건")

    # 자동 모드도 블로그 + 카페 마이닝 추가
    print(f"\n🔎 [1.5/6] 블로그 + 카페 마이닝 — 카테고리당 대표 키워드 1개...")
    for cat_name, keywords in CATEGORY_PRESETS.items():
        if not keywords:
            continue
        rep_keyword = keywords[0]   # 카테고리 대표 키워드
        blog_brands = mine_brands_from_blog(rep_keyword, max_brands=10)
        for item in blog_brands:
            item["_keyword"] = f"{rep_keyword} (블로그)"
            item["_category_preset"] = cat_name
            item["_rank"] = 99
            candidates.append(item)
        cafe_brands = mine_brands_from_cafe(rep_keyword, max_brands=10)
        for item in cafe_brands:
            item["_keyword"] = f"{rep_keyword} (카페)"
            item["_category_preset"] = cat_name
            item["_rank"] = 99
            candidates.append(item)
        print(f"   ✓ {cat_name:18s} → 블로그 {len(blog_brands)}건 · 카페 {len(cafe_brands)}건")

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


# ── [3.5/6] 시장 타깃 크로스체크 (B 대기업 컷 / C 부정 키워드 차단) ──
# ⭐ 2026-05-13 정책 통일: 모든 모드 A(영유아 키워드 필수) 건너뜀
#    이유: 사용자가 직접 검색하는 워크플로우라 시장 의도는 검색 키워드로 표현됨.
#    "튼살크림" 같은 specific 키워드 검색 시 A 강제는 false negative 양산.
print(f"🎯 [3.5/6] 시장 타깃 크로스체크 (B 대기업 컷 / C 다른시장 차단 — A 영유아 키워드 건너뜀)...")
fit_candidates = []
fail_log = {"a": [], "b": [], "c": []}

for c in unique_candidates:
    brand = c.get("mallName", "").strip()
    title = clean_html_tags(c.get("title", ""))
    result, reason = market_fit_check(brand, title)

    # 모든 모드 A 탈락은 무시 (시장 의도는 검색 키워드로 표현)
    if result == "a":
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
    print(f"\n   ❌ 상품 통과 셀러 없음.")
    # ⭐ 2026-06-01: 서비스 수집 모드면 상품 0건이어도 종료하지 않고 계속
    #   (출산서비스 카테고리·키워드·자동은 뒤에서 서비스 업체를 따로 수집)
    _will_service = (
        (COLLECT_MODE == "category" and TARGET_CATEGORY == "출산 서비스")
        or COLLECT_MODE in ("keywords", "auto")
    )
    if not _will_service:
        print(f"   임계치 조정 또는 카테고리 변경 필요")
        sys.exit(0)
    print(f"   → 서비스 업체 수집은 계속 진행합니다.")
print()


# ── [5/6] 디테일 수집 + 대기업 자동 제외 (카페 50만+ 컷) ──
# 기존 selected는 점수순 5건만 잡았지만, 대기업 제외하면 5건 부족할 수 있어서
# passed (70점+ 통과 전체)에서 시작해서 5건 채울 때까지 진행
print(f"🔬 [5/6] 디테일 수집 + 대기업 자동 제외 (카페 50만+ 자동 컷)...")

# 매칭 컨텍스트 한 번만 빌드 → 5-1.3 (모든 모드), 5-1.6 (키워드 모드만) 재사용
# ⭐ 2026-05-13 통일: 자동 모드도 사용자 키워드 관련도 기반 종합몰 컷 적용
#   - 자동: 카테고리별 처리 → 후보의 _category_preset 키워드 사용 (동적 빌드)
#   - 카테고리: TARGET_CATEGORY 키워드 (정적 빌드)
#   - 키워드: USER_KEYWORDS (정적 빌드)
search_kw_pool_strict = set()
user_kw_tokens = []
auto_mode_contexts = {}   # 자동 모드용: 카테고리별 컨텍스트 캐시

if COLLECT_MODE in ("keywords", "category"):
    search_kw_pool_strict, user_kw_tokens = _build_keyword_match_context(
        COLLECT_MODE, USER_KEYWORDS, TARGET_CATEGORY, CATEGORY_PRESETS
    )
    print(f"   ℹ️ 키워드 매칭 풀: {len(search_kw_pool_strict)}개 + AND 토큰 {len(user_kw_tokens)}쌍")
elif COLLECT_MODE == "auto":
    # 자동 모드: 카테고리별로 컨텍스트 사전 빌드 (후보별 lookup 빠르게)
    for cat_name in CATEGORY_PRESETS.keys():
        pool, tokens = _build_keyword_match_context(
            "category", [], cat_name, CATEGORY_PRESETS
        )
        auto_mode_contexts[cat_name] = (pool, tokens)
    print(f"   ℹ️ 자동 모드 카테고리별 매칭 풀 {len(auto_mode_contexts)}개 빌드 완료")

results = []
big_company_skipped = []   # 대기업으로 제외된 셀러 기록
processed_brands = set()   # 같은 브랜드 중복 처리 방지


def _is_accessible_homepage(url: str) -> bool:
    """⭐ 2026-06-02: 비공개/준비중/접속불가 사이트 판별 (수집 제외용).
    '열기' 눌렀을 때 비공개로 뜨는 사이트는 영업처로 쓸 수 없음.
    """
    try:
        r = requests.get(
            url,
            headers={"User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )},
            timeout=8,
            allow_redirects=True,
        )
        if r.status_code != 200:
            return False
        body = re.sub(r"<[^>]+>", " ", r.text or "")[:3000]
        CLOSED_MARKERS = (
            "비공개", "준비중인 사이트", "사이트 준비중", "쇼핑몰 준비중",
            "접근이 제한", "접속이 제한", "휴면", "운영이 중지", "운영중지",
            "사용할 수 없", "권한이 없", "폐쇄", "서비스가 종료",
        )
        if any(mk in body for mk in CLOSED_MARKERS):
            return False
        return True
    except Exception:
        return False


def _collect_service_candidates(queries, category_label, target):
    """⭐ 2026-06-01: 서비스 업체(청소·마사지·산후도우미) 수집.
    스마트스토어 상품이 아니라 웹검색으로 업체 홈페이지를 찾고,
    홈페이지에서 연락처 수집 + '스토어 주소'를 홈페이지로 저장.
    상품 데이터(가격·리뷰·관심고객수)는 없으므로 빈 값.
    """
    seen_domains = set()   # 같은 업체(도메인) 중복 방지
    for q in queries:
        if len(results) >= target:
            break
        # ⭐ 웹검색(자연결과) + 파워링크(광고) 둘 다 모아서 합침
        homepages = find_service_business_homepages(q, max_results=8)
        homepages += find_powerlink_businesses(q, max_results=8)
        for hp in homepages:
            if len(results) >= target:
                break
            url = hp.get("url", "")
            if not url:
                continue
            # 도메인 중복 제거 (두 소스에서 같은 업체가 나올 수 있음)
            # ⭐ 2026-06-02: 모바일 주소(m.) 정규화 — 'm'이 브랜드명 되던 문제 + 중복 방지
            try:
                from urllib.parse import urlparse as _up_dom
                _dom = _up_dom(url).netloc.replace("www.", "").lower()
                if _dom.startswith("m."):
                    _dom = _dom[2:]
                    url = f"https://{_dom}"   # 모바일 → 데스크톱 주소로 통일
            except Exception:
                _dom = url
            if _dom in seen_domains:
                continue
            seen_domains.add(_dom)

            # ⭐ 2026-06-02: 비공개/접속불가 사이트 제외 (열어도 못 보는 링크 수집 방지)
            if not _is_accessible_homepage(url):
                print(f"        🚫 비공개/접속불가 사이트 제외: {url}")
                continue

            # 도메인명 (검색·브랜드 매칭 기준 — hint_url과 도메인 일치 → 공식 신뢰)
            domain_name = _dom.split(".")[0] if "." in _dom else ""
            search_name = domain_name or (hp.get("name") or "")[:30]

            # 홈페이지 footer에서 연락처 수집 — ⭐ only_hint: 이 홈페이지만 읽음(검색 X)
            info = find_business_info_from_homepage(
                search_name, hint_url=url, only_hint=True
            )

            # 표시 브랜드명: ⭐ 사이트명(한글) > 푸터 상호 > 도메인명 > 검색 제목
            name = (
                info.get("site_name") or info.get("company_name")
                or domain_name or (hp.get("name") or "")
            ).strip()[:40]
            if not name or name in processed_brands or name in already_collected:
                continue
            processed_brands.add(name)
            print(f"\n   ▶ [서비스] {name}  ({url})")
            mg = calculate_marketing_grade(name, "", category_label)
            has_contact = bool(info.get("phone") or info.get("email"))
            results.append({
                "수집일":               datetime.now().strftime("%Y-%m-%d"),
                "Selpic 점수":          0,
                "발견 카테고리":        category_label or "출산 서비스",
                "발견 키워드":          f"{q} (서비스)",
                "수집 모드":            COLLECT_MODE,
                "브랜드명":             name,
                "스마트스토어 주소":    url,   # ⭐ 스토어열기 → 공식 홈페이지
                "주력상품명":           "",
                "상품 카테고리":        "서비스",
                "가격":                 "",
                "점수 근거":            "서비스 업체 (웹검색 발굴)",
                "마케팅 검색 키워드 (자동)": mg.get("query", name),
                "마케팅 등급 (자동)":   mg["grade"],
                "마케팅 점수 (자동)":   mg["score"],
                "마케팅 채널별 노출 (자동)": (
                    f"블로그 {mg['blog']:,} · 카페 {mg['cafe']:,}"
                ),
                "마케팅 활동 단계 (자동)": f"{mg['size']} — {mg['size_note']}",
                "관심고객수 (자동)":    0,
                "상호 (자동)":               info.get("company_name", ""),
                "대표 (자동)":               info.get("ceo", ""),
                "사업자번호 (자동)":         info.get("business_number", ""),
                "전화 (자동)":               info.get("phone", ""),
                "이메일 (자동)":             info.get("email", ""),
                "사업자정보 출처 (자동)":    "서비스 홈페이지" if has_contact else "공식 홈페이지 미발견 — 수기 입력 필요",
                "사업자정보 신뢰도 (자동)":  "중간" if has_contact else "미발견",
                "관심고객수 (수기)":         "",
                "리뷰수 (수기)":             "",
                "상호 (수기)":               "",
                "대표 (수기)":               "",
                "이메일 (수기)":             "",
                "전화 (수기)":               "",
                "마케팅 분석 메모 (수기)":   "",
            })
            print(f"        ✓ 서비스 업체 추가: 전화={info.get('phone','')}, "
                  f"이메일={info.get('email','')}")
            time.sleep(0.3)


# ⭐ 서비스 업체 수집 (출산서비스 카테고리 / 키워드 / 자동) — 상품 수집 전에 일부 채움
SERVICE_CATEGORIES = {"출산 서비스"}

# ⭐ 2026-06-01: 키워드가 '서비스 키워드'인지 판별.
#   청소·마사지·도우미·사진 등 서비스 단어가 든 키워드만 서비스(공식홈) 발굴.
#   '튼살크림'·'분유' 같은 상품 키워드는 서비스 발굴 안 함 → 상품(스마트스토어)만.
SERVICE_HINT_TOKENS = (
    "청소", "마사지", "도우미", "조리원", "산후조리", "사진", "촬영",
    "만삭", "앨범", "스튜디오", "요가", "필라테스", "에스테틱",
    "베이비시터", "시터", "출장", "방문", "교육", "클래스", "레슨",
    "컨설팅", "산후관리", "산후케어", "산후회복", "체형관리",
)


def _is_service_keyword(kw: str) -> bool:
    k = (kw or "").replace(" ", "")
    return any(t in k for t in SERVICE_HINT_TOKENS)


# ⭐ 2026-06-01: '출산 서비스' 카테고리 = 서비스 전용 → 상품(스마트스토어) 수집 안 함.
#   (서비스로 다 못 채워도 의류 등 상품으로 빈자리 채우지 않음 — 사용자 요청)
is_service_only = (COLLECT_MODE == "category" and TARGET_CATEGORY in SERVICE_CATEGORIES)
_svc_queries = []
_svc_target = 0
if COLLECT_MODE == "category" and TARGET_CATEGORY in SERVICE_CATEGORIES:
    _svc_queries = CATEGORY_PRESETS.get(TARGET_CATEGORY, [])
    _svc_target = TARGET_COUNT                      # 서비스 카테고리 → 전부 서비스
elif COLLECT_MODE == "keywords":
    # ⭐ 서비스 단어가 든 키워드만 서비스 발굴 (튼살크림 등 상품 키워드는 제외)
    _svc_queries = [k for k in USER_KEYWORDS if _is_service_keyword(k)]
    _svc_target = max(1, TARGET_COUNT // 2) if _svc_queries else 0
    if not _svc_queries:
        print("   ℹ️ 서비스 단어 없는 상품 키워드 → 서비스 발굴 생략 (상품만 수집)")
elif COLLECT_MODE == "auto":
    _svc_queries = CATEGORY_PRESETS.get("출산 서비스", [])[:2]
    _svc_target = min(2, TARGET_COUNT)              # 자동 → 소수만

if _svc_queries:
    print(f"\n🧹 [서비스] 서비스 업체 수집 시작 "
          f"(키워드 {len(_svc_queries)}개, 목표 {_svc_target}건)...")
    _collect_service_candidates(_svc_queries, TARGET_CATEGORY, _svc_target)

# ⭐ 상품(스마트스토어) 수집 — 단, '출산 서비스' 카테고리(서비스 전용)는 건너뜀
if not is_service_only:
    for sel in passed:
        if len(results) >= TARGET_COUNT:
            break
        _process_one_candidate(
            sel, processed_brands, results, big_company_skipped,
            search_kw_pool_strict, user_kw_tokens, auto_mode_contexts,
        )

# ─── 부족 시 자동 확장 검색 (사용자 요청: 미달 시 검색범위 자동 확대) ───
# ⭐ 2026-05-30: 자동 모드도 확장 지원 (DB가 차서 기본검색이 전부 중복일 때
#    깊은 페이지·다른 정렬로 '덜 유명한 새 브랜드' 자동 발굴)
# ⭐ 2026-06-01: 서비스 전용 카테고리는 상품 확장 안 함
expansion_round = 0
MAX_EXPANSION_ROUNDS = 2
while (not is_service_only) and len(results) < TARGET_COUNT and expansion_round < MAX_EXPANSION_ROUNDS:
    expansion_round += 1
    print(f"\n⚡ 결과 {len(results)}/{TARGET_COUNT}건 미달 → 확장 라운드 {expansion_round} 시작...")

    extra_passed = _perform_expansion_search(
        expansion_round, expanded_keywords, processed_brands, already_collected,
    )
    print(f"   확장 후보: {len(extra_passed)}건 → 디테일 수집 시작...")

    for sel in extra_passed:
        if len(results) >= TARGET_COUNT:
            break
        _process_one_candidate(
            sel, processed_brands, results, big_company_skipped,
            search_kw_pool_strict, user_kw_tokens, auto_mode_contexts,
            is_expansion=True,
        )

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
