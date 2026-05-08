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
    initial_sidebar_state="expanded",
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
    예: '블로그 13,146 · 카페 19,689 · 뉴스 863 · 지식인 3,150'
        → {'블로그': 13146, '카페': 19689, '뉴스': 863, '지식인': 3150}
    """
    if not text or pd.isna(text):
        return {}
    result = {}
    pattern = r"(블로그|카페|뉴스|지식인)\s+([\d,]+)"
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

            # 1차: redirect 추적으로 진짜 셀러 메인 URL 시도
            real_url = ""
            link = fetch_smartstore_link(brand)
            if link:
                real_url = resolve_real_store_url(link)

            # 2차: 실패 시 검색 페이지 URL fallback (절대 빈 칸 X)
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
        result = sb.table(TABLE_NAME).select("*").execute()
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

        return df
    except Exception as e:
        st.error(f"Supabase 데이터 로드 실패: {e}")
        return pd.DataFrame()


df = load_all_data()


# ─────────────────────────────────────────────────────────────────
# 헤더
# ─────────────────────────────────────────────────────────────────
st.markdown("# PICK10")
st.markdown(
    "<div class='subtitle'>셀픽 영업처 큐레이션 대시보드 · 누적 셀러 관리 · 영업 우선순위 정렬</div>",
    unsafe_allow_html=True,
)

# ─────────────────────────────────────────────────────────────────
# 액션 — 새 5건 수집 버튼
# ─────────────────────────────────────────────────────────────────
action_col1, action_col2, action_col3 = st.columns([1, 1, 3])
with action_col1:
    collect_clicked = st.button(
        "+ 5건 새로 수집",
        type="primary",
        use_container_width=True,
    )
with action_col2:
    fix_clicked = st.button(
        "빈 스토어 주소 채우기",
        use_container_width=True,
        help="스토어 주소가 비어있거나 검색 페이지로 fallback된 행을 다시 검색해 진짜 스마트스토어 URL로 갱신",
    )
with action_col3:
    st.markdown(
        "<div style='padding-top: 8px; color: #6b7280; font-size: 13px;'>"
        "수집: 30~60초 · 채우기: 빈 행 N건 × 약 0.3초"
        "</div>",
        unsafe_allow_html=True,
    )

if collect_clicked:
    with st.spinner("수집 중... 네이버 검색 + 분석 진행 중 (30~60초 소요)"):
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            result = subprocess.run(
                [sys.executable, "collect_5.py"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                cwd=script_dir,
            )
            if result.returncode == 0:
                # 캐시 무효화 → 새 데이터 로드되도록
                st.cache_data.clear()
                st.success("수집 완료! 새 셀러가 아래 테이블에 추가됐어요.")
                # 로그 일부 보여주기 (선택)
                with st.expander("실행 로그 보기", expanded=False):
                    st.code(result.stdout[-3000:], language="text")
                # 자동 새로고침
                st.rerun()
            else:
                st.error("수집 실패")
                st.code(
                    f"종료 코드: {result.returncode}\n\n"
                    f"--- stderr ---\n{result.stderr[-1500:]}\n\n"
                    f"--- stdout (마지막 부분) ---\n{result.stdout[-1500:]}",
                    language="text",
                )
        except subprocess.TimeoutExpired:
            st.error("시간 초과 (5분). 네트워크 또는 API 응답이 늦을 수 있어요. 잠시 후 다시 시도하세요.")
        except FileNotFoundError:
            st.error("collect_5.py 파일을 찾을 수 없어요. dashboard.py와 같은 폴더에 있는지 확인하세요.")
        except Exception as e:
            st.error(f"실행 중 오류: {e}")

if fix_clicked:
    with st.spinner("빈 스토어 주소 검색 + CSV 갱신 중..."):
        try:
            result = fill_empty_urls_in_all_csvs()
            st.cache_data.clear()
            if result["fixed"] > 0:
                st.success(
                    f"{result['fixed']}건 스마트스토어 URL로 갱신 완료. "
                    f"({result['files']}개 파일 검사)"
                )
            else:
                st.info("빈 행이 없거나 모두 정상이에요.")
            if result["not_found"]:
                with st.expander(f"검색 API에서 못 찾은 셀러 {len(result['not_found'])}건", expanded=False):
                    st.write(", ".join(result["not_found"]))
            st.rerun()
        except Exception as e:
            st.error(f"채우기 중 오류: {e}")

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

# 마케팅 등급
selected_grades = None
if "마케팅 등급 (자동)" in df.columns:
    grade_order = ["상", "중", "하"]
    all_grades = [g for g in grade_order if g in df["마케팅 등급 (자동)"].dropna().unique()]
    if not all_grades:
        all_grades = sorted(df["마케팅 등급 (자동)"].dropna().unique())
    selected_grades = st.sidebar.multiselect(
        "마케팅 등급",
        all_grades,
        default=all_grades,
    )

# 규모 추정
selected_sizes = None
size_col = "규모 추정 (자동)"
if size_col in df.columns:
    all_sizes = sorted(df[size_col].dropna().unique())
    selected_sizes = st.sidebar.multiselect(
        "규모 추정",
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
if selected_grades is not None:
    filtered = filtered[filtered["마케팅 등급 (자동)"].isin(selected_grades)]
if selected_sizes is not None:
    filtered = filtered[filtered[size_col].isin(selected_sizes)]
if selected_cats is not None:
    filtered = filtered[filtered["발견 카테고리"].isin(selected_cats)]
if selected_statuses is not None and "영업 상태 (수기)" in filtered.columns:
    filtered = filtered[filtered["영업 상태 (수기)"].fillna("").isin(selected_statuses)]
if search_brand:
    filtered = filtered[
        filtered["브랜드명"].str.contains(search_brand, na=False, case=False)
    ]


# ─────────────────────────────────────────────────────────────────
# KPI 4개
# ─────────────────────────────────────────────────────────────────
col1, col2, col3, col4 = st.columns(4)
col1.metric("누적 셀러", f"{len(df):,}")
col2.metric("필터 결과", f"{len(filtered):,}")

if len(filtered) > 0:
    col3.metric("평균 점수", f"{filtered['Selpic 점수'].mean():.1f}")
    if "마케팅 등급 (자동)" in filtered.columns:
        top_grade = filtered["마케팅 등급 (자동)"].mode().values[0]
        grade_count = (filtered["마케팅 등급 (자동)"] == top_grade).sum()
        col4.metric("최다 등급", f"{top_grade} · {grade_count}건")
else:
    col3.metric("평균 점수", "—")
    col4.metric("최다 등급", "—")


# ─────────────────────────────────────────────────────────────────
# 영업 후보 셀러 테이블 (메인)
# ─────────────────────────────────────────────────────────────────
st.markdown(f"## 영업 후보 — {len(filtered)}건")

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
        "브랜드명",
        "스마트스토어 주소",   # 브랜드명 바로 옆 — 빠르게 셀러 페이지 확인
        "영업 상태 (수기)",
        "전화 (수기)",         # 영업 상태 다음 — 통화 우선 워크플로우
        "이메일 (수기)",
        "마케팅 등급 (자동)",
    ]
    safe_main_cols = [c for c in main_cols if c in filtered.columns]

    extra_cols = ["_source_file"] if "_source_file" in filtered.columns else []
    sort_col = "Selpic 점수" if "Selpic 점수" in filtered.columns else safe_main_cols[0]
    main_df = filtered[safe_main_cols + extra_cols + (["Selpic 점수"] if "Selpic 점수" in filtered.columns else [])]
    main_df = main_df.sort_values([sort_col], ascending=[False]).reset_index(drop=True)

    # 등급 표시값 — 컬러 도트 + 텍스트 (Linear/Notion 스타일 미니멀)
    # 셀 자체는 흰색 유지, 도트만 색상 → 가장 깔끔한 간소화
    GRADE_DISPLAY = {
        "상": "🟢  상",
        "중": "🟡  중",
        "하": "⚪  하",
    }

    display_df = main_df[safe_main_cols].copy()
    if "마케팅 등급 (자동)" in display_df.columns:
        display_df["마케팅 등급 (자동)"] = (
            display_df["마케팅 등급 (자동)"]
            .map(GRADE_DISPLAY)
            .fillna("")
        )

    # 순번 열 추가 (Selpic 점수 정렬 후 1부터) — 브랜드명 앞에 위치
    display_df.insert(0, "No.", range(1, len(display_df) + 1))

    # ─────────────────────────────────────────────────────────
    # AgGrid 설정 — 가운데 정렬 + 행 클릭 선택 + 깔끔한 헤더
    # ─────────────────────────────────────────────────────────
    gb = GridOptionsBuilder.from_dataframe(display_df)

    # 모든 셀 가운데 정렬 (default)
    gb.configure_default_column(
        cellStyle={"text-align": "center"},
        sortable=True,
        filter=False,
        resizable=True,
        suppressMenu=True,
        wrapText=False,
    )

    # 컬럼별 너비/표시 이름
    gb.configure_column("No.", width=70, pinned="left")
    gb.configure_column("브랜드명", width=200)
    if "영업 상태 (수기)" in display_df.columns:
        gb.configure_column("영업 상태 (수기)", headerName="영업 상태", width=130)
    if "이메일 (수기)" in display_df.columns:
        gb.configure_column("이메일 (수기)", headerName="이메일", width=200)
    if "전화 (수기)" in display_df.columns:
        gb.configure_column("전화 (수기)", headerName="연락처", width=140)
    if "마케팅 등급 (자동)" in display_df.columns:
        gb.configure_column("마케팅 등급 (자동)", headerName="마케팅 활동", width=120)

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

    # 단일 행 선택 (체크박스 X, 행 클릭 O)
    gb.configure_selection(selection_mode="single", use_checkbox=False)

    # 헤더 가운데 정렬 CSS
    custom_css = {
        ".ag-header-cell-label": {"justify-content": "center"},
        ".ag-header-cell-text": {"font-weight": "600"},
    }

    grid_options = gb.build()

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

    # CSV 다운로드 (테이블 위 액션 영역)
    download_col1, download_col2 = st.columns([1, 5])
    with download_col1:
        csv_export = filtered.to_csv(index=False, encoding="utf-8-sig")
        today_label = datetime.now().strftime("%Y-%m-%d_%H%M")
        st.download_button(
            label="CSV 다운로드",
            data=csv_export,
            file_name=f"PICK10_filtered_{today_label}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    # ─────────────────────────────────────────────────────────
    # 디테일 패널 — 선택된 행이 있을 때만
    # ─────────────────────────────────────────────────────────
    # AgGrid 응답에서 선택된 행 추출 (DataFrame 또는 list 형식 둘 다 지원)
    selected = response.get("selected_rows") if isinstance(response, dict) else response["selected_rows"]
    sel_brand = ""
    if selected is not None:
        if isinstance(selected, pd.DataFrame) and len(selected) > 0:
            sel_brand = str(selected.iloc[0].get("브랜드명", "")).strip()
        elif isinstance(selected, list) and len(selected) > 0:
            sel_brand = str(selected[0].get("브랜드명", "")).strip()

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
                # 헤더
                header_col1, header_col2 = st.columns([5, 1])
                with header_col1:
                    st.markdown(
                        f"<div style='font-size: 20px; font-weight: 600; color: #111827;'>{sel_brand}</div>"
                        f"<div style='font-size: 12px; color: #6b7280; margin-top: 2px;'>"
                        f"{sel_full.get('발견 카테고리', '')} · "
                        f"Selpic {sel_full.get('Selpic 점수', '-')}점 · "
                        f"마케팅 {sel_full.get('마케팅 등급 (자동)', '-')} · "
                        f"규모 {sel_full.get(size_col, '-')}"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                with header_col2:
                    store_url = sel_full.get("스마트스토어 주소", "")
                    if store_url:
                        st.markdown(
                            f"<div style='text-align: right; padding-top: 8px;'>"
                            f"<a href='{store_url}' target='_blank' style='color: #2563eb; font-size: 13px; text-decoration: none;'>"
                            f"스토어 열기 ↗</a></div>",
                            unsafe_allow_html=True,
                        )

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
                    st.caption("마케팅 노출 (4채널)")
                    max_channel = max(exposure, key=exposure.get)
                    max_value = max(exposure.values()) or 1

                    metric_cols = st.columns(4)
                    for i, ch in enumerate(["블로그", "카페", "뉴스", "지식인"]):
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

                # 수기 편집 영역
                edit_col1, edit_col2 = st.columns(2)
                with edit_col1:
                    current_status = str(sel_full.get("영업 상태 (수기)", ""))
                    if current_status not in SALES_STATUS_OPTIONS:
                        current_status = ""
                    new_status = st.selectbox(
                        "영업 상태",
                        SALES_STATUS_OPTIONS,
                        index=SALES_STATUS_OPTIONS.index(current_status),
                        key=f"status_{sel_brand}",
                    )
                with edit_col2:
                    new_count = st.text_input(
                        "관심고객수",
                        value=str(sel_full.get("관심고객수 (수기)", "")),
                        key=f"count_{sel_brand}",
                    )

                edit_col3, edit_col4 = st.columns(2)
                with edit_col3:
                    new_email = st.text_input(
                        "이메일",
                        value=str(sel_full.get("이메일 (수기)", "")),
                        key=f"email_{sel_brand}",
                    )
                with edit_col4:
                    new_phone = st.text_input(
                        "연락처",
                        value=str(sel_full.get("전화 (수기)", "")),
                        key=f"phone_{sel_brand}",
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
                    new_values = {
                        "영업 상태 (수기)": new_status,
                        "관심고객수 (수기)": new_count,
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
