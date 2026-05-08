"""
PICK10 - 시장 타깃 안 맞는 기존 브랜드 일괄 삭제 (1회용)
=================================================================
실행:
    python cleanup_unfit_brands.py

목적:
    이미 DB에 저장된 브랜드 중 새 필터(A+B+C)에 안 맞는
    브랜드들을 찾아서 일괄 삭제.

3중 필터 (collect_5.py와 동일):
    A. 영유아·산모 시장 키워드 1개+ 필수
    B. 대기업 명단 자동 제외
    C. 부정 키워드 자동 차단

매칭 범위: 브랜드명 + 주력 상품명 (DB의 flagship_product)

⚠️  안전장치:
    - 삭제 전 미리보기 출력
    - 사용자 확인 입력 필수 (y / N)
    - 영업 진행 중인 브랜드(영업 상태 != 미접촉/빈값)는 자동 제외
=================================================================
"""

import sys
import time

from supabase_client import get_supabase_client, TABLE_NAME

# 단일 진실의 원천 (market_filter.py)
from market_filter import market_fit_check


# 영업 진행 중인 브랜드 보호 — 이런 상태인 브랜드는 절대 삭제 X
PROTECTED_STATUSES = {
    "메일 발송", "응답 대기", "미팅 중", "계약 완료", "거절", "기타) 패싱"
}


def main():
    print("=" * 60)
    print("PICK10 시장 타깃 미적합 브랜드 일괄 삭제")
    print("=" * 60)

    sb = get_supabase_client()
    if sb is None:
        print("⚠️  Supabase 클라이언트 생성 실패. .env 확인.")
        sys.exit(1)

    # 1) DB 전체 조회
    print("\n[1/4] DB에서 전체 브랜드 조회 중...")
    result = sb.table(TABLE_NAME).select(
        "brand_name, flagship_product, sales_status, selpic_score"
    ).execute()
    all_rows = result.data
    print(f"  총 {len(all_rows)}건")

    # 2) 필터 적용
    print(f"\n[2/4] A+B+C 크로스체크 적용...")
    delete_candidates = []
    protected_unfit = []
    keep_count = 0

    for row in all_rows:
        brand = (row.get("brand_name") or "").strip()
        title = (row.get("flagship_product") or "").strip()
        status = (row.get("sales_status") or "").strip()

        if not brand:
            continue

        result_tag, reason = market_fit_check(brand, title)

        if result_tag == "ok":
            keep_count += 1
            continue

        # 영업 진행 중이면 보호
        if status in PROTECTED_STATUSES:
            protected_unfit.append({
                "brand": brand,
                "reason": reason,
                "status": status,
            })
            continue

        delete_candidates.append({
            "brand": brand,
            "reason": reason,
            "tag": result_tag,
            "title": title[:40],
        })

    print(f"  ✅ 유지 (필터 통과): {keep_count}건")
    print(f"  🛡  보호 (영업 진행 중인데 미적합): {len(protected_unfit)}건")
    print(f"  ❌ 삭제 대상: {len(delete_candidates)}건")

    if not delete_candidates and not protected_unfit:
        print("\n  ✅ 모든 브랜드가 필터 통과. 삭제 대상 없음.")
        return

    # 3) 미리보기
    print(f"\n[3/4] 삭제 대상 미리보기")
    if delete_candidates:
        # 사유별 그룹핑
        by_tag = {"a": [], "b": [], "c": []}
        for c in delete_candidates:
            by_tag[c["tag"]].append(c)

        if by_tag["b"]:
            print(f"\n  [B 대기업] {len(by_tag['b'])}건")
            for c in by_tag["b"][:5]:
                print(f"    - {c['brand']} ({c['reason']})")
            if len(by_tag["b"]) > 5:
                print(f"    ... 그 외 {len(by_tag['b']) - 5}건")

        if by_tag["c"]:
            print(f"\n  [C 다른 시장] {len(by_tag['c'])}건")
            for c in by_tag["c"][:5]:
                print(f"    - {c['brand']} ({c['reason']})")
            if len(by_tag["c"]) > 5:
                print(f"    ... 그 외 {len(by_tag['c']) - 5}건")

        if by_tag["a"]:
            print(f"\n  [A 영유아 시장 X] {len(by_tag['a'])}건")
            for c in by_tag["a"][:10]:
                print(f"    - {c['brand']} (상품: {c['title']})")
            if len(by_tag["a"]) > 10:
                print(f"    ... 그 외 {len(by_tag['a']) - 10}건")

    if protected_unfit:
        print(f"\n  [🛡 보호 (영업 진행 중) - 삭제 X]")
        for p in protected_unfit[:10]:
            print(f"    - {p['brand']} (영업: {p['status']}, 사유: {p['reason']})")
        if len(protected_unfit) > 10:
            print(f"    ... 그 외 {len(protected_unfit) - 10}건")

    # 4) 사용자 확인
    print("\n" + "=" * 60)
    if not delete_candidates:
        print("실제 삭제할 브랜드 없음. (모두 영업 진행 중이라 보호됨)")
        return

    print(f"⚠️  {len(delete_candidates)}건을 영구 삭제합니다.")
    print("    이 작업은 되돌릴 수 없습니다.")
    print("=" * 60)
    confirm = input(f"\n진짜 삭제할까요? 'DELETE' 입력 (소문자 X, 정확히 대문자 7자): ").strip()

    if confirm != "DELETE":
        print("취소됨.")
        return

    # 5) 삭제 실행
    print(f"\n[4/4] 삭제 진행 중...")
    deleted_count = 0
    failed_count = 0

    for i, c in enumerate(delete_candidates, 1):
        try:
            sb.table(TABLE_NAME).delete().eq("brand_name", c["brand"]).execute()
            print(f"  [{i}/{len(delete_candidates)}] ✅ {c['brand']} 삭제 ({c['reason']})")
            deleted_count += 1
        except Exception as e:
            print(f"  [{i}/{len(delete_candidates)}] ❌ {c['brand']} 실패: {e}")
            failed_count += 1
        time.sleep(0.1)

    print("\n" + "=" * 60)
    print("완료!")
    print("=" * 60)
    print(f"  삭제 성공: {deleted_count}건")
    print(f"  삭제 실패: {failed_count}건")
    print(f"  보호 (영업 진행 중): {len(protected_unfit)}건")
    print(f"  유지 (필터 통과): {keep_count}건")
    print(f"  남은 브랜드: {keep_count + len(protected_unfit)}건")


if __name__ == "__main__":
    main()
