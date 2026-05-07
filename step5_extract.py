"""
PICK10 - Step 5: 스마트스토어 판매자정보 추출
=================================================================
이번 단계가 하는 일:
  1) Step 4에서 받은 상품 URL을 Playwright로 열기
  2) 자동 redirect 후 실제 스토어 ID 알아내기
  3) {스토어ID}/policy 페이지로 이동 (판매자 의무 공시 페이지)
  4) HTML/스크린샷 저장 (디버깅용)
  5) 사업자등록번호, 이메일, 전화번호 등 자동 패턴 추출

실행 방법:
    cd "C:\\Users\\PC\\Documents\\Claude\\Projects\\셀픽 영업처 수집"
    venv\\Scripts\\activate
    python step5_extract.py

⚠️ 실행 시 자동화 전용 크롬 창이 자동으로 열려요. 놀라지 마세요!
=================================================================
"""

import os
import re
import sys
import urllib.parse
from datetime import datetime

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


# 한글 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────
# 입력: Step 4에서 찾은 상품 URL (지금은 하드코딩, 나중에 통합)
# ─────────────────────────────────────────────────────────────────
PRODUCT_URL = "https://smartstore.naver.com/main/products/12990153460"

# 디버깅용 파일 저장 폴더
DEBUG_DIR = "debug"
os.makedirs(DEBUG_DIR, exist_ok=True)

print("\n" + "=" * 60)
print("  📦 PICK10 / Step 5 — 판매자정보 추출")
print("=" * 60)
print(f"  대상 URL: {PRODUCT_URL}\n")


# ─────────────────────────────────────────────────────────────────
# Playwright 시작
# ─────────────────────────────────────────────────────────────────
with sync_playwright() as p:
    # ⭐ Plan B: channel="chrome" → 시스템에 깔린 진짜 크롬을 그대로 사용
    #    (Playwright 번들 Chromium은 봇으로 감지될 가능성 높음)
    browser = p.chromium.launch(
        channel="chrome",   # ← 핵심: 시스템 크롬 사용
        headless=False,
        slow_mo=300,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-dev-shm-usage",
            "--no-default-browser-check",
        ],
    )
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"   # 최신 버전으로 업데이트
        ),
        viewport={"width": 1280, "height": 900},
        locale="ko-KR",
        timezone_id="Asia/Seoul",
    )

    # 자동화 시그널 더 강하게 숨기기
    context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        window.chrome = window.chrome || { runtime: {}, loadTimes: function(){}, csi: function(){} };
        Object.defineProperty(navigator, 'languages', {get: () => ['ko-KR', 'ko', 'en-US', 'en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [{name:'Chrome PDF Plugin'}, {name:'Native Client'}]});
        Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
        // permissions
        const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
        if (originalQuery) {
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : originalQuery(parameters)
            );
        }
        """
    )

    page = context.new_page()

    # ── 네이버 메인 먼저 방문 (쿠키 받기 + 자연스러운 흐름) ──
    print("🌐 [0/3] 네이버 메인 방문 (쿠키 받기)...")
    try:
        page.goto("https://www.naver.com", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)

        # 사람처럼 마우스 살짝 움직이기 (자동화 감지 추가 회피)
        page.mouse.move(300, 200)
        page.mouse.move(600, 400)
        page.mouse.move(800, 300)
        page.wait_for_timeout(800)
        print("   ✅ 메인 방문 + 마우스 자연스러운 움직임\n")
    except Exception as e:
        print(f"   ⚠️ 메인 방문 실패 (무시하고 계속): {e}\n")

    try:
        # ─────────────────────────────────────────────────────────
        # 1) 상품 페이지 진입 → 진짜 storeId 캐내기
        #    (네이버 쇼핑 link는 /main/products/... 통합 URL이라
        #     page.url로는 진짜 storeId를 알 수 없음.
        #     페이지 안의 메타데이터나 내부 링크에서 추출)
        # ─────────────────────────────────────────────────────────
        print("🌐 [1/3] 상품 페이지 열기...")
        page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)
        print(f"   페이지 URL : {page.url}")

        # 진짜 storeId를 여러 방법으로 시도 (fallback)
        real_store_id = None
        method_used = None

        # 방법 0 (NEW): 로그인 페이지로 redirect된 경우, url 파라미터에 셀러 URL이 통째로 박혀있음
        # 예) https://nid.naver.com/nidlogin.login?url=https%3A%2F%2Fsmartstore.naver.com%2Fcicalab%2Fproducts%2F...
        if "nid.naver.com" in page.url:
            try:
                parsed = urllib.parse.urlparse(page.url)
                params = urllib.parse.parse_qs(parsed.query)
                if "url" in params:
                    target = params["url"][0]
                    m = re.search(r"smartstore\.naver\.com/([^/?#]+)", target)
                    if m and m.group(1) not in ("main", "search", "category"):
                        real_store_id = m.group(1)
                        method_used = "로그인 redirect URL의 url 파라미터"
            except Exception:
                pass

        # 방법 1: <meta property="og:url"> content
        if not real_store_id:
            try:
                og = page.locator('meta[property="og:url"]').first
                if og.count() > 0:
                    content = og.get_attribute("content") or ""
                    m = re.search(r"smartstore\.naver\.com/([^/?#]+)", content)
                    if m and m.group(1) not in ("main", "search", "category"):
                        real_store_id = m.group(1)
                        method_used = "og:url 메타태그"
            except Exception:
                pass

        # 방법 2: <link rel="canonical"> href
        if not real_store_id:
            try:
                canonical = page.locator('link[rel="canonical"]').first
                if canonical.count() > 0:
                    href = canonical.get_attribute("href") or ""
                    m = re.search(r"smartstore\.naver\.com/([^/?#]+)", href)
                    if m and m.group(1) not in ("main", "search", "category"):
                        real_store_id = m.group(1)
                        method_used = "canonical 링크"
            except Exception:
                pass

        # 방법 3: 페이지 안의 모든 a 태그 살펴보기
        if not real_store_id:
            try:
                hrefs = page.eval_on_selector_all(
                    "a[href]", "els => els.map(e => e.href)"
                )
                for href in hrefs:
                    m = re.match(r"https?://smartstore\.naver\.com/([^/?#]+)", href)
                    if m and m.group(1) not in ("main", "search", "category"):
                        real_store_id = m.group(1)
                        method_used = f"페이지 내 링크 ({href[:80]}...)"
                        break
            except Exception:
                pass

        # 디버깅: 상품 페이지 자체도 저장
        page.screenshot(path=f"{DEBUG_DIR}/product_page.png", full_page=True)
        with open(f"{DEBUG_DIR}/product_page.html", "w", encoding="utf-8") as f:
            f.write(page.content())

        if not real_store_id:
            print("❌ 세 가지 방법 다 시도했는데 진짜 storeId를 못 찾았어요.")
            print(f"   debug/product_page.html 와 product_page.png 를 확인해주세요.")
            sys.exit(1)

        store_id = real_store_id
        print(f"   ✅ 진짜 storeId 발견: '{store_id}' (방법: {method_used})\n")

        # ─────────────────────────────────────────────────────────
        # 2) 셀러 메인 페이지로 이동 → footer에 사업자정보 노출됨
        #    (모든 스마트스토어는 전자상거래법상 footer 의무 표시)
        # ─────────────────────────────────────────────────────────
        main_url = f"https://smartstore.naver.com/{store_id}"
        print(f"🌐 [2/3] 셀러 메인 페이지 이동: {main_url}")
        page.goto(main_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)

        # 페이지 끝까지 스크롤 → lazy load되는 footer 정보가 그려질 시간 줌
        print("   📜 페이지 끝까지 스크롤 (footer 노출)...")
        for _ in range(3):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(800)
        page.wait_for_timeout(1000)
        print("   ✅ 페이지 로드 완료\n")

        # 페이지 텍스트에 "페이지를 찾을 수가 없습니다" 가 있으면 셀러 자체가 비공개일 가능성
        body_text_check = page.evaluate("document.body.innerText")
        if "페이지를 찾을 수가 없습니다" in body_text_check or "페이지를 찾을 수 없" in body_text_check:
            print(f"   ⚠️ 셀러 메인 페이지도 없음. cicalab 외 다른 storeId 가능성.")

        # 디버깅용 셀러 메인 페이지 HTML / 스크린샷 저장
        seller_html = page.content()
        with open(f"{DEBUG_DIR}/seller_{store_id}.html", "w", encoding="utf-8") as f:
            f.write(seller_html)
        page.screenshot(path=f"{DEBUG_DIR}/seller_{store_id}.png", full_page=True)

        # ─────────────────────────────────────────────────────────
        # 2.5) 셀러 메인 페이지에서 popup seller-info URL 자동 추출
        #      패턴: shopping.naver.com/popup/seller-info/{popupId}/profile
        # ─────────────────────────────────────────────────────────
        print("   🔍 판매자정보 popup URL 검색 중...")
        popup_url = None

        # 방법 A: a 태그의 href 살펴보기
        try:
            hrefs = page.eval_on_selector_all(
                "a[href]", "els => els.map(e => e.href)"
            )
            for href in hrefs:
                if "shopping.naver.com/popup/seller-info" in href:
                    popup_url = href
                    print(f"   ✅ a 태그 href에서 발견")
                    break
        except Exception:
            pass

        # 방법 B: 페이지 HTML 전체에서 정규식 검색 (JS로 박힌 경우 대비)
        if not popup_url:
            m = re.search(
                r"https?://shopping\.naver\.com/popup/seller-info/[A-Za-z0-9_\-]+/profile[^\"'\s<>]*",
                seller_html,
            )
            if m:
                popup_url = m.group()
                print(f"   ✅ HTML 정규식 검색에서 발견")

        # 방법 C: popupId만 박혀 있을 수도 → 직접 URL 조립
        if not popup_url:
            m = re.search(r"['\"]([A-Za-z0-9_\-]{15,30})['\"][^>]*seller[\-_]?info", seller_html, re.I)
            if m:
                popup_id = m.group(1)
                popup_url = f"https://shopping.naver.com/popup/seller-info/{popup_id}/profile"
                print(f"   ✅ popupId 추출하여 URL 조립: {popup_id}")

        if not popup_url:
            print(f"   ❌ popup URL을 못 찾았어요.")
            print(f"      debug/seller_{store_id}.html 을 확인해서 popup URL 패턴 파악 필요")
            print(f"      (지금은 셀러 메인 페이지의 footer 텍스트로 정보 추출 시도)\n")
            html = seller_html
            html_path = f"{DEBUG_DIR}/seller_{store_id}.html"
            png_path = f"{DEBUG_DIR}/seller_{store_id}.png"
        else:
            print(f"   📍 popup URL: {popup_url[:100]}...\n")

            # ─────────────────────────────────────────────────────
            # 2.6) popup 페이지로 이동 + 정보 노출
            # ─────────────────────────────────────────────────────
            print(f"🌐 [2.5/3] popup 페이지 이동")
            page.goto(popup_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)
            page.wait_for_timeout(2000)
            print("   ✅ popup 페이지 로드 완료\n")

            # popup 페이지 HTML / 스크린샷 저장
            html = page.content()
            html_path = f"{DEBUG_DIR}/popup_{store_id}.html"
            png_path = f"{DEBUG_DIR}/popup_{store_id}.png"
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
            page.screenshot(path=png_path, full_page=True)

        print(f"   📁 디버깅 파일 저장:")
        print(f"      - HTML       : {html_path}")
        print(f"      - 스크린샷    : {png_path}\n")

        # ─────────────────────────────────────────────────────────
        # 3) HTML에서 판매자정보 자동 추출 시도
        # ─────────────────────────────────────────────────────────
        print("🔎 [3/3] 판매자정보 자동 추출 시도...\n")

        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(separator="\n", strip=True)

        # 정규식 기반 1차 추출 (안전한 패턴들만)
        result = {}

        # 사업자등록번호 (XXX-XX-XXXXX)
        m = re.search(r"\d{3}-\d{2}-\d{5}", text)
        if m:
            result["사업자등록번호"] = m.group()

        # 이메일
        m = re.search(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text)
        if m:
            result["이메일"] = m.group()

        # 전화번호 (XX-XXXX-XXXX 또는 XXX-XXX-XXXX 패턴)
        m = re.search(r"\d{2,4}-\d{3,4}-\d{4}", text)
        if m:
            result["전화번호"] = m.group()

        # 통신판매업신고번호
        m = re.search(r"제\s*\d{4}-[\w가-힣]+-\d+\s*호", text)
        if m:
            result["통신판매업신고"] = m.group()

        # 라벨 기반 추출 (상호, 대표자, 주소)
        # '상호 : 시카슈어' 같은 패턴을 찾기
        def find_after_label(labels: list) -> str:
            """주어진 라벨 뒤에 오는 값을 텍스트에서 찾아 반환"""
            for label in labels:
                # 라벨 뒤 콜론·공백·줄바꿈 후 다음 줄까지
                pattern = rf"{label}\s*[:：]?\s*([^\n]+)"
                match = re.search(pattern, text)
                if match:
                    val = match.group(1).strip()
                    # 너무 긴 건 잘림 처리 (한 줄 안에 여러 항목 들어간 경우)
                    return val[:100]
            return ""

        result["상호"] = find_after_label(["상호명", "상호", "회사명", "법인명"])
        result["대표자"] = find_after_label(["대표자명", "대표자", "대표"])
        result["사업장주소"] = find_after_label(["사업장 소재지", "사업장주소", "주소", "소재지"])

        # ─────────────────────────────────────────────────────────
        # 결과 출력
        # ─────────────────────────────────────────────────────────
        print("─" * 60)
        print("📌 추출된 판매자정보")
        print("─" * 60)
        if any(result.values()):
            for key, value in result.items():
                mark = "✅" if value else "⚠️ (못찾음)"
                print(f"  {mark} {key:12s}: {value or '-'}")
        else:
            print("  ⚠️ 정보를 하나도 못 찾았어요.")
            print(f"     → {png_path} 와 {html_path} 를 확인해서 페이지 구조 파악 필요")
        print("─" * 60)

        # ─────────────────────────────────────────────────────────
        # 디버깅 보조: 페이지 텍스트 앞부분 같이 출력
        # ─────────────────────────────────────────────────────────
        print("\n📄 페이지 텍스트 미리보기 (앞 800자)")
        print("─" * 60)
        print(text[:800])
        print("─" * 60)

    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        try:
            page.screenshot(path=f"{DEBUG_DIR}/error.png", full_page=True)
            print(f"   에러 스크린샷 저장: {DEBUG_DIR}/error.png")
        except Exception:
            pass

    finally:
        # 사용자가 결과를 볼 수 있게 잠깐 머문 후 닫기
        print("\n   (3초 후 브라우저 자동으로 닫힘...)")
        page.wait_for_timeout(3000)
        browser.close()

print("\n👉 끝났어요! 위 결과랑 debug 폴더 확인하시고 알려주세요.\n")
