"""
PICK10 - 잘못 저장된 스토어 URL 일괄 수정 (1회용 스크립트)
=================================================================
실행:
    python fix_smartstore_urls.py

목적:
    Supabase의 sellers 테이블에서
    smartstore_url이 검색 페이지 URL로 잘못 저장된 행들을
    실제 스마트스토어 URL로 일괄 변경.

3중 fallback 전략:
    1순위: redirect 추적 → 셀러 메인 URL
    2순위: API 원본 link (상품 페이지)
    3순위 (최후): 검색 페이지 (API link도 없을 때만)
=================================================================
"""

import os
import re
import sys
import time
import urllib.parse

import requests
from dotenv import load_dotenv

from supabase_client import get_supabase_client, TABLE_NAME

load_dotenv()
CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    print("⚠️  .env에 NAVER_CLIENT_ID, NAVER_CLIENT_SECRET 필요")
    sys.exit(1)

EXCLUDE_IDS = {"main", "search", "category", "popup"}


def fetch_smartstore_link(brand_name: str) -> str:
    """브랜드명으로 검색 → 그 셀러의 link 받기"""
    if not brand_name:
        return ""
    headers = {
        "X-Naver-Client-Id": CLIENT_ID,
        "X-Naver-Client-Secret": CLIENT_SECRET,
    }
    try:
        resp = requests.get(
            "https://openapi.naver.com/v1/search/shop.json",
            headers=headers,
            params={"query": brand_name, "display": 5, "sort": "sim"},
            timeout=10,
        )
        items = resp.json().get("items", [])
        # 가장 잘 맞는 mallName 매칭 우선
        for it in items:
            if it.get("mallName", "").strip() == brand_name and "smartstore.naver.com" in it.get("link", ""):
                return it.get("link", "")
        # 없으면 첫번째 smartstore link
        for it in items:
            if "smartstore.naver.com" in it.get("link", ""):
                return it.get("link", "")
        # 없으면 brand.naver.com link
        for it in items:
            if "brand.naver.com" in it.get("link", ""):
                return it.get("link", "")
    except Exception as e:
        print(f"  API 오류 ({brand_name}): {e}")
    return ""


def resolve_real_store_url(link: str) -> str:
    """API link → redirect/HTML 파싱 → 셀러 메인 URL 시도"""
    if not link:
        return ""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    }

    def match_url(url: str) -> str:
        if not url:
            return ""
        m = re.match(r"https?://smartstore\.naver\.com/([^/?#]+)", url)
        if m and m.group(1) not in EXCLUDE_IDS:
            return f"https://smartstore.naver.com/{m.group(1)}"
        m = re.match(r"https?://brand\.naver\.com/([^/?#]+)", url)
        if m and m.group(1) not in EXCLUDE_IDS:
            return f"https://brand.naver.com/{m.group(1)}"
        return ""

    try:
        resp = requests.get(link, headers=headers, allow_redirects=True, timeout=15)
        # 1) 최종 redirect URL
        result = match_url(resp.url)
        if result:
            return result
        # 2) HTML 파싱 (og:url / canonical / 본문)
        html = resp.text or ""
        for pattern in [
            r'<meta\s+property=["\']og:url["\']\s+content=["\']([^"\']+)["\']',
            r'<link\s+rel=["\']canonical["\']\s+href=["\']([^"\']+)["\']',
        ]:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                result = match_url(m.group(1))
                if result:
                    return result
        for sid in re.findall(r"https?://smartstore\.naver\.com/([a-zA-Z0-9_\-]+)", html):
            if sid not in EXCLUDE_IDS:
                return f"https://smartstore.naver.com/{sid}"
        for bid in re.findall(r"https?://brand\.naver\.com/([a-zA-Z0-9_\-]+)", html):
            if bid not in EXCLUDE_IDS:
                return f"https://brand.naver.com/{bid}"
    except Exception:
        pass
    return ""


def needs_fix(url: str) -> bool:
    """이 URL이 수정 필요한지"""
    if not url:
        return True
    if "search.shopping.naver.com" in url:
        return True
    return False


def main():
    print("=" * 60)
    print("PICK10 스토어 URL 일괄 수정")
    print("=" * 60)

    sb = get_supabase_client()

    # 1) 모든 행 조회
    print("\n[1/3] DB에서 전체 행 조회 중...")
    result = sb.table(TABLE_NAME).select("brand_name, smartstore_url").execute()
    all_rows = result.data
    print(f"  총 {len(all_rows)}개 행")

    # 2) 수정 필요한 행 필터링
    bad_rows = [r for r in all_rows if needs_fix(r.get("smartstore_url", ""))]
    print(f"\n[2/3] 수정 대상: {len(bad_rows)}개 행")

    if not bad_rows:
        print("  ✅ 수정 필요한 행 없음. 모두 정상.")
        return

    # 미리보기 — 무엇이 바뀌는지
    print("\n  수정 대상 미리보기 (최대 10개):")
    for r in bad_rows[:10]:
        url = r.get("smartstore_url", "") or "(빈 값)"
        print(f"    - {r['brand_name']}: {url[:70]}")
    if len(bad_rows) > 10:
        print(f"    ... 그 외 {len(bad_rows) - 10}개")

    print(f"\n→ {len(bad_rows)}개 행 자동 수정 시작...")
    time.sleep(2)   # 사용자가 미리보기 볼 시간

    # 3) 한 행씩 수정 — 3중 fallback
    print(f"\n[3/3] 수정 중...")
    stats = {"resolved": 0, "product_fallback": 0, "search_fallback": 0, "skipped": 0}

    for i, row in enumerate(bad_rows, 1):
        brand = row["brand_name"]
        print(f"\n[{i}/{len(bad_rows)}] {brand}")

        # API에서 새 link 가져오기
        link = fetch_smartstore_link(brand)
        if not link:
            print(f"  ⚠️  API에서 link 못 받음 → 스킵")
            stats["skipped"] += 1
            time.sleep(0.3)
            continue

        # 1순위: redirect 추적
        new_url = resolve_real_store_url(link)
        tier = "셀러 메인 URL"

        # 2순위: API link 보존 (상품 페이지)
        if not new_url:
            if "smartstore.naver.com" in link or "brand.naver.com" in link:
                new_url = link
                tier = "상품 페이지 fallback"

        # 3순위 (최후): 검색 페이지 — 거의 도달 X
        if not new_url:
            new_url = (
                f"https://search.shopping.naver.com/search/all?"
                f"query={urllib.parse.quote(brand)}"
            )
            tier = "검색 페이지 (최후)"

        # 통계
        if "smartstore.naver.com" in new_url and "/main/products/" not in new_url:
            stats["resolved"] += 1
        elif "/main/products/" in new_url or "brand.naver.com/" in new_url:
            stats["product_fallback"] += 1
        else:
            stats["search_fallback"] += 1

        # DB 업데이트
        try:
            sb.table(TABLE_NAME).update(
                {"smartstore_url": new_url}
            ).eq("brand_name", brand).execute()
            print(f"  ✅ {tier}: {new_url[:70]}")
        except Exception as e:
            print(f"  ❌ DB 업데이트 실패: {e}")

        time.sleep(0.3)   # API rate limit 대비

    # 4) 최종 통계
    print("\n" + "=" * 60)
    print("완료!")
    print("=" * 60)
    print(f"  셀러 메인 URL 추출 성공: {stats['resolved']}개")
    print(f"  상품 페이지 fallback: {stats['product_fallback']}개")
    print(f"  검색 페이지 (최후 fallback): {stats['search_fallback']}개")
    print(f"  API 못 받음 (스킵): {stats['skipped']}개")
    print(f"  합계: {sum(stats.values())}개 / 대상 {len(bad_rows)}개")


if __name__ == "__main__":
    main()
