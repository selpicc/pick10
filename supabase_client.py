"""
PICK10 - Supabase 공통 모듈
=================================================================
- Supabase 클라이언트 초기화
- DB 영문 컬럼 ↔ 표시 한글 컬럼 매핑
- 양방향 변환 함수
=================================================================
"""

import os
from dotenv import load_dotenv
from supabase import Client, create_client


load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

TABLE_NAME = "sellers"

# DB(영문) → 표시(한글) 매핑
DB_TO_KOR = {
    "collected_at":           "수집일",
    "selpic_score":           "Selpic 점수",
    "category":               "발견 카테고리",
    "keyword":                "발견 키워드",
    "collect_mode":           "수집 모드",   # auto / category / keywords
    "auto_followers":         "관심고객수 (자동)",   # 스마트스토어에서 자동 수집
    "brand_name":             "브랜드명",
    "smartstore_url":         "스마트스토어 주소",
    "flagship_product":       "주력상품명",
    "product_category":       "상품 카테고리",
    "price":                  "가격",
    "score_breakdown":        "점수 근거",
    "marketing_keyword":      "마케팅 검색 키워드 (자동)",
    "marketing_grade":        "마케팅 등급 (자동)",
    "marketing_score":        "마케팅 점수 (자동)",
    "marketing_exposure":     "마케팅 채널별 노출 (자동)",
    "size_estimate":          "마케팅 활동 단계 (자동)",
    "manual_followers":       "관심고객수 (수기)",
    "manual_reviews":         "리뷰수 (수기)",
    "manual_company_name":    "상호 (수기)",
    "manual_ceo":             "대표 (수기)",
    "manual_email":           "이메일 (수기)",
    "manual_phone":           "전화 (수기)",
    "manual_business_number": "사업자번호 (수기)",   # ⭐ 사용자 직접 입력
    "manual_marketing_memo":  "마케팅 분석 메모 (수기)",
    "sales_status":           "영업 상태 (수기)",
    "activity_memo":          "활동 메모 (수기)",
    # ⭐ 사업자 정보 자동 수집 (Phase 1+2+3, 2026-05-13 추가)
    # ⭐ 2026-05-26: "상호/대표/전화/이메일 (자동)" = 영업 컨택 (브랜드 공식몰 우선)
    "auto_company_name":      "상호 (자동)",
    "auto_ceo":               "대표 (자동)",
    "auto_business_number":   "사업자번호 (자동)",
    "auto_phone":             "전화 (자동)",
    "auto_email":             "이메일 (자동)",
    "auto_address":           "주소 (자동)",
    "auto_biz_sources":       "사업자정보 출처 (자동)",
    "auto_biz_confidence":    "사업자정보 신뢰도 (자동)",
    # ⭐ 2026-05-26 추가: 본사 정보 (공정위 DB · 참고용)
    # 브랜드 공식몰의 영업 컨택과 별개로, 사업자등록증 기준 본사 정보
    "hq_company_name":        "본사 상호 (자동)",
    "hq_ceo":                 "본사 대표 (자동)",
    "hq_phone":               "본사 전화 (자동)",
    "hq_address":             "본사 주소 (자동)",
    # ⭐ 2026-07 추가: 메일 발송·회신 추적 (메일_추적.py 가 Gmail에서 읽어 채움)
    #   대시보드는 이 값을 '메일 상태'로 보여준다 (수기 편집 대상 아님)
    "mail_draft_id":          "메일 초안ID",
    "mail_thread_id":         "메일 스레드ID",
    "mail_sent_at":           "메일 발송일",
    "mail_replied_at":        "메일 회신일",
    "mail_followup_count":    "팔로업 횟수",
    "mail_last_followup_at":  "마지막 팔로업일",
    "mail_followup1_at":      "1차 팔로업일",
    "mail_followup2_at":      "2차 팔로업일",
    # ⭐ 2026-07 추가: 메일 '단계' 표시용
    #   mail_sent_count = 이 대화에서 실제로 나간 메일 수
    #     0 미발송 / 1 첫 메일 / 2 1차 팔로업까지 / 3 2차 팔로업까지
    #   위 '팔로업 횟수'는 초안을 만든 횟수라 '보냄'과 다르다 → 둘을 나눠야
    #   "초안 생성"과 "송신"을 구분해 보여줄 수 있다
    "mail_sent_count":        "메일 발송 수",
    "mail_followup1_sent_at": "1차 팔로업 송신일",
}

# 한글 → 영문 (역매핑)
KOR_TO_DB = {v: k for k, v in DB_TO_KOR.items()}

# 수기 컬럼 (한글)
MANUAL_COLUMNS = [
    "관심고객수 (수기)",
    "리뷰수 (수기)",
    "상호 (수기)",
    "대표 (수기)",
    "이메일 (수기)",
    "전화 (수기)",
    "마케팅 분석 메모 (수기)",
    "영업 상태 (수기)",
    "활동 메모 (수기)",
]


def get_supabase_client() -> Client:
    """Supabase 클라이언트 생성. 실패 시 None 반환."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        return None


def kor_row_to_db(row: dict) -> dict:
    """한글 row → DB 영문 row + 타입 변환"""
    db_row = {}
    for kor_col, val in row.items():
        if kor_col not in KOR_TO_DB:
            continue
        db_col = KOR_TO_DB[kor_col]

        if val is None:
            val = ""
        val = str(val).strip()

        # 타입 변환
        if db_col == "selpic_score":
            try:
                db_row[db_col] = int(val) if val else 0
            except ValueError:
                db_row[db_col] = 0
        elif db_col == "auto_followers":
            try:
                db_row[db_col] = int(val) if val else 0
            except ValueError:
                db_row[db_col] = 0
        elif db_col == "marketing_score":
            try:
                db_row[db_col] = float(val) if val else None
            except ValueError:
                db_row[db_col] = None
        elif db_col == "collected_at":
            from datetime import datetime
            if not val:
                db_row[db_col] = None
            else:
                try:
                    datetime.strptime(val, "%Y-%m-%d")
                    db_row[db_col] = val
                except ValueError:
                    db_row[db_col] = None
        else:
            db_row[db_col] = val

    return db_row
