# -*- coding: utf-8 -*-
"""
PICK10 전체 자가진단 (이메일 자동화 단계 진입 전 검증용)
─────────────────────────────────────────────────────────────
실행:  venv\\Scripts\\python self_check.py
       (네트워크 수집까지 빼고 빠르게:  venv\\Scripts\\python self_check.py --fast)

각 단계가 [PASS]/[FAIL] 로 나오고, 맨 끝에 종합 결과를 알려줍니다.
하나라도 FAIL 이면 거기서 원인을 알려주세요. 같이 고치면 됩니다.
"""
import sys
import io
import traceback

# 윈도우 한글 출력 깨짐 방지
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

FAST = "--fast" in sys.argv

results = []   # (이름, 통과여부, 메모)


def check(name):
    """데코레이터: 함수 하나 = 검사 하나. 예외 나면 FAIL 로 기록."""
    def deco(fn):
        print(f"\n▶ {name} ...")
        try:
            memo = fn()
            results.append((name, True, memo or ""))
            print(f"   [PASS] {memo or ''}")
        except AssertionError as e:
            results.append((name, False, str(e)))
            print(f"   [FAIL] {e}")
        except Exception as e:
            results.append((name, False, f"예외: {e}"))
            print(f"   [FAIL] 예외 발생: {e}")
            traceback.print_exc()
        return fn
    return deco


# ─────────────────────────────────────────────────────────
# 1단계: 모든 모듈 import (문법·import 오류를 한 번에 잡음)
# ─────────────────────────────────────────────────────────
@check("1. 모듈 import (문법 검사)")
def _():
    # 이 3개는 '순수 모듈'(함수·상수만 정의) → 안전하게 실제 import
    import business_info_collector   # noqa
    import market_filter             # noqa
    import supabase_client           # noqa
    # ⚠ collect_5.py 와 dashboard.py 는 import 즉시 '실행'되는 스크립트:
    #    - collect_5: 모듈 레벨에서 전체 수집+DB 저장이 돌아감 (subprocess 전용)
    #    - dashboard: streamlit 실행 컨텍스트 필요
    #   → import 하면 안 됨. 문법 오류만 컴파일로 검사한다 (실행 X).
    import email_templates          # noqa  (순수 모듈)
    import gmail_drafts             # noqa  (구글 라이브러리는 함수 안에서 lazy import)
    import brand_intro              # noqa  (AI 도입부 — 키 없어도 import 는 되어야 함)
    import py_compile
    py_compile.compile("collect_5.py", doraise=True)
    py_compile.compile("dashboard.py", doraise=True)
    py_compile.compile("메일초안_생성.py", doraise=True)
    return "순수모듈 6개 import + 스크립트 3개 문법 정상"


# ─────────────────────────────────────────────────────────
# 2단계: 핵심 함수가 실제로 존재하고 호출 가능한지
# ─────────────────────────────────────────────────────────
@check("2. 핵심 함수 존재 확인")
def _():
    import business_info_collector as b
    import market_filter as m
    import supabase_client as s
    need_b = [
        "collect_business_info", "find_service_business_homepages",
        "find_powerlink_businesses", "find_business_info_from_homepage",
        "render_html_with_browser", "close_browser",
        "_brand_match_tokens", "_url_domain_matches_brand",
        "_verify_homepage_match", "_is_brand_own_site",
        "pick_best_email", "extract_labeled_email",
    ]
    for fn in need_b:
        assert callable(getattr(b, fn, None)), f"business_info_collector.{fn} 없음"
    need_m = ["market_fit_check", "classify_category", "is_excluded_brand",
              "expand_keyword", "generate_space_variants"]
    for fn in need_m:
        assert callable(getattr(m, fn, None)), f"market_filter.{fn} 없음"
    assert callable(getattr(s, "get_supabase_client", None)), "get_supabase_client 없음"
    return f"필수 함수 {len(need_b)+len(need_m)+1}개 모두 존재"


# ─────────────────────────────────────────────────────────
# 3단계: 순수 함수 단위 검증 (네트워크 X, 결과가 항상 같아야 함)
#   오늘 고친 로직들이 의도대로 작동하는지 회귀 검사
# ─────────────────────────────────────────────────────────
@check("3-1. 브랜드 토큰: 일반어 제외 + 핵심어 보존 (오아센 케이스)")
def _():
    import business_info_collector as b
    toks = b._brand_match_tokens("아토피 연구소 오아센")
    assert any("오아센" in t for t in toks), f"핵심어 '오아센' 누락: {toks}"
    # 일반어가 '단독 토큰'으로 남지 않아야 함 (전체/공백제거 토큰엔 들어갈 수 있음)
    single = [t for t in toks if " " not in t and len(t) <= 4]
    assert "아토피" not in single and "연구소" not in single, \
        f"일반어가 단독 토큰에 남음: {single}"
    return f"토큰={toks}"


@check("3-2. 한글 도메인(punycode) 매칭 (까꿍맘마 케이스)")
def _():
    import business_info_collector as b
    ok = b._url_domain_matches_brand(
        "https://xn--hl0bo2a92t2b.com/", "까꿍맘마이유식연구소"
    )
    assert ok is True, "punycode 도메인이 브랜드와 매칭 안 됨"
    # 무관한 도메인은 False 여야 함 (오탐 방지)
    bad = b._url_domain_matches_brand("https://coupang.com/", "까꿍맘마")
    assert bad is False, "무관 도메인이 잘못 매칭됨"
    return "한글도메인 매칭 정상 / 오탐 없음"


@check("3-3. 메타검증 footer 범위 확장 (30KB+15KB, 오아센 케이스)")
def _():
    import business_info_collector as b
    # 앞부분은 메뉴(브랜드명 다수), footer 사업자번호가 30KB 밖에 있는 가짜 페이지
    head = "<title>피앤에스</title>" + ("오아센 " * 40)   # 보이는 브랜드 반복
    filler = "x" * 32000                                  # 30KB 넘기는 채움
    footer = "사업자등록번호 707-81-00148 대표 홍길동 통신판매업신고"
    html = f"<html><body>{head}{filler}{footer}</body></html>"
    score = b._verify_homepage_match(html, "아토피 연구소 오아센")
    assert score >= 25, f"footer 범위 확장 실패 — 점수 {score} (25 미만)"
    return f"점수 {score} (footer 신호 정상 반영)"


@check("3-4. 시장 적합성 / 카테고리 분류 함수 동작")
def _():
    import market_filter as m
    fit = m.market_fit_check("바니블라썸", "유아 이유식 턱받이")
    assert isinstance(fit, tuple), f"market_fit_check 반환형 이상: {type(fit)}"
    cat = m.classify_category("신생아 배냇저고리 유아복")
    assert isinstance(cat, str) and cat, f"classify_category 결과 이상: {cat}"
    return f"fit={fit[0] if fit else '?'}, category='{cat}'"


@check("3-5. 영업메일 템플릿 엔진 (제품형/서비스형/skip)")
def _():
    from email_templates import build_email
    # 제품형
    prod = build_email({
        "brand_name": "엘빈즈", "auto_email": "alvins@alvins.co.kr",
        "category": "분유·이유식", "product_category": "출산/육아 > 이유식",
        "flagship_product": "엘빈즈 수제 이유식 20+2팩",
        "auto_biz_confidence": "높음",
    })
    assert prod["template"] == "product" and not prod["skip"], "제품형 분기 실패"
    assert "엘빈즈" in prod["subject"] and prod["html"], "제품형 본문 생성 실패"
    # 서비스형
    svc = build_email({
        "brand_name": "맘스테라", "auto_email": "info@momstera.kr",
        "category": "출산 서비스", "product_category": "출산 서비스",
        "flagship_product": "산후 마사지", "auto_biz_confidence": "높음",
    })
    assert svc["template"] == "service", "서비스형 분기 실패"
    # 가격 미노출 정책 (2026-07) — 첫 메일에 금액을 넣으면 거절 트리거가 된다.
    # 견적은 회신·미팅에서. 금액 문구가 다시 들어오면 여기서 잡는다.
    for m in ("만원", "150원", "vat", "VAT"):
        for t in (prod["plain"], svc["plain"]):
            assert m not in t, f"메일에 가격 문구('{m}')가 들어감 — 첫 메일은 금액 미노출"
    # 신뢰 근거(레퍼런스) + 소개서 첨부 안내 + 미팅 유도 CTA 가 살아있는지
    for t in (prod["plain"], svc["plain"]):
        assert "네슬레" in t, "레퍼런스 문구 누락"
        assert "셀픽 소개서" in t, "소개서 첨부 안내 누락"
        assert "찾아뵙고" in t, "유선·미팅 유도 CTA 누락"
    # 이메일 없으면 skip
    skip = build_email({"brand_name": "노메일", "auto_email": ""})
    assert skip["skip"] is True, "이메일 없음 skip 처리 실패"

    # 수기 이메일이 자동 수집을 이겨야 한다 (사람이 고친 값이 최우선)
    fixed = build_email({
        "brand_name": "수정브랜드",
        "auto_email": "wrong@auto.kr",       # 자동 수집이 틀리게 잡은 주소
        "manual_email": "right@manual.kr",   # 사람이 대시보드에서 고친 주소
        "category": "수유용품", "auto_biz_confidence": "높음",
    })
    assert fixed["to"] == "right@manual.kr", (
        f"수기 이메일이 무시됨 → {fixed['to']} (자동값이 이기면 안 됨)"
    )
    return "제품형/서비스형 분기 + 금액 미노출 + 레퍼런스/CTA + 수기이메일 우선 + skip 정상"


@check("3-6. AI 도입부 안전장치 (거짓 주장 차단 + 폴백)")
def _():
    from brand_intro import _is_safe
    from email_templates import build_email

    # 검증 불가능한 주장은 반드시 폐기되어야 한다 (메일에 나가면 신뢰가 깨짐)
    bad = [
        "업계 1위 브랜드로 알고 있습니다. 인상 깊게 봤습니다.",       # 순위
        "누적 판매 30만개를 돌파하신 걸 보고 연락드립니다.",          # 숫자
        "대한민국 대표 유아 브랜드로 유명하신 걸 알고 있습니다.",      # 검증 불가
        "짧다",                                                    # 너무 짧음
    ]
    for s in bad:
        assert not _is_safe(s), f"차단됐어야 할 문장이 통과함: {s}"

    good = "무향과 순한 성분을 앞세우신 걸 보고 연락드립니다. 성분을 깐깐하게 보는 시기라고 봤습니다."
    assert _is_safe(good), f"정상 문장이 차단됨: {good}"

    # ai_opener 가 있으면 도입부로 쓰이고, 없으면 카테고리 문구로 폴백해야 한다
    row = {
        "brand_name": "테스트", "auto_email": "a@b.kr",
        "category": "베이비 스킨케어", "auto_biz_confidence": "높음",
    }
    assert good not in build_email(row)["plain"], "폴백 경로가 AI 문장을 끌어옴"
    row["ai_opener"] = good
    assert good in build_email(row)["plain"], "ai_opener 가 본문에 반영 안 됨"
    return "거짓 주장 4종 차단 · 정상 문장 통과 · 폴백 정상"


# ─────────────────────────────────────────────────────────
# 4단계: Supabase 연결 (DB 읽기 1건)
# ─────────────────────────────────────────────────────────
@check("4. Supabase 연결 + 테이블 읽기")
def _():
    import supabase_client as s
    cli = s.get_supabase_client()
    assert cli is not None, ".env의 SUPABASE_URL/KEY 확인 필요 (None 반환)"
    res = cli.table(s.TABLE_NAME).select("*").limit(1).execute()
    n = len(res.data) if res and res.data is not None else 0
    return f"'{s.TABLE_NAME}' 테이블 읽기 성공 (샘플 {n}행)"


# ─────────────────────────────────────────────────────────
# 5단계: 실제 수집 스모크 테스트 (네트워크 — 1개 브랜드)
#   --fast 면 건너뜀
# ─────────────────────────────────────────────────────────
@check("5. 실제 수집 스모크 (바니블라썸)")
def _():
    if FAST:
        return "건너뜀 (--fast)"
    import business_info_collector as b
    info = b.collect_business_info("바니블라썸", "")
    assert isinstance(info, dict), "반환형이 dict 아님"
    # 공식몰이 명확한 브랜드 → 신뢰도 '높음' + 연락처 또는 사업자번호 중 하나는 있어야
    conf = info.get("confidence", "")
    got = info.get("phone") or info.get("business_number") or info.get("email")
    assert conf == "높음" and got, f"수집 실패: confidence={conf}, info={info}"
    try:
        b.close_browser()
    except Exception:
        pass
    return f"phone={info.get('phone')}, bizno={info.get('business_number')}, conf={conf}"


# ─────────────────────────────────────────────────────────
# 종합
# ─────────────────────────────────────────────────────────
print("\n" + "=" * 55)
print("  PICK10 자가진단 종합 결과")
print("=" * 55)
passed = sum(1 for _, ok, _ in results if ok)
for name, ok, memo in results:
    mark = "✅" if ok else "❌"
    print(f"{mark} {name}")
    if not ok:
        print(f"     → {memo}")
print("-" * 55)
print(f"  {passed}/{len(results)} 통과")
if passed == len(results):
    print("  🎉 전부 정상 — 이메일 자동화 단계로 진행 가능")
else:
    print("  ⚠ 위 ❌ 항목의 메모를 알려주세요. 원인 잡아 고치면 됩니다.")
print("=" * 55)
