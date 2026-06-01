"""
사업자 정보 자동 수집 모듈
=================================================================
신규 셀러 수집 시 자동으로 사업자 정보 수집:
  - 상호, 대표자, 사업자번호, 전화번호
  - 이메일 (다층 수집 — 강화됨)

⭐ 2026-05-26 업데이트:
  1. 주소 자동수집 제외 (정확도 미달 — 사용자 요청)
  2. 이메일 수집 정확도 강화:
     - 다양한 footer 패턴 추가 (cafe24/godo/imweb 등)
     - HTML 분리 패턴 인식 (<dt>E-mail</dt><dd>...</dd>)
     - 이메일 우선순위 점수 (customer/cs > info > webmaster)
     - 약관/개인정보 페이지 추가 fetch (사업자 정보 자주 노출)

데이터 소스 (4-tier, 모두 무료):
  Phase 1: 스마트스토어 사업자정보 페이지 스크래핑 (HTTP 429 대비)
  Phase 2: 공정위 통신판매사업자 DB API (상호·대표·전화)
  Phase 3: 공식 홈페이지 + 약관 페이지 (이메일 핵심 소스)
  Phase 4: Naver/Google 확장 검색 (이메일·대표·전화 보완)

각 소스에서 정보 수집 → 가장 신뢰도 높은 정보 선택 → 신뢰도 점수
=================================================================
"""

import os
import re
import time
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

# API 키 (없으면 해당 단계 skip)
PUBLIC_DATA_API_KEY = os.getenv("PUBLIC_DATA_API_KEY", "")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")   # Google Custom Search API 키
GOOGLE_CX = os.getenv("GOOGLE_CX", "")              # Google Search Engine ID

# 공통 HTTP 헤더 (스마트스토어/홈페이지 fetch용)
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

# ⭐ 2026-05-30: 무료 메일을 별도 분리.
#   - 블로그/카페/검색 결과의 무료 메일 → 차단 (엉뚱한 개인 메일 방지)
#   - 단, 사업자등록번호가 박힌 '진짜 공식 footer'의 무료 메일 → 허용
#     (코코핏 nanumcnc@naver.com 처럼 작은 회사는 네이버 메일을 영업 컨택으로 씀)
FREE_MAIL_DOMAINS = [
    "@naver.com", "@gmail.com", "@daum.net", "@hanmail.net",
    "@hotmail.com", "@yahoo.com", "@outlook.com", "@nate.com",
    "@kakao.com",
]

# 무관 이메일 패턴 (플랫폼/시스템 자체 메일) — ⭐ 항상 차단 (공식 footer라도 X)
# ⭐ 2026-05-26 강화: 채용/쇼핑 플랫폼 자체 메일 차단 (help@saramin.co.kr 등 오인 방지)
EMAIL_HARD_BLACKLIST = [
    # ─── 시스템·테스트용 ───
    "example", "noreply", "no-reply", "donotreply",
    "sentry.io", "wixpress.com", "intercom.io",

    # ⭐ ─── 채용 사이트 자체 메일 (사람인 help@... 같은 오인 차단) ───
    "@saramin.co.kr", "@jobkorea.co.kr", "@wanted.co.kr",
    "@incruit.com", "@worknet.go.kr", "@peoplenjob.com",
    "@jobplanet.co.kr", "@catch.co.kr", "@albamon.com",

    # ⭐ ─── 쇼핑·오픈마켓 자체 메일 ───
    "@coupang.com", "@coupangcorp.com",
    "@gmarket.co.kr", "@auction.co.kr", "@ebay.co.kr",
    "@11st.co.kr", "@interpark.com", "@ssg.com",
    "@lotteon.com", "@wemakeprice.com", "@tmon.co.kr",
    "@kurly.com", "@oliveyoung.co.kr",

    # ⭐ ─── 포털·플랫폼 자체 메일 ───
    "@navercorp.com", "@kakaocorp.com", "@kakaomobility.com",
    "@google.com", "@youtube.com", "@meta.com",
    "@instagram.com", "@facebook.com", "@line.me",

    # ⭐ ─── 결제/택배/공공 ───
    "@kcp.co.kr", "@inicis.com", "@danal.co.kr", "@nicepay.co.kr",
    "@cj.net", "@cjlogistics.com", "@hanjin.co.kr", "@lotteglogis.com",
    "@epost.go.kr",

    # ⭐ ─── 호스팅·솔루션 (cafe24/godo 등 자체 메일) ───
    "@cafe24corp.com", "@simplexi.com", "@godo.co.kr",
    "@imweb.me", "@nhnent.com", "@nhn.com",

    # ⭐ ─── 뉴스레터·이메일 마케팅·상담 SaaS (2026-05-30) ───
    # 코코핏이 스티비로 뉴스레터 발송 → footer의 support@stibee.com 오수집 차단
    # 이런 서비스의 support@ 메일은 브랜드 영업 컨택이 아님
    "@stibee.com",        # 스티비 (뉴스레터)
    "@mailchimp.com", "@sendgrid.net", "@sendgrid.com",
    "@mailerlite.com", "@getresponse.com", "@hubspot.com",
    "@amazonses.com", "@sendinblue.com", "@brevo.com",
    "@channel.io",        # 채널톡 (상담)
    "@zendesk.com", "@freshdesk.com", "@tawk.to",
    "@wix.com", "@squarespace.com", "@shopify.com",
]

# 하위 호환: 전체 블랙리스트 (무료메일 + 하드)
EMAIL_BLACKLIST_DOMAINS = FREE_MAIL_DOMAINS + EMAIL_HARD_BLACKLIST


# ─────────────────────────────────────────────────────────────────
# ⭐ 2026-05-30 추가: 헤드리스 브라우저 (SPA 사이트 렌더링)
# -----------------------------------------------------------------
# 문제: kokofit.kr 처럼 자바스크립트로 화면을 그리는 SPA 사이트는
#       requests.get()으로 받으면 빈 껍데기만 옴 → footer(전화·이메일) 못 읽음
#       → 엉뚱한 다른 사이트(cowave.kr)의 정보를 가져오는 오류 발생
# 해결: Playwright로 진짜 브라우저처럼 JS 실행 후 완성된 HTML을 읽는다.
#       (Playwright 미설치 시 빈 문자열 반환 → 기존 동작 그대로 유지)
#
# 설치(사용자 PC에서 1회):
#   pip install playwright
#   playwright install chromium
# ─────────────────────────────────────────────────────────────────
_BROWSER_CTX = {
    "playwright": None,
    "browser": None,
    "checked": False,      # _get_browser를 한 번이라도 시도했는지
    "available": False,    # 사용 가능 여부
}


def _get_browser():
    """Playwright 크롬 브라우저 싱글톤 반환 (한 번 켜서 재사용).

    미설치/실행실패 시 None 반환 + 1회만 안내 메시지 출력.
    """
    if _BROWSER_CTX["browser"] is not None:
        return _BROWSER_CTX["browser"]
    # 이미 시도했고 실패했으면 재시도 안 함 (매번 에러 출력 방지)
    if _BROWSER_CTX["checked"] and not _BROWSER_CTX["available"]:
        return None

    _BROWSER_CTX["checked"] = True
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("           ⚠ Playwright 미설치 — SPA(자바스크립트) 사이트는 렌더링 못 함")
        print("              설치:  pip install playwright")
        print("                     playwright install chromium")
        _BROWSER_CTX["available"] = False
        return None

    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        _BROWSER_CTX["playwright"] = pw
        _BROWSER_CTX["browser"] = browser
        _BROWSER_CTX["available"] = True
        print("           🌐 헤드리스 브라우저 준비 완료 (SPA 사이트 렌더링 가능)")
        return browser
    except Exception as e:
        print(f"           ⚠ 브라우저 실행 실패: {type(e).__name__}: {e}")
        print("              'playwright install chromium' 를 실행했는지 확인하세요")
        _BROWSER_CTX["available"] = False
        return None


def render_html_with_browser(url: str, timeout: int = 15) -> str:
    """SPA 사이트를 실제 브라우저로 렌더링해 완성된 HTML 반환.

    requests로는 자바스크립트 실행 전 빈 껍데기만 받기 때문에,
    코코핏(kokofit.kr) 같은 SPA 사이트는 이 함수로 진짜 footer까지 읽는다.

    Playwright 미설치/실패 시 빈 문자열 반환 → 호출부에서 기존 동작으로 fallback.
    """
    browser = _get_browser()
    if browser is None:
        return ""

    page = None
    try:
        page = browser.new_page(user_agent=HTTP_HEADERS["User-Agent"])
        # 1) DOM 로드까지 대기 (networkidle은 광고/추적 스크립트 때문에
        #    영영 안 끝나는 사이트가 많아 사용 안 함 → 속도 핵심)
        page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
        # 2) 지연 렌더링 footer 대비 짧게만 대기 (1.2초)
        page.wait_for_timeout(1200)
        html = page.content()
        return html or ""
    except Exception as e:
        print(f"           [디버그] 브라우저 렌더링 실패 ({url[:50]}): {type(e).__name__}")
        return ""
    finally:
        if page is not None:
            try:
                page.close()
            except Exception:
                pass


def close_browser():
    """프로그램 종료 시 브라우저 정리 (수집 스크립트 마지막에 호출 권장)."""
    try:
        if _BROWSER_CTX["browser"] is not None:
            _BROWSER_CTX["browser"].close()
        if _BROWSER_CTX["playwright"] is not None:
            _BROWSER_CTX["playwright"].stop()
    except Exception:
        pass
    finally:
        _BROWSER_CTX["browser"] = None
        _BROWSER_CTX["playwright"] = None
        _BROWSER_CTX["available"] = False
        _BROWSER_CTX["checked"] = False


def is_valid_email(email: str) -> bool:
    """이메일 유효성 추가 검증 (해시값·UUID 등 가짜 이메일 차단)"""
    if not email or "@" not in email:
        return False
    local, _, domain = email.partition("@")
    # 로컬 파트 검증
    if len(local) < 2 or len(local) > 40:
        return False
    # 16진수 해시값 같은 거 제외 (32자 이상 + 모두 16진수)
    if len(local) >= 16 and all(c in "0123456789abcdefABCDEF" for c in local):
        return False
    # 도메인 검증
    if "." not in domain or len(domain) < 4:
        return False
    # 일반 도메인 확장자 확인
    valid_tlds = (
        ".com", ".kr", ".net", ".co.kr", ".or.kr", ".org",
        ".io", ".biz", ".info", ".store", ".shop", ".me",
    )
    if not any(domain.lower().endswith(tld) for tld in valid_tlds):
        return False
    return True

# 시·도 패턴 (참고용 — 주소는 자동 수집에서 제외됨, 2026-05-26)
ADDRESS_REGION = (
    r"(?:서울|부산|대구|인천|광주|대전|울산|세종|"
    r"경기|강원|충북|충남|전북|전남|경북|경남|제주)"
)


# ─────────────────────────────────────────────────────────────────
# ⭐ 이메일 우선순위 점수 (2026-05-26 추가)
# 한 페이지에서 여러 이메일이 나올 때 가장 적합한 것 선택
# ─────────────────────────────────────────────────────────────────
def score_email(email: str) -> int:
    """이메일을 사업자 대표 이메일로 적합한 정도 점수.

    높을수록 좋음:
      - customer/cs/contact/help 계열 → 100점 (사업자 대표 메일)
      - info/sales/order 계열 → 70점
      - 일반 (브랜드명 도메인) → 50점
      - admin/webmaster/master → 10점 (시스템 관리 메일, 우선순위 낮음)
    """
    if not email or "@" not in email:
        return 0
    local = email.lower().split("@")[0]

    # 사용자 응대용 메일 (가장 좋음)
    PRIME_KEYWORDS = ["customer", "cs", "contact", "help",
                      "support", "hello", "service"]
    if any(kw in local for kw in PRIME_KEYWORDS):
        return 100

    # 영업/주문/문의용
    SECONDARY_KEYWORDS = ["info", "sales", "order", "biz",
                          "marketing", "ceo", "office"]
    if any(kw in local for kw in SECONDARY_KEYWORDS):
        return 70

    # 시스템 관리용 (낮은 우선순위)
    LOW_KEYWORDS = ["admin", "webmaster", "master", "root",
                    "postmaster", "noreply", "no-reply"]
    if any(kw in local for kw in LOW_KEYWORDS):
        return 10

    # 그 외 일반 메일 (브랜드명, 사람 이름 등)
    return 50


def _is_domain_matching_brand(email: str, brand_name: str) -> bool:
    """이메일 도메인이 브랜드명과 매칭되는지 검사.

    ⭐ 2026-05-26 추가: "판옵티콘" 브랜드 + "support@poolix.io" 같은
    무관 도메인 매칭 방지.

    매칭 기준:
      - 한글 브랜드 → 영문 변환 추측 (가장 흔한 패턴)
      - 도메인 일부에 브랜드명 키워드 포함
      - 한글 브랜드명의 일부 음절 포함
    """
    if not email or "@" not in email or not brand_name:
        return False
    domain = email.split("@")[1].lower()
    # 최상위 도메인 제거: "plagentra.kr" → "plagentra"
    domain_main = domain.split(".")[0]

    brand_clean = brand_name.lower().strip()
    # "주식회사" 등 회사 접두사 제거
    for prefix in ["주식회사", "(주)", "주)", "유한회사", "(유)",
                   "유)", "(재)", "재단법인", "협동조합"]:
        brand_clean = brand_clean.replace(prefix, "")
    brand_clean = brand_clean.strip().replace(" ", "")

    if not brand_clean:
        return False

    # 한글 브랜드 → 도메인에 일부 음절 매칭 시도 (한글 → 영문 직접 비교 어려움)
    # 도메인이 브랜드명 키워드 포함하면 OK (영문 도메인 케이스)
    if brand_clean in domain_main or domain_main in brand_clean:
        return True

    # 한글 음절 1개라도 포함 (영문 도메인은 보통 한글 X → False)
    # 단, 영문 브랜드명 케이스 처리
    if all(ord(c) < 128 for c in brand_clean):   # 영문 브랜드
        # 영문 브랜드와 도메인 부분 일치 (3자 이상)
        if len(brand_clean) >= 3 and brand_clean[:3] in domain_main:
            return True

    return False


def pick_best_email(candidates: list, brand_name: str = "",
                    allow_free_mail: bool = False) -> str:
    """후보 이메일 리스트에서 가장 적합한 것 선택 (점수 기준).

    - 플랫폼/시스템 자체 메일(EMAIL_HARD_BLACKLIST) → 항상 제외
    - 무료 메일(@naver/@gmail 등) → 기본 제외, 단 allow_free_mail=True면 허용
      (⭐ 2026-05-30: 사업자번호 있는 진짜 공식 footer의 네이버 메일 살리기용)
    - is_valid_email() 통과만
    - score_email() 점수 최고값 반환
    - ⭐ brand_name 제공 시 도메인-브랜드 매칭 가산점 (판옵티콘↔poolix 같은 무관 메일 차단)
    """
    if not candidates:
        return ""
    valid = []
    for email in candidates:
        if not email or "@" not in email:
            continue
        email_lower = email.lower()
        # 플랫폼/시스템 자체 메일 → 항상 차단
        if any(skip in email_lower for skip in EMAIL_HARD_BLACKLIST):
            continue
        # 무료 메일 → allow_free_mail 아니면 차단
        if not allow_free_mail and any(skip in email_lower for skip in FREE_MAIL_DOMAINS):
            continue
        if not is_valid_email(email):
            continue

        score = score_email(email)
        # ⭐ 2026-05-26: 사이트 검증(메타+body)이 1차 안전망 → 이메일 매칭은 보조
        # 도메인 매칭 안 되어도, 사이트 검증을 통과한 페이지의 이메일이면 신뢰
        if brand_name:
            if _is_domain_matching_brand(email, brand_name):
                score += 50   # 매칭되면 강력 보너스
            else:
                score -= 30   # ⭐ 미매칭 감산 완화 (-60 → -30)

        valid.append((score, email))
    if not valid:
        return ""
    # 점수 내림차순 정렬, 동점이면 짧은 이메일 우선 (덜 generic)
    valid.sort(key=lambda x: (-x[0], len(x[1])))

    # ⭐ 임계값 완화 (50 → 30)
    # 한글 브랜드 ↔ 영문 도메인 매칭 안 되는 케이스 통과
    # 잘못된 사이트 차단은 페이지 메타+body 검증이 담당 (find_business_info_from_homepage)
    best_score, best_email = valid[0]
    if brand_name and best_score < 30:
        # 30점 미만 = webmaster 같은 시스템 메일 + 미매칭만 차단
        return ""

    return best_email


def extract_emails_from_html(html: str) -> list:
    """HTML/텍스트에서 모든 이메일 후보 추출.

    ⭐ HTML 분리 케이스 처리:
      <dt>E-mail</dt><dd>customer@brand.com</dd>
      HTML 태그 제거 후 → 'E-mail customer@brand.com' 형태로 매칭 가능
    """
    if not html:
        return []
    # HTML 태그 제거
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;|&amp;", " ", text)
    text = re.sub(r"\s+", " ", text)

    # 모든 이메일 후보 (중복 제거)
    emails = re.findall(r"\b([\w._%+-]+@[\w.-]+\.[a-zA-Z]{2,})\b", text)
    seen = set()
    unique = []
    for e in emails:
        if e.lower() not in seen:
            seen.add(e.lower())
            unique.append(e)
    return unique


# ─────────────────────────────────────────────────────────────────
# Phase 1: 스마트스토어 사업자정보 페이지 스크래핑
# ─────────────────────────────────────────────────────────────────
def fetch_smartstore_business_info(store_url: str) -> dict:
    """스마트스토어 셀러 페이지에서 사업자정보 자동 추출.

    ⭐ 2026-05-26 강화:
      1. 다양한 User-Agent 시도 (HTTP 429 우회)
      2. 모바일 페이지(m.smartstore.naver.com) fallback
      3. __NEXT_DATA__ JSON 추출 (SPA 데이터 직접 파싱)
      4. 정규식 fallback (footer 패턴)

    반환:
        {
            "company_name": "(주)프라젠트라",
            "ceo": "홍길동",
            "business_number": "123-45-67890",
            "phone": "02-1234-5678",
            "email": "info@prajentra.com" (있을 시),
        }
    """
    if not store_url or "smartstore.naver.com" not in store_url:
        print(f"           [디버그] 스마트스토어 URL X: {store_url[:60]}")
        return {}

    info = {}

    # ⭐ 다양한 User-Agent (HTTP 429 우회 강화)
    USER_AGENTS = [
        # Chrome on macOS
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        # Chrome on Windows (default)
        HTTP_HEADERS["User-Agent"],
        # iPhone Safari (모바일)
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    ]

    try:
        # 1. 메인 페이지 fetch (UA 변형으로 retry)
        print(f"           [디버그] 스마트스토어 fetch 시작: {store_url[:60]}")
        html = ""
        response = None
        for idx, ua in enumerate(USER_AGENTS):
            headers = {**HTTP_HEADERS, "User-Agent": ua}
            try:
                response = requests.get(store_url, headers=headers, timeout=15)
                print(f"           [디버그] UA {idx+1} HTTP: {response.status_code}")
                if response.status_code == 200:
                    html = response.text
                    break
                elif response.status_code == 429:
                    time.sleep(0.5)
                    continue
            except Exception as e:
                print(f"           [디버그] UA {idx+1} 실패: {e}")
                continue

        # 메인 모두 실패 → 모바일 URL fallback
        if not html:
            mobile_url = store_url.replace(
                "smartstore.naver.com", "m.smartstore.naver.com"
            )
            print(f"           [디버그] 모바일 fallback: {mobile_url[:60]}")
            try:
                response = requests.get(
                    mobile_url,
                    headers={**HTTP_HEADERS, "User-Agent": USER_AGENTS[2]},
                    timeout=15,
                )
                if response.status_code == 200:
                    html = response.text
            except Exception as e:
                print(f"           [디버그] 모바일 fallback 실패: {e}")

        if not html:
            print(f"           [디버그] 스마트스토어 fetch 완전 실패")
            return {}

        print(f"           [디버그] HTML 길이: {len(html)}자")

        # ⭐ 2. __NEXT_DATA__ JSON 추출 시도 (스마트스토어 SPA 핵심 데이터)
        # 성공 시 100% 정확한 사업자 정보 (스마트스토어가 직접 제공)
        next_data_match = re.search(
            r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(\{.+?\})</script>',
            html, re.DOTALL,
        )
        if next_data_match:
            try:
                import json
                data = json.loads(next_data_match.group(1))

                # 깊은 dict 탐색 — 가능한 sellerInfo / businessInfo 키 모두 찾기
                def _find_seller_info(obj, depth=0):
                    if depth > 10:   # 무한 재귀 방지
                        return None
                    if isinstance(obj, dict):
                        # 직접 매칭 키
                        for key in ("sellerInfo", "businessInfo", "storeInfo",
                                    "seller", "store", "shopInfo"):
                            if key in obj and isinstance(obj[key], dict):
                                # 실제 사업자 정보 포함 확인
                                if any(k in obj[key] for k in
                                       ("companyName", "businessRegistrationNumber",
                                        "representativeName", "ceoName")):
                                    return obj[key]
                        # 재귀 탐색
                        for v in obj.values():
                            result = _find_seller_info(v, depth+1)
                            if result:
                                return result
                    elif isinstance(obj, list):
                        for item in obj:
                            result = _find_seller_info(item, depth+1)
                            if result:
                                return result
                    return None

                seller = _find_seller_info(data)
                if seller:
                    info = {
                        "company_name": (
                            seller.get("companyName") or
                            seller.get("businessName") or ""
                        ),
                        "ceo": (
                            seller.get("representativeName") or
                            seller.get("ceoName") or
                            seller.get("representative") or ""
                        ),
                        "business_number": (
                            seller.get("businessRegistrationNumber") or
                            seller.get("brno") or
                            seller.get("businessNumber") or ""
                        ),
                        "phone": (
                            seller.get("phoneNumber") or
                            seller.get("contactPhone") or
                            seller.get("telno") or ""
                        ),
                        "email": (
                            seller.get("email") or
                            seller.get("emailAddress") or
                            seller.get("contactEmail") or ""
                        ),
                        # ⭐ 2026-05-26: 외부 공식몰 URL 추출 (판매자 직접 등록)
                        # 가장 신뢰도 높은 공식 홈페이지 힌트
                        "website_url": (
                            seller.get("websiteUrl") or
                            seller.get("homepageUrl") or
                            seller.get("homepage") or
                            seller.get("website") or
                            seller.get("officialSiteUrl") or
                            seller.get("companyUrl") or ""
                        ),
                    }
                    info = {k: v for k, v in info.items() if v}
                    if info:
                        print(f"           ✅ __NEXT_DATA__ 추출 성공: {list(info.keys())}")
                        if info.get("website_url"):
                            print(f"               🎯 외부 공식몰 URL 발견: {info['website_url']}")
                        return info
            except Exception as e:
                print(f"           [디버그] __NEXT_DATA__ 파싱 실패: {e}")

        # 2. 사업자정보 페이지/popup URL 추출 시도
        # 스마트스토어는 사업자정보를 별도 페이지 또는 popup으로 제공
        # 일반적 URL 패턴: /sellerInfo, /companyInfo, /business-info 등
        info_html = html   # 기본은 메인 페이지

        info_url_match = re.search(
            r'href=["\']([^"\']*(?:sellerInfo|business[Ii]nfo|companyInfo|/info)[^"\']*)["\']',
            html,
        )
        if info_url_match:
            info_url = info_url_match.group(1)
            if not info_url.startswith("http"):
                if info_url.startswith("/"):
                    info_url = "https://smartstore.naver.com" + info_url
                else:
                    info_url = store_url.rstrip("/") + "/" + info_url
            print(f"           [디버그] 사업자정보 페이지 발견: {info_url[:60]}")
            try:
                info_resp = requests.get(info_url, headers=HTTP_HEADERS, timeout=10)
                print(f"           [디버그] 사업자정보 페이지 HTTP: {info_resp.status_code}")
                if info_resp.status_code == 200:
                    info_html = info_resp.text
            except Exception as e:
                print(f"           [디버그] 사업자정보 페이지 fetch 실패: {e}")
        else:
            print(f"           [디버그] 사업자정보 페이지 URL 찾기 실패 (메인 페이지에서 추출 시도)")

        # 3. 정보 추출 (정규식)
        # 상호 (회사명)
        m = re.search(
            r"(?:상호|회사명|법인명)[\s:：]+([^\n<\r]{2,40}?)(?=\s*(?:대표|사업자|소재지|<|\n))",
            info_html,
        )
        if m:
            info["company_name"] = m.group(1).strip()

        # 대표자
        m = re.search(
            r"(?:대표자|대표이사|대표)[\s:：]+([^\n<\r]{2,30}?)(?=\s*(?:사업자|상호|소재지|<|\n))",
            info_html,
        )
        if m:
            info["ceo"] = m.group(1).strip()

        # 사업자번호 (NNN-NN-NNNNN 형태)
        m = re.search(r"\b(\d{3}-\d{2}-\d{5})\b", info_html)
        if m:
            info["business_number"] = m.group(1)

        # 전화번호 (다양한 형태)
        phone_patterns = [
            r"\b(0\d{1,2}-\d{3,4}-\d{4})\b",   # 02-1234-5678
            r"\b(\d{2,3}-\d{3,4}-\d{4})\b",    # 1234-5678
        ]
        for pattern in phone_patterns:
            m = re.search(pattern, info_html)
            if m:
                info["phone"] = m.group(1)
                break

        # 주소: 자동수집 제외 (2026-05-26 — 정확도 미달, 사용자 요청)

        # 이메일 — ⭐ 우선순위 점수로 가장 적합한 메일 선택
        email_candidates = extract_emails_from_html(info_html)
        best_email = pick_best_email(email_candidates)
        if best_email:
            info["email"] = best_email

        # 디버그: 추출 결과 요약
        print(f"           [디버그] 정규식 매칭 결과:")
        print(f"               상호: {info.get('company_name', '(매칭 X)')[:30]}")
        print(f"               대표: {info.get('ceo', '(매칭 X)')[:20]}")
        print(f"               사업자번호: {info.get('business_number', '(매칭 X)')}")
        print(f"               전화: {info.get('phone', '(매칭 X)')}")
        print(f"               이메일: {info.get('email', '(매칭 X)')}")
        print(f"               전체 이메일 후보 수: {len(email_candidates)}")

    except Exception as e:
        print(f"           [디버그] 스마트스토어 fetch 예외: {type(e).__name__}: {e}")
        pass

    return info


# ─────────────────────────────────────────────────────────────────
# Phase 2: 공정거래위원회 통신판매사업자 DB 조회
# ─────────────────────────────────────────────────────────────────
def fetch_ftc_telecom_seller_info(
    business_number: str = "",
    company_name: str = "",
) -> dict:
    """공정위 통신판매사업자 등록상세 API 조회.

    API: 공정거래위원회_통신판매사업자 등록상세 제공 서비스
    Base URL: https://apis.data.go.kr/1130000/MllBsDtl_3Service

    사업자번호 우선, 없으면 상호명으로 조회.

    응답 데이터 (일반):
      - bzmnNm: 상호
      - rprFnm: 대표자성명
      - brno: 사업자등록번호
      - bsadr: 사업장소재지
      - prmmiMnno: 통신판매업번호
      - telno: 전화번호
      - emlAddr: 전자우편
    """
    if not PUBLIC_DATA_API_KEY:
        print(f"           [디버그] 공정위 API 키 없음 (skip)")
        return {}   # API 키 없으면 skip

    if not business_number and not company_name:
        print(f"           [디버그] 사업자번호/상호 없음 (skip)")
        return {}

    info = {}

    try:
        # ⭐ 2026-05-26: 등록현황 API로 전환 (등록상세 → 등록현황)
        # End Point: https://apis.data.go.kr/1130000/MllBs_2Service
        # 메서드:
        #   /getMllBsBiznoInfo_2  — 사업자번호로 검색 (정확)
        #   /getMllBsCoNmInfo_2   — 상호명으로 검색 ⭐ (스마트스토어 차단 시 핵심)
        # 일일 트래픽: 10,000회
        base_url = "https://apis.data.go.kr/1130000/MllBs_2Service"

        # 검색 메서드 결정 — 사업자번호 우선, 없으면 상호명
        if business_number:
            api_url = f"{base_url}/getMllBsBiznoInfo_2"
            params = {
                "serviceKey": PUBLIC_DATA_API_KEY,
                "pageNo": "1",
                "numOfRows": "10",
                "resultType": "json",
                "brno": business_number.replace("-", ""),
            }
            search_type = "사업자번호"
        else:
            # ⭐ 상호명으로 검색 (사업자번호 없을 때)
            api_url = f"{base_url}/getMllBsCoNmInfo_2"
            params = {
                "serviceKey": PUBLIC_DATA_API_KEY,
                "pageNo": "1",
                "numOfRows": "10",
                "resultType": "json",
                "bzmnNm": company_name,
            }
            search_type = "상호명"

        print(f"           [디버그] 공정위 API 호출 ({search_type}): {api_url}")
        print(f"           [디버그] 검색 조건: 사업자번호={business_number}, 상호명={company_name}")

        response = requests.get(api_url, params=params, timeout=15)
        print(f"           [디버그] 공정위 API 응답: HTTP {response.status_code}")
        if response.status_code != 200:
            print(f"           [디버그] 응답 본문 일부: {response.text[:200]}")
            return {}

        # JSON 응답 시도 (실패하면 XML 가능성)
        try:
            data = response.json()
            print(f"           [디버그] JSON 파싱 성공. 구조: {list(data.keys())[:5]}")
        except Exception as e:
            print(f"           [디버그] JSON 파싱 실패 (XML 가능성): {e}")
            print(f"           [디버그] 응답 본문 일부: {response.text[:200]}")
            return {}

        # 응답 구조 — 공공데이터포털 API 표준 형태들 모두 시도
        items = (
            data.get("response", {}).get("body", {}).get("items", {}).get("item", [])
            or data.get("response", {}).get("body", {}).get("items", [])
            or data.get("items", [])
            or data.get("data", [])
        )

        # items가 dict 1개일 수도, list일 수도 있음
        if isinstance(items, dict):
            items = [items]

        if items:
            first = items[0]
            # ⭐ 디버그: 실제 응답의 모든 필드 출력 (정확한 필드명 확인용)
            print(f"           [디버그] 공정위 응답 첫 번째 record의 모든 필드:")
            for k, v in first.items():
                print(f"               {k}: {v}")
            # 공정위 API 표준 필드 + 한글 필드 모두 시도 (응답 구조 대비)
            # ⭐ 2026-05-26: 주소(bsadr) 자동수집 제외 — 정확도 미달
            info = {
                "company_name": (
                    first.get("bzmnNm", "")
                    or first.get("상호", "")
                    or first.get("companyName", "")
                ),
                "ceo": (
                    first.get("rprFnm", "")
                    or first.get("대표자성명", "")
                    or first.get("ceoNm", "")
                ),
                "business_number": (
                    first.get("brno", "")
                    or first.get("사업자등록번호", "")
                    or first.get("bizrno", "")
                ),
                "phone": (
                    first.get("telno", "")
                    or first.get("전화번호", "")
                    or first.get("phoneNo", "")
                ),
                "email": (
                    first.get("emlAddr", "")
                    or first.get("전자우편", "")
                    or first.get("email", "")
                ),
            }
            # 빈 값 제거
            info = {k: v for k, v in info.items() if v}
            print(f"           [디버그] 공정위 추출 결과: {len(info)}개 항목 — {list(info.keys())}")
        else:
            print(f"           [디버그] 공정위 API 데이터 없음. 응답 일부: {str(data)[:300]}")

    except Exception as e:
        print(f"           [디버그] 공정위 API 예외: {type(e).__name__}: {e}")
        pass

    return info


# ─────────────────────────────────────────────────────────────────
# Phase 3: 이메일 다층 수집
# ─────────────────────────────────────────────────────────────────
def find_email_from_homepage(brand_name: str, hint_url: str = "") -> Optional[str]:
    """공식 홈페이지에서 이메일 자동 추출 (기존 호환)."""
    info = find_business_info_from_homepage(brand_name, hint_url=hint_url)
    return info.get("email") if info else None


def _brand_match_tokens(brand_name: str) -> list:
    """브랜드명에서 매칭 가능한 토큰들 추출.

    ⭐ 2026-05-26 신규: "아토피엔 더순해" → ["아토피엔 더순해", "아토피엔더순해", "더순해"]
    공백 포함 브랜드는 핵심 단어만으로도 매칭 가능하게.
    """
    if not brand_name:
        return []
    brand_clean = brand_name.lower().strip()
    for prefix in ["주식회사", "(주)", "주)", "유한회사", "(유)"]:
        brand_clean = brand_clean.replace(prefix, "")
    brand_clean = brand_clean.strip()

    if not brand_clean:
        return []

    tokens = [brand_clean]   # 전체

    # 공백 제거 버전 ("아토피엔 더순해" → "아토피엔더순해")
    no_space = brand_clean.replace(" ", "")
    if no_space != brand_clean and no_space:
        tokens.append(no_space)

    # 공백으로 분리된 각 단어 (3자 이상만, false positive 방지)
    # 길이순으로 정렬 (긴 단어가 핵심 브랜드명일 가능성)
    words = sorted(
        [w for w in brand_clean.split() if len(w) >= 3],
        key=len, reverse=True,
    )
    for w in words[:2]:   # 상위 2개만 (false positive 방지)
        if w not in tokens:
            tokens.append(w)

    return tokens


def _verify_homepage_match(html: str, brand_name: str) -> int:
    """페이지 메타 정보(title/og:site_name/meta keywords)로 브랜드 매칭 점수 계산.

    ⭐ 2026-05-26 신규: 공식몰 후보 URL 자동 검증
      - title에 브랜드명 포함 → +50
      - og:site_name에 브랜드명 포함 → +30
      - meta keywords/description에 브랜드명 → +20
      - body text에 브랜드명 등장 → +15~40
      - ⭐ 토큰 매칭 ("아토피엔 더순해" → "더순해"만으로도 매칭) → 점수 차등

    임계값 25점 이상 = 공식몰일 확률 높음
    """
    if not html or not brand_name:
        return 0

    tokens = _brand_match_tokens(brand_name)
    if not tokens:
        return 0

    score = 0
    # tokens[0] = 전체 브랜드명, tokens[1] = 공백제거, tokens[2+] = 핵심 단어들

    def _match_score(text: str, full_pts: int) -> int:
        """텍스트에서 토큰 매칭 → 우선순위 점수 반환.

        - 전체 브랜드명 매칭 → full_pts
        - 공백제거 매칭 → full_pts - 5
        - 핵심 단어 매칭 → full_pts // 2 (절반)
        """
        text_lower = text.lower()
        # 우선순위: 전체 → 공백제거 → 핵심 단어
        for idx, token in enumerate(tokens):
            if token in text_lower:
                if idx == 0:
                    return full_pts
                elif idx == 1:
                    return max(0, full_pts - 5)
                else:
                    return full_pts // 2   # 핵심 단어만 매칭 → 절반 점수
        return 0

    # ⭐ 메타 영역과 body 영역 점수 분리 — 메타 0점이면 body는 보조용으로만
    meta_score = 0
    body_score = 0

    # 1. <title>
    title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if title_match:
        meta_score += _match_score(title_match.group(1), 50)

    # 2. og:site_name
    og_site_match = re.search(
        r'<meta[^>]*property=["\']og:site_name["\'][^>]*content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if og_site_match:
        meta_score += _match_score(og_site_match.group(1), 30)

    # 3. og:title
    og_title_match = re.search(
        r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if og_title_match:
        meta_score += _match_score(og_title_match.group(1), 20)

    # 4. meta keywords
    keywords_match = re.search(
        r'<meta[^>]*name=["\']keywords["\'][^>]*content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if keywords_match:
        meta_score += _match_score(keywords_match.group(1), 20)

    # 5. meta description
    desc_match = re.search(
        r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if desc_match:
        meta_score += _match_score(desc_match.group(1), 15)

    # ⭐ 6. body text (처음 30KB) — 보조 점수
    body_text = re.sub(r"<[^>]+>", " ", html[:30000]).lower()
    body_text = re.sub(r"\s+", " ", body_text)

    for idx, token in enumerate(tokens):
        if token in body_text:
            occ = body_text.count(token)
            base = 40 if idx == 0 else (35 if idx == 1 else 20)
            if occ >= 5:
                body_score = base
            elif occ >= 2:
                body_score = base - 10
            else:
                body_score = max(5, base - 20)
            break   # 가장 높은 우선순위 토큰만 점수

    # ⭐ 7. footer 신호 (공식몰만 가지는 표지) — 2026-05-26 추가
    # 영문 title/SPA 사이트도 공식몰이면 footer에 사업자정보 표시 의무 (전자상거래법)
    # 리뷰/큐레이션 사이트는 이 신호 없음 → 자동 구분
    footer_signal_score = 0
    footer_patterns = [
        # 사업자등록번호 라벨
        (r"사업자\s*등록\s*번호|사업자\s*번호|business\s*license", 15),
        # 통신판매업 신고
        (r"통신판매업|통신판매신고|통신판매\s*신고번호", 15),
        # 사업자번호 실제 패턴 (XXX-XX-XXXXX)
        (r"\b\d{3}-?\d{2}-?\d{5}\b", 15),
        # 대표자 표기 (footer 형식)
        (r"대표[자]?\s*[:\s]\s*[가-힣]{2,4}|ceo\s*[:\s]\s*[a-z가-힣]{2,}", 10),
        # 약관/개인정보 페이지 링크
        (r"개인정보처리방침|이용약관|terms\s*of\s*(?:use|service)|privacy\s*policy", 10),
        # 결제/배송 (커머스 사이트 표지)
        (r"고객센터|customer\s*service|cs\s*center", 10),
    ]
    for pattern, pts in footer_patterns:
        if re.search(pattern, body_text, re.IGNORECASE):
            footer_signal_score += pts

    # ⭐ 종합 점수 계산
    # 메타 0점 + footer 신호 약함 → 리뷰/큐레이션 사이트 (차단)
    # 메타 0점 + footer 신호 강함 → 영문 title 공식몰 (통과)
    # 메타 매칭 + footer 신호 → 진짜 공식몰 (확실 통과)
    if meta_score == 0:
        # 메타 없으면 footer 신호로 판단 (body는 보조)
        # footer 신호 30점+ 있으면 진짜 공식몰일 확률 높음
        if footer_signal_score >= 30:
            return meta_score + body_score + footer_signal_score   # 통과 가능
        else:
            # footer 신호 약함 → 리뷰/큐레이션 사이트 가능성 → body 깎음
            return (body_score // 4) + footer_signal_score

    # 메타 매칭 있으면 종합 점수
    return meta_score + body_score + footer_signal_score


def _brand_presence_score(html: str, brand_name: str) -> int:
    """페이지에 '브랜드명 자체'가 얼마나 분명히 등장하는지만 점수화.

    ⭐ 2026-05-30 신규: 신뢰도 게이트 전용.
    _verify_homepage_match는 사업자번호/고객센터 같은 '일반 footer 신호'까지
    더해서, 브랜드와 무관한 아무 쇼핑몰도 높은 점수가 나옴(예: gileduzon).
    그래서 '진짜 이 브랜드의 사이트인가' 판단에는 footer 신호를 빼고
    제목/메타/본문에 브랜드명이 실제로 있는지만 본다.

    점수: 제목 +50 / og:site_name +30 / og:title +20 / keywords +20 /
          description +15 / body 등장 +5~40 (가장 높은 항목 기준 합산)
    """
    if not html or not brand_name:
        return 0
    tokens = _brand_match_tokens(brand_name)
    if not tokens:
        return 0

    def _match_score(text: str, full_pts: int) -> int:
        text_lower = text.lower()
        for idx, token in enumerate(tokens):
            if token in text_lower:
                if idx == 0:
                    return full_pts
                elif idx == 1:
                    return max(0, full_pts - 5)
                else:
                    return full_pts // 2
        return 0

    score = 0
    title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if title_match:
        score += _match_score(title_match.group(1), 50)
    og_site = re.search(
        r'<meta[^>]*property=["\']og:site_name["\'][^>]*content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if og_site:
        score += _match_score(og_site.group(1), 30)
    og_title = re.search(
        r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if og_title:
        score += _match_score(og_title.group(1), 20)
    kw = re.search(
        r'<meta[^>]*name=["\']keywords["\'][^>]*content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if kw:
        score += _match_score(kw.group(1), 20)
    desc = re.search(
        r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if desc:
        score += _match_score(desc.group(1), 15)

    # body 본문에 브랜드명 등장
    body_text = re.sub(r"<[^>]+>", " ", html[:30000]).lower()
    body_text = re.sub(r"\s+", " ", body_text)
    for idx, token in enumerate(tokens):
        if token in body_text:
            occ = body_text.count(token)
            base = 40 if idx == 0 else (35 if idx == 1 else 20)
            if occ >= 5:
                score += base
            elif occ >= 2:
                score += base - 10
            else:
                score += max(5, base - 20)
            break

    return score


def _url_domain_matches_brand(url: str, brand_name: str) -> bool:
    """후보 홈페이지 URL의 도메인이 브랜드명과 일치하는지 검사.

    ⭐ 2026-05-30: 영문 도메인(agazzang.co.kr) ↔ 영문 브랜드명(agazzang) 매칭.
    브랜드가 영문으로 저장됐는데 사이트 내용은 한글("아가짱")이라
    본문 브랜드일치 점수가 0이 되는 케이스 구제 (도메인이 곧 강한 신호).
    """
    if not url or not brand_name:
        return False
    try:
        from urllib.parse import urlparse
        target = url if url.startswith("http") else "http://" + url
        netloc = urlparse(target).netloc.lower()
    except Exception:
        return False
    netloc = netloc.replace("www.", "")
    domain_main = netloc.split(".")[0] if netloc else ""
    if not domain_main or len(domain_main) < 3:
        return False

    brand_clean = brand_name.lower().strip()
    for prefix in ["주식회사", "(주)", "주)", "유한회사", "(유)", "유)"]:
        brand_clean = brand_clean.replace(prefix, "")
    brand_clean = brand_clean.strip().replace(" ", "")
    if not brand_clean or len(brand_clean) < 3:
        return False

    # 도메인과 브랜드명 부분 일치 (영문 브랜드 ↔ 영문 도메인)
    if brand_clean in domain_main or domain_main in brand_clean:
        return True
    return False


def find_business_info_from_homepage(brand_name: str, hint_url: str = "") -> dict:
    """공식 홈페이지에서 사업자 정보 종합 추출.

    ⭐ 2026-05-26 강화 (3차) — 이메일 수집 정확도 ↑:
      이전 문제:
        - 베리맘(theverymom.com) 같은 영문 도메인 공식 사이트 못 찾음
        - cafe24 <dt>E-mail</dt><dd>customer@</dd> HTML 분리 패턴 누락
        - webmaster@ 같은 시스템 메일이 customer@보다 먼저 매칭
      개선:
        - 검색 키워드 6개로 확장 (mall/store/공식몰/이메일 포함)
        - 후보 URL 8개로 확대 (Naver web + shopping)
        - 약관/이용안내/개인정보 페이지 추가 fetch (사업자정보 노출)
        - extract_emails_from_html() + pick_best_email() 사용
        - 주소 자동수집 제외 (사용자 요청)

    반환:
      {"email": "...", "ceo": "...", "phone": "..."}   ← 주소 제외
    """
    if not brand_name or not NAVER_CLIENT_ID:
        return {}

    info = {}

    try:
        # ⭐ 다양한 검색 키워드 (공식 홈페이지 찾을 확률 ↑)
        # mall/store/공식 + 영문도 시도 (영문 도메인 사이트 잡기 위함)
        # ⭐ 2026-05-26: 브랜드명 단독 검색 추가 (SEO 1위 = 공식몰일 확률 높음)
        # 메타 검증으로 잘못된 사이트는 자동 차단되므로 안전
        search_queries = [
            f"{brand_name}",                # ⭐ 브랜드명 단독 (가장 자연스러운 검색)
            f"{brand_name} 공식 홈페이지",
            f"{brand_name} 공식몰",
            f"{brand_name} 쇼핑몰",
            f"{brand_name} 공식 사이트",
            f"{brand_name} 이메일",
            f"{brand_name} 고객센터",   # footer 이메일 잡기 좋은 키워드
            f"{brand_name} cs",          # 영문 CS 페이지
        ]

        # 무관 사이트 도메인 (Naver/Google 둘 다 사용)
        SKIP_DOMAINS = [
            "smartstore.naver.com", "blog.naver.com",
            "cafe.naver.com", "shopping.naver.com",
            "post.naver.com", "search.naver.com",
            "map.naver.com", "image.naver.com",
            "kakao.com", "tistory.com", "youtube.com",
            "instagram.com", "facebook.com", "twitter.com",
            "wikipedia.org", "namu.wiki",
            "saramin.co.kr", "jobkorea.co.kr", "wanted.co.kr",
            "coupang.com", "11st.co.kr", "gmarket.co.kr",
            "auction.co.kr", "ssg.com", "lotteon.com",
        ]

        def _is_valid_homepage(url: str) -> bool:
            """후보 URL이 공식 홈페이지로 적합한지 검사."""
            if not url or not url.startswith("http"):
                return False
            return not any(skip in url.lower() for skip in SKIP_DOMAINS)

        candidate_urls = []

        # ─── 1단계: 네이버 웹 검색 ───
        naver_api = "https://openapi.naver.com/v1/search/webkr.json"
        naver_headers = {
            "X-Naver-Client-Id": NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
        }
        for query in search_queries:
            try:
                response = requests.get(
                    naver_api, headers=naver_headers,
                    params={"query": query, "display": 5}, timeout=10,
                )
                if response.status_code == 200:
                    items = response.json().get("items", [])
                    for item in items[:5]:
                        url = item.get("link", "")
                        if _is_valid_homepage(url) and url not in candidate_urls:
                            candidate_urls.append(url)
                time.sleep(0.1)
            except Exception:
                continue

        # ⭐ ─── 2단계: Google 검색으로 영문 도메인 보강 (방안 핵심) ───
        # 한글 브랜드명 → 영문 도메인 케이스 (프라젠트라 → plagentra.kr) 잡기
        # Google이 네이버보다 영문 도메인 매칭 훨씬 잘 함
        if GOOGLE_API_KEY and GOOGLE_CX:
            # ⭐ 브랜드명 단독 추가 (Google SEO 강함, 공식몰 1위 노출)
            google_queries = [
                f"{brand_name}",                # ⭐ 브랜드명 단독
                f"{brand_name} 공식몰",
                f"{brand_name} 공식 사이트",
                f"{brand_name} cs 고객센터",   # footer/CS 페이지 직접 노출
            ]
            for gquery in google_queries:
                try:
                    g_resp = requests.get(
                        "https://www.googleapis.com/customsearch/v1",
                        params={
                            "key": GOOGLE_API_KEY,
                            "cx": GOOGLE_CX,
                            "q": gquery,
                            "num": 5,
                        },
                        timeout=10,
                    )
                    if g_resp.status_code == 200:
                        for item in g_resp.json().get("items", []):
                            url = item.get("link", "")
                            if _is_valid_homepage(url) and url not in candidate_urls:
                                candidate_urls.append(url)
                    time.sleep(0.15)
                except Exception:
                    continue

        # ⭐ 스마트스토어 판매자 등록 외부 URL 있으면 최우선 후보로 (100% 신뢰)
        if hint_url and hint_url.startswith("http"):
            if hint_url not in candidate_urls:
                candidate_urls.insert(0, hint_url)
                print(f"           🎯 hint_url 최우선 후보 추가: {hint_url}")

        print(f"           [디버그] 후보 공식 홈페이지 URL: {len(candidate_urls)}개 "
              f"(hint+Naver+Google 통합)")

        # ⭐ 상위 8개 사이트 시도 (이전 5개 → 8개)
        # 메타 검증 점수 30점 이상 사이트만 사용 (잘못된 사이트 자동 차단)
        all_email_candidates = []   # 사이트별 후보 모아서 마지막에 베스트 선택

        # ⭐ 2026-05-30: 브라우저 렌더링은 느리므로 셀러당 최대 3곳까지만 허용.
        #    공식몰은 보통 검색 상위에 있어 3곳이면 충분. (시간 초과 방지)
        #    list로 감싼 이유: 중첩 함수 _fetch_with_retry에서 값 변경하기 위함.
        render_budget = [3]

        # ⭐ 2026-05-30: 브랜드 일치 최고 점수 추적 (신뢰도 게이트용)
        #    best_meta_score: footer 신호 포함 (참고/로그용)
        #    best_brand_score: 순수 브랜드명 일치만 (게이트 판단용 — gileduzon 오수집 차단)
        best_meta_score = 0
        best_brand_score = 0

        for item_url in candidate_urls[:8]:
            url = item_url
            try:
                # 메인 페이지 + 다양한 쇼핑몰 솔루션의 사업자 정보 페이지
                # ⭐ 2026-05-26 강화: cafe24/godo/imweb/makeshop/sixshop 모두 지원
                base_url = url.rstrip("/")
                pages_to_try = [
                    url,
                    # ─── 공통 (대부분 솔루션) ───
                    f"{base_url}/contact",
                    f"{base_url}/about",
                    f"{base_url}/company",
                    f"{base_url}/info",
                    f"{base_url}/cs",
                    f"{base_url}/agreement",
                    f"{base_url}/privacy",
                    f"{base_url}/terms",
                    f"{base_url}/index.html",
                    # ─── cafe24 표준 경로 ───
                    f"{base_url}/shopinfo/company.html",
                    f"{base_url}/shopinfo/guide.html",
                    f"{base_url}/shopinfo/agreement.html",
                    f"{base_url}/shopinfo/privacy.html",
                    f"{base_url}/order/order_pop_terms.html",
                    # ─── godo (고도몰) 표준 경로 ───
                    f"{base_url}/shop/info.php",
                    f"{base_url}/shop/proc/agreement.php",
                    f"{base_url}/shop/proc/privacy.php",
                    # ─── makeshop 표준 경로 ───
                    f"{base_url}/shopinfo.html",
                    f"{base_url}/page/agreement.html",
                    f"{base_url}/page/privacy.html",
                    # ─── imweb 표준 경로 ───
                    f"{base_url}/pages/about-us",
                    f"{base_url}/pages/contact",
                    f"{base_url}/policy/terms",
                    f"{base_url}/policy/privacy",
                    # ─── sixshop / Shopify 등 ───
                    f"{base_url}/pages/about",
                    f"{base_url}/pages/contact-us",
                    f"{base_url}/policies/terms-of-service",
                    f"{base_url}/policies/privacy-policy",
                    # ─── 한글 경로 (간혹) ───
                    f"{base_url}/이용약관",
                    f"{base_url}/회사소개",
                ]

                combined_text = ""
                main_text = ""
                # ⭐ 메타 검증 — 메인 페이지 fetch 후 점수 매김
                # hint_url은 검증 skip (스마트스토어 판매자 직접 등록 → 100% 신뢰)
                meta_verified = (item_url == hint_url)

                # ⭐ 2026-05-26: User-Agent 다양화 (SPA/봇차단 우회)
                BROWSER_UAS = [
                    HTTP_HEADERS["User-Agent"],   # Chrome Windows
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
                ]

                def _fetch_with_retry(url: str, timeout: int = 8) -> str:
                    """다양한 User-Agent로 fetch 시도.

                    ⭐ 2026-05-30: SPA(자바스크립트) 사이트 대응 추가.
                    requests 결과가 '빈 껍데기'(태그 제거 후 보이는 글자 < 300자)면
                    헤드리스 브라우저로 실제 렌더링한 HTML을 가져온다 (코코핏 케이스).
                    """
                    html = ""
                    for ua in BROWSER_UAS:
                        try:
                            headers = {**HTTP_HEADERS, "User-Agent": ua}
                            resp = requests.get(url, headers=headers, timeout=timeout)
                            if resp.status_code == 200 and len(resp.text) > 500:
                                html = resp.text
                                break
                        except Exception:
                            continue

                    # ⭐ SPA 감지: 태그 제거 후 보이는 글자가 너무 적으면 JS 렌더링 사이트
                    #    단, 렌더링 예산(셀러당 3회)이 남아있을 때만 (시간 초과 방지)
                    visible_len = (
                        len(re.sub(r"<[^>]+>", " ", html).strip()) if html else 0
                    )
                    if visible_len < 300 and render_budget[0] > 0:
                        render_budget[0] -= 1
                        rendered = render_html_with_browser(url, timeout=12)
                        if rendered:
                            rendered_visible = len(
                                re.sub(r"<[^>]+>", " ", rendered).strip()
                            )
                            if rendered_visible > visible_len:
                                print(f"               🌐 SPA 감지 → 브라우저 렌더링 "
                                      f"({rendered_visible}자 확보, 남은 예산 {render_budget[0]})")
                                return rendered
                    return html

                for idx, page_url in enumerate(pages_to_try):
                    try:
                        # ⭐ 메인 페이지는 retry 강화 (SPA/봇차단 우회)
                        if idx == 0:
                            page_html = _fetch_with_retry(page_url, timeout=10)
                            status_ok = bool(page_html)
                        else:
                            try:
                                page = requests.get(page_url, headers=HTTP_HEADERS, timeout=8)
                                page_html = page.text if page.status_code == 200 else ""
                                status_ok = bool(page_html)
                            except Exception:
                                page_html = ""
                                status_ok = False

                        if status_ok:
                            # 메인 페이지에서 메타 검증
                            if idx == 0 and not meta_verified:
                                meta_score = _verify_homepage_match(page_html, brand_name)
                                print(f"               [메타검증] {page_url[:50]} → {meta_score}점")
                                if meta_score < 25:
                                    print(f"               ⚠ 메타+body 점수 미달 (<25) → 이 사이트 skip")
                                    break   # 점수 미달 → 이 후보 URL 자체를 skip
                                meta_verified = True
                                # ⭐ 신뢰도 게이트용 점수 기록
                                if meta_score > best_meta_score:
                                    best_meta_score = meta_score
                                # ⭐ footer 신호 제외, 순수 브랜드명 일치 점수
                                brand_score = _brand_presence_score(page_html, brand_name)
                                # ⭐ 2026-05-30: 도메인이 브랜드명과 일치하면 강한 신호
                                #   (영문 도메인 agazzang.co.kr ↔ 영문 브랜드명 agazzang).
                                #   한글 사이트라 본문 매칭이 0이어도 도메인으로 신뢰 인정.
                                if _url_domain_matches_brand(page_url, brand_name):
                                    brand_score += 60
                                    print(f"               [도메인일치] {page_url[:40]} "
                                          f"↔ {brand_name} (+60)")
                                if brand_score > best_brand_score:
                                    best_brand_score = brand_score
                                print(f"               [브랜드일치] {brand_score}점 "
                                      f"(이 점수로 신뢰 판단)")

                            combined_text += page_html + "\n"
                            if idx == 0:
                                main_text = page_html
                                # 메인 페이지에서 contact/about/agreement 링크 추가 발견
                                for link_match in re.finditer(
                                    r'href=["\']([^"\']*(?:contact|about|company|info|cs|footer|agreement|privacy|guide|shopinfo)[^"\']*\.(?:html?|php|asp))["\']',
                                    page_html, re.IGNORECASE,
                                ):
                                    sub_link = link_match.group(1)
                                    if sub_link.startswith("//"):
                                        sub_link = "https:" + sub_link
                                    elif not sub_link.startswith("http"):
                                        sub_link = base_url + ("/" + sub_link.lstrip("/"))
                                    if sub_link not in pages_to_try:
                                        try:
                                            sub_page = requests.get(sub_link, headers=HTTP_HEADERS, timeout=6)
                                            if sub_page.status_code == 200:
                                                combined_text += sub_page.text + "\n"
                                        except Exception:
                                            pass
                        time.sleep(0.05)
                    except Exception:
                        continue

                if not combined_text:
                    continue

                # ⭐ HTML 분리 케이스 처리 위해 태그 제거 + 공백 정규화
                text_only = re.sub(r"<[^>]+>", " ", combined_text)
                text_only = re.sub(r"&nbsp;|&amp;", " ", text_only)
                text_only = re.sub(r"\s+", " ", text_only)

                # ─── CEO 패턴 (footer) ───
                # ⭐ extract_ceo_from_text()와 동일 blacklist 사용 (통일)
                if not info.get("ceo"):
                    m = re.search(
                        r"(?:CEO|대표(?:이사|자|자명|자성명)?)\s*[:\s]?\s*([가-힣]{2,4})(?=\s|[<,.\)])",
                        text_only, re.IGNORECASE,
                    )
                    if m:
                        name = m.group(1).strip()
                        # 빠른 검증: 함수 호출로 blacklist 일관 적용
                        verified = extract_ceo_from_text(f"대표 {name}")
                        if verified:
                            info["ceo"] = verified

                # ─── 전화 패턴 (footer) — ⭐ 2026-05-26 우선순위 점수화 ───
                # 모든 후보 추출 → 점수 기반 최적 선택
                #   - "대표전화", "고객센터" 강라벨 +30
                #   - 1588/1599/1899 대표번호 +20
                #   - FAX/팩스 가까이 있으면 -50 (FAX 자동 차단)
                if not info.get("phone"):
                    phone_candidates = []
                    label_re = (
                        r"(CALL|TEL|TELEPHONE|전화|문의|연락처|"
                        r"고객\s*센터|고객\s*만족\s*센터|상담\s*센터|"
                        r"대표\s*전화|대표\s*번호|cs|customer)"
                    )
                    num_re = (
                        r"(1[5-9]\d{2}[-.\s]?\d{4}"              # 대표번호 1588-7601
                        r"|0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}"  # 일반 02-1234-5678
                        r"|\d{2,4}[-.\s]?\d{3,4}[-.\s]?\d{4})"  # 일반 패턴
                    )
                    full_pattern = f"{label_re}[\\s\\.\\-:전화번호문의]{{0,15}}{num_re}"

                    for m in re.finditer(full_pattern, text_only, re.IGNORECASE):
                        label = m.group(1).lower()
                        num = m.group(2)
                        score = 50   # 기본
                        # 강라벨 가산
                        if "대표" in label:
                            score += 30
                        if "고객" in label or "센터" in label:
                            score += 30
                        # 대표번호(1588/1599/1899 등) 가산
                        if re.match(r"1[5-9]\d{2}", num.replace("-", "").replace(" ", "").replace(".", "")):
                            score += 20
                        # FAX/팩스 근처 검사 (앞 30글자) → 감산
                        context_start = max(0, m.start() - 30)
                        context = text_only[context_start:m.end()].lower()
                        if "fax" in context or "팩스" in context:
                            score -= 80   # 강력 차단
                        # 정리
                        num_clean = re.sub(r"[.\s]+", "-", num.strip())
                        phone_candidates.append((score, num_clean))

                    if phone_candidates:
                        # 점수 내림차순 + 0점 이상만 선택
                        phone_candidates.sort(key=lambda x: -x[0])
                        best_score, best_phone = phone_candidates[0]
                        if best_score > 0:
                            info["phone"] = best_phone
                            print(f"               [전화선택] {best_phone} (점수 {best_score}, "
                                  f"후보 {len(phone_candidates)}개)")

                # ─── 사업자번호 패턴 (footer) ───
                if not info.get("business_number"):
                    m = re.search(
                        r"(?:BUSINESS\s*(?:LICENSE|NO)?|사업자(?:등록)?번호)\s*[:\s]?\s*"
                        r"(\d{3}-?\d{2}-?\d{5})",
                        text_only, re.IGNORECASE,
                    )
                    if m:
                        info["business_number"] = m.group(1).strip()
                        # ⭐ 2026-05-30: 사업자번호가 박힌 = 진짜 공식 footer.
                        #   이 페이지의 이메일은 무료메일(@naver 등)이라도 공식 컨택일
                        #   확률 높음 (코코핏 nanumcnc@naver.com 케이스).
                        #   1순위 브랜드/시스템 메일 → 없으면 무료메일까지 허용.
                        if not info.get("email"):
                            page_cands = extract_emails_from_html(text_only)
                            pe = pick_best_email(page_cands, brand_name=brand_name)
                            if not pe:
                                pe = pick_best_email(
                                    page_cands, brand_name="",
                                    allow_free_mail=True,
                                )
                            if pe:
                                info["email"] = pe
                                print(f"               [공식footer 이메일] {pe} "
                                      f"(사업자번호 동일 페이지)")

                # ─── 이메일 추출 — ⭐ 모든 후보 모아서 전체 사이트에서 베스트 선택 ───
                # 1) footer 패턴 (E-mail customer@...)
                # ⭐ 구분자 확장 (2026-05-26): "E-MAIL.", "EMAIL,", "이메일·" 등 모두 매칭
                #   기존: \s*[:\s]?\s*  → 콜론/공백만
                #   변경: \s*[:.\,\-·│|]?\s*  → 점/콤마/하이픈/중점/세로선 등 footer 구분자
                #   추가 라벨: "메일", "Mail", "@" 단독, "고객센터 메일"
                for m in re.finditer(
                    r"(?:E[-\s]?mail|이메일|EMAIL|MAIL|메일주소|문의메일|문의\s*메일|"
                    r"고객\s*메일|상담\s*메일|메일)\s*[:.\,\-·│|]?\s*"
                    r"([\w._%+-]+@[\w.-]+\.[a-zA-Z]{2,})",
                    text_only, re.IGNORECASE,
                ):
                    all_email_candidates.append(m.group(1).strip())

                # 2) 페이지 전체 일반 매칭 (footer 못 찾았을 때 대비)
                page_emails = extract_emails_from_html(combined_text)
                all_email_candidates.extend(page_emails)

                # 충분히 모았으면 다음 사이트로 이동 X (1개 사이트로 충분)
                if info.get("ceo") and info.get("phone") and all_email_candidates:
                    break

            except Exception:
                continue

        # ⭐ 모든 사이트 이메일 후보 중 베스트 선택 (customer/cs > info > webmaster)
        # ⭐ brand_name 전달 → 도메인-브랜드 매칭 검증
        if all_email_candidates and not info.get("email"):
            best = pick_best_email(all_email_candidates, brand_name=brand_name)
            if best:
                info["email"] = best
                print(f"           [디버그] 이메일 후보 {len(all_email_candidates)}개 중 베스트 선택: {best}")
            else:
                print(f"           [디버그] 이메일 후보 {len(all_email_candidates)}개 있었으나 브랜드 매칭 X → 미선택")

        # ⭐ 2026-05-30: 신뢰도 게이트용 — 순수 브랜드 일치 점수를 결과에 포함
        if info:
            info["meta_score"] = best_meta_score      # 참고용 (footer 포함)
            info["brand_score"] = best_brand_score    # ⭐ 게이트 판단용
            print(f"           [디버그] 공식 홈페이지 추출: {list(info.keys())} "
                  f"(브랜드일치 {best_brand_score}점 / 메타 {best_meta_score}점)")

    except Exception as e:
        print(f"           [디버그] 공식 홈페이지 검색 예외: {e}")

    return info


def search_email_via_naver(brand_name: str) -> Optional[str]:
    """Naver 검색 결과(블로그/카페/웹)에서 이메일 추출.

    ⭐ 2026-05-26 개선:
      - 검색 소스 확대 (web → web + blog + cafearticle)
      - 모든 후보 모아서 pick_best_email()로 최적 선택
      - 검색 키워드 다양화 (제휴/입점/연락처)
    """
    if not brand_name or not NAVER_CLIENT_ID:
        return None

    queries = [
        f"{brand_name} 이메일 문의",
        f"{brand_name} 대표 메일",
        f"{brand_name} 사업 제휴",
        f"{brand_name} 입점 문의",
        f"{brand_name} 고객센터 이메일",
    ]

    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }

    all_candidates = []
    for query in queries:
        for source in ["webkr", "blog", "cafearticle"]:
            try:
                api_url = f"https://openapi.naver.com/v1/search/{source}.json"
                params = {"query": query, "display": 5}
                response = requests.get(api_url, headers=headers, params=params, timeout=10)
                if response.status_code != 200:
                    continue
                items = response.json().get("items", [])
                for item in items:
                    text = (
                        re.sub(r"<[^>]+>", " ", item.get("title", "")) + " " +
                        re.sub(r"<[^>]+>", " ", item.get("description", ""))
                    )
                    emails = re.findall(
                        r"\b([\w._%+-]+@[\w.-]+\.[a-zA-Z]{2,})\b",
                        text,
                    )
                    all_candidates.extend(emails)
                time.sleep(0.05)
            except Exception:
                continue

    # ⭐ 베스트 이메일 선택 (customer/cs/info 우선) + 도메인-브랜드 매칭 검증
    best = pick_best_email(all_candidates, brand_name=brand_name)
    return best if best else None


# ─────────────────────────────────────────────────────────────────
# Phase 4: 확장 검색 (Naver + Google) — 사업자번호 기반 정보 추출
# 공정위 API에 없는 정보 (대표/전화/주소) 자동 수집 시도
# ─────────────────────────────────────────────────────────────────
def extract_ceo_from_text(text: str) -> str:
    """텍스트에서 대표자 이름 추출.

    패턴:
      - "대표 OOO", "대표이사 OOO", "대표자 OOO"
      - "OOO 대표", "OOO 대표이사"

    ⭐ 2026-05-26: blacklist 대폭 확장 — 회사 정보 페이지 라벨 단어 제거
      예: "대표 / 설립일 / 사업자번호" 같은 라벨 나열 텍스트에서
          "설립일"이 사람 이름으로 오인되던 문제 해결
    """
    if not text:
        return ""
    # ⭐ 일반 단어 + 회사 정보 라벨 모두 제외 (false positive 방지)
    blacklist = {
        # 기존
        "대표", "이사", "본사", "회사", "사장", "정보", "소개",
        "직원", "팀장", "기자", "사람", "고객", "주식", "회원",
        # ⭐ 회사 정보 페이지 라벨 (자주 매칭되는 false positive)
        "설립일", "설립", "등록일", "등록", "생년월일", "생년",
        "주소", "전화", "전번", "팩스", "사업자", "사업장",
        "이메일", "메일", "상호", "법인", "법인명",
        "업종", "업태", "종목", "분류", "코드",
        "통신", "판매", "신고", "번호", "센터", "센타",
        "담당", "담당자", "관리", "관리자", "운영자",
        "성명", "성함", "이름", "직책", "직위",
        "기업", "조직", "단체", "기관", "협회",
        "위치", "지역", "지점", "지사", "본점",
        "은행", "계좌", "결제",
    }

    patterns = [
        r"대표(?:이사|자)?[:\s]+([가-힣]{2,4})(?=\s|[,.\)\]<>])",
        r"\b([가-힣]{2,4})\s+대표(?:이사)?\b",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for name in matches:
            name = name.strip()
            if name not in blacklist and len(name) >= 2:
                return name
    return ""


def extract_phone_from_text(text: str) -> str:
    """텍스트에서 전화번호 추출 (다양한 형식)."""
    if not text:
        return ""
    patterns = [
        r"\b(0\d{1,2}-\d{3,4}-\d{4})\b",     # 02-1234-5678
        r"\b(0\d{1,2}\.\d{3,4}\.\d{4})\b",   # 02.1234.5678
        r"\b(1\d{3}-\d{4})\b",                # 1588-XXXX
        r"\b(01\d-\d{3,4}-\d{4})\b",          # 010-1234-5678
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(1)
    return ""


def extract_address_from_text(text: str) -> str:
    """텍스트에서 주소 추출 — 엄격한 패턴 매칭.

    실제 주소 패턴:
      - "서울특별시 강남구 OO동/OO로"
      - "경기도 성남시 분당구 OO로"
      - "OO도 OO시 OO구 OO동"

    뉴스 기사·일반 텍스트 false positive 차단:
      - 시·도 + 시/구/군 + 동/로/길 패턴 필수
      - 블랙리스트 키워드 (회의·뉴스·기사 등) 포함되면 제외
    """
    if not text:
        return ""

    # 주소가 아닌 텍스트 패턴 (뉴스·기사·이벤트 등)
    ADDRESS_BLACKLIST = [
        "회의", "정상회담", "뉴스", "기사", "행사", "이벤트",
        "방문", "출장", "축제", "박람회", "포럼", "정책",
        "통상", "외교", "정부", "발표", "발생",
    ]

    # 엄격한 주소 패턴 (시·도 + 시/구/군 + 동/로 필수)
    address_patterns = [
        # 특별시·광역시: "서울특별시 강남구 OO동"
        rf"({ADDRESS_REGION}(?:특별시|광역시|특별자치시|특별자치도)\s+"
        rf"[가-힣]+(?:시|구|군)\s+[가-힣]+(?:동|로|길|읍|면|리)[^\n\r<>]{{0,60}})",
        # 도: "경기도 성남시 분당구 ..."
        rf"({ADDRESS_REGION}도\s+[가-힣]+(?:시|군)\s+"
        rf"[가-힣]+(?:동|로|길|읍|면|리|구)[^\n\r<>]{{0,60}})",
        # 도로명: "서울 강남구 역삼로 123"
        rf"({ADDRESS_REGION}\s+[가-힣]+(?:구|시|군)\s+"
        rf"[가-힣]+로\s*\d+(?:-\d+)?(?:\s*\d+층)?)",
    ]

    candidates = []
    for pattern in address_patterns:
        for m in re.finditer(pattern, text):
            address = m.group(0).strip()
            address = re.sub(r"<[^>]+>", "", address)
            address = " ".join(address.split())

            # 길이 검증
            if len(address) < 10:
                continue

            # 블랙리스트 키워드 포함 시 제외
            if any(bad in address for bad in ADDRESS_BLACKLIST):
                continue

            candidates.append(address[:100])

    # 가장 첫 번째 매칭 반환 (없으면 빈 값)
    return candidates[0] if candidates else ""


def search_business_via_naver_extended(brand_name: str, business_number: str = "") -> dict:
    """Naver 검색 강화 — 사업자번호 기반 다양한 키워드로 정보 추출.

    검색 키워드:
      - "{회사명} 대표"
      - "{회사명} 본사 연락처"
      - "{사업자번호}" (직접 검색)
      - "{회사명} 주소"
    """
    if not NAVER_CLIENT_ID:
        return {}

    info = {}

    # 다양한 검색 키워드 조합
    queries = [
        f"{brand_name} 대표이사",
        f"{brand_name} 대표 연락처",
        f"{brand_name} 본사 주소",
    ]
    if business_number:
        queries.append(business_number.replace("-", ""))   # 사업자번호 직접 검색
        queries.append(f"{brand_name} {business_number}")

    all_text = ""
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }

    for query in queries[:5]:   # 최대 5개 쿼리 (API 부담 ↓)
        for source in ["blog", "cafearticle", "webkr"]:
            try:
                api_url = f"https://openapi.naver.com/v1/search/{source}.json"
                resp = requests.get(
                    api_url,
                    headers=headers,
                    params={"query": query, "display": 5},
                    timeout=10,
                )
                if resp.status_code == 200:
                    items = resp.json().get("items", [])
                    for item in items:
                        text = (
                            re.sub(r"<[^>]+>", "", item.get("title", "")) + " " +
                            re.sub(r"<[^>]+>", "", item.get("description", ""))
                        )
                        all_text += text + "\n"
                time.sleep(0.1)
            except Exception:
                continue

    # 패턴 추출 — 주소 제외 (2026-05-26)
    info["ceo"] = extract_ceo_from_text(all_text)
    info["phone"] = extract_phone_from_text(all_text)

    # 이메일도 추출 (블로그·카페에 종종 노출) — 도메인-브랜드 매칭 검증
    emails_in_text = re.findall(r"\b([\w._%+-]+@[\w.-]+\.[a-zA-Z]{2,})\b", all_text)
    best_email = pick_best_email(emails_in_text, brand_name=brand_name)
    if best_email:
        info["email"] = best_email

    # 빈 값 제거
    info = {k: v for k, v in info.items() if v}

    if info:
        print(f"           [디버그] Naver 확장 검색 추출: {list(info.keys())}")

    return info


def search_business_via_google(brand_name: str, business_number: str = "") -> dict:
    """Google Custom Search API로 사업자 정보 검색.

    API: https://www.googleapis.com/customsearch/v1
    무료: 일 100회

    site: 검색으로 특정 사이트 우선:
      - site:saramin.co.kr (사람인)
      - site:jobkorea.co.kr (잡코리아)
    """
    if not GOOGLE_API_KEY or not GOOGLE_CX:
        print(f"           [디버그] Google API 키/CX 없음 (skip)")
        return {}

    info = {}

    # 검색 키워드 (사이트 특화 + 일반)
    queries = [
        f"{brand_name} 대표",
        f"{brand_name} site:saramin.co.kr",
        f"{brand_name} site:jobkorea.co.kr",
    ]
    if business_number:
        queries.append(f'"{business_number}"')

    all_text = ""

    for query in queries[:4]:   # 최대 4개 (Google 한도 절약)
        try:
            response = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "key": GOOGLE_API_KEY,
                    "cx": GOOGLE_CX,
                    "q": query,
                    "num": 5,
                },
                timeout=10,
            )
            if response.status_code == 200:
                data = response.json()
                for item in data.get("items", []):
                    text = (
                        item.get("title", "") + " " +
                        item.get("snippet", "") + " " +
                        item.get("link", "")
                    )
                    all_text += text + "\n"
            time.sleep(0.2)
        except Exception as e:
            print(f"           [디버그] Google 검색 예외: {e}")
            continue

    # 패턴 추출 — 주소 제외 (2026-05-26)
    info["ceo"] = extract_ceo_from_text(all_text)
    info["phone"] = extract_phone_from_text(all_text)

    # 이메일 (Google snippet에 노출 자주 됨) — 도메인-브랜드 매칭 검증
    emails_in_text = re.findall(r"\b([\w._%+-]+@[\w.-]+\.[a-zA-Z]{2,})\b", all_text)
    best_email = pick_best_email(emails_in_text, brand_name=brand_name)
    if best_email:
        info["email"] = best_email

    info = {k: v for k, v in info.items() if v}

    if info:
        print(f"           [디버그] Google 검색 추출: {list(info.keys())}")

    return info


def collect_extended_business_info(brand_name: str, business_number: str = "") -> dict:
    """확장 사업자 정보 수집 — 공식 홈페이지 + Naver + Google 끝까지 시도.

    ⭐ 2026-05-26 정책:
      Phase 4a: 공식 홈페이지 ⭐ 최우선 (대표/전화/이메일)
                → 공식 홈페이지가 있고 정보 충분하면 여기서 완료
      Phase 4b: Naver 검색 강화 (홈페이지 부족·없을 때)
      Phase 4c: Google 검색 (그래도 부족하면 Google 사이트 검색)

    ⚠ 주소(address) 자동수집 제외 — 정확도 미달 (사용자 요청).
    "데이터 찾을 때까지 자동 시도" — 검색 도우미 없이 자동 완성.
    """
    info = {}
    sources_tried = []

    # 1차: 공식 홈페이지 ⭐ 최우선
    print(f"           [Phase 4a] 공식 홈페이지 추출 시작...")
    homepage_info = find_business_info_from_homepage(brand_name)
    if homepage_info:
        sources_tried.append("공식홈페이지")
    for k, v in homepage_info.items():
        if v and not info.get(k):
            info[k] = v

    # 누락된 필드 확인 — ⭐ address 제외
    missing = [k for k in ["ceo", "phone", "email"] if not info.get(k)]

    # 2차: Naver 검색 강화 (홈페이지 부족하면)
    if missing:
        print(f"           [Phase 4b] Naver 확장 검색 시작 (보완: {missing})...")
        naver_info = search_business_via_naver_extended(brand_name, business_number)
        if naver_info:
            sources_tried.append("Naver검색")
        for k, v in naver_info.items():
            if v and not info.get(k):
                info[k] = v

    # 3차: Naver 검색 — 이메일 보완 (별도 함수, 우선순위 점수 적용)
    if not info.get("email"):
        print(f"           [Phase 4b-2] Naver 검색으로 이메일 찾기...")
        naver_email = search_email_via_naver(brand_name)
        if naver_email:
            info["email"] = naver_email
            if "Naver검색" not in sources_tried:
                sources_tried.append("Naver검색")

    # 4차: Google 검색 (마지막 보완) — ⭐ address 제외
    missing = [k for k in ["ceo", "phone", "email"] if not info.get(k)]
    if missing:
        print(f"           [Phase 4c] Google 검색 시작 (보완: {missing})...")
        google_info = search_business_via_google(brand_name, business_number)
        if google_info:
            sources_tried.append("Google검색")
        for k, v in google_info.items():
            if v and not info.get(k):
                info[k] = v

    if sources_tried:
        print(f"           [Phase 4 완료] 시도된 소스: {sources_tried}, "
              f"수집 항목: {len([k for k in info if info.get(k)])}개")

    return info


# ─────────────────────────────────────────────────────────────────
# 통합 함수: 모든 소스에서 정보 수집 → 가장 신뢰도 높은 정보 선택
# ─────────────────────────────────────────────────────────────────
def collect_business_info(brand_name: str, store_url: str) -> dict:
    """영업 컨택 정보 자동 수집 — 검색 기반 (100% Naver/Google).

    ⭐ 2026-05-26 전면 재설계:
      이전: Phase 1 (스마트스토어) → Phase 2 (공정위 DB) → Phase 3 (검색)
      변경: 100% 검색 기반 — 사용자 워크플로우 그대로
        1. 네이버/구글에 브랜드명 검색
        2. 검색 결과의 자사 홈페이지 들어감
        3. 메타+body 검증으로 진짜 공식몰 판별
        4. footer + 약관 페이지에서 이메일/전화/사업자번호 추출

      제거 이유:
        - 스마트스토어: HTTP 429 차단 자주, 영업 컨택과 무관
        - 공정위 DB: 본사 정보라 브랜드 영업 컨택과 무관

    반환:
        {
            "company_name": "...",
            "ceo": "...",
            "business_number": "...",
            "phone": "...",
            "email": "...",
            "sources": ["공식홈페이지", "Naver검색"],
            "confidence": "높음" / "중간" / "낮음",
        }
    """
    result = {
        "company_name": "",
        "ceo": "",
        "business_number": "",
        "phone": "",
        "email": "",
        "sources": [],
    }

    print(f"        🔍 영업 컨택 자동 수집 시작 ({brand_name}) — 검색 기반")

    if not brand_name:
        print(f"           ⚠ 브랜드명 없음 → skip")
        result["confidence"] = "낮음"
        return result

    # ───────────────────────────────────────────────
    # Phase 1: 공식 홈페이지 (네이버 + Google 검색 → footer 추출) ⭐ 메인
    # 사용자 워크플로우와 동일:
    #   "네이버에 '브랜드명' 검색 → 자사 홈페이지 클릭 → 하단 정보 복사"
    # ───────────────────────────────────────────────
    print(f"           [Phase 1] 공식 홈페이지 검색·추출 시작...")
    homepage_info = find_business_info_from_homepage(brand_name)

    # ───────────────────────────────────────────────
    # ⭐ 2026-05-30: 신뢰도 게이트 (사용자 요청)
    #   "확실할 때만 자동 저장, 아니면 '수기 입력 필요'로 표시"
    #   확실함 = ① 공식몰 footer에서 사업자등록번호를 찾음 (진짜 회사 footer)
    #          AND ② 제목/메타/본문에 '브랜드명 자체'가 분명히 있음 (brand_score)
    #   ⚠ brand_score는 footer 일반신호(사업자번호/고객센터 등) 제외 → 아무 쇼핑몰이
    #      통과하던 문제(gileduzon) 차단. 진짜 그 브랜드 사이트만 통과.
    #   (기준 점수는 아래 TRUST_BRAND_MIN — 너무 많이 '수기 필요'면 낮추면 됨)
    # ───────────────────────────────────────────────
    TRUST_BRAND_MIN = 30
    has_bizno = bool(homepage_info.get("business_number"))
    brand_score = int(homepage_info.get("brand_score", 0) or 0)
    trusted = bool(homepage_info) and has_bizno and brand_score >= TRUST_BRAND_MIN

    if trusted:
        for k in ["company_name", "ceo", "business_number", "phone", "email"]:
            if homepage_info.get(k) and not result[k]:
                result[k] = homepage_info[k]
        result["sources"].append("공식홈페이지")
        result["confidence"] = "높음"
        print(f"           ✓ 공식 홈페이지 추출(신뢰 확인): "
              f"이메일={result['email']}, 전화={result['phone']}, "
              f"대표={result['ceo']} (브랜드일치 {brand_score}점)")
    else:
        # 신뢰 기준 미달 → 자동 저장 보류, 수기 입력 유도
        reasons = []
        if not has_bizno:
            reasons.append("사업자등록번호 미발견")
        if brand_score < TRUST_BRAND_MIN:
            reasons.append(f"브랜드일치 약함({brand_score}점)")
        reason_str = ", ".join(reasons) or "공식몰 미발견"
        print(f"           ⚠ 자동수집 보류 → 수기 입력 필요 ({reason_str})")
        # 잘못된 다른 회사 정보를 넣지 않도록 연락처는 비워 둠
        result["sources"] = ["공식 홈페이지 미발견 — 수기 입력 필요"]
        result["confidence"] = "미발견"
        return result

    time.sleep(0.3)

    # 정보 채워진 항목 수
    filled = sum(1 for k in ["company_name", "ceo", "business_number", "phone", "email"]
                 if result.get(k))

    print(f"           ✓ 수집 완료: {len(result['sources'])}개 소스 / "
          f"{filled}개 항목 채움 (신뢰도: {result['confidence']})")

    return result
