"""
사업자 정보 자동 수집 모듈
=================================================================
신규 셀러 수집 시 자동으로 사업자 정보 수집:
  - 상호, 대표자, 사업자번호, 전화번호, 주소
  - 이메일 (다층 수집)

데이터 소스 (3-tier, 모두 무료):
  Phase 1: 스마트스토어 사업자정보 페이지 스크래핑 (성공률 90%+)
  Phase 2: 공정위 통신판매사업자 DB API (이메일 보완 60%+)
  Phase 3: 이메일 다층 수집 (공식 홈페이지 + Naver 검색)

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

# 무관 이메일 패턴 (네이버·구글 등 — 브랜드 이메일 아님)
EMAIL_BLACKLIST_DOMAINS = [
    "@naver.com", "@gmail.com", "@daum.net", "@hanmail.net",
    "@hotmail.com", "@yahoo.com", "@outlook.com",
    "example", "noreply", "no-reply", "donotreply",
    "sentry.io", "wixpress.com", "intercom.io",
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

# 시·도 패턴 (주소 추출용)
ADDRESS_REGION = (
    r"(?:서울|부산|대구|인천|광주|대전|울산|세종|"
    r"경기|강원|충북|충남|전북|전남|경북|경남|제주)"
)


# ─────────────────────────────────────────────────────────────────
# Phase 1: 스마트스토어 사업자정보 페이지 스크래핑
# ─────────────────────────────────────────────────────────────────
def fetch_smartstore_business_info(store_url: str) -> dict:
    """스마트스토어 셀러 페이지에서 사업자정보 자동 추출.

    동작:
      1. 메인 페이지 fetch
      2. 사업자정보 popup/페이지 URL 발견 시 추가 fetch
      3. 정규식으로 정보 추출

    반환:
        {
            "company_name": "(주)프라젠트라",
            "ceo": "홍길동",
            "business_number": "123-45-67890",
            "phone": "02-1234-5678",
            "address": "서울특별시 강남구 ...",
            "email": "info@prajentra.com" (있을 시),
        }
    """
    if not store_url or "smartstore.naver.com" not in store_url:
        print(f"           [디버그] 스마트스토어 URL X: {store_url[:60]}")
        return {}

    info = {}

    try:
        # 1. 메인 페이지 fetch
        print(f"           [디버그] 스마트스토어 fetch 시작: {store_url[:60]}")
        response = requests.get(store_url, headers=HTTP_HEADERS, timeout=15)
        print(f"           [디버그] HTTP 상태: {response.status_code}, HTML 길이: {len(response.text)}자")
        if response.status_code != 200:
            return {}
        html = response.text

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

        # 주소
        m = re.search(
            rf"({ADDRESS_REGION}\s*[특별시광역시도]*\s*[^\n<\r]{{10,100}})",
            info_html,
        )
        if m:
            address = m.group(1).strip()
            # HTML 태그·과한 공백 정리
            address = re.sub(r"<[^>]+>", "", address)
            address = " ".join(address.split())
            info["address"] = address[:100]   # 최대 100자

        # 이메일 (사업자 이메일)
        emails = re.findall(r"\b([\w._%+-]+@[\w.-]+\.[a-zA-Z]{2,})\b", info_html)
        for email in emails:
            email_lower = email.lower()
            # 일반 메일 서비스·테스트 메일 제외
            if not any(skip in email_lower for skip in EMAIL_BLACKLIST_DOMAINS):
                if is_valid_email(email):
                    info["email"] = email
                    break

        # 디버그: 추출 결과 요약
        print(f"           [디버그] 정규식 매칭 결과:")
        print(f"               상호: {info.get('company_name', '(매칭 X)')[:30]}")
        print(f"               대표: {info.get('ceo', '(매칭 X)')[:20]}")
        print(f"               사업자번호: {info.get('business_number', '(매칭 X)')}")
        print(f"               전화: {info.get('phone', '(매칭 X)')}")
        print(f"               이메일: {info.get('email', '(매칭 X)')}")
        print(f"               전체 이메일 후보 수: {len(emails)}")

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
                "address": (
                    first.get("bsadr", "")
                    or first.get("사업장소재지", "")
                    or first.get("address", "")
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

    ⭐ 2026-05-26 강화:
      이전: 이메일만 추출
      변경: 이메일·대표·전화·주소 모두 추출

    동작:
      1. Naver 웹 검색으로 공식 홈페이지 후보 찾기
      2. 후보 페이지 fetch (메인 + /contact, /about 페이지)
      3. 각 페이지에서 정보 정규식 추출

    반환:
      {"email": "...", "ceo": "...", "phone": "...", "address": "..."}
    """
    if not brand_name or not NAVER_CLIENT_ID:
        return {}

    info = {}

    try:
        # Naver 웹 검색 (공식 홈페이지 후보)
        api_url = "https://openapi.naver.com/v1/search/webkr.json"
        headers = {
            "X-Naver-Client-Id": NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
        }
        params = {"query": f"{brand_name} 공식 홈페이지", "display": 5}

        response = requests.get(api_url, headers=headers, params=params, timeout=10)
        if response.status_code != 200:
            return {}

        items = response.json().get("items", [])

        # 상위 3개 사이트만 시도 (속도)
        for item in items[:3]:
            url = item.get("link", "")
            # 무관 사이트 제외
            if any(skip in url for skip in [
                "smartstore.naver.com", "blog.naver.com", "cafe.naver.com",
                "shopping.naver.com", "post.naver.com",
            ]):
                continue

            try:
                # 메인 페이지 + 일반적인 사업자 정보 페이지들 시도
                base_url = url.rstrip("/")
                pages_to_try = [
                    url,
                    f"{base_url}/contact",
                    f"{base_url}/about",
                    f"{base_url}/company",
                    f"{base_url}/info",
                ]

                combined_text = ""
                for page_url in pages_to_try:
                    try:
                        page = requests.get(page_url, headers=HTTP_HEADERS, timeout=8)
                        if page.status_code == 200:
                            combined_text += page.text + "\n"
                            # 메인 페이지에서 contact/about 링크 찾기 (보너스)
                            for link_match in re.finditer(
                                r'href=["\']([^"\']*(?:contact|about|company|info|cs|footer)[^"\']*)["\']',
                                page.text, re.IGNORECASE,
                            ):
                                sub_link = link_match.group(1)
                                if not sub_link.startswith("http"):
                                    sub_link = base_url + ("/" + sub_link.lstrip("/"))
                                if sub_link not in pages_to_try:
                                    try:
                                        sub_page = requests.get(sub_link, headers=HTTP_HEADERS, timeout=6)
                                        if sub_page.status_code == 200:
                                            combined_text += sub_page.text + "\n"
                                            break   # 첫 contact/about 페이지만
                                    except Exception:
                                        pass
                            break   # 메인 페이지 1개만 처리하면 충분
                        time.sleep(0.1)
                    except Exception:
                        continue

                if not combined_text:
                    continue

                # HTML 태그 제거하고 텍스트만
                text_only = re.sub(r"<[^>]+>", " ", combined_text)
                text_only = re.sub(r"\s+", " ", text_only)

                # 이메일 추출 (이미 검증된 로직)
                emails = re.findall(
                    r"\b([\w._%+-]+@[\w.-]+\.[a-zA-Z]{2,})\b",
                    text_only,
                )
                for email in emails:
                    email_lower = email.lower()
                    if not any(skip in email_lower for skip in EMAIL_BLACKLIST_DOMAINS):
                        if is_valid_email(email) and not info.get("email"):
                            info["email"] = email
                            break

                # 대표자 추출
                if not info.get("ceo"):
                    ceo = extract_ceo_from_text(text_only)
                    if ceo:
                        info["ceo"] = ceo

                # 전화 추출
                if not info.get("phone"):
                    phone = extract_phone_from_text(text_only)
                    if phone:
                        info["phone"] = phone

                # 주소 추출
                if not info.get("address"):
                    address = extract_address_from_text(text_only)
                    if address:
                        info["address"] = address

                # 정보 충분히 모았으면 break
                if len([k for k in ["email", "ceo", "phone", "address"] if info.get(k)]) >= 3:
                    break

            except Exception:
                continue

        if info:
            print(f"           [디버그] 공식 홈페이지 추출: {list(info.keys())}")

    except Exception as e:
        print(f"           [디버그] 공식 홈페이지 검색 예외: {e}")

    return info


def search_email_via_naver(brand_name: str) -> Optional[str]:
    """Naver 검색 결과(블로그/카페/웹)에서 이메일 추출."""
    if not brand_name or not NAVER_CLIENT_ID:
        return None

    queries = [
        f"{brand_name} 이메일 문의",
        f"{brand_name} 대표 메일",
        f"{brand_name} 사업 제휴",
    ]

    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }

    for query in queries:
        try:
            api_url = "https://openapi.naver.com/v1/search/webkr.json"
            params = {"query": query, "display": 5}
            response = requests.get(api_url, headers=headers, params=params, timeout=10)
            if response.status_code != 200:
                continue

            items = response.json().get("items", [])
            for item in items:
                text = item.get("title", "") + " " + item.get("description", "")
                emails = re.findall(
                    r"\b([\w._%+-]+@[\w.-]+\.[a-zA-Z]{2,})\b",
                    text,
                )
                for email in emails:
                    email_lower = email.lower()
                    if not any(skip in email_lower for skip in EMAIL_BLACKLIST_DOMAINS):
                        if is_valid_email(email):
                            return email
            time.sleep(0.1)
        except Exception:
            continue

    return None


# ─────────────────────────────────────────────────────────────────
# Phase 4: 확장 검색 (Naver + Google) — 사업자번호 기반 정보 추출
# 공정위 API에 없는 정보 (대표/전화/주소) 자동 수집 시도
# ─────────────────────────────────────────────────────────────────
def extract_ceo_from_text(text: str) -> str:
    """텍스트에서 대표자 이름 추출.

    패턴:
      - "대표 OOO", "대표이사 OOO", "대표자 OOO"
      - "OOO 대표", "OOO 대표이사"
    """
    if not text:
        return ""
    # 일반 단어 제외 (false positive 방지)
    blacklist = {"대표", "이사", "본사", "회사", "사장", "정보", "소개",
                 "직원", "팀장", "기자", "사람", "고객", "주식", "회원"}

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

    # 패턴 추출
    info["ceo"] = extract_ceo_from_text(all_text)
    info["phone"] = extract_phone_from_text(all_text)
    info["address"] = extract_address_from_text(all_text)

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

    # 패턴 추출
    info["ceo"] = extract_ceo_from_text(all_text)
    info["phone"] = extract_phone_from_text(all_text)
    info["address"] = extract_address_from_text(all_text)

    info = {k: v for k, v in info.items() if v}

    if info:
        print(f"           [디버그] Google 검색 추출: {list(info.keys())}")

    return info


def collect_extended_business_info(brand_name: str, business_number: str = "") -> dict:
    """확장 사업자 정보 수집 — 공식 홈페이지 + Naver + Google 통합.

    ⭐ 2026-05-26 강화:
      Phase 4a: 공식 홈페이지 (대표/전화/주소/이메일) — 가장 정확
      Phase 4b: Naver 검색 강화 (홈페이지 못 찾은 정보 보완)
      Phase 4c: Google 검색 (Naver도 못 찾은 정보만)
    """
    info = {}

    # 1차: 공식 홈페이지 ⭐ (가장 정확, 모든 정보 시도)
    print(f"           [Phase 4a] 공식 홈페이지 추출 시작...")
    homepage_info = find_business_info_from_homepage(brand_name)
    for k, v in homepage_info.items():
        if v and not info.get(k):
            info[k] = v

    # 2차: Naver 검색 강화 (보완)
    missing = [k for k in ["ceo", "phone", "address"] if not info.get(k)]
    if missing:
        print(f"           [Phase 4b] Naver 확장 검색 시작 (보완: {missing})...")
        naver_info = search_business_via_naver_extended(brand_name, business_number)
        for k, v in naver_info.items():
            if v and not info.get(k):
                info[k] = v

    # 3차: Google (마지막 보완)
    missing = [k for k in ["ceo", "phone", "address"] if not info.get(k)]
    if missing:
        print(f"           [Phase 4c] Google 검색 시작 (보완: {missing})...")
        google_info = search_business_via_google(brand_name, business_number)
        for k, v in google_info.items():
            if v and not info.get(k):
                info[k] = v

    return info


# ─────────────────────────────────────────────────────────────────
# 통합 함수: 모든 소스에서 정보 수집 → 가장 신뢰도 높은 정보 선택
# ─────────────────────────────────────────────────────────────────
def collect_business_info(brand_name: str, store_url: str) -> dict:
    """사업자 정보 자동 수집 — 3-tier 통합.

    동작:
      Phase 1 (스마트스토어) → Phase 2 (공정위 DB) → Phase 3 (이메일 보완)
      각 소스에서 정보 수집 → 빈 값만 다음 소스로 보완

    반환:
        {
            "company_name": "...",
            "ceo": "...",
            "business_number": "...",
            "phone": "...",
            "address": "...",
            "email": "...",
            "sources": ["스마트스토어", "공정위", "홈페이지"],
            "confidence": "높음" / "중간" / "낮음",
        }
    """
    result = {
        "company_name": "",
        "ceo": "",
        "business_number": "",
        "phone": "",
        "address": "",
        "email": "",
        "sources": [],
    }

    print(f"        🔍 사업자정보 자동 수집 시작 ({brand_name})")

    # ───────────────────────────────────────────────
    # Phase 1: 스마트스토어 사업자정보 페이지
    # ───────────────────────────────────────────────
    info1 = fetch_smartstore_business_info(store_url)
    if info1:
        for k in ["company_name", "ceo", "business_number", "phone", "address", "email"]:
            if info1.get(k) and not result[k]:
                result[k] = info1[k]
        if any(info1.get(k) for k in ["company_name", "ceo", "business_number"]):
            result["sources"].append("스마트스토어")
            print(f"           ✓ 스마트스토어: 상호={result['company_name'][:20]}, "
                  f"대표={result['ceo'][:10]}, 전화={result['phone']}")
    time.sleep(0.3)

    # ───────────────────────────────────────────────
    # Phase 2: 공정위 통신판매사업자 DB
    # ───────────────────────────────────────────────
    if PUBLIC_DATA_API_KEY:
        info2 = fetch_ftc_telecom_seller_info(
            business_number=result.get("business_number", ""),
            company_name=result.get("company_name", "") or brand_name,
        )
        if info2:
            for k in ["company_name", "ceo", "business_number", "phone", "address", "email"]:
                if info2.get(k) and not result[k]:
                    result[k] = info2[k]
            if any(info2.get(k) for k in ["company_name", "ceo", "business_number", "email"]):
                result["sources"].append("공정위DB")
                print(f"           ✓ 공정위 DB: 정보 보완 (이메일={info2.get('email', '')})")
        time.sleep(0.3)

    # ───────────────────────────────────────────────
    # Phase 3: 이메일 다층 수집 (이메일 없을 때만)
    # ───────────────────────────────────────────────
    if not result["email"] and brand_name:
        # 3-1) 공식 홈페이지
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
