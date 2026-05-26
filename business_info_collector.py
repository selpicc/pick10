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

# 무관 이메일 패턴 (브랜드 이메일 아님)
# ⭐ 2026-05-26 강화: 채용/쇼핑 플랫폼 자체 메일 차단 (help@saramin.co.kr 등 오인 방지)
EMAIL_BLACKLIST_DOMAINS = [
    # ─── 무료 메일 (개인 메일) ───
    "@naver.com", "@gmail.com", "@daum.net", "@hanmail.net",
    "@hotmail.com", "@yahoo.com", "@outlook.com", "@nate.com",
    "@kakao.com",

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
]


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


def pick_best_email(candidates: list, brand_name: str = "") -> str:
    """후보 이메일 리스트에서 가장 적합한 것 선택 (점수 기준).

    - 블랙리스트 도메인(@naver.com 등) 제외
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
        if any(skip in email_lower for skip in EMAIL_BLACKLIST_DOMAINS):
            continue
        if not is_valid_email(email):
            continue

        score = score_email(email)
        # ⭐ 2026-05-26 강화: 도메인-브랜드 매칭 더 엄격
        # 한글 브랜드 ↔ 영문 도메인 매칭 어려움 인정 →
        # 매칭 안 될 때 잘못된 정보보다 미수집(빈 값) 우선
        if brand_name:
            if _is_domain_matching_brand(email, brand_name):
                score += 50   # 매칭되면 강력 보너스
            else:
                score -= 60   # ⭐ 미매칭 감산 강화 (-30 → -60)

        valid.append((score, email))
    if not valid:
        return ""
    # 점수 내림차순 정렬, 동점이면 짧은 이메일 우선 (덜 generic)
    valid.sort(key=lambda x: (-x[0], len(x[1])))

    # ⭐ 임계값 50점 — support@choandkang(100-60=40) 같은 미매칭 케이스 차단
    # 한글 브랜드는 영문 도메인 매칭 거의 불가 → 미수집(빈 값) 됨
    # 진짜 영업 메일은 사용자가 수기 입력 (정확도 100%)
    best_score, best_email = valid[0]
    if brand_name and best_score < 50:
        # 50점 미만 = 도메인 미매칭 → 신뢰도 부족, 미선택
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
                    }
                    info = {k: v for k, v in info.items() if v}
                    if info:
                        print(f"           ✅ __NEXT_DATA__ 추출 성공: {list(info.keys())}")
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
def find_email_from_homepage(brand_name: str) -> Optional[str]:
    """공식 홈페이지에서 이메일 자동 추출 (기존 호환)."""
    info = find_business_info_from_homepage(brand_name)
    return info.get("email") if info else None


def find_business_info_from_homepage(brand_name: str) -> dict:
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
        search_queries = [
            f"{brand_name} 공식 홈페이지",
            f"{brand_name} 공식몰",
            f"{brand_name} 쇼핑몰",
            f"{brand_name} 공식 사이트",
            f"{brand_name} 이메일",
            f"{brand_name} 고객센터",   # ⭐ footer 이메일 잡기 좋은 키워드
            f"{brand_name} cs",          # ⭐ 영문 CS 페이지
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
            google_queries = [
                f"{brand_name} 공식몰",
                f"{brand_name} 공식 사이트",
                f"{brand_name} cs 고객센터",   # ⭐ footer/CS 페이지 직접 노출
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

        print(f"           [디버그] 후보 공식 홈페이지 URL: {len(candidate_urls)}개 "
              f"(Naver+Google 통합)")

        # ⭐ 상위 8개 사이트 시도 (이전 5개 → 8개)
        all_email_candidates = []   # 사이트별 후보 모아서 마지막에 베스트 선택

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
                for idx, page_url in enumerate(pages_to_try):
                    try:
                        page = requests.get(page_url, headers=HTTP_HEADERS, timeout=8)
                        if page.status_code == 200:
                            combined_text += page.text + "\n"
                            if idx == 0:
                                main_text = page.text
                                # 메인 페이지에서 contact/about/agreement 링크 추가 발견
                                for link_match in re.finditer(
                                    r'href=["\']([^"\']*(?:contact|about|company|info|cs|footer|agreement|privacy|guide|shopinfo)[^"\']*\.(?:html?|php|asp))["\']',
                                    page.text, re.IGNORECASE,
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

                # ─── 전화 패턴 (footer) ───
                if not info.get("phone"):
                    m = re.search(
                        r"(?:CALL|TEL|TELEPHONE|전화|문의|고객센터|연락처)\s*[:\s]?\s*"
                        r"(\d{2,4}[-.\s]?\d{3,4}[-.\s]?\d{4})",
                        text_only, re.IGNORECASE,
                    )
                    if m:
                        phone = m.group(1).strip()
                        phone = re.sub(r"[.\s]+", "-", phone)
                        info["phone"] = phone

                # ─── 사업자번호 패턴 (footer) ───
                if not info.get("business_number"):
                    m = re.search(
                        r"(?:BUSINESS\s*(?:LICENSE|NO)?|사업자(?:등록)?번호)\s*[:\s]?\s*"
                        r"(\d{3}-?\d{2}-?\d{5})",
                        text_only, re.IGNORECASE,
                    )
                    if m:
                        info["business_number"] = m.group(1).strip()

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

        if info:
            print(f"           [디버그] 공식 홈페이지 추출: {list(info.keys())}")

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
    """사업자 정보 자동 수집 — 3-tier 통합.

    동작:
      Phase 1 (스마트스토어) → Phase 2 (공정위 DB) → Phase 3 (이메일 보완)
      각 소스에서 정보 수집 → 빈 값만 다음 소스로 보완

    반환 (주소는 자동수집 제외, 2026-05-26):
        {
            "company_name": "...",
            "ceo": "...",
            "business_number": "...",
            "phone": "...",
            "email": "...",
            "sources": ["스마트스토어", "공정위", "홈페이지"],
            "confidence": "높음" / "중간" / "낮음",
        }
    """
    # ⭐ 2026-05-26: address 자동수집 제외
    result = {
        "company_name": "",
        "ceo": "",
        "business_number": "",
        "phone": "",
        "email": "",
        "sources": [],
    }

    print(f"        🔍 사업자정보 자동 수집 시작 ({brand_name})")

    # ───────────────────────────────────────────────
    # Phase 1: 스마트스토어 사업자정보 페이지
    # ───────────────────────────────────────────────
    info1 = fetch_smartstore_business_info(store_url)
    if info1:
        for k in ["company_name", "ceo", "business_number", "phone", "email"]:
            if info1.get(k) and not result[k]:
                result[k] = info1[k]
        if any(info1.get(k) for k in ["company_name", "ceo", "business_number"]):
            result["sources"].append("스마트스토어")
            print(f"           ✓ 스마트스토어: 상호={result['company_name'][:20]}, "
                  f"대표={result['ceo'][:10]}, 전화={result['phone']}")
    time.sleep(0.3)

    # ───────────────────────────────────────────────
    # ⭐ 2026-05-26: Phase 2 (공정위 DB) 제거
    # 공정위 DB는 본사 정보(사업자번호, 본사 대표, 본사 전화)라
    # 영업 컨택(브랜드 공식몰 CS)과 완전히 별개 → 호출 안 함
    # ───────────────────────────────────────────────

    # ───────────────────────────────────────────────
    # Phase 3: 공식 홈페이지 + Naver 검색 (이메일 없을 때만)
    # ───────────────────────────────────────────────
    if not result["email"] and brand_name:
        # 3-1) 공식 홈페이지 (브랜드 공식몰 footer)
        email = find_email_from_homepage(brand_name)
        if email:
            result["email"] = email
            result["sources"].append("공식홈페이지")
            print(f"           ✓ 공식 홈페이지 이메일: {email}")
        else:
            # 3-2) Naver 검색 결과
            email = search_email_via_naver(brand_name)
            if email:
                result["email"] = email
                result["sources"].append("Naver검색")
                print(f"           ✓ Naver 검색 이메일: {email}")
        time.sleep(0.3)

    # ───────────────────────────────────────────────
    # 신뢰도 평가
    # ───────────────────────────────────────────────
    source_count = len(result["sources"])
    if source_count >= 2:
        result["confidence"] = "높음"
    elif source_count == 1:
        result["confidence"] = "중간"
    else:
        result["confidence"] = "낮음"

    # 정보 채워진 항목 수
    filled = sum(1 for k in ["company_name", "ceo", "business_number", "phone", "email"]
                 if result.get(k))

    print(f"           ✓ 수집 완료: {source_count}개 소스 / "
          f"{filled}개 항목 채움 (신뢰도: {result['confidence']})")

    return result
