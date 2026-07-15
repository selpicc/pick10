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

# ⭐ 2026-06: 클라우드 배포(Streamlit Cloud) 대응.
#   클라우드엔 .env가 없고 비밀키가 st.secrets로 들어옴 → 그 값을
#   os.environ 으로 옮겨서 기존 os.getenv 코드가 그대로 작동하게 한다.
#   (supabase_client 가 import 시 os.getenv 를 읽으므로 그 전에 실행)
try:
    if hasattr(st, "secrets") and len(st.secrets):
        for _k in st.secrets.keys():
            os.environ.setdefault(_k, str(st.secrets[_k]))
except Exception:
    pass

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
# 2026-07 정리: 미접촉·응답 대기·미팅 중 제거, '컨택중' 하나로 통합.
#   (미접촉 = 빈값과 사실상 같고, 응답대기/미팅중은 실무에서 '컨택중' 하나로 충분)
SALES_STATUS_OPTIONS = [
    "",            # 빈값 (기본)
    "메일 발송",
    "컨택중",
    "계약 완료",
    "거절",
    "기타) 패싱",   # 의도적 보류/스킵 (다음 라운드에 다시 검토)
]

# 옛 데이터 호환 — 사라진 상태값이 DB에 남아 있어도 드롭다운/필터가 깨지지 않게 매핑.
#   (수기로 입력해둔 값을 조용히 날리지 않는다)
LEGACY_STATUS_MAP = {
    "미접촉": "",
    "응답 대기": "컨택중",
    "미팅 중": "컨택중",
}


# fetch_smartstore_link() / resolve_real_store_url() / needs_fix() 제거됨 (2026-07)
#   '빈 스토어 채우기' 버튼 전용 함수들이었고, 버튼과 함께 삭제했다.
#   같은 기능이 필요하면 독립 스크립트가 그대로 있다:
#     venv\Scripts\python fix_smartstore_urls.py


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



# fill_empty_urls_in_all_csvs() 제거됨 (2026-07) — '빈 스토어 채우기' 버튼과 함께 삭제.
# 이 함수를 부르는 곳이 더 이상 없다.


# ─────────────────────────────────────────────────────────────────
# 시장 미적합 브랜드 검사 + 삭제 (A+B+C 필터 기반)
# 영업 진행 중인 브랜드는 자동 보호
# ─────────────────────────────────────────────────────────────────
# 영업 진행 중이면 자동 삭제에서 보호. 옛 상태값(응답 대기·미팅 중)도 그대로 두어
# 아직 DB에 남아 있는 legacy 행이 보호에서 빠지지 않게 한다.
PROTECTED_STATUSES = {
    "메일 발송", "컨택중", "계약 완료", "거절", "기타) 패싱",
    "응답 대기", "미팅 중",   # legacy
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

# 메일 상태가 언제 갱신되는지 한 줄 안내 (자동추적 스케줄 + 마지막 실행 시각)
#   마지막 시각은 메일_추적_로그.txt 의 파일 수정 시각(=매 실행마다 갱신)으로 표시.
_mail_log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "메일_추적_로그.txt")
_last_upd = ""
try:
    if os.path.exists(_mail_log):
        _ts = datetime.fromtimestamp(os.path.getmtime(_mail_log))
        _last_upd = f" · 마지막 업데이트 {_ts:%Y-%m-%d %H:%M}"
except Exception:
    _last_upd = ""
st.caption(f"🕘 메일 상태는 매일 09:30 · 14:00 · 17:00 자동 업데이트됩니다{_last_upd}")

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
    st.markdown(
        "<div style='color: #6b7280; font-size: 12px; margin-top: 6px;'>"
        "쉼표(,)로 여러 키워드 가능 · 각 키워드별 검색 후 점수 상위 통합 추출"
        "</div>",
        unsafe_allow_html=True,
    )

# ─────────────────────────────────────────────────────────────────
# 수집 실행 — ⭐ 2026-06-01: 백그라운드 실행 + 2초 폴링
#   기존엔 subprocess를 블로킹으로 돌려서, 깊은 검색+공식홈 확인으로 몇 분 걸리는
#   동안 화면이 멈춘 것처럼 보였음('반응 없음' 오해). → 백그라운드(Popen)로 띄우고
#   2초마다 상태 확인. '수집 중' 배너가 경과시간과 함께 끝까지 떠 있고,
#   끝나면 자동으로 목록 갱신. 버튼 사용감은 그대로.
# ─────────────────────────────────────────────────────────────────
import time as _time
import tempfile as _tempfile

# (A) 버튼 클릭 → 백그라운드 수집 시작
if collect_clicked and not st.session_state.get("collect_running"):
    cmd_args = [sys.executable, "collect_5.py", "--count", str(collect_n)]
    if collect_mode == "카테고리 지정" and collect_category:
        cmd_args += ["--category", collect_category]
        mode_label = f"'{collect_category}' 카테고리"
    elif collect_mode == "키워드 입력" and collect_keywords:
        cmd_args += ["--keywords", collect_keywords]
        mode_label = f"키워드 '{collect_keywords[:30]}'"
    else:
        mode_label = "전체 카테고리"

    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        logpath = os.path.join(
            _tempfile.gettempdir(), f"pick10_collect_{int(_time.time())}.log"
        )
        logfile = open(logpath, "w", encoding="utf-8", errors="replace")
        # 자식 프로세스가 항상 UTF-8로 출력하도록 (한글 깨짐 방지)
        _env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        proc = subprocess.Popen(
            cmd_args, stdout=logfile, stderr=subprocess.STDOUT,
            cwd=script_dir, text=True, env=_env,
        )
        st.session_state["collect_running"] = True
        st.session_state["collect_proc"] = proc
        st.session_state["collect_logf"] = logfile
        st.session_state["collect_logpath"] = logpath
        st.session_state["collect_start"] = _time.time()
        st.session_state["collect_meta"] = {
            "mode_label": mode_label,
            "collect_n": collect_n,
            "collect_mode": collect_mode,
        }
        st.rerun()
    except FileNotFoundError:
        st.error("collect_5.py 파일을 찾을 수 없어요. dashboard.py와 같은 폴더에 있는지 확인하세요.")
    except Exception as e:
        st.error(f"수집 시작 중 오류: {e}")

# (B) 수집 진행 중 → '수집 중' 배너 표시 + 2초마다 자동 확인
if st.session_state.get("collect_running"):
    proc = st.session_state["collect_proc"]
    meta = st.session_state["collect_meta"]
    elapsed = int(_time.time() - st.session_state["collect_start"])
    mm, ss = elapsed // 60, elapsed % 60
    ret = proc.poll()

    # 25분 초과 → 강제 종료 (무한 대기 방지)
    #   2026-06: 클라우드 무료 사양은 날마다 속도 편차가 커서 15분(900s)에
    #   아슬아슬하게 걸리던 케이스 → 25분(1500s)으로 여유 확대.
    if ret is None and elapsed > 1500:
        try:
            proc.kill()
        except Exception:
            pass
        ret = -9   # 타임아웃 표시

    if ret is None:
        # 아직 수집 중 → 노란 배너 + 2초 후 자동 새로고침
        st.warning(
            f"🔄 수집 중입니다…  {meta['mode_label']} → {meta['collect_n']}건\n\n"
            f"⏱ {mm}분 {ss}초 경과 · 보통 3~5분 걸려요. "
            f"멈춘 게 아니니 그대로 두세요 — 끝나면 자동으로 목록이 갱신됩니다."
        )
        _time.sleep(2)
        st.rerun()
    else:
        # 수집 종료 → 로그 읽어 요약 만들고 상태 정리
        try:
            st.session_state["collect_logf"].close()
        except Exception:
            pass
        stdout = ""
        try:
            with open(st.session_state["collect_logpath"], "r",
                      encoding="utf-8", errors="replace") as f:
                stdout = f.read()
        except Exception:
            pass

        import re as _re

        def _grab(pattern, default=0):
            m = _re.search(pattern, stdout)
            return int(m.group(1)) if m else default

        st.session_state["last_collect_summary"] = {
            "success": (ret == 0),
            "saved": _grab(r"저장 완료:\s*(\d+)\s*/\s*\d+건"),
            "target": meta["collect_n"],
            "big": _grab(r"대기업 자동 제외:\s*(\d+)건"),
            "a": _grab(r"A 탈락.*?:\s*(\d+)건"),
            "b": _grab(r"B 탈락.*?:\s*(\d+)건"),
            "c": _grab(r"C 탈락.*?:\s*(\d+)건"),
            "flagship": stdout.count("주력상품 재검사 탈락"),
            "mode": meta["collect_mode"],
            "timeout": (ret == -9),
        }

        # 디버그 로그를 Streamlit 콘솔로
        import sys as _sys
        print("\n" + "=" * 60, file=_sys.stderr)
        print("📋 [collect_5.py 출력]", file=_sys.stderr)
        print(stdout, file=_sys.stderr)
        print("=" * 60 + "\n", file=_sys.stderr)

        # 임시 로그 삭제 + 상태 키 정리
        try:
            os.remove(st.session_state["collect_logpath"])
        except Exception:
            pass
        for _k in ["collect_running", "collect_proc", "collect_logf",
                   "collect_logpath", "collect_start", "collect_meta"]:
            st.session_state.pop(_k, None)

        if ret == 0:
            st.cache_data.clear()
        st.rerun()


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
        elif s.get("timeout"):
            st.error(
                "⏱ 시간 초과 (25분)로 중단했어요. 수집 건수를 줄이거나(1~2건) "
                "잠시 후 다시 시도해 주세요."
            )
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


# 영업메일 초안 일괄 생성 버튼은 제거됨 (2026-07).
# 이유: 대시보드에서 전체 브랜드를 대상으로 한 번에 만들면 원치 않는 초안이 무더기로
#       생긴다. 초안은 '방금 수집한 브랜드'에만 만드는 게 맞아서 명령으로 일원화했다.
#   venv\Scripts\python 메일초안_생성.py --brands "브랜드A,브랜드B"
# (위 712행의 구분선 하나로 충분 — 여기 있던 두 번째 구분선은 빈 띠를 만들어 삭제)


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

# 마케팅 활동 단계 필터 제거됨 (2026-07, 사용자 요청) — 사이드바·표 상단 양쪽.
#   단계 값 자체는 셀러 디테일에 그대로 표시되므로 _단계명 정규화는 유지한다.
selected_sizes = None
size_col = "마케팅 활동 단계 (자동)"
if size_col in df.columns:
    # 저장된 텍스트에서 단계명만 추출 (예: "확장기 — 카페..." → "확장기")
    df["_단계명"] = df[size_col].fillna("").astype(str).str.split(" ").str[0]
    # Legacy → 신규 매핑 (옛 데이터 "초기/안정기"를 새 단계로 정규화)
    LEGACY_STAGE_MAP = {"초기": "도입기", "안정기": "확장기"}
    df["_단계명"] = df["_단계명"].replace(LEGACY_STAGE_MAP)

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
# 마케팅 등급·활동 단계는 필터 미적용 (필터 UI 제거됨 — 2026-07)
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
# ⭐ 2026-07: 메일 알림 — 회신 온 브랜드 / 팔로업할 때가 된 브랜드
#   회신을 놓치면 영업 기회를 통째로 날린다 → 표보다 위에 띄운다.
#   값은 메일_추적.py 가 Gmail에서 읽어 DB에 채운다 (여기선 읽기만).
# ─────────────────────────────────────────────────────────────────
if "메일 스레드ID" in df.columns:
    _tracked = df[df["메일 스레드ID"].fillna("").astype(str).str.strip() != ""]
    _replied = _tracked[
        _tracked["메일 회신일"].fillna("").astype(str).str.strip() != ""
    ] if "메일 회신일" in _tracked.columns else _tracked.iloc[0:0]

    # 팔로업 대상: 발송 7일 경과 · 회신 없음 · 팔로업 2회 미만
    _fu = []
    if "메일 발송일" in _tracked.columns:
        for _, _r in _tracked.iterrows():
            if str(_r.get("메일 회신일", "") or "").strip():
                continue
            _sent = str(_r.get("메일 발송일", "") or "").strip()
            if not _sent:
                continue
            try:
                _d = datetime.fromisoformat(_sent.replace("Z", "+00:00"))
                _days = (datetime.now(_d.tzinfo) - _d).days
            except Exception:
                continue
            try:
                _c = int(_r.get("팔로업 횟수", 0) or 0)
            except Exception:
                _c = 0
            if _days >= 7 and _c < 2:
                _fu.append(str(_r.get("브랜드명", "")))

    if len(_replied) > 0:
        st.error(
            f"💬 **회신 온 브랜드 {len(_replied)}건** — "
            + ", ".join(_replied["브랜드명"].astype(str).tolist())
            + "  · Gmail에서 확인하세요"
        )
    if _fu:
        st.warning(
            f"🔔 **팔로업할 때가 된 브랜드 {len(_fu)}건** — " + ", ".join(_fu)
            + "  · 초안 만들기: `venv\\Scripts\\python 메일_추적.py --followup`"
        )


# ─────────────────────────────────────────────────────────────────
# 영업 후보 셀러 테이블 (메인)
# ─────────────────────────────────────────────────────────────────
st.markdown(f"## 영업 후보")

# ─────────────────────────────────────────────────────────────────
# 테이블 상단 필터 (브랜드 검색 / 카테고리 / 영업 상태 / 마케팅 활동)
# 멀티 셀렉트 → 여러 값 동시 선택 가능 / 빈 값 = 전체
# 브랜드 검색을 맨 왼쪽 — 가장 자주 쓰는 필터
# ─────────────────────────────────────────────────────────────────
ft_col1, ft_col2, ft_col3, ft_col5 = st.columns([1.4, 1.4, 1.4, 2.9])

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

# '마케팅 활동' 필터 제거됨 (2026-07, 사용자 요청)
#   단계 정보 자체는 셀러 디테일에 그대로 표시된다.

# 필터 적용
if sel_categories_tbl:
    filtered = filtered[filtered["카테고리"].isin(sel_categories_tbl)]
if sel_statuses_tbl:
    filtered = filtered[
        filtered["영업 상태 (수기)"].fillna("").astype(str).isin(sel_statuses_tbl)
    ]
if sel_brand_search_tbl.strip():
    keyword = sel_brand_search_tbl.strip().lower()
    filtered = filtered[
        filtered["브랜드명"].fillna("").astype(str).str.lower().str.contains(keyword, na=False)
    ]

with ft_col5:
    active_filters = sum([
        bool(sel_categories_tbl),
        bool(sel_statuses_tbl),
        bool(sel_brand_search_tbl.strip()),
    ])
    st.markdown(
        f"<div style='padding-top: 30px; color: #6b7280; font-size: 13px; text-align: right;'>"
        f"<b style='color: #111827; font-size: 18px;'>{len(filtered)}건</b> "
        + (f"· 필터 {active_filters}개 적용 중" if active_filters else "· 필터 미적용")
        + "</div>",
        unsafe_allow_html=True,
    )

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


# ─────────────────────────────────────────────────────────────────
# 활동 히스토리 (자동) — 수집·발송·팔로업·회신을 날짜순 한 줄씩.
#   활동 메모칸 상단에 붙여 함께 보여주되, 저장할 땐 마커 아래 '수기 메모'만
#   저장한다. → 자동분은 매번 새로 그려져 최신이고, 밍이 쓴 메모는 절대 안 덮어씀.
# ─────────────────────────────────────────────────────────────────
ACTIVITY_AUTO_HEADER = "📋 활동 히스토리 (자동 · 이 위쪽은 편집해도 저장 안 됨)"
ACTIVITY_MARKER = "───────── ✏️ 아래에 직접 메모 (저장됨) ─────────"


def _fmt_md(ts) -> str:
    """타임스탬프 → 'MM/DD'. 실패하면 빈 문자열."""
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return f"{d.month:02d}/{d.day:02d}"
    except Exception:
        return ""


def build_activity_history(row) -> list:
    """브랜드 row(Series/dict) → 활동 타임라인 문자열 리스트 (오래된→최근)."""
    def g(k):
        try:
            return str(row.get(k, "") or "").strip()
        except Exception:
            return ""

    lines = []
    c = _fmt_md(g("수집일"))
    lines.append(f"· {c} 신규 수집" if c else "· 신규 수집")

    thread = g("메일 스레드ID")
    sent = g("메일 발송일")
    if thread and not sent:
        lines.append("· 메일 초안 생성 (아직 미발송)")
    if sent:
        lines.append(f"· {_fmt_md(sent)} 메일 발송")

    # 팔로업 — 회차별 날짜(1차/2차)를 각각 표시. 새 컬럼이 있으면 그걸 쓰고,
    #   없거나 옛 데이터면 '마지막 팔로업일 + 횟수'로 보완한다.
    fu1 = g("1차 팔로업일")
    fu2 = g("2차 팔로업일")
    if fu1 or fu2:
        if fu1:
            lines.append(f"· {_fmt_md(fu1)} 1차 팔로업 송부")
        if fu2:
            lines.append(f"· {_fmt_md(fu2)} 2차 팔로업 송부")
    else:
        try:
            cnt = int(g("팔로업 횟수") or 0)
        except Exception:
            cnt = 0
        last_fu = g("마지막 팔로업일")
        if cnt >= 2:
            lines.append("· 1차 팔로업 송부")                   # 옛 데이터 — 1차 날짜 미보관
            lines.append(f"· {_fmt_md(last_fu)} 2차 팔로업 송부")
        elif cnt == 1:
            lines.append(f"· {_fmt_md(last_fu)} 1차 팔로업 송부")

    rep = g("메일 회신일")
    if rep:
        lines.append(f"· {_fmt_md(rep)} 회신 받음")
    return lines


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
    # ⭐ 2026-05-26: 영업 상태 → 마케팅 활동 바로 앞으로 이동
    # 연락처·이메일을 카테고리 다음에 배치 (사업자정보 한 묶음)
    # 영업 상태는 마케팅 활동과 인접 (상태 변경 워크플로우 한눈에)
    main_cols = [
        "수집일",              # 최신순으로 보기 위함 — No. 바로 옆에 위치
        "브랜드명",
        "스마트스토어 주소",   # 브랜드명 바로 옆 — 빠르게 셀러 페이지 확인
        "카테고리",            # 스토어 옆 — 주력상품 기반 자동 분류
        "전화 (수기)",         # ⭐ 사업자정보 묶음 1
        "이메일 (수기)",       # ⭐ 사업자정보 묶음 2
        "영업 상태 (수기)",    # ⭐ 마케팅 활동 바로 앞 (상태↔활동 한 눈에)
        "마케팅 활동 단계 (자동)",   # 도입기/성장기/확장기 — 등급 대체
    ]
    safe_main_cols = [c for c in main_cols if c in filtered.columns]

    # ⭐ 2026-07: 메일 상태 계산에 필요한 추적 컬럼 (표시용으로만 끌어옴)
    _mail_cols = [
        c for c in ["메일 스레드ID", "메일 발송일", "메일 회신일",
                    "팔로업 횟수", "마지막 팔로업일"]
        if c in filtered.columns
    ]

    extra_cols = ["_source_file"] if "_source_file" in filtered.columns else []
    main_df = filtered[
        safe_main_cols + extra_cols + _mail_cols +
        (["Selpic 점수"] if "Selpic 점수" in filtered.columns else [])
    ].copy()   # ⭐ copy()로 안전한 복사본 (SettingWithCopyWarning 회피)

    # ⭐ 2026-05-26: 메인 테이블에 자동수집 정보 통합 표시
    # 수기 컬럼이 비어있을 때 자동값으로 fallback (메모리상만, DB 영향 X)
    # → 사용자가 [수집] 버튼만 누르면 메인 테이블에도 즉시 반영
    def _fallback_with_auto(manual_col: str, auto_col: str):
        if manual_col not in main_df.columns:
            return
        if auto_col not in filtered.columns:
            return
        # filtered와 main_df는 같은 index를 가짐 (정렬 전)
        auto_vals = filtered.loc[main_df.index, auto_col].fillna("").astype(str).str.strip()
        manual_vals = main_df[manual_col].fillna("").astype(str).str.strip()
        # 수기 비어있으면 자동값으로 표시
        merged = manual_vals.where(manual_vals != "", auto_vals)
        # ⭐ 2026-06-01: 자동·수기 둘 다 비면 '수기 입력 필요' 안내 (공식홈은 찾았어도
        #   연락처/이메일이 footer에 없으면 이 칸에 노출 — 사용자 요청)
        main_df[manual_col] = merged.where(merged.str.strip() != "", "✏️ 수기 입력 필요")

    _fallback_with_auto("이메일 (수기)", "이메일 (자동)")
    _fallback_with_auto("전화 (수기)",   "전화 (자동)")

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
    # ⭐ 2026-06: 마케팅 활동 단계는 리스트에서 숨김 (셀러 디테일에서만 노출).
    if "마케팅 활동 단계 (자동)" in display_df.columns:
        display_df = display_df.drop(columns=["마케팅 활동 단계 (자동)"])
    # 2026-07: 표의 '✉️ 메일' 컬럼(Gmail 작성창 링크) 제거.
    #   그 링크는 DB 원본 행을 못 넘겨 지역 분기·AI 도입부가 빠진 반쪽 메일을 만들었다.
    #   메일은 행을 클릭해 열리는 '셀러 디테일'의 초안 버튼으로 일원화한다.

    # ─────────────────────────────────────────────────────────
    # ⭐ 2026-07: 메일 상태 (발송·회신 추적 결과를 한 칸으로)
    #   값은 메일_추적.py 가 Gmail에서 읽어 DB에 채운다. 여기서는 표시만 한다.
    #   (클라우드 대시보드에서도 '누가 답장했나'는 볼 수 있어야 하므로 DB 기반)
    # ─────────────────────────────────────────────────────────
    def _mail_state(row) -> str:
        thread = str(row.get("메일 스레드ID", "") or "").strip()
        if not thread:
            return ""                        # 초안을 만든 적 없음
        replied = str(row.get("메일 회신일", "") or "").strip()
        if replied:
            return "💬 회신"                  # ← 최우선. 이걸 놓치면 안 된다
        sent = str(row.get("메일 발송일", "") or "").strip()
        if not sent:
            return "📝 초안"                  # 만들었지만 아직 안 보냄
        try:
            d = datetime.fromisoformat(sent.replace("Z", "+00:00"))
            days = (datetime.now(d.tzinfo) - d).days
        except Exception:
            days = -1
        try:
            fu = int(row.get("팔로업 횟수", 0) or 0)
        except Exception:
            fu = 0
        # 다음 팔로업 시점은 '마지막 연락'(마지막 팔로업일, 없으면 발송일) + 7일.
        #   → 1차는 발송 기준, 2차는 1차 팔로업 기준으로 정확히 계산 (메일_추적.py와 동일 원칙)
        last_contact = str(row.get("마지막 팔로업일", "") or "").strip() or sent
        try:
            _ld = datetime.fromisoformat(last_contact.replace("Z", "+00:00"))
            days_since_last = (datetime.now(_ld.tzinfo) - _ld).days
        except Exception:
            days_since_last = days
        base = f"발송 {days}일" if days >= 0 else "발송"
        # 팔로업 2회 미만 + 마지막 연락 후 7일 경과 → 지금 해야 할 회차를 강조
        if fu < 2 and days_since_last >= 7:
            return f"🔔 {fu + 1}차 팔로업 때"
        if fu >= 1:
            return f"{base} · {fu}차 팔로업함"   # 이미 보낸 팔로업 회차
        return base

    if "메일 스레드ID" in main_df.columns:
        display_df["메일"] = main_df.apply(_mail_state, axis=1)

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

    # ⭐ 메일 상태 — 회신 온 브랜드는 빨간 굵은 글씨로 (놓치면 안 되는 신호)
    if "메일" in display_df.columns:
        gb.configure_column(
            "메일",
            headerName="메일",
            width=130,
            cellStyle=JsCode("""
            function(params) {
                var v = params.value || '';
                if (v.indexOf('회신') >= 0) {
                    return {'color': '#dc2626', 'fontWeight': '700',
                            'text-align': 'center'};
                }
                if (v.indexOf('🔔') >= 0) {
                    return {'color': '#e8590c', 'fontWeight': '600',
                            'text-align': 'center'};
                }
                return {'color': '#6b7280', 'text-align': 'center'};
            }
            """),
        )

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

    # '✉️ 메일' 컬럼 제거됨 (2026-07) — 메일 초안은 행 클릭 → 셀러 디테일의 버튼으로.

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

    # CSV 다운로드 (테이블 아래 액션 영역)
    # 체크박스 선택 우선:
    #   - 1개 이상 체크: 체크된 행만 다운로드
    #   - 미체크: 현재 필터 결과 전체 다운로드
    download_col1, download_col2 = st.columns([1.3, 4.7])
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
            # ⭐ 2026-07 사용자 요청 비노출 (원본 컬럼명 기준 — rename 전에 제외됨)
            "관심고객수 (수기)",        # 화면 라벨: 스마트스토어 관심고객수
            "상호 (수기)",              # 화면 라벨: 상호
            "대표 (수기)",              # 화면 라벨: 대표자 성함
            "이메일 (수기)",            # 화면 라벨: 이메일
            "전화 (수기)",              # 화면 라벨: 연락처
            "사업자번호 (수기)",
            "상호 (자동)",
            "대표 (자동)",
            "사업자번호 (자동)",
            "주소 (자동)",
            "사업자정보 출처 (자동)",
            "사업자정보 신뢰도 (자동)",
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
    # '빈 스토어 채우기' 버튼 제거됨 (2026-07, 사용자 요청)
    # 일괄 삭제 expander 제거 — 메인 표 체크박스로 충분

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

                # 마케팅 활동 단계 — 리스트에서 숨겼으므로 디테일에서 노출
                _stage_raw = str(sel_full.get("마케팅 활동 단계 (자동)", "")).strip()
                if _stage_raw:
                    _stage_word = _stage_raw.split(" ")[0]
                    _stage_emoji = {
                        "도입기": "⚪", "성장기": "🟢", "확장기": "🟡",
                        "초기": "⚪", "안정기": "🟡",
                    }.get(_stage_word, "")
                    st.caption("마케팅 활동 단계")
                    st.markdown(f"**{_stage_emoji} {_stage_raw}**")
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
                # 옛 상태값(미접촉/응답 대기/미팅 중)이 저장돼 있으면 새 값으로 보여준다
                current_status = LEGACY_STATUS_MAP.get(current_status, current_status)
                if current_status not in SALES_STATUS_OPTIONS:
                    current_status = ""

                # ⭐ 2026-05-26 단순화 (재수집 버튼 제거)
                # 영업 컨택 자동수집은 신규 셀러 수집 시점(collect_5.py)에서만 일어남
                # 디테일 패널은 결과 표시 + 수기 편집 전용
                def _fmt(val):
                    v = str(val).strip()
                    return v if v and v not in ("-", "None") else "정보없음 — 수기 입력 필요"

                # ─── 영업 컨택 박스 (자동 수집 결과 — 메일/전화 발송 대상) ───
                contact_email_raw = str(sel_full.get('이메일 (자동)', '')).strip()
                contact_phone_raw = str(sel_full.get('전화 (자동)', '')).strip()

                auto_sources = str(sel_full.get("사업자정보 출처 (자동)", "")).strip()
                auto_confidence = str(sel_full.get("사업자정보 신뢰도 (자동)", "")).strip()
                confidence_emoji = {
                    "높음": "🟢", "중간": "🟡", "낮음": "🔴", "미발견": "🟠",
                }.get(auto_confidence, "⚪")

                if contact_email_raw or contact_phone_raw:
                    st.markdown(
                        f"<div style='background: #ecfdf5; border: 2px solid #10b981; "
                        f"border-radius: 6px; padding: 10px 14px; margin-bottom: 12px; font-size: 13px;'>"
                        f"<b style='color: #047857;'>📞 영업 컨택 "
                        f"<span style='font-size: 11px; color: #065f46; font-weight: normal;'>"
                        f"(자동 수집 · 메일/전화 발송 대상)</span></b> "
                        f"<span style='color: #064e3b; font-size: 11px; margin-left: 6px;'>"
                        f"{confidence_emoji} 출처: {auto_sources or '미상'}</span><br>"
                        f"<span style='color: #064e3b;'>"
                        f"CS 전화: {_fmt(contact_phone_raw)} · CS 이메일: {_fmt(contact_email_raw)}"
                        f"</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                elif not (
                    str(sel_full.get("이메일 (수기)", "")).strip()
                    or str(sel_full.get("전화 (수기)", "")).strip()
                ):
                    # ⭐ 2026-05-30: 자동 수집 보류(공식홈 미발견/정확도 낮음) +
                    #   수기 연락처도 아직 없음 → '수기 입력 필요' 안내
                    #   (이미 수기로 채운 셀러에는 안내 안 띄움 — legacy 호환)
                    st.markdown(
                        f"<div style='background: #fffbeb; border: 2px solid #f59e0b; "
                        f"border-radius: 6px; padding: 10px 14px; margin-bottom: 12px; font-size: 13px;'>"
                        f"<b style='color: #b45309;'>🟠 공식 홈페이지 미발견 — 수기 입력 필요</b><br>"
                        f"<span style='color: #92400e; font-size: 12px;'>"
                        f"확실한 공식몰을 찾지 못해 자동 수집을 보류했어요. "
                        f"아래 칸에 직접 입력 후 저장하시면 됩니다."
                        f"</span>"
                        f"</div>",
                        unsafe_allow_html=True,
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

                # ─── ✉️ 이 브랜드 영업메일 초안 만들기 (Gmail 임시보관함) ───
                #   예전에는 Gmail '작성창'을 URL로 열었는데, 그 방식은 DB 원본 행을
                #   못 넘겨서 지역 분기(김해 권역 등)와 AI 맞춤 도입부가 빠진 반쪽
                #   메일이 나갔다. 그래서 명령(메일초안_생성.py)과 '완전히 같은 경로'로
                #   초안을 만든다 — DB 원본 행 + AI 도입부 + Gmail 초안 API.
                # Gmail 열쇠(token.json)는 사용자 PC에만 있고 클라우드에는 올리지 않는다
                # (올리면 Gmail 계정 접근 권한이 외부 서버에 놓임 → .gitignore 처리).
                # 그래서 클라우드에서는 이 버튼이 눌러도 안 되니 아예 감춘다.
                _gmail_ready = os.path.exists("token.json")

                _to_mail = (new_email or "").strip()
                if not _gmail_ready:
                    # 클라우드(열쇠 없음): 안내 문구도 띄우지 않고 조용히 감춘다.
                    pass
                elif _to_mail and "@" in _to_mail:
                    _mk = st.button(
                        "✉️ 이 브랜드 메일 초안 만들기",
                        key=f"mkdraft_{sel_brand}",
                        help="Gmail 임시보관함에 초안을 만듭니다 (발송 X). "
                             "위 이메일 칸의 주소로 만들어져요.",
                    )
                    st.caption(
                        "Gmail 임시보관함에 초안이 생겨요. 셀픽 소개서를 첨부한 뒤 "
                        "직접 보내시면 됩니다. (발송은 자동으로 안 함)"
                    )
                    if _mk:
                        try:
                            from email_templates import build_email as _be2
                            from brand_intro import make_opener as _mko
                            import gmail_drafts as _gd
                            _svc2 = _gd.get_service()
                        except RuntimeError as _e:
                            _svc2 = None
                            st.error(str(_e))
                        except Exception as _e:
                            _svc2 = None
                            st.error(f"모듈 로드 오류: {_e}")

                        if _svc2 is None:
                            st.warning(
                                "Gmail 연동이 아직 안 됐어요 (token.json 없음). "
                                "**Gmail_연동_설정가이드.md** 대로 1회 설정한 뒤 다시 눌러주세요."
                            )
                        else:
                            # DB 원본 행을 그대로 가져온다 (전화·주소 → 지역 분기에 필요)
                            _sb2 = get_supabase_client()
                            _res2 = (
                                _sb2.table(TABLE_NAME).select("*")
                                .eq("brand_name", sel_brand).limit(1).execute()
                            )
                            _row2 = (_res2.data or [{}])[0]
                            # 디테일 패널에서 고친 이메일이 항상 우선
                            _row2["manual_email"] = _to_mail

                            with st.spinner("브랜드 홈페이지를 읽고 맞춤 도입부를 쓰는 중..."):
                                try:
                                    _op = _mko(_row2)
                                except Exception:
                                    _op = ""
                            if _op:
                                _row2["ai_opener"] = _op

                            _bb = _be2(_row2)
                            if _bb.get("skip"):
                                st.warning(f"초안 보류: {_bb['skip_reason']}")
                            else:
                                try:
                                    _ids = _gd.create_draft(
                                        _svc2, _bb["to"], _bb["subject"],
                                        _bb["html"], _bb["plain"],
                                    )
                                    # 발송·회신 추적용 ID 기록 (메일_추적.py 가 사용)
                                    try:
                                        _sb2.table(TABLE_NAME).update({
                                            "mail_draft_id": _ids.get("draft_id", ""),
                                            "mail_thread_id": _ids.get("thread_id", ""),
                                            "mail_sent_at": None,
                                            "mail_replied_at": None,
                                            "mail_followup_count": 0,
                                            "mail_last_followup_at": None,
                                        }).eq("brand_name", sel_brand).execute()
                                    except Exception as _e2:
                                        st.caption(f"(추적 정보 저장 못 함 — {_e2})")
                                    st.success(
                                        f"✅ 초안 생성 완료 → {_bb['to']}  "
                                        f"(Gmail 임시보관함 확인)"
                                    )
                                    st.caption(
                                        ("브랜드 맞춤 도입부 포함" if _op
                                         else "맞춤 도입부 미생성 → 카테고리 문구 사용")
                                        + f" · 제목: {_bb['subject']}"
                                    )
                                except Exception as _e:
                                    st.error(f"초안 생성 오류: {_e}")
                else:
                    st.caption("✉️ 이메일을 입력/확인하면 '메일 초안 만들기' 버튼이 나타나요.")

                # ⭐ 영업 상태 (하단 재배치) — 활동메모 바로 위
                new_status = st.selectbox(
                    "영업 상태",
                    SALES_STATUS_OPTIONS,
                    index=SALES_STATUS_OPTIONS.index(current_status),
                    key=f"status_{sel_brand}",
                )

                # 활동 메모 — 위쪽은 자동 히스토리, 구분선 아래는 수기 메모(저장 대상)
                _hist = build_activity_history(sel_full)
                _auto_block = ACTIVITY_AUTO_HEADER + "\n" + "\n".join(_hist)
                _manual = str(sel_full.get("활동 메모 (수기)", "") or "")
                _memo_value = _auto_block + "\n\n" + ACTIVITY_MARKER + "\n" + _manual
                new_memo = st.text_area(
                    "활동 메모",
                    value=_memo_value,
                    height=260,
                    key=f"memo_{sel_brand}",
                )
                st.caption(
                    "위쪽 '활동 히스토리'는 자동으로 채워져요(편집해도 저장 안 됨). "
                    "구분선 아래에만 직접 메모를 남기면 계속 쌓입니다."
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
                    # ⭐ 2026-05-26: UI에서 제거된 필드들 (상호/대표/사업자번호/관심고객수) 저장 안 함
                    # 기존 DB 데이터는 유지 (덮어쓰지 않음)
                    # 활동 메모: 마커 위 '자동 히스토리'는 버리고, 마커 아래 수기 메모만 저장
                    #   → 자동분이 DB에 쌓이거나 밍이 쓴 메모를 덮어쓰는 일이 없다.
                    if ACTIVITY_MARKER in new_memo:
                        _manual_only = new_memo.split(ACTIVITY_MARKER, 1)[1].lstrip("\n")
                    else:
                        _manual_only = new_memo   # 마커가 지워졌으면 안전하게 전체를 수기로 취급
                    new_values = {
                        "영업 상태 (수기)": new_status,
                        "이메일 (수기)": new_email,
                        "전화 (수기)": new_phone,
                        "활동 메모 (수기)": _manual_only,
                    }
                    if save_one_brand(sel_brand, new_values):
                        st.cache_data.clear()
                        # 위젯 상태를 비워, 다음 렌더에서 자동 히스토리가 최신으로 새로 그려지게
                        st.session_state.pop(f"memo_{sel_brand}", None)
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
