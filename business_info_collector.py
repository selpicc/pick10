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
]

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
        return {}

    info = {}

    try:
        # 1. 메인 페이지 fetch
        response = requests.get(store_url, headers=HTTP_HEADERS, timeout=15)
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
            try:
                info_resp = requests.get(info_url, headers=HTTP_HEADERS, timeout=10)
                if info_resp.status_code == 200:
                    info_html = info_resp.text
            except Exception:
                pass

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
                info["email"] = email
                break

    except Exception:
        pass   # 실패해도 다음 단계 진행

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
        return {}   # API 키 없으면 skip

    if not business_number and not company_name:
        return {}

    info = {}

    try:
        # 공정위 통신판매사업자 등록상세 API
        # base URL + 메서드명 (활용가이드 따라 조정 가능)
        api_url = "https://apis.data.go.kr/1130000/MllBsDtl_3Service/getMllBsDtl_3"

        params = {
            "ServiceKey": PUBLIC_DATA_API_KEY,
            "pageNo": "1",
            "numOfRows": "5",
            "resultType": "json",   # JSON 응답 요청
        }

        if business_number:
            # 사업자번호 (하이픈 제거)
            params["brno"] = business_number.replace("-", "")
        elif company_name:
            params["bzmnNm"] = company_name

        response = requests.get(api_url, params=params, timeout=15)
        if response.status_code != 200:
            return {}

        # JSON 응답 시도 (실패하면 XML 가능성)
        try:
            data = response.json()
        except Exception:
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

    except Exception:
        pass

    return info


# ─────────────────────────────────────────────────────────────────
# Phase 3: 이메일 다층 수집
# ─────────────────────────────────────────────────────────────────
def find_email_from_homepage(brand_name: str) -> Optional[str]:
    """공식 홈페이지에서 이메일 자동 추출.

    Naver 웹 검색으로 공식 홈페이지 발견 → 페이지 fetch → 이메일 정규식 추출
    """
    if not brand_name or not NAVER_CLIENT_ID:
        return None

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
            return None

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
                page = requests.get(url, headers=HTTP_HEADERS, timeout=10)
                if page.status_code != 200:
                    continue

                # 이메일 정규식 추출
                emails = re.findall(
                    r"\b([\w._%+-]+@[\w.-]+\.[a-zA-Z]{2,})\b",
                    page.text,
                )
                for email in emails:
                    email_lower = email.lower()
                    if not any(skip in email_lower for skip in EMAIL_BLACKLIST_DOMAINS):
                        return email
            except Exception:
                continue

    except Exception:
        pass

    return None


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
                        return email
            time.sleep(0.1)
        except Exception:
            continue

    return None


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
