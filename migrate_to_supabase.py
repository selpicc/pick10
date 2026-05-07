"""
PICK10 - 기존 CSV → Supabase sellers 테이블 일괄 이전
=================================================================
한 번만 실행. 모든 PICK10_v3_*.csv를 Supabase로 UPSERT.
중복(브랜드명 동일)은 자동 갱신.

실행:
  python migrate_to_supabase.py
=================================================================
"""

import csv
import glob
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from supabase import create_client


sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ .env에 SUPABASE_URL, SUPABASE_KEY 설정 필요")
    sys.exit(1)

RESULTS_DIR = "results"

# CSV 한글 컬럼 → DB 영문 컬럼 매핑
COLUMN_MAP = {
    "수집일":                       "collected_at",
    "Selpic 점수":                  "selpic_score",
    "발견 카테고리":                "category",
    "발견 키워드":                  "keyword",
    "브랜드명":                     "brand_name",
    "스마트스토어 주소":            "smartstore_url",
    "주력상품명":                   "flagship_product",
    "상품 카테고리":                "product_category",
    "가격":                         "price",
    "점수 근거":                    "score_breakdown",
    "마케팅 검색 키워드 (자동)":    "marketing_keyword",
    "마케팅 등급 (자동)":           "marketing_grade",
    "마케팅 점수 (자동)":           "marketing_score",
    "마케팅 채널별 노출 (자동)":    "marketing_exposure",
    "규모 추정 (자동)":             "size_estimate",
    "관심고객수 (수기)":            "manual_followers",
    "리뷰수 (수기)":                "manual_reviews",
    "상호 (수기)":                  "manual_company_name",
    "대표 (수기)":                  "manual_ceo",
    "이메일 (수기)":                "manual_email",
    "전화 (수기)":                  "manual_phone",
    "마케팅 분석 메모 (수기)":      "manual_marketing_memo",
    "영업 상태 (수기)":             "sales_status",
    "활동 메모 (수기)":             "activity_memo",
}


def csv_row_to_db(row: dict) -> dict:
    """CSV 한글 행 → DB 영문 행 변환"""
    db_row = {}
    for csv_col, db_col in COLUMN_MAP.items():
        val = row.get(csv_col, "")
        if val is None:
            val = ""
        val = str(val).strip()

        # 타입 변환
        if db_col == "selpic_score":
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
            # 빈 값 또는 잘못된 형식이면 None
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


def main():
    print("\n" + "=" * 60)
    print("  PICK10 - CSV → Supabase 마이그레이션")
    print("=" * 60 + "\n")

    # Supabase 연결
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    # CSV 파일 모으기
    csv_files = sorted(glob.glob(os.path.join(RESULTS_DIR, "PICK10_v3_*.csv")))
    csv_files = [f for f in csv_files if "_backup_" not in f and "_old" not in f]

    if not csv_files:
        print("❌ results 폴더에 PICK10_v3_*.csv 파일 없음")
        return

    print(f"📂 처리할 CSV 파일: {len(csv_files)}개")
    for f in csv_files:
        print(f"   - {os.path.basename(f)}")
    print()

    # 모든 행 모으기 (브랜드명 기준 중복 제거 — 가장 최근 유지)
    all_rows = {}
    for path in csv_files:
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    brand = (row.get("브랜드명") or "").strip()
                    if brand:
                        all_rows[brand] = row   # 같은 브랜드는 마지막 행이 덮어씀
        except Exception as e:
            print(f"⚠️ 파일 읽기 실패: {path}: {e}")

    print(f"📊 고유 브랜드 수: {len(all_rows)}")
    print()

    # Supabase에 UPSERT
    print("⬆️  Supabase에 업로드 중...")
    success_count = 0
    fail_count = 0
    failed_brands = []

    for brand, row in all_rows.items():
        db_row = csv_row_to_db(row)
        try:
            sb.table("sellers").upsert(db_row, on_conflict="brand_name").execute()
            success_count += 1
            print(f"   ✓ {brand}")
        except Exception as e:
            fail_count += 1
            failed_brands.append(f"{brand}: {str(e)[:80]}")
            print(f"   ✗ {brand} — 실패")

    # 결과
    print()
    print("=" * 60)
    print(f"  ✅ 성공: {success_count}건")
    if fail_count > 0:
        print(f"  ❌ 실패: {fail_count}건")
        for fb in failed_brands:
            print(f"     - {fb}")
    print("=" * 60)
    print()
    print("👉 Supabase Table Editor에서 sellers 테이블 확인 →")
    print("   https://supabase.com/dashboard/project/_/editor")


if __name__ == "__main__":
    main()
