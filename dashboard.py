"""
PICK10 - Streamlit 웹 대시보드 (정돈된 UX/UI)
=================================================================
실행:
    streamlit run dashboard.py
=================================================================
"""

import os
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode, GridUpdateMode

from supabase_client import (
    get_supabase_client,
    DB_TO_KOR,
    KOR_TO_DB,
    MANUAL_COLUMNS as SUPA_MANUAL_COLUMNS,
    TABLE_NAME,
)

# 시장 타깃 크로스체크 (A+B+C 필터) + 자동 카테고리 분류 + 검색 키워드
from market_filter import (
    market_fit_check,
    classify_category,
    CATEGORY_SEARCH_KEYWORDS,
)

load_dotenv()
CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")   # 비밀번호 보호 (선택)


# ─────────────────────────────────────────────────────────────────
# 페이지 설정
# ─────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="PICK10 Dashboard",
    page_icon="P",
    layout="wide",
    initial_sidebar_state="collapsed",   # 처음 접속 시 사이드바 자동 접힘 → 표 가시성 ↑
)


# ─────────────────────────────────────────────────────────────────
# 비밀번호 보호 (배포 환경에서만 활성화)
# 환경변수 APP_PASSWORD 가 설정되어 있으면 로그인 화면 표시
# ─────────────────────────────────────────────────────────────────
def check_password():
    """APP_PASSWORD 환경변수 있을 때만 작동. 비밀번호 맞으면 통과."""
    if not APP_PASSWORD:
        return True   # 로컬 개발: 인증 X

    if st.session_state.get("auth_ok"):
        return True

    st.markdown("### PICK10")
    st.caption("팀 전용 대시보드 — 비밀번호 입력")
    pw = st.text_input("비밀번호", type="password", key="pw_input")
    if st.button("입장"):
        if pw == APP_PASSWORD:
            st.session_state["auth_ok"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()


check_password()


RESULTS_DIR = "results"   # 더 이상 거의 안 씀 (백업용으로만)

import csv
import glob
import re

EXCLUDE_IDS = {"main", "search", "category", "popup"}

# 수기 컬럼은 supabase_client에서 import (단일 진실의 원천)
MANUAL_COLUMNS = SUPA_MANUAL_COLUMNS

# 영업 상태 선택지 (드롭다운)
SALES_STATUS_OPTIONS = [
    "",            # 빈값 (기본)
    "미접촉",
    "메일 발송",
    "응답 대기",
    "미팅 중",
    "계약 완료",
    "거절",
    "기타) 패싱",   # 의도적 보류/스킵 (다음 라운드에 다시 검토)
]


def fetch_smartstore_link(brand_name: str) -> str:
    """브랜드명으로 검색 → 그 셀러의 link 받기 (검색 API 응답)"""
    if not brand_name or not CLIENT_ID or not CLIENT_SECRET:
        return ""
    api_url = "https://openapi.naver.com/v1/search/shop.json"
    headers = {
        "X-Naver-Client-Id": CLIENT_ID,
        "X-Naver-Client-Secret": CLIENT_SECRET,
    }
    params = {"query": brand_name, "display": 10, "sort": "sim"}
    try:
        resp = requests.get(api_url, headers=headers, params=params, timeout=10)
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            for item in items:
                if (
                    item.get("mallName", "").strip() == brand_name.strip()
                    and "smartstore.naver.com" in item.get("link", "")
                ):
                    return item.get("link", "")
    except Exception:
        pass
    return ""


def resolve_real_store_url(link: str) -> str:
    """검색 API link → redirect 추적 + HTML 파싱 → 진짜 storeId 형태 URL"""
    if not link:
        return ""

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
        # 1) redirect 후 최종 URL
        result = match_url(resp.url)
        if result:
            return result

        # 2) HTML에서 og:url
        html = resp.text or ""
        og_match = re.search(
            r'<meta\s+property=["\']og:url["\']\s+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE,
        )
        if og_match:
            result = match_url(og_match.group(1))
            if result:
                return result

        # 3) HTML에서 canonical
        canonical_match = re.search(
            r'<link\s+rel=["\']canonical["\']\s+href=["\']([^"\']+)["\']',
            html, re.IGNORECASE,
        )
        if canonical_match:
            result = match_url(canonical_match.group(1))
            if result:
                return result

        # 4) HTML 본문의 첫 smartstore/brand 링크
        for sid in re.findall(r"https?://smartstore\.naver\.com/([a-zA-Z0-9_\-]+)", html):
            if sid not in EXCLUDE_IDS:
                return f"https://smartstore.naver.com/{sid}"
        for bid in re.findall(r"https?://brand\.naver\.com/([a-zA-Z0-9_\-]+)", html):
            if bid not in EXCLUDE_IDS:
                return f"https://brand.naver.com/{bid}"
    except Exception:
        pass
    return ""


def parse_marketing_exposure(text) -> dict:
    """마케팅 채널별 노출 텍스트 → {채널명: 숫자} 딕셔너리.
    예: '블로그 13,146 · 카페 19,689 · SNS 3,150'
        → {'블로그': 13146, '카페': 19689, 'SNS': 3150}
    """
    if not text or pd.isna(text):
        return {}
    result = {}
    pattern = r"(블로그|카페|SNS|뉴스|지식인)\s+([\d,]+)"
    for match in re.finditer(pattern, str(text)):
        channel = match.group(1)
        try:
            number = int(match.group(2).replace(",", ""))
            result[channel] = number
        except ValueError:
            continue
    return result


def needs_fix(addr: str) -> bool:
    """이 주소가 갱신 필요한지 판정.
    - 빈 값
    - 검색 페이지 fallback
    - /main/products/ 통합 URL (일부 셀러는 로그인 redirect됨 → 진짜 storeId 필요)
    """
    if not addr:
        return True
    if "search.shopping.naver.com" in addr:
        return True
    if "/main/products/" in addr:
        return True
    return False


def fill_empty_urls_in_all_csvs() -> dict:
    """Supabase에서 갱신 필요한 스마트스토어 URL 일괄 보강.

    영구 보장 패턴 — 빈 칸 절대 안 남김:
      1. 검색 API + redirect 추적 (성공: 진짜 셀러 메인 URL)
      2. 실패 시 검색 페이지 URL fallback (항상 작동)
    """
    sb = get_supabase_client()
    if not sb:
        return {"fixed": 0, "not_found": [], "files": 0}

    fixed_count = 0
    fallback_count = 0

    try:
        result = sb.table(TABLE_NAME).select("brand_name, smartstore_url").execute()
        for row in result.data:
            addr = (row.get("smartstore_url") or "").strip()
            if not needs_fix(addr):
                continue
            brand = (row.get("brand_name") or "").strip()
            if not brand:
                continue

            # 3중 fallback 전략 (검색 페이지는 최후 안전망)
            # 1순위: redirect 추적 → 셀러 메인 URL
            # 2순위: API 원본 link → 상품 상세 페이지 (진짜 스마트스토어)
            # 3순위 (최후): 검색 페이지 — API link도 없는 비정상 케이스
            real_url = ""
            link = fetch_smartstore_link(brand)
            if link:
                real_url = resolve_real_store_url(link)

            # 2차: redirect 실패 → API 원본 link 보존 (상품 페이지)
            if not real_url and link and (
                "smartstore.naver.com" in link or "brand.naver.com" in link
            ):
                real_url = link
                fallback_count += 1   # 상품 페이지 fallback 카운트

            # 3차 (최후): API link도 없으면 검색 페이지
            if not real_url:
                real_url = (
                    f"https://search.shopping.naver.com/search/all?"
                    f"query={urllib.parse.quote(brand)}"
                )
                fallback_count += 1

            sb.table(TABLE_NAME).update(
                {"smartstore_url": real_url}
            ).eq("brand_name", brand).execute()
            fixed_count += 1
            time.sleep(0.3)
    except Exception:
        pass

    not_found_list = []
    if fallback_count > 0:
        not_found_list.append(
            f"{fallback_count}건은 검색 페이지 URL로 fallback "
            "(클릭 시 그 브랜드 검색 결과로 이동)"
        )

    return {"fixed": fixed_count, "not_found": not_found_list, "files": 1}


# ─────────────────────────────────────────────────────────────────
# 시장 미적합 브랜드 검사 + 삭제 (A+B+C 필터 기반)
# 영업 진행 중인 브랜드는 자동 보호
# ─────────────────────────────────────────────────────────────────
PROTECTED_STATUSES = {
    "메일 발송", "응답 대기", "미팅 중", "계약 완료", "거절", "기타) 패싱"
}


def scan_unfit_brands() -> dict:
    """필터 미적합 브랜드 미리보기 (삭제 X).
    반환: {
        "delete_candidates": [...],   # 삭제 대상
        "protected_unfit": [...],      # 영업 진행 중이라 보호됨
        "keep_count": int,             # 통과
    }
    """
    sb = get_supabase_client()
    if sb is None:
        return {"delete_candidates": [], "protected_unfit": [], "keep_count": 0}

    result = sb.table(TABLE_NAME).select(
        "brand_name, flagship_product, sales_status"
    ).execute()

    delete_candidates = []
    protected_unfit = []
    keep_count = 0

    for row in result.data:
        brand = (row.get("brand_name") or "").strip()
        title = (row.get("flagship_product") or "").strip()
        status = (row.get("sales_status") or "").strip()
        if not brand:
            continue

        tag, reason = market_fit_check(brand, title)
        if tag == "ok":
            keep_count += 1
            continue

        if status in PROTECTED_STATUSES:
            protected_unfit.append({
                "brand": brand, "reason": reason, "status": status,
            })
            continue

        delete_candidates.append({
            "brand": brand, "reason": reason, "tag": tag,
            "title": title[:40],
        })

    return {
        "delete_candidates": delete_candidates,
        "protected_unfit": protected_unfit,
        "keep_count": keep_count,
    }


def delete_unfit_brands(delete_candidates: list) -> dict:
    """실제 삭제 실행. 호출 전 사용자 확인 필수.
    반환: {"deleted": int, "failed": int}
    """
    sb = get_supabase_client()
    if sb is None:
        return {"deleted": 0, "failed": len(delete_candidates)}

    deleted, failed = 0, 0
    for c in delete_candidates:
        try:
            sb.table(TABLE_NAME).delete().eq("brand_name", c["brand"]).execute()
            deleted += 1
        except Exception:
            failed += 1
    return {"deleted": deleted, "failed": failed}


def delete_brands_by_name(brand_names: list) -> dict:
    """브랜드명 리스트로 일괄 삭제 (사용자 수동 선택용).
    반환: {"deleted": int, "failed": int, "failed_brands": [...]}
    """
    sb = get_supabase_client()
    if sb is None:
        return {"deleted": 0, "failed": len(brand_names), "failed_brands": brand_names}

    deleted, failed = 0, 0
    failed_brands = []
    for brand in brand_names:
        if not brand:
            continue
        try:
            sb.table(TABLE_NAME).delete().eq("brand_name", brand).execute()
            deleted += 1
        except Exception:
            failed += 1
            failed_brands.append(brand)
    return {"deleted": deleted, "failed": failed, "failed_brands": failed_brands}


# ─────────────────────────────────────────────────────────────────
# 커스텀 CSS — 깔끔한 디자인 톤
# ─────────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
    /* 페이지 여백 */
    .block-container {
        padding-top: 2.5rem;
        padding-bottom: 3rem;
        max-width: 1280px;
    }

    /* 헤딩 */
    h1, h2, h3 {
        font-weight: 600 !important;
        letter-spacing: -0.4px;
    }
    h1 { font-size: 1.75rem !important; margin-bottom: 0.25rem !important; }
    h2 { font-size: 1.05rem !important; margin-top: 2rem !important; margin-bottom: 0.75rem !important; }

    /* 메트릭 카드 — 라이트 톤 */
    [data-testid="stMetric"] {
        background: #f8f9fa;
        padding: 16px 20px;
        border-radius: 10px;
        border: 1px solid #ececec;
    }
    [data-testid="stMetricLabel"] {
        font-size: 12px !important;
        color: #6b7280 !important;
        font-weight: 500 !important;
    }
    [data-testid="stMetricValue"] {
        font-size: 28px !important;
        font-weight: 600 !important;
        color: #111827 !important;
    }

    /* 사이드바 */
    section[data-testid="stSidebar"] > div {
        padding-top: 2rem;
    }
    section[data-testid="stSidebar"] h2 {
        font-size: 14px !important;
        text-transform: uppercase;
        letter-spacing: 1px;
        color: #6b7280 !important;
        margin-bottom: 1rem !important;
    }

    /* 차트 컨테이너 */
    .chart-card {
        background: #ffffff;
        padding: 1.25rem;
        border-radius: 10px;
        border: 1px solid #ececec;
    }
    .chart-card h3 {
        font-size: 14px !important;
        font-weight: 500 !important;
        color: #374151;
        margin: 0 0 1rem 0 !important;
    }

    /* 데이터프레임 */
    [data-testid="stDataFrame"] {
        border-radius: 10px;
        overflow: hidden;
    }

    /* 다운로드 버튼 */
    [data-testid="stDownloadButton"] button {
        background: #111827;
        color: white;
        border: none;
        border-radius: 8px;
        font-weight: 500;
        padding: 8px 18px;
    }
    [data-testid="stDownloadButton"] button:hover {
        background: #374151;
    }

    /* 캡션 */
    .subtitle {
        font-size: 13px;
        color: #6b7280;
        margin-bottom: 2rem;
    }

    /* Streamlit 기본 메뉴·푸터 숨기기 */
    [data-testid="stToolbar"] { display: none !important; }
    [data-testid="stStatusWidget"] { display: none !important; }
    [data-testid="stHeader"] { background: transparent; height: 0; }
    footer { display: none !important; }
    #MainMenu { visibility: hidden; }
</style>
    """,
    unsafe_allow_html=True,
)


# ─────────────────────────────────────────────────────────────────
# 데이터 로드
# ─────────────────────────────────────────────────────────────────
@st.cache_data(ttl=10)   # 짧은 TTL: 자주 갱신
def load_all_data() -> pd.DataFrame:
    """Supabase sellers 테이블에서 모든 데이터 로드 + 한글 컬럼명 변환"""
    sb = get_supabase_client()
    if not sb:
        st.error("Supabase 연결 실패. .env의 SUPABASE_URL / SUPABASE_KEY 확인.")
        return pd.DataFrame()

    try:
        # id 내림차순 정렬 → 가장 최근 INSERT가 위로 오게 함
        # (id는 Supabase가 자동 증가시키는 PK → 신규 추가 시 큰 값)
        result = (
            sb.table(TABLE_NAME)
            .select("*")
            .order("id", desc=True)
            .execute()
        )
        if not result.data:
            return pd.DataFrame()

        df = pd.DataFrame(result.data)
        # 영문 → 한글 컬럼명 변환
        df = df.rename(columns=DB_TO_KOR)

        # 수기 컬럼 string 타입 보장 (NaN-only 컬럼 → float 함정 방지)
        for col in MANUAL_COLUMNS:
            if col not in df.columns:
                df[col] = ""
            df[col] = df[col].fillna("").astype(str)

        # Selpic 점수 정수화
        if "Selpic 점수" in df.columns:
            df["Selpic 점수"] = pd.to_numeric(
                df["Selpic 점수"], errors="coerce"
            ).fillna(0).astype(int)

        # 카테고리 분류 — 모든 모드 통일: 주력상품 기반 자동 분류
        # 이유: 브랜드별 진짜 시장을 표시 (그 브랜드가 실제 뭘 파는지)
        # 검색 의도와 다를 수 있지만, 영업 시 그 브랜드의 진짜 라인 이해에 도움
        if "주력상품명" in df.columns:
            df["카테고리"] = df["주력상품명"].fillna("").astype(str).apply(classify_category)
        else:
            df["카테고리"] = "기타"

        return df
    except Exception as e:
        st.error(f"Supabase 데이터 로드 실패: {e}")
        return pd.DataFrame()


df = load_all_data()


# ─────────────────────────────────────────────────────────────────
# 헤더
# ─────────────────────────────────────────────────────────────────
st.markdown("# 셀픽 영업처 관리 페이지")
st.markdown(
    "<div class='subtitle'>셀픽 영업처 큐레이션 대시보드 · 누적 셀러 관리 · 영업 우선순위 정렬</div>",
    unsafe_allow_html=True,
)

# ─────────────────────────────────────────────────────────────────
# 액션 — 수집 모드 전환 + 옵션 + 버튼 (3가지 모드)
# 모드 1: 자동 (전체 12개 카테고리)
# 모드 2: 카테고리 지정 (단일 카테고리)
# 모드 3: 키워드 입력 (사용자 직접)
# ─────────────────────────────────────────────────────────────────

# 카테고리 프리셋 — 메인 표 자동 분류와 동일 10개 (market_filter.py 단일 진실의 원천)
COLLECT_CATEGORIES = list(CATEGORY_SEARCH_KEYWORDS.keys())

mode_col, _spacer = st.columns([2, 3])
with mode_col:
    collect_mode = st.radio(
        "수집 모드",
        options=["자동 (전체)", "카테고리 지정", "키워드 입력"],
        horizontal=True,
        key="collect_mode",
        label_visibility="collapsed",
    )

# 모드별 입력 영역 (한 줄, 동적 변경)
collect_n = 5
collect_category = ""
collect_keywords = ""

if collect_mode == "자동 (전체)":
    a_col1, a_col2, a_col3 = st.columns([0.7, 1.5, 3.8])
    with a_col1:
        collect_n = st.selectbox(
            "건수", [1, 2, 3, 4, 5], index=4,
            key="collect_count_auto",
            label_visibility="collapsed",
            format_func=lambda x: f"{x}건",
        )
    with a_col2:
        collect_clicked = st.button(
            f"+ {collect_n}건 자동 수집",
            type="primary", use_container_width=True,
            key="collect_btn_auto",
        )
    with a_col3:
        st.markdown(
            "<div style='padding-top: 8px; color: #6b7280; font-size: 13px;'>"
            f"12개 카테고리에서 점수 상위 {collect_n}건 · 약 {30 + collect_n * 8}초"
            "</div>",
            unsafe_allow_html=True,
        )
    # 빈 스토어 채우기 버튼은 메인 표 상단으로 이동됨

elif collect_mode == "카테고리 지정":
    c_col1, c_col2, c_col3, c_col4 = st.columns([1.4, 0.7, 1.5, 2.4])
    with c_col1:
        collect_category = st.selectbox(
            "카테고리", COLLECT_CATEGORIES,
            key="collect_cat_sel",
            label_visibility="collapsed",
        )
    with c_col2:
        collect_n = st.selectbox(
            "건수", [1, 2, 3, 4, 5], index=4,
            key="collect_count_cat",
            label_visibility="collapsed",
            format_func=lambda x: f"{x}건",
        )
    with c_col3:
        collect_clicked = st.button(
            f"+ {collect_n}건 수집",
            type="primary", use_container_width=True,
            key="collect_btn_cat",
        )
    with c_col4:
        st.markdown(
            "<div style='padding-top: 8px; color: #6b7280; font-size: 13px;'>"
            f"<b>{collect_category}</b> 카테고리에서만 {collect_n}건"
            "</div>",
            unsafe_allow_html=True,
        )
    # 빈 스토어 채우기 버튼은 메인 표 상단으로 이동됨

else:   # 키워드 입력
    k_col1, k_col2, k_col3 = st.columns([3.5, 0.7, 1.5])
    with k_col1:
        collect_keywords = st.text_input(
            "키워드", value="",
            placeholder="예: 산양분유, 유기농 기저귀, 임산부 엽산  (쉼표로 여러 개)",
            key="collect_kw",
            label_visibility="collapsed",
        )
    with k_col2:
        collect_n = st.selectbox(
            "건수", [1, 2, 3, 4, 5], index=4,
            key="collect_count_kw",
            label_visibility="collapsed",
            format_func=lambda x: f"{x}건",
        )
    with k_col3:
        collect_clicked = st.button(
            f"+ {collect_n}건 수집",
            type="primary", use_container_width=True,
            key="collect_btn_kw",
            disabled=not collect_keywords.strip(),
        )
    # 빈 스토어 채우기 버튼은 메인 표 상단으로 이동됨
    st.markdown(
        "<div style='color: #6b7280; font-size: 12px; margin-top: 6px;'>"
        "쉼표(,)로 여러 키워드 가능 · 각 키워드별 검색 후 점수 상위 통합 추출"
        "</div>",
        unsafe_allow_html=True,
    )

if collect_clicked:
    # ────────────────────────────────────────────────
    # ⚠️ 자동 미적합 정리 로직 제거 (2026-05-13)
    # 이유: market_fit_check는 substring 비교 + 영유아 키워드 필수라
    #       정상 브랜드도 잘못 부적합 판정될 수 있음
    #       (예: "프라젠트라" — 브랜드명/주력상품명에 영유아 키워드 없으면 A 탈락)
    # 결과: 사용자가 검토할 기회도 없이 신규 수집 직후 자동 삭제됨
    # 해결: 정리는 사용자가 명시적으로 트리거 (디테일 패널의 "🗑️ 이 셀러 삭제"
    #       또는 메인 표 체크박스 선택 → 일괄 삭제 사용)
    # ────────────────────────────────────────────────
    # try:
    #     with st.spinner("기존 브랜드 검토 중..."):
    #         scan = scan_unfit_brands()
    #         del_cands = scan["delete_candidates"]
    #         if del_cands:
    #             delete_unfit_brands(del_cands)
    # except Exception:
    #     pass

    # ────────────────────────────────────────────────
    # 수집 (모드별 인자 전달 — collect_5.py에 [3.5/6] A+B+C 필터 내장)
    # collect_5.py 내부 필터는 신규 수집 후보에만 적용 → 기존 DB 영향 X
    # ────────────────────────────────────────────────
    # 모드별 명령줄 인자 구성
    cmd_args = [sys.executable, "collect_5.py", "--count", str(collect_n)]
    if collect_mode == "카테고리 지정" and collect_category:
        cmd_args += ["--category", collect_category]
        mode_label = f"'{collect_category}' 카테고리"
    elif collect_mode == "키워드 입력" and collect_keywords:
        cmd_args += ["--keywords", collect_keywords]
        mode_label = f"키워드 '{collect_keywords[:30]}'"
    else:
        mode_label = "전체 카테고리"

    with st.spinner(f"수집 중... {mode_label} → {collect_n}건 (약 {15 + collect_n * 8}초)"):
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            result = subprocess.run(
                cmd_args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,   # 5분 → 10분 (Fallback + 확장 라운드 시간 여유)
                cwd=script_dir,
            )
            # 수집 로그 파싱 → 핵심 숫자만 추출 (복잡한 로그 X, 요약만)
            import re
            stdout = result.stdout or ""

            def _grab(pattern: str, default: int = 0) -> int:
                m = re.search(pattern, stdout)
                return int(m.group(1)) if m else default

            saved_n = _grab(r"저장 완료:\s*(\d+)\s*/\s*\d+건")
            big_n = _grab(r"대기업 자동 제외:\s*(\d+)건")
            a_fail = _grab(r"A 탈락.*?:\s*(\d+)건")
            b_fail = _grab(r"B 탈락.*?:\s*(\d+)건")
            c_fail = _grab(r"C 탈락.*?:\s*(\d+)건")
            flagship_fail = stdout.count("주력상품 재검사 탈락")

            # ⭐ 디버그용: collect_5.py 출력을 Streamlit Cloud 로그에 전달
            # subprocess.run의 capture_output 때문에 print가 안 보이는 문제 해결
            import sys as _sys
            print("\n" + "=" * 60, file=_sys.stderr)
            print("📋 [collect_5.py 실행 출력]", file=_sys.stderr)
            print("=" * 60, file=_sys.stderr)
            print(stdout, file=_sys.stderr)
            if result.stderr:
                print("\n" + "=" * 60, file=_sys.stderr)
                print("⚠️ [collect_5.py 에러 출력]", file=_sys.stderr)
                print("=" * 60, file=_sys.stderr)
                print(result.stderr, file=_sys.stderr)
            print("=" * 60 + "\n", file=_sys.stderr)

            # session_state에 결과 저장 (rerun 후에도 표시 유지)
            st.session_state["last_collect_summary"] = {
                "success": result.returncode == 0,
                "saved": saved_n,
                "target": collect_n,
                "big": big_n,
                "a": a_fail,
                "b": b_fail,
                "c": c_fail,
                "flagship": flagship_fail,
                "mode": collect_mode,
            }

            if result.returncode == 0:
                st.cache_data.clear()
                st.rerun()
        except subprocess.TimeoutExpired:
            st.error("시간 초과 (5분). 네트워크 또는 API 응답이 늦을 수 있어요. 잠시 후 다시 시도하세요.")
        except FileNotFoundError:
            st.error("collect_5.py 파일을 찾을 수 없어요. dashboard.py와 같은 폴더에 있는지 확인하세요.")
        except Exception as e:
            st.error(f"실행 중 오류: {e}")


# ─────────────────────────────────────────────────────────────────
# 수집 결과 요약 (깔끔한 메시지 — 닫기 버튼으로 닫기 가능)
# 핵심 원칙:
#   - 요청 수만큼 채워짐  → 단순 "N건 수집 완료" (사유 X)
#   - 요청 수에 못 미침   → 부족분 표시 + 사유 간략히
#   - 0건 수집           → 사유 표시
# ─────────────────────────────────────────────────────────────────
if "last_collect_summary" in st.session_state:
    s = st.session_state["last_collect_summary"]
    saved = s.get("saved", 0)
    target = s.get("target", 0)
    big = s.get("big", 0)
    a, b, c = s.get("a", 0), s.get("b", 0), s.get("c", 0)
    flagship = s.get("flagship", 0)

    # 제외 사유 간략 (0건은 표시 X)
    reasons = []
    if a:
        reasons.append(f"영유아 시장 외 {a}건")
    if b:
        reasons.append(f"대기업 {b}건")
    if c:
        reasons.append(f"다른 시장 {c}건")
    if flagship:
        reasons.append(f"주력상품 부적합 {flagship}건")
    if big:
        reasons.append(f"카페 노출 50만+ {big}건")
    reasons_text = " · ".join(reasons) if reasons else ""

    res_col1, res_col2 = st.columns([6, 1])
    with res_col1:
        if s["success"] and saved >= target and saved > 0:
            # 요청 수만큼 다 채움 — 깔끔하게 완료만 표시
            st.success(f"✅ {saved}건 수집 완료")
        elif s["success"] and 0 < saved < target:
            # 부족 — 부족분 + 사유
            short = target - saved
            msg = f"✅ {saved}건 수집 완료  /  ⚠️ {short}건 부족"
            if reasons_text:
                msg += f" ({reasons_text})"
            st.warning(msg)
        elif s["success"] and saved == 0:
            # 0건 — 모두 필터에서 제외
            info_msg = "수집 0건. 모든 후보가 필터에서 제외됨"
            if reasons_text:
                info_msg += f" ({reasons_text})"
            st.info(info_msg)
        else:
            st.error("❌ 수집 실패")
    with res_col2:
        if st.button("닫기", key="close_summary_btn", use_container_width=True):
            del st.session_state["last_collect_summary"]
            st.rerun()


st.markdown("---")


if df.empty:
    st.warning(
        "누적 데이터가 없어요. 위의 **+ 5건 새로 수집** 버튼을 클릭해서 첫 데이터를 만드세요."
    )
    st.stop()


# ─────────────────────────────────────────────────────────────────
# 사이드바 — 필터
# ─────────────────────────────────────────────────────────────────
st.sidebar.markdown("## 필터")

# 점수
score_min = int(df["Selpic 점수"].min())
score_max = int(df["Selpic 점수"].max())
if score_min == score_max:
    st.sidebar.caption(f"Selpic 점수: 모두 {score_min}점")
    score_range = (score_min, score_max)
else:
    score_range = st.sidebar.slider(
        "Selpic 점수",
        score_min,
        score_max,
        (score_min, score_max),
    )

# 마케팅 등급은 비노출 (마케팅 활동 단계로 통합됨)
selected_grades = None

# 마케팅 활동 단계 (도입기/성장기/확장기) — legacy 자동 정규화
selected_sizes = None
size_col = "마케팅 활동 단계 (자동)"
if size_col in df.columns:
    # 저장된 텍스트에서 단계명만 추출 (예: "확장기 — 카페..." → "확장기")
    df["_단계명"] = df[size_col].fillna("").astype(str).str.split(" ").str[0]
    # Legacy → 신규 매핑 (옛 데이터 "초기/안정기"를 새 단계로 정규화)
    LEGACY_STAGE_MAP = {"초기": "도입기", "안정기": "확장기"}
    df["_단계명"] = df["_단계명"].replace(LEGACY_STAGE_MAP)
    # 사이드바 표시는 항상 3단계 (legacy는 위에서 이미 매핑됨)
    all_sizes = ["도입기", "성장기", "확장기"]
    selected_sizes = st.sidebar.multiselect(
        "마케팅 활동 단계",
        all_sizes,
        default=all_sizes,
    )

# 카테고리
selected_cats = None
if "발견 카테고리" in df.columns:
    all_cats = sorted(df["발견 카테고리"].dropna().unique())
    selected_cats = st.sidebar.multiselect(
        "카테고리",
        all_cats,
        default=all_cats,
    )

# 영업 상태
selected_statuses = None
if "영업 상태 (수기)" in df.columns:
    status_values = df["영업 상태 (수기)"].fillna("").unique().tolist()
    # 빈값을 "(미입력)"으로 표시용 변환
    display_options = ["(미입력)" if not s else s for s in SALES_STATUS_OPTIONS]
    selected_display = st.sidebar.multiselect(
        "영업 상태",
        display_options,
        default=display_options,
    )
    selected_statuses = ["" if s == "(미입력)" else s for s in selected_display]

# 검색
search_brand = st.sidebar.text_input("브랜드명 검색", placeholder="부분 일치")

st.sidebar.markdown("---")
st.sidebar.caption(f"총 누적 {len(df)}건")


# ─────────────────────────────────────────────────────────────────
# 필터 적용
# ─────────────────────────────────────────────────────────────────
filtered = df[
    (df["Selpic 점수"] >= score_range[0])
    & (df["Selpic 점수"] <= score_range[1])
]
# 마케팅 등급은 비노출 (필터 미적용)
if selected_sizes is not None and "_단계명" in filtered.columns:
    # 저장된 텍스트의 단계명만 비교 (예: "확장기 — 카페..." → "확장기")
    filtered = filtered[filtered["_단계명"].isin(selected_sizes)]
if selected_cats is not None:
    filtered = filtered[filtered["발견 카테고리"].isin(selected_cats)]
if selected_statuses is not None and "영업 상태 (수기)" in filtered.columns:
    filtered = filtered[filtered["영업 상태 (수기)"].fillna("").isin(selected_statuses)]
if search_brand:
    filtered = filtered[
        filtered["브랜드명"].str.contains(search_brand, na=False, case=False)
    ]


# ─────────────────────────────────────────────────────────────────
# KPI 4개 (누적/필터/평균/등급) — 비노출 처리
# 필요 시 아래 블록 주석 해제하면 다시 표시됨
# ─────────────────────────────────────────────────────────────────
# col1, col2, col3, col4 = st.columns(4)
# col1.metric("누적 셀러", f"{len(df):,}")
# col2.metric("필터 결과", f"{len(filtered):,}")
# if len(filtered) > 0:
#     col3.metric("평균 점수", f"{filtered['Selpic 점수'].mean():.1f}")
#     if "마케팅 등급 (자동)" in filtered.columns:
#         top_grade = filtered["마케팅 등급 (자동)"].mode().values[0]
#         grade_count = (filtered["마케팅 등급 (자동)"] == top_grade).sum()
#         col4.metric("최다 등급", f"{top_grade} · {grade_count}건")
# else:
#     col3.metric("평균 점수", "—")
#     col4.metric("최다 등급", "—")


# ─────────────────────────────────────────────────────────────────
# 영업 후보 셀러 테이블 (메인)
# ─────────────────────────────────────────────────────────────────
st.markdown(f"## 영업 후보")

# ─────────────────────────────────────────────────────────────────
# 테이블 상단 필터 (브랜드 검색 / 카테고리 / 영업 상태 / 마케팅 활동)
# 멀티 셀렉트 → 여러 값 동시 선택 가능 / 빈 값 = 전체
# 브랜드 검색을 맨 왼쪽 — 가장 자주 쓰는 필터
# ─────────────────────────────────────────────────────────────────
ft_col1, ft_col2, ft_col3, ft_col4, ft_col5 = st.columns([1.4, 1.4, 1.4, 1.4, 1.5])

with ft_col1:
    sel_brand_search_tbl = st.text_input(
        "브랜드 검색",
        value="",
        placeholder="브랜드명 입력",
        key="tbl_filter_brand_search",
        label_visibility="visible",
    )

with ft_col2:
    if "카테고리" in filtered.columns:
        cat_options = sorted([c for c in filtered["카테고리"].dropna().unique() if c])
    else:
        cat_options = []
    sel_categories_tbl = st.multiselect(
        "카테고리",
        options=cat_options,
        default=[],
        placeholder="전체",
        key="tbl_filter_category",
        label_visibility="visible",
    )

with ft_col3:
    if "영업 상태 (수기)" in filtered.columns:
        status_raw = filtered["영업 상태 (수기)"].fillna("").astype(str).unique().tolist()
        status_options = sorted([s for s in status_raw if s])
    else:
        status_options = []
    sel_statuses_tbl = st.multiselect(
        "영업 상태",
        options=status_options,
        default=[],
        placeholder="전체",
        key="tbl_filter_status",
        label_visibility="visible",
    )

with ft_col4:
    # 표시는 3단계만 — legacy 데이터는 내부 매핑으로 처리
    stage_options = ["도입기", "성장기", "확장기"]
    sel_grades_tbl = st.multiselect(
        "마케팅 활동",
        options=stage_options,
        default=[],
        placeholder="전체",
        key="tbl_filter_grade",
        label_visibility="visible",
    )

# 필터 적용
if sel_categories_tbl:
    filtered = filtered[filtered["카테고리"].isin(sel_categories_tbl)]
if sel_statuses_tbl:
    filtered = filtered[
        filtered["영업 상태 (수기)"].fillna("").astype(str).isin(sel_statuses_tbl)
    ]
if sel_grades_tbl and "_단계명" in filtered.columns:
    # 단계명 추출하여 비교 (저장값: "확장기 — 카페...")
    filtered = filtered[filtered["_단계명"].isin(sel_grades_tbl)]
if sel_brand_search_tbl.strip():
    keyword = sel_brand_search_tbl.strip().lower()
    filtered = filtered[
        filtered["브랜드명"].fillna("").astype(str).str.lower().str.contains(keyword, na=False)
    ]

with ft_col5:
    active_filters = sum([
        bool(sel_categories_tbl),
        bool(sel_statuses_tbl),
        bool(sel_grades_tbl),
        bool(sel_brand_search_tbl.strip()),
    ])
    st.markdown(
        f"<div style='padding-top: 30px; color: #6b7280; font-size: 13px; text-align: right;'>"
        f"<b style='color: #111827; font-size: 18px;'>{len(filtered)}건</b> "
        + (f"· 필터 {active_filters}개 적용 중" if active_filters else "· 필터 미적용")
        + "</div>",
        unsafe_allow_html=True,
    )

# 빈 스토어 채우기 클릭 처리 (표 상단 버튼)
# fix_clicked_table 핸들러는 버튼 정의(CSV 옆) 이후로 이동


def save_one_brand(brand: str, new_values: dict) -> bool:
    """한 셀러의 수기 컬럼 값을 Supabase에 update.
    new_values: 한글 컬럼명 → 값 (예: {'영업 상태 (수기)': '미팅 중'})
    """
    sb = get_supabase_client()
    if not sb or not brand:
        return False
    # 한글 → 영문 컬럼명 변환
    db_values = {}
    for kor_col, val in new_values.items():
        if kor_col in KOR_TO_DB:
            db_values[KOR_TO_DB[kor_col]] = "" if val is None else str(val)
    if not db_values:
        return False
    try:
        sb.table(TABLE_NAME).update(db_values).eq("brand_name", brand).execute()
        return True
    except Exception:
        return False


if len(filtered) > 0:
    filtered = filtered.copy()   # 캐시 보호용

    # ⚠️ 이중 안전망 — load_all_data()의 캐시가 살아있어도 안전
    for col in MANUAL_COLUMNS:
        if col not in filtered.columns:
            filtered[col] = ""
        filtered[col] = filtered[col].fillna("").astype(str)

    # ─────────────────────────────────────────────────────────
    # 메인 테이블 — 6 컬럼만 (브랜드명/영업상태/이메일/연락처/등급/스토어)
    # 행 클릭 → 디테일 패널 펼침
    # ─────────────────────────────────────────────────────────
    main_cols = [
        "수집일",              # 최신순으로 보기 위함 — No. 바로 옆에 위치
        "브랜드명",
        "스마트스토어 주소",   # 브랜드명 바로 옆 — 빠르게 셀러 페이지 확인
        "카테고리",            # 스토어 옆 — 주력상품 기반 자동 분류
        "영업 상태 (수기)",
        "전화 (수기)",         # 영업 상태 다음 — 통화 우선 워크플로우
        "이메일 (수기)",
        "마케팅 활동 단계 (자동)",   # 도입기/성장기/확장기 — 등급 대체
    ]
    safe_main_cols = [c for c in main_cols if c in filtered.columns]

    extra_cols = ["_source_file"] if "_source_file" in filtered.columns else []
    main_df = filtered[safe_main_cols + extra_cols + (["Selpic 점수"] if "Selpic 점수" in filtered.columns else [])]

    # 정렬: 수집일 desc만 적용 (안정 정렬)
    # 내부 동일 날짜는 load_all_data의 id desc 순서 그대로 유지 → 방금 INSERT한 게 위로
    if "수집일" in main_df.columns:
        main_df = main_df.sort_values(
            "수집일", ascending=False, kind="stable"
        ).reset_index(drop=True)
    else:
        main_df = main_df.reset_index(drop=True)

    # 마케팅 활동 단계 표시값 — 컬러 도트 + 단계명 (Linear/Notion 스타일)
    # 도입기 = ⚪ 회색 / 성장기 = 🟢 초록 / 확장기 = 🟡 노랑
    # Legacy(옛 데이터): 초기 → 도입기 / 안정기 → 확장기 로 매핑
    STAGE_DISPLAY = {
        # 신규
        "도입기": "⚪  도입기",
        "성장기": "🟢  성장기",
        "확장기": "🟡  확장기",
        # Legacy (옛 데이터 매핑)
        "초기": "⚪  도입기",
        "안정기": "🟡  확장기",
    }

    def stage_to_display(text: str) -> str:
        """저장된 '확장기 — 카페 중심...' 텍스트에서 단계만 추출 + 도트 적용"""
        if not text:
            return ""
        # 첫 단어가 단계명
        first_word = str(text).split(" ")[0].strip()
        return STAGE_DISPLAY.get(first_word, first_word)

    display_df = main_df[safe_main_cols].copy()
    if "마케팅 활동 단계 (자동)" in display_df.columns:
        display_df["마케팅 활동 단계 (자동)"] = (
            display_df["마케팅 활동 단계 (자동)"]
            .fillna("")
            .astype(str)
            .apply(stage_to_display)
        )

    # 순번 열 추가 (역순) — 최근 수집이 큰 숫자
    # 표는 수집일 desc로 정렬됨 → 위쪽이 최신 → 위쪽 행에 N, 아래로 갈수록 1
    # 신규 수집 시 가장 큰 번호 부여 → 누적 흐름 한눈에
    n_rows = len(display_df)
    display_df.insert(0, "No.", range(n_rows, 0, -1))

    # ─────────────────────────────────────────────────────────
    # AgGrid 설정 — 가운데 정렬 + 행 클릭 선택 + 깔끔한 헤더
    # ─────────────────────────────────────────────────────────
    gb = GridOptionsBuilder.from_dataframe(display_df)

    # 모든 셀 가운데 정렬 (default)
    # ⚠️ streamlit-aggrid는 AG Grid 래퍼 — 옵션 이름이 약간 다름
    #     filter (X) → filterable (O)
    #     sortable (X) → sorteable (O, 오타 그대로)
    gb.configure_default_column(
        cellStyle={"text-align": "center"},
        filterable=False,                  # ⭐ 컬럼별 필터 메뉴 완전 제거
        sorteable=True,                    # 헤더 클릭 → 정렬 가능
        resizable=True,
        editable=False,
        groupable=False,
        suppressMenu=True,                 # 메뉴 버튼 (구버전)
        suppressHeaderMenuButton=True,     # 메뉴 버튼 (신버전)
        suppressMovable=True,              # 컬럼 드래그 비활성
        menuTabs=[],                       # 메뉴 탭 없음 → 필터/컬럼/필터 다 X
    )

    # 컬럼별 너비/표시 이름
    gb.configure_column("No.", width=70)
    if "수집일" in display_df.columns:
        gb.configure_column("수집일", headerName="수집일", width=110)
    gb.configure_column("브랜드명", width=180)
    if "카테고리" in display_df.columns:
        gb.configure_column("카테고리", headerName="카테고리", width=130)
    if "영업 상태 (수기)" in display_df.columns:
        gb.configure_column("영업 상태 (수기)", headerName="영업 상태", width=130)
    if "이메일 (수기)" in display_df.columns:
        gb.configure_column("이메일 (수기)", headerName="이메일", width=200)
    if "전화 (수기)" in display_df.columns:
        gb.configure_column("전화 (수기)", headerName="연락처", width=140)
    if "마케팅 활동 단계 (자동)" in display_df.columns:
        gb.configure_column("마케팅 활동 단계 (자동)", headerName="마케팅 활동", width=130)

    # 스토어 링크 컬럼 — "열기" 텍스트 + 클릭 시 새 탭으로 이동
    # ⚠️ AG Grid는 React 래핑이라 DOM 엘리먼트 직접 반환 불가
    # → valueFormatter로 표시 텍스트를 "열기"로 바꾸고, onCellClicked로 링크 열기
    if "스마트스토어 주소" in display_df.columns:
        gb.configure_column(
            "스마트스토어 주소",
            headerName="스토어",
            width=90,
            valueFormatter=JsCode("""
            function(params) {
                return params.value ? '열기' : '';
            }
            """),
            cellStyle={
                "color": "#2563eb",
                "text-decoration": "underline",
                "cursor": "pointer",
                "text-align": "center",
                "font-weight": "500",
            },
            onCellClicked=JsCode("""
            function(params) {
                if (params.value) {
                    window.open(params.value, '_blank', 'noopener');
                }
            }
            """),
        )

    # 다중 선택 + 체크박스 (1열에 표시)
    # 행 클릭 = 그 행만 선택 (다른 선택 해제) → 디테일 열림
    # 체크박스 클릭 = 다중 선택 (삭제용)
    gb.configure_selection(
        selection_mode="multiple",
        use_checkbox=True,
        header_checkbox=True,
    )

    # 헤더 가운데 정렬 CSS
    custom_css = {
        ".ag-header-cell-label": {"justify-content": "center"},
        ".ag-header-cell-text": {"font-weight": "600"},
    }

    grid_options = gb.build()

    # ⚠️ wrapper 옵션이 안 먹힘 → AG Grid raw API에 직접 강제 적용
    # defaultColDef에 모든 필터/메뉴 차단 옵션을 직접 설정
    grid_options.setdefault("defaultColDef", {})
    grid_options["defaultColDef"].update({
        "filter": False,
        "floatingFilter": False,
        "suppressMenu": True,
        "suppressHeaderMenuButton": True,
        "suppressFiltersToolPanel": True,
        "suppressColumnsToolPanel": True,
        "menuTabs": [],
        "sortable": True,
        "resizable": True,
        "suppressMovable": True,
    })
    # 다중 선택 모드 — 행 클릭은 선택을 1개로 replace, 체크박스로 multi
    grid_options["suppressRowClickSelection"] = False
    grid_options["rowSelection"] = "multiple"

    # 모든 컬럼에도 동일하게 강제 적용 (per-column override 방지)
    for col in grid_options.get("columnDefs", []):
        col["filter"] = False
        col["suppressMenu"] = True
        col["suppressHeaderMenuButton"] = True
        col["menuTabs"] = []

    response = AgGrid(
        display_df,
        gridOptions=grid_options,
        height=420,
        width="100%",
        allow_unsafe_jscode=True,
        update_mode=GridUpdateMode.SELECTION_CHANGED,
        fit_columns_on_grid_load=False,
        theme="streamlit",
        custom_css=custom_css,
        key="main_aggrid",
    )

    # ─────────────────────────────────────────────────────────
    # 1) 체크박스로 선택된 행 → 다중 선택 (삭제용)
    # 2) 행 클릭 → 디테일 패널용 (selection과 별개)
    # ─────────────────────────────────────────────────────────
    # 체크박스 선택된 브랜드 추출
    selected = response.get("selected_rows") if isinstance(response, dict) else response["selected_rows"]
    selected_brands = []
    if selected is not None:
        if isinstance(selected, pd.DataFrame) and len(selected) > 0:
            selected_brands = [
                str(row.get("브랜드명", "")).strip()
                for _, row in selected.iterrows()
                if str(row.get("브랜드명", "")).strip()
            ]
        elif isinstance(selected, list) and len(selected) > 0:
            selected_brands = [
                str(row.get("브랜드명", "")).strip()
                for row in selected
                if str(row.get("브랜드명", "")).strip()
            ]

    # 디테일 패널용 — 첫 번째 선택된 브랜드 (체크박스든 행 클릭이든 동일)

    # 액션 바 — 2건 이상 체크박스로 선택 시에만 표시
    # 행 1개 클릭 시는 디테일만 (액션 바 X) → 깔끔한 검토 UX
    if len(selected_brands) >= 2:
        action_col_left, action_col_right = st.columns([4, 2])
        with action_col_left:
            st.markdown(
                f"<div style='background: #fff7e6; border: 0.5px solid #fbbf24; "
                f"border-radius: 8px; padding: 10px 14px; font-size: 14px; color: #92400e;'>"
                f"<b>{len(selected_brands)}건 선택됨</b> · 일괄 삭제하려면 우측 버튼"
                f"</div>",
                unsafe_allow_html=True,
            )
        with action_col_right:
            if st.button(
                f"🗑️ 선택 {len(selected_brands)}건 삭제",
                type="primary",
                use_container_width=True,
                key="delete_selected_btn",
            ):
                st.session_state["pending_delete_brands"] = selected_brands
                st.session_state["show_delete_confirm"] = True
                st.rerun()

    # 삭제 확인 모달 (session_state 기반)
    if st.session_state.get("show_delete_confirm", False):
        pending = st.session_state.get("pending_delete_brands", [])
        if pending:
            st.markdown("---")
            with st.container(border=True):
                st.markdown(
                    f"<div style='color: #991b1b; font-size: 17px; font-weight: 600; margin-bottom: 8px;'>"
                    f"⚠️ 정말 삭제할까요?</div>"
                    f"<div style='font-size: 14px; color: #6b7280; margin-bottom: 12px;'>"
                    f"<b style='color: #111827;'>{len(pending)}건의 브랜드</b>가 영구 삭제됩니다. 되돌릴 수 없습니다."
                    f"</div>",
                    unsafe_allow_html=True,
                )
                # 삭제 대상 미리보기 (최대 10개)
                preview = pending[:10]
                preview_html = "<br>".join([f"• <s>{b}</s>" for b in preview])
                if len(pending) > 10:
                    preview_html += f"<br><span style='color: #9ca3af;'>... 그 외 {len(pending) - 10}건</span>"
                st.markdown(
                    f"<div style='background: #f3f4f6; padding: 10px 14px; border-radius: 8px; "
                    f"font-size: 13px; line-height: 1.7; max-height: 220px; overflow-y: auto;'>"
                    f"{preview_html}</div>",
                    unsafe_allow_html=True,
                )
                st.markdown("<br>", unsafe_allow_html=True)
                btn_col1, btn_col2, btn_col3 = st.columns([3, 1.2, 1.5])
                with btn_col2:
                    if st.button("취소", use_container_width=True, key="delete_cancel_btn"):
                        st.session_state["show_delete_confirm"] = False
                        st.session_state["pending_delete_brands"] = []
                        st.rerun()
                with btn_col3:
                    if st.button(
                        "🗑️ 삭제 실행",
                        type="primary",
                        use_container_width=True,
                        key="delete_confirm_btn",
                    ):
                        with st.spinner(f"{len(pending)}건 삭제 중..."):
                            result = delete_brands_by_name(pending)
                            st.cache_data.clear()
                            if result["deleted"] > 0:
                                st.toast(f"✅ {result['deleted']}건 삭제 완료", icon="✅")
                            if result["failed"] > 0:
                                st.toast(f"⚠️ {result['failed']}건 삭제 실패", icon="⚠️")
                            st.session_state["show_delete_confirm"] = False
                            st.session_state["pending_delete_brands"] = []
                            st.rerun()

    # CSV 다운로드 + 빈 스토어 채우기 (테이블 아래 액션 영역)
    # 체크박스 선택 우선:
    #   - 1개 이상 체크: 체크된 행만 다운로드
    #   - 미체크: 현재 필터 결과 전체 다운로드
    download_col1, download_col2, download_col3 = st.columns([1.3, 1, 3.7])
    with download_col1:
        # CSV 내보내기 시 비노출 컬럼 (사용자 요청)
        # 내부용·기술적 컬럼은 영업자에게 불필요
        CSV_EXCLUDE_COLS = [
            "Selpic 점수",
            "점수 근거",
            "마케팅 등급 (자동)",
            "마케팅 분석 메모 (수기)",
            "created_at",
            "updated_at",
            "수집 모드",
            "관심고객수 (자동)",
            "리뷰수 (수기)",   # 영업자에게 불필요
            "_단계명",        # 내부 정규화용
            "_source_file",   # 내부 메타데이터
            "id",             # DB 키
        ]

        # CSV 컬럼명을 셀러 디테일 라벨과 통일 (혼동 방지)
        # 디테일 패널에서 사용하는 라벨 = CSV 헤더로 동일하게
        CSV_RENAME = {
            # 수기 입력 컬럼 (디테일 라벨과 동일)
            "영업 상태 (수기)":    "영업 상태",
            "전화 (수기)":         "연락처",                    # ⭐ 전화 → 연락처
            "이메일 (수기)":       "이메일",
            "관심고객수 (수기)":   "스마트스토어 관심고객수",   # ⭐ 출처 명시
            "활동 메모 (수기)":    "활동 메모",
            "상호 (수기)":         "상호",
            "대표 (수기)":         "대표자 성함",                # ⭐ 의미 명확
            # 자동 컬럼 (디테일 라벨과 동일하게)
            "주력상품명":               "주력 상품",
            "마케팅 활동 단계 (자동)":  "마케팅 활동",
            "마케팅 채널별 노출 (자동)": "마케팅 노출",
        }

        # 체크박스 선택된 행만 / 없으면 전체
        # ⚠️ 정규화 양쪽 일관성: selected_brands는 .strip() 적용됨 → 비교 시 양쪽 strip
        if selected_brands:
            selected_set = set(selected_brands)
            export_source = filtered[
                filtered["브랜드명"].fillna("").astype(str).str.strip().isin(selected_set)
            ]
            export_count = len(export_source)
            download_label = f"⬇️ 선택 {export_count}건 다운로드"
            file_suffix = f"selected_{export_count}"
        else:
            export_source = filtered
            export_count = len(export_source)
            download_label = f"⬇️ 전체 {export_count}건 다운로드"
            file_suffix = "all"

        export_df = export_source.drop(
            columns=[c for c in CSV_EXCLUDE_COLS if c in export_source.columns],
            errors="ignore",
        )
        # 컬럼명 통일 (디테일 라벨과 일치)
        export_df = export_df.rename(
            columns={k: v for k, v in CSV_RENAME.items() if k in export_df.columns}
        )

        # 컬럼 순서 재배치 (사용자 가독성 우선)
        #   - 수집일 → 맨 왼쪽
        #   - 카테고리 → 브랜드명 바로 오른쪽
        #   - 스마트스토어 관심고객수 → 스마트스토어 주소 바로 왼쪽
        # 영업자가 표를 좌→우로 읽을 때 자연스러운 순서:
        #   [수집일 · 브랜드명 · 카테고리 · ... · 스마트스토어 관심고객수 · 스마트스토어 주소 · ...]
        def _move_column(cols: list, target: str, anchor: str, position: str) -> list:
            """target 컬럼을 anchor 컬럼의 'before' 또는 'after' 위치로 이동.
            둘 중 하나라도 없으면 변경 없이 반환 (safe-fallback).
            """
            if target not in cols or anchor not in cols or target == anchor:
                return cols
            cols = [c for c in cols if c != target]   # 일단 제거
            idx = cols.index(anchor)
            insert_at = idx + 1 if position == "after" else idx
            cols.insert(insert_at, target)
            return cols

        def _move_to_front(cols: list, target: str) -> list:
            """target 컬럼을 맨 왼쪽(0번째)으로 이동. 없으면 그대로."""
            if target not in cols:
                return cols
            cols = [c for c in cols if c != target]
            cols.insert(0, target)
            return cols

        cols = list(export_df.columns)
        cols = _move_to_front(cols, "수집일")                                       # ⭐ 수집일 맨 왼쪽
        cols = _move_column(cols, "카테고리", "브랜드명", "after")
        cols = _move_column(cols, "스마트스토어 관심고객수", "스마트스토어 주소", "before")
        export_df = export_df[cols]
        # to_csv()는 string 반환 시 encoding 무시 → 직접 BOM 포함 bytes로 변환
        # BOM(Byte Order Mark) 있어야 Excel이 UTF-8 인식 → 한글 깨짐 방지
        csv_export = export_df.to_csv(index=False).encode("utf-8-sig")
        today_label = datetime.now().strftime("%Y-%m-%d_%H%M")
        st.download_button(
            label=download_label,
            data=csv_export,
            file_name=f"PICK10_{file_suffix}_{today_label}.csv",
            mime="text/csv",
            use_container_width=True,
            disabled=(export_count == 0),
        )
    with download_col2:
        fix_clicked_table = st.button(
            "빈 스토어 채우기",
            use_container_width=True,
            key="fix_btn_table",
            help="스토어 주소가 비어있거나 검색 페이지로 fallback된 행을 다시 검색해 진짜 스마트스토어 URL로 갱신",
        )

    # 일괄 삭제 expander 제거 — 메인 표 체크박스로 충분

    # 빈 스토어 채우기 클릭 처리 (버튼 정의 직후)
    if fix_clicked_table:
        with st.spinner("빈 스토어 주소 갱신 중..."):
            try:
                result = fill_empty_urls_in_all_csvs()
                st.cache_data.clear()
                if result["fixed"] > 0:
                    st.toast(f"✅ {result['fixed']}건 갱신 완료", icon="✅")
                else:
                    st.toast("이미 모두 정상입니다", icon="ℹ️")
                st.rerun()
            except Exception:
                st.toast("갱신 중 오류 발생", icon="⚠️")

    # ─────────────────────────────────────────────────────────
    # 디테일 패널 — 선택된 행이 있을 때만
    # ─────────────────────────────────────────────────────────
    # AgGrid 응답에서 선택된 행 추출 (DataFrame 또는 list 형식 둘 다 지원)
    # 디테일 패널 — 첫 번째 선택된 브랜드 (행 클릭이든 체크박스든)
    sel_brand = selected_brands[0] if selected_brands else ""

    if sel_brand:
        # 원본에서 전체 정보 가져오기
        sel_full = filtered[filtered["브랜드명"] == sel_brand]
        if len(sel_full) > 0:
            sel_full = sel_full.iloc[0]

            st.markdown("")
            st.markdown(
                "<div style='color: #6b7280; font-size: 13px; margin-bottom: 8px;'>↓ 선택한 셀러 디테일</div>",
                unsafe_allow_html=True,
            )

            with st.container(border=True):
                # 헤더 (브랜드명 + 분석 정보 + 우측 삭제 버튼)
                header_left, header_right = st.columns([5, 1])
                with header_left:
                    # 카테고리 = 메인 표와 동일 로직 (classify_category(주력상품))
                    detail_category = classify_category(
                        str(sel_full.get("주력상품명", "")).strip()
                    )
                    st.markdown(
                        f"<div style='font-size: 24px; font-weight: 600; color: #111827;'>{sel_brand}</div>"
                        f"<div style='font-size: 16px; color: #4b5563; margin-top: 6px; line-height: 1.5;'>"
                        f"<b style='color: #374151;'>분석:</b> "
                        f"{detail_category} · "
                        f"{sel_full.get(size_col, '-')}"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                with header_right:
                    st.markdown("<div style='padding-top: 8px;'></div>", unsafe_allow_html=True)
                    if st.button(
                        "🗑️ 이 셀러 삭제",
                        key=f"delete_single_{sel_brand}",
                        use_container_width=True,
                    ):
                        st.session_state["pending_delete_brands"] = [sel_brand]
                        st.session_state["show_delete_confirm"] = True
                        st.rerun()

                st.markdown("---")

                # 자동 정보 (read-only) — 주력 상품 + 가격
                product_col, price_col = st.columns([4, 1])
                with product_col:
                    st.caption("주력 상품")
                    st.text(str(sel_full.get("주력상품명", "-")))
                with price_col:
                    st.caption("가격")
                    st.text(str(sel_full.get("가격", "-")))

                st.markdown("")

                # 마케팅 노출 — 4개 강조 메트릭 카드 (★ 강세 채널)
                exposure = parse_marketing_exposure(
                    sel_full.get("마케팅 채널별 노출 (자동)", "")
                )
                if exposure:
                    st.caption("마케팅 노출 (3채널)")
                    max_channel = max(exposure, key=exposure.get)
                    max_value = max(exposure.values()) or 1

                    metric_cols = st.columns(3)
                    for i, ch in enumerate(["블로그", "카페", "SNS"]):
                        with metric_cols[i]:
                            value = exposure.get(ch, 0)
                            is_top = (ch == max_channel)
                            ratio = (value / max_value) if max_value > 0 else 0
                            ratio_pct = max(2, min(100, int(ratio * 100)))

                            if is_top:
                                # 강세 채널 — 검정 테두리 + ★ + 진한 막대
                                st.markdown(
                                    f"""<div style="border: 2px solid #111827; border-radius: 8px; padding: 13px 11px; text-align: center; background: white;">
                                        <div style="font-size: 11px; color: #111827; font-weight: 500; margin-bottom: 6px;">{ch} ★</div>
                                        <div style="font-size: 20px; font-weight: 600; color: #111827;">{value:,}</div>
                                        <div style="height: 4px; background: #e5e7eb; border-radius: 2px; margin-top: 8px; position: relative;">
                                            <div style="position: absolute; left: 0; top: 0; height: 4px; width: 100%; background: #111827; border-radius: 2px;"></div>
                                        </div>
                                    </div>""",
                                    unsafe_allow_html=True,
                                )
                            else:
                                # 일반 채널 — 옅은 테두리 + 회색 막대
                                st.markdown(
                                    f"""<div style="border: 1px solid #ececec; border-radius: 8px; padding: 14px 12px; text-align: center; background: white;">
                                        <div style="font-size: 11px; color: #6b7280; font-weight: 500; margin-bottom: 6px;">{ch}</div>
                                        <div style="font-size: 20px; font-weight: 600; color: #111827;">{value:,}</div>
                                        <div style="height: 4px; background: #e5e7eb; border-radius: 2px; margin-top: 8px; position: relative;">
                                            <div style="position: absolute; left: 0; top: 0; height: 4px; width: {ratio_pct}%; background: #9ca3af; border-radius: 2px;"></div>
                                        </div>
                                    </div>""",
                                    unsafe_allow_html=True,
                                )

                    st.markdown(
                        f"<div style='font-size: 11px; color: #6b7280; margin-top: 10px;'>★ 가장 강세인 채널 — 영업 메시지 어프로치 힌트</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.caption("마케팅 노출")
                    st.text("-")

                st.markdown("")

                # ⭐ 2026-05-26: 영업상태/관심고객수 위치 조정
                #   - 스마트스토어 관심고객수 → 제거 (자동 컬럼만 사용)
                #   - 영업상태 → 활동메모 위로 이동 (하단으로 재배치)
                # current_status는 하단 selectbox에서 사용
                current_status = str(sel_full.get("영업 상태 (수기)", ""))
                if current_status not in SALES_STATUS_OPTIONS:
                    current_status = ""

                # ⭐ 사업자등록번호 입력 + 자동 수집 버튼 (2026-05-26 추가)
                # 사용자가 사업자번호 한 줄만 입력하면 → 공정위 API로 상호·대표·전화·주소 자동 채움
                current_biz_num = (
                    str(sel_full.get("사업자번호 (수기)", "")).strip()
                    or str(sel_full.get("사업자번호 (자동)", "")).strip()
                )
                auto_company_name = str(sel_full.get("상호 (자동)", "")).strip()

                # 자동 수집 정보가 비어있고 사업자번호도 없으면 → 빨간 강조 안내
                needs_biz_input = (not current_biz_num) and (not auto_company_name)

                if needs_biz_input:
                    st.markdown(
                        "<div style='background: #fef2f2; border: 2px solid #ef4444; "
                        "border-radius: 8px; padding: 12px 16px; margin-bottom: 12px;'>"
                        "<div style='color: #dc2626; font-weight: 600; font-size: 14px;'>"
                        "⚠️ 사업자등록번호 입력 필요"
                        "</div>"
                        "<div style='color: #7f1d1d; font-size: 12px; margin-top: 4px;'>"
                        "사업자번호 입력 후 [🔍 사업자정보 수집] 버튼 클릭 → "
                        "상호·대표·전화·주소 자동 입력"
                        "</div>"
                        "</div>",
                        unsafe_allow_html=True,
                    )

                biz_col_input, biz_col_btn = st.columns([2.5, 1.5])
                with biz_col_input:
                    new_biz_num = st.text_input(
                        "사업자등록번호 📌" if needs_biz_input else "사업자등록번호",
                        value=current_biz_num,
                        placeholder="예) 123-45-67890 또는 1234567890",
                        help="입력 후 옆 [사업자정보 수집] 버튼 클릭 → 공정위 DB에서 정보 자동 수집",
                        key=f"biz_num_{sel_brand}",
                    )
                with biz_col_btn:
                    st.markdown("<div style='padding-top: 28px;'></div>", unsafe_allow_html=True)
                    collect_biz_clicked = st.button(
                        "🔍 사업자정보 수집",
                        type="primary" if (new_biz_num.strip() and not auto_company_name) else "secondary",
                        use_container_width=True,
                        key=f"collect_biz_{sel_brand}",
                        disabled=not new_biz_num.strip(),
                        help="공정위 통신판매사업자 DB에서 상호·대표·전화·주소 자동 수집",
                    )

                # 자동 수집된 정보 표시 (참고용) — 주소 제외 (2026-05-26)
                # ⭐ 빈 값은 "🔍 미확인" 표기 (수기 확인 필요 명시)
                if auto_company_name:
                    def _fmt(val):
                        v = str(val).strip()
                        return v if v and v not in ("-", "None") else "🔍 미확인"

                    st.markdown(
                        f"<div style='background: #f0fdf4; border: 1px solid #86efac; "
                        f"border-radius: 6px; padding: 10px 14px; margin-bottom: 12px; font-size: 13px;'>"
                        f"<b style='color: #166534;'>✅ 공정위 DB 자동 수집 정보</b><br>"
                        f"<span style='color: #14532d;'>"
                        f"상호: {auto_company_name} · "
                        f"대표: {_fmt(sel_full.get('대표 (자동)', ''))} · "
                        f"전화: {_fmt(sel_full.get('전화 (자동)', ''))}<br>"
                        f"이메일: {_fmt(sel_full.get('이메일 (자동)', ''))}"
                        f"</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                st.markdown("<div style='margin-top: 8px;'></div>", unsafe_allow_html=True)

                # 사업자정보 수집 버튼 클릭 처리
                # 4단계 통합 수집:
                #   1. 공정위 API (상호·사업자번호·통신판매업번호)
                #   2. Naver 검색 (대표·전화·주소)
                #   3. Google 검색 (대표·전화·주소 보완)
                #   4. 모든 결과 통합 + DB 저장
                if collect_biz_clicked and new_biz_num.strip():
                    with st.spinner(
                        "🔍 공정위 DB + Naver + Google 통합 검색 중... (10-30초)"
                    ):
                        try:
                            from business_info_collector import (
                                fetch_ftc_telecom_seller_info,
                                collect_extended_business_info,
                            )

                            # 1차: 공정위 API (상호, 사업자번호)
                            biz_info = fetch_ftc_telecom_seller_info(
                                business_number=new_biz_num.strip()
                            )

                            # 2차: Naver + Google 확장 검색 (대표/전화/주소)
                            # 공정위에서 가져온 상호명 사용 (정확)
                            company_for_search = (
                                biz_info.get("company_name", "") or sel_brand
                            )
                            ext_info = collect_extended_business_info(
                                brand_name=company_for_search,
                                business_number=new_biz_num.strip(),
                            )

                            # 통합: 공정위 우선 + 확장 검색 보완
                            # ⭐ 2026-05-26: 주소(address) 자동수집 제외, 이메일 추가
                            merged = dict(biz_info)
                            for k in ["ceo", "phone", "email"]:
                                if not merged.get(k) and ext_info.get(k):
                                    merged[k] = ext_info[k]

                            if merged and any(merged.get(k) for k in
                                              ["company_name", "ceo", "phone", "email"]):
                                # 출처 표시
                                sources = []
                                if biz_info.get("company_name"):
                                    sources.append("공정위DB")
                                if ext_info.get("ceo") or ext_info.get("phone") or ext_info.get("email"):
                                    sources.append("Naver/Google")

                                # 자동 수집 정보를 DB에 업데이트 — 주소 제외
                                update_data = {
                                    "사업자번호 (수기)": new_biz_num.strip(),
                                    "상호 (자동)": merged.get("company_name", ""),
                                    "대표 (자동)": merged.get("ceo", ""),
                                    "사업자번호 (자동)": merged.get(
                                        "business_number", new_biz_num.strip()
                                    ),
                                    "전화 (자동)": merged.get("phone", ""),
                                    "이메일 (자동)": merged.get("email", ""),
                                    "사업자정보 출처 (자동)": ", ".join(sources),
                                    "사업자정보 신뢰도 (자동)": (
                                        "높음" if len(sources) >= 2 else "중간"
                                    ),
                                }

                                # ⭐ 2026-05-26: 수기 필드 4개 자동 채움
                                # 사용자가 이미 입력한 값은 보존, 빈 값일 때만 자동값으로 채움
                                # → 빨간 박스(상호/대표/이메일/연락처)에 자동 기재
                                def _is_empty(val):
                                    return not str(val or "").strip()

                                manual_fill_map = [
                                    ("상호 (수기)",   "company_name"),
                                    ("대표 (수기)",   "ceo"),
                                    ("이메일 (수기)", "email"),
                                    ("전화 (수기)",   "phone"),
                                ]
                                for manual_col, merged_key in manual_fill_map:
                                    current_val = sel_full.get(manual_col, "")
                                    auto_val = merged.get(merged_key, "")
                                    if _is_empty(current_val) and auto_val:
                                        update_data[manual_col] = auto_val
                                if save_one_brand(sel_brand, update_data):
                                    filled = sum(
                                        1 for k in ["company_name", "ceo", "phone", "email"]
                                        if merged.get(k)
                                    )
                                    st.success(
                                        f"✅ {filled}개 항목 자동 수집 완료! "
                                        f"(출처: {', '.join(sources)}) 화면 갱신 중..."
                                    )
                                    st.cache_data.clear()
                                    st.rerun()
                                else:
                                    st.error("저장 실패. Supabase 연결 확인.")
                            else:
                                st.warning(
                                    "⚠️ 사업자 정보 찾기 실패. "
                                    "사업자번호 확인 또는 수기 입력 권장."
                                )
                        except Exception as e:
                            st.error(f"❌ 수집 오류: {e}")

                st.markdown("<div style='margin-top: 8px;'></div>", unsafe_allow_html=True)

                # 상호 / 대표자 성함 (신규)
                edit_col_a, edit_col_b = st.columns(2)
                with edit_col_a:
                    new_company = st.text_input(
                        "상호",
                        value=(
                            str(sel_full.get("상호 (수기)", "")).strip()
                            or str(sel_full.get("상호 (자동)", "")).strip()
                        ),
                        placeholder="사업자등록증 상의 상호 (자동 채워질 수 있음)",
                        key=f"company_{sel_brand}",
                    )
                with edit_col_b:
                    new_ceo = st.text_input(
                        "대표자 성함",
                        value=(
                            str(sel_full.get("대표 (수기)", "")).strip()
                            or str(sel_full.get("대표 (자동)", "")).strip()
                        ),
                        placeholder="대표자 성함 (자동 채워질 수 있음)",
                        key=f"ceo_{sel_brand}",
                    )

                edit_col3, edit_col4 = st.columns(2)
                with edit_col3:
                    new_email = st.text_input(
                        "이메일",
                        value=(
                            str(sel_full.get("이메일 (수기)", "")).strip()
                            or str(sel_full.get("이메일 (자동)", "")).strip()
                        ),
                        placeholder="이메일 (자동 채워질 수 있음)",
                        key=f"email_{sel_brand}",
                    )
                with edit_col4:
                    new_phone = st.text_input(
                        "연락처",
                        value=(
                            str(sel_full.get("전화 (수기)", "")).strip()
                            or str(sel_full.get("전화 (자동)", "")).strip()
                        ),
                        placeholder="연락처 (자동 채워질 수 있음)",
                        key=f"phone_{sel_brand}",
                    )

                # ⭐ 영업 상태 (하단 재배치) — 활동메모 바로 위
                new_status = st.selectbox(
                    "영업 상태",
                    SALES_STATUS_OPTIONS,
                    index=SALES_STATUS_OPTIONS.index(current_status),
                    key=f"status_{sel_brand}",
                )

                new_memo = st.text_area(
                    "활동 메모",
                    value=str(sel_full.get("활동 메모 (수기)", "")),
                    height=140,
                    placeholder="예) 5/8 첫 메일, 5/12 답장, 5/15 미팅 예정",
                    key=f"memo_{sel_brand}",
                )

                # 저장 버튼
                save_btn_col1, save_btn_col2 = st.columns([1, 5])
                with save_btn_col1:
                    save_clicked = st.button(
                        "변경사항 저장",
                        type="primary",
                        use_container_width=True,
                        key=f"save_{sel_brand}",
                    )
                with save_btn_col2:
                    st.caption("저장 안 하고 다른 셀러로 이동하면 변경분 사라져요")

                if save_clicked:
                    # ⭐ 2026-05-26: 관심고객수 (수기) 제거 — UI에서 빠짐
                    new_values = {
                        "영업 상태 (수기)": new_status,
                        "상호 (수기)":      new_company,
                        "대표 (수기)":      new_ceo,
                        "사업자번호 (수기)": new_biz_num,   # ⭐ 사업자번호 저장
                        "이메일 (수기)": new_email,
                        "전화 (수기)": new_phone,
                        "활동 메모 (수기)": new_memo,
                    }
                    if save_one_brand(sel_brand, new_values):
                        st.cache_data.clear()
                        st.success(f"{sel_brand} 저장 완료")
                        st.rerun()
                    else:
                        st.error("저장 실패. Supabase 연결 확인.")
    else:
        st.markdown("")
        st.info("위 테이블에서 셀러 행을 클릭하면 디테일 패널이 열려요.")
else:
    st.info("필터 조건을 만족하는 셀러가 없어요. 필터를 완화해보세요.")


# ─────────────────────────────────────────────────────────────────
# 푸터
# ─────────────────────────────────────────────────────────────────
st.markdown("---")
st.caption("PICK10 v4 · 셀픽 영업처 자동 큐레이션 도구")
