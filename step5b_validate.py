"""
PICK10 - Step 5b: popup URL 직접 추출 (1건 검증용)
=================================================================
이번 단계의 목적:
  - 자동화로 popup URL을 캐오는 부분은 잠시 보류
  - 사용자가 평소 크롬에서 직접 알려준 popup URL을 코드에 넣고
  - 거기서 사업자정보가 잘 추출되는지만 우선 확인
  - "추출 로직" 자체를 검증하는 단계

이게 잘 되면 → 6단계(CSV 저장)로 진행 가능.
다음 라운드에서 popup URL 자동 추출 + 로그인 우회는 다시 다듬어요.

실행 방법:
    python step5b_validate.py
=================================================================
"""

import os
import re
import sys

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


sys.stdout.reconfigure(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────
# 사용자가 평소 크롬에서 본 판매자정보 popup URL (시카슈어)
# 다른 셀러 검증할 땐 이 URL만 바꿔서 같은 코드 재사용 가능
# ─────────────────────────────────────────────────────────────────
POPUP_URL = (
    "https://shopping.naver.com/popup/seller-info/"
    "2sWE0dJnwtYmlpj7lohRo/profile"
    "?from=brandstore"
    "&prevUrl=https%3A%2F%2Fbrand.naver.com%2Fcicasure%2Fprofile%3Fcp%3D1"
)

# referer로 쓸 prev URL (popup이 referer 검사할 경우 대비)
PREV_URL = "https://brand.naver.com/cicasure/profile?cp=1"

DEBUG_DIR = "debug"
os.makedirs(DEBUG_DIR, exist_ok=True)


print("\n" + "=" * 60)
print("  📦 PICK10 / Step 5b — popup URL 직접 추출 (1건 검증)")
print("=" * 60)
print(f"  대상: {POPUP_URL[:80]}...\n")


with sync_playwright() as p:
    # 시스템 크롬 + 자동화 시그널 숨김 (Plan B 그대로)
    browser = p.chromium.launch(
        channel="chrome",
        headless=False,
        slow_mo=300,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-default-browser-check",
        ],
    )
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 900},
        locale="ko-KR",
        timezone_id="Asia/Seoul",
    )
    context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        window.chrome = window.chrome || { runtime: {}, loadTimes: function(){}, csi: function(){} };
        Object.defineProperty(navigator, 'languages', {get: () => ['ko-KR', 'ko', 'en-US', 'en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [{name:'Chrome PDF Plugin'}, {name:'Native Client'}]});
        """
    )

    page = context.new_page()

    try:
        # ── 자연스러운 흐름 만들기: 네이버 메인 → prevUrl → popup ──
        print("🌐 [1/3] 네이버 메인 방문 (쿠키 받기)...")
        page.goto("https://www.naver.com", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(1500)
        print("   ✅ 메인 OK\n")

        # prevUrl 먼저 방문 (popup의 자연스러운 referer 만들기)
        print(f"🌐 [2/3] prev URL 방문: {PREV_URL}")
        try:
            page.goto(PREV_URL, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)
            print(f"   페이지 URL: {page.url}")
            print("   ✅ prev URL OK\n")
        except Exception as e:
            print(f"   ⚠️ prev URL 실패 (무시하고 계속): {e}\n")

        # 핵심: popup URL 이동
        print(f"🌐 [3/3] popup URL 이동...")
        page.goto(POPUP_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)
        page.wait_for_timeout(2000)
        print(f"   페이지 URL : {page.url}\n")

        # ── 디버깅 파일 저장 ──
        html = page.content()
        html_path = f"{DEBUG_DIR}/popup_validate.html"
        png_path = f"{DEBUG_DIR}/popup_validate.png"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        page.screenshot(path=png_path, full_page=True)
        print(f"📁 디버깅 파일 저장:")
        print(f"   - HTML       : {html_path}")
        print(f"   - 스크린샷    : {png_path}\n")

        # ── 로그인/에러 페이지 감지 ──
        page_title = page.evaluate("document.title")
        if "nidlogin" in page.url or "로그인" in page_title:
            print("⚠️ popup URL이 로그인 페이지로 튕겼어요.")
            print("   → 다음 단계: persistent context로 1회 로그인 받는 길로 갈게요.")
            sys.exit(1)

        # ── 정보 추출 ──
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(separator="\n", strip=True)

        # 페이지 텍스트 미리보기 (디버깅 보조)
        print("─" * 60)
        print("📄 페이지 텍스트 미리보기 (앞 1500자)")
        print("─" * 60)
        print(text[:1500])
        print("─" * 60 + "\n")

        # 정규식 패턴 추출
        result = {}

        # 사업자등록번호 (XXX-XX-XXXXX)
        m = re.search(r"\d{3}-\d{2}-\d{5}", text)
        if m:
            result["사업자등록번호"] = m.group()

        # 이메일
        m = re.search(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text)
        if m:
            result["이메일"] = m.group()

        # 전화번호 (XX-XXXX-XXXX 또는 XXX-XXX-XXXX)
        m = re.search(r"\d{2,4}-\d{3,4}-\d{4}", text)
        if m:
            result["전화번호"] = m.group()

        # 통신판매업신고번호
        m = re.search(r"제\s*\d{4}-[\w가-힣]+-\d+\s*호", text)
        if m:
            result["통신판매업신고"] = m.group()

        # 라벨 기반 추출 (상호, 대표자, 주소)
        def find_after_label(labels):
            for label in labels:
                pattern = rf"{label}\s*[:：]?\s*([^\n]+)"
                match = re.search(pattern, text)
                if match:
                    return match.group(1).strip()[:120]
            return ""

        result["상호"] = find_after_label(
            ["상호명", "상호", "회사명", "법인명", "사업자명"]
        )
        result["대표자"] = find_after_label(["대표자명", "대표자", "대표"])
        result["사업장주소"] = find_after_label(
            ["사업장 소재지", "사업장주소", "주소", "소재지"]
        )

        # ── 결과 출력 ──
        print("─" * 60)
        print("📌 추출된 판매자정보")
        print("─" * 60)
        for key, value in result.items():
            mark = "✅" if value else "⚠️ (못찾음)"
            print(f"  {mark} {key:12s}: {value or '-'}")
        print("─" * 60)

        # 추출 성공 여부 평가
        success_count = sum(1 for v in result.values() if v)
        total = len(result)
        print(f"\n  📊 추출률: {success_count}/{total}")
        if success_count >= 5:
            print("  🎉 충분히 잘 추출됐어요! → 6단계(CSV 저장) 진행 가능")
        elif success_count >= 3:
            print("  💛 부분 성공. debug 파일 보고 라벨 수정해서 보강 가능")
        else:
            print("  ❌ 추출 거의 실패. 페이지 구조 다시 확인 필요")

    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        try:
            page.screenshot(path=f"{DEBUG_DIR}/error_validate.png", full_page=True)
        except Exception:
            pass

    finally:
        print("\n   (3초 후 브라우저 자동으로 닫힘...)")
        page.wait_for_timeout(3000)
        browser.close()


print("\n👉 결과 + debug 폴더 확인하시고 알려주세요.\n")
