# -*- coding: utf-8 -*-
"""브랜드 분석 리포트(PPTX) 생성 — CLI 래퍼

사용:
    venv\\Scripts\\python 리포트_생성.py --brand 베베가닉
    venv\\Scripts\\python 리포트_생성.py --brand 베베가닉 --out C:\\경로\\파일.pptx

대시보드 버튼과 '완전히 같은 경로'(report_generator.make_report)로 만든다.
발송·저장 자동화 아님 — 파일 1개를 만들어 저장할 뿐.
"""
import argparse
import os

from dotenv import load_dotenv
from supabase import create_client

from report_generator import make_report

load_dotenv()


def _fetch_row(brand: str) -> dict:
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    res = (
        sb.table("sellers").select("*")
        .ilike("brand_name", f"%{brand}%").limit(1).execute()
    )
    if not res.data:
        raise SystemExit(f"❌ '{brand}'에 해당하는 브랜드를 sellers에서 못 찾았어요.")
    return res.data[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True, help="브랜드명(부분일치)")
    ap.add_argument("--out", default="", help="저장 경로(기본: 현재 폴더)")
    args = ap.parse_args()

    row = _fetch_row(args.brand)
    print(f"🔎 대상 브랜드: {row.get('brand_name')}")
    print("🧠 제품 정보 읽고 리포트 생성 중... (Gemini)")

    data, filename = make_report(row)
    out_path = args.out or filename
    with open(out_path, "wb") as f:
        f.write(data)
    print(f"✅ 생성 완료: {out_path}  ({len(data):,} bytes)")


if __name__ == "__main__":
    main()
