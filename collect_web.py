# -*- coding: utf-8 -*-
"""
PICK10 — 웹검색 기반 상품 브랜드 수집 (쇼핑 API 종료 대체 엔진)
=================================================================
왜 만들었나:
  네이버 '쇼핑 검색 API'가 2026-07-31 종료되어 collect_5.py(스마트스토어 기반)가
  후보 0건이 된다. 대신 아직 살아있는 '웹문서 검색 API(webkr)'로 브랜드 공식몰을
  직접 찾고, 공식 홈페이지에서 연락처를 수집한다.
  → 우리 원칙("연락처는 공식 홈페이지 표기값만")과도 잘 맞는다.

흐름 (collect_5.py의 _collect_service_candidates 패턴을 상품용으로 특화):
  [1] 사용자 키워드 → 웹문서 검색 → 브랜드 공식몰 후보 수집
  [2] 종합몰·가격비교·후기포털·뉴스 도메인 컷 (BLOCK_HOSTS)
  [3] 홈페이지 열어 검증: (상품 단어) + (영유아/산모 단어) 동시 포함 + 대기업 컷
  [4] 홈페이지 footer에서 연락처(전화·이메일·상호·대표) 수집
  [5] 중복(DB·도메인) 제외하고 목표 건수까지 → Supabase 저장

실행 예:
  venv\\Scripts\\python collect_web.py --keywords "아기 샴푸, 유아 치약, 아기 칫솔" --count 10
  → 저장 후  venv\\Scripts\\python 엑셀_추출.py --brands "수집된브랜드들" 로 엑셀화
     (또는 collect_web.py 가 끝나며 출력하는 브랜드 목록 사용)
=================================================================
"""
import argparse
import io
import re
import sys
import time
from datetime import datetime
from urllib.parse import urlparse

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

import requests

from supabase_client import get_supabase_client, kor_row_to_db, TABLE_NAME
from market_filter import (
    market_fit_check,
    is_excluded_brand,
    classify_category,
    MARKET_FIT_KEYWORDS,
    GENERIC_TOKENS,
    expand_keyword,
)
from business_info_collector import (
    find_service_business_homepages,
    find_business_info_from_homepage,
    HTTP_HEADERS,
)

# ─────────────────────────────────────────────────────────────────
# 종합몰·가격비교·오픈마켓·후기포털·뉴스 등 '브랜드 공식몰이 아닌' 도메인 컷
# (find_service_business_homepages 의 기본 SKIP 에 더해 상품검색 특화로 보강)
# ─────────────────────────────────────────────────────────────────
BLOCK_HOSTS = (
    # 가격비교·종합몰·오픈마켓
    "danawa.", "domeggook.", "enuri.", "wonprice", "coupang.", "11st",
    "gmarket.", "auction.", "ssg.com", "lotteon.", "wemakeprice", "tmon.",
    "interpark.", "gsshop.", "hnsmall.", "cjonstyle.", "hyundaihmall.",
    "nsmall.", "kmall", "homeplus.", "emart.", "shinsegae.", "himart.",
    "hmall.", "lottemart.", "costco.", "iherb.", "amazon.", "aliexpress.",
    "qoo10.", "tmall.", "musinsa.", "brandi.", "zigzag.", "ably",
    # B2B 도매·유통 플랫폼 (브랜드 아님 — 오너클랜/도매매 등)
    "ownerclan.", "domesin.", "domae", "sedok.", "sinsangmarket.",
    "b2b", "wholesale", "vendor", "domero.", "alibaba", "1688.",
    "made-in-china", "ec21.", "tradekorea", "onnuristore.", "vitatra.",
    # 뷰티 편집샵 (멀티브랜드 — 특정 브랜드 공식몰 아님)
    "oliveyoung.", "chicor.", "lalavla", "aritaum.", "wconcept.",
    # 후기·정보 포털/미디어 (브랜드 아님)
    "doctornow.", "momguide.", "steptohealth.", "bebeheaven.",
    "whittlezip.", "hortitimes.", "datanet.", "mhns.", "babybilly.",
    "10x10.", "ppomppu.", "clien.", "ruliweb.", "mom-ns.",
    # 리뷰·큐레이션·블로그성
    "wikitree.", "insight.", "hankyung.", "mk.co", "sedaily.",
    # 앱/노코드 호스팅 (임시앱·개인프로젝트 — 정식 브랜드몰 아님)
    "vercel.app", "netlify.app", "web.app", "firebaseapp.",
    "github.io", "pages.dev", "notion.site", "imweb.me",
    "modoo.at", "creatorlink.net",
    # 뉴스·매거진·언론·미디어 (브랜드 아님)
    "abcn.", "queen.co.kr", "newsis.", "news1.", "yna.", "edaily.",
    "asiae.", "etnews.", "zdnet.", "bloter.", "inews24.", "ohmynews.",
    "pressian.", "newspim.", "topstarnews.", "econovill.", "mediapen.",
    "khan.", "seoul.co.kr", "kmib.", "segye.", "munhwa.", "heraldcorp.",
    "ilyo.", "ohfun.", "wikitree", "biz.", "fnnews.", "ajunews.",
    "moneys.", "dailian.", "newsculture", "kukinews.", "ceoscore",
    # 커뮤니티·블로그 플랫폼 서브도메인 (bbangtory/mompick 등)
    "bbangtory.", "mompick.", "cafe24.com", "blogspot.", "wordpress.com",
    "babylist.", "mom-mom.",
    # 고객지원(헬프데스크) SaaS — 브랜드 공식몰 아님 (LG생활건강 zendesk 등)
    "zendesk.", "freshdesk.", "zohodesk.", "helpshift.", "gitbook.",
    "gitbook.io", "channel.io", "gitbooks.io",
)

# 뉴스·매거진·커뮤니티성 '이름' 컷 (도메인으로 못 거른 미디어 차단)
MEDIA_NAME_HINTS = (
    "뉴스", "news", "매거진", "magazine", "기자", "신문", "미디어", "media",
    "데일리", "daily", "일보", "타임스", "times", "이코노미", "economy",
    "프레스", "press", "리스트", "list", "저널", "journal", "위클리",
)

# 성인/일반 대상(영유아·산모 전용 아님) 브랜드 컷 — 도메인/이름 신호
# 아기샴푸 검색에 딸려오는 성인 헤어·두피 브랜드 제외
NON_TARGET_BRAND_HINTS = (
    # 성인 헤어·두피 (아기샴푸에 딸려오는)
    "kundal", "쿤달", "pyunkang", "편강", "라보에이치", "닥터포헤어",
    "tsnc", "그루밍", "탈모",
    # 반려동물(펫) 브랜드/몰
    "펫몰", "필펫", "pillpet", "petcare", "drpetra", "반려", "냥이", "댕댕",
)

# ─────────────────────────────────────────────────────────────────
def _domain(url: str) -> str:
    try:
        d = urlparse(url).netloc.replace("www.", "").lower()
        if d.startswith("m."):
            d = d[2:]
        return d
    except Exception:
        return ""


def _is_blocked_host(url: str) -> bool:
    d = _domain(url)
    return any(h in d for h in BLOCK_HOSTS)


# 브랜드명에서 떼어낼 '몰 꼬리표' (공식몰/공식스토어/Mall 등)
_MALL_SUFFIX = re.compile(
    r"(공식\s*온라인\s*(스토어|몰|샵|숍)|공식\s*(스토어|몰|샵|숍|사이트|쇼핑몰)|"
    r"온라인\s*(스토어|몰)|쇼핑몰|공식|official\s*(store|mall|shop)?|store|mall|shop)"
    r"\s*$", re.IGNORECASE)


def _clean_brand_name(name: str) -> str:
    """'닥터노아 공식몰', '큐라프록스 공식쇼핑몰입니다' → '닥터노아', '큐라프록스'."""
    if not name:
        return ""
    # 구분자(| ｜ - / :) 뒷부분 잘라내기 (부제/영문병기 제거)
    name = re.split(r"[|｜/:\-–—]", name)[0].strip()
    # 문장형 꼬리표 제거 ('...입니다/이에요/예요/입니당')
    name = re.sub(r"\s*(공식\s*)?(온라인\s*)?(쇼핑몰|스토어|몰|샵|숍)?\s*"
                  r"(입니다|이에요|예요|입니당|이예요)\s*$", "", name).strip()
    # 꼬리표 반복 제거
    prev = None
    while name and name != prev:
        prev = name
        name = _MALL_SUFFIX.sub("", name).strip()
    return name.strip(" ·-|｜")


def _is_non_target_brand(text: str) -> bool:
    t = (text or "").lower()
    return any(h.lower() in t for h in NON_TARGET_BRAND_HINTS)


def _is_media_name(name: str) -> bool:
    """이름에 뉴스/매거진/리스트 등 미디어·커뮤니티 신호가 있으면 True."""
    t = (name or "").lower()
    return any(h.lower() in t for h in MEDIA_NAME_HINTS)


def _fetch_text(url: str, timeout: int = 8) -> str:
    """홈페이지 HTML → 태그 제거한 텍스트 (검증용). 실패 시 빈 문자열."""
    try:
        r = requests.get(url, headers=HTTP_HEADERS, timeout=timeout)
        if r.status_code != 200 or not r.text:
            return ""
        html = r.text
        html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
        html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
        # og:title / title / description 은 태그 제거 전에 살려둔다 (SPA 대비)
        metas = " ".join(re.findall(
            r'<meta[^>]*content=["\']([^"\']+)["\']', html, flags=re.I))
        title = " ".join(re.findall(r"<title>([^<]+)</title>", html, flags=re.I))
        body = re.sub(r"<[^>]+>", " ", html)
        return f"{title} {metas} {body}"
    except Exception:
        return ""


def _product_tokens(keywords: list) -> set:
    """사용자 키워드에서 '상품 단어' 토큰 추출 (영유아/산모 generic 제외).
    예: ['아기 샴푸','유아 치약','아기 칫솔'] → {'샴푸','치약','칫솔'}
    이 토큰이 홈페이지에 있어야 '그 상품을 다루는 브랜드'로 인정.
    """
    toks = set()
    for kw in keywords:
        for t in re.split(r"[\s,]+", kw.strip()):
            t = t.strip().lower()
            if len(t) >= 2 and t not in GENERIC_TOKENS:
                toks.add(t)
    return toks


# 상품 단어 동의어 보강 (홈페이지 표현 다양성 대비)
PRODUCT_TOKEN_SYNONYMS = {
    "샴푸": {"샴푸", "shampoo", "워시", "wash", "바디워시"},
    "치약": {"치약", "toothpaste", "구강", "양치", "덴탈", "dental"},
    "칫솔": {"칫솔", "toothbrush", "구강", "양치", "덴탈", "dental"},
}


def _expand_product_tokens(toks: set) -> set:
    out = set(toks)
    for t in toks:
        out |= PRODUCT_TOKEN_SYNONYMS.get(t, set())
    return out


MOM_BABY_TOKENS = {
    k.lower() for k in MARKET_FIT_KEYWORDS
    if k in {
        "영유아", "신생아", "영아", "유아", "베이비", "baby", "아기", "아이",
        "출산", "임산부", "임신", "임부", "수유", "모유", "산모", "산후",
        "맘", "mom", "육아", "젖병", "기저귀", "이유식",
    }
}


def _has_any(text: str, tokens) -> bool:
    return any(t in text for t in tokens)


# ─────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="웹검색 기반 상품 브랜드 수집")
    ap.add_argument("--keywords", required=True, help="상품 키워드 (콤마 구분)")
    ap.add_argument("--count", type=int, default=10, help="목표 수집 건수")
    ap.add_argument("--per-query", type=int, default=15, help="키워드당 후보 홈페이지 수")
    ap.add_argument("--allow-big", action="store_true", help="대기업도 포함(기본 제외)")
    ap.add_argument("--out", default="영업처_웹수집.xlsx", help="저장할 엑셀 파일명")
    ap.add_argument("--exclude", default="", help="제외할 브랜드명(콤마 구분) — 기존 수집분 중복 방지")
    ap.add_argument("--exclude-domains", default="", help="제외할 도메인(콤마 구분)")
    args = ap.parse_args()

    target = max(1, min(args.count, 30))
    user_keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    if not user_keywords:
        print("❌ 키워드가 비어있어요.")
        sys.exit(1)

    prod_tokens = _expand_product_tokens(_product_tokens(user_keywords))

    print("=" * 60)
    print("  🌐 웹검색 기반 상품 브랜드 수집 (쇼핑 API 대체)")
    print(f"  키워드: {', '.join(user_keywords)}")
    print(f"  목표: {target}건 · 상품토큰: {sorted(prod_tokens)}")
    print("=" * 60)

    # Supabase 는 있으면 쓰고, 없거나 죽어있어도(DNS 등) 수집·엑셀은 계속 진행
    sb = get_supabase_client()
    db_ok = False
    already = set()
    if sb:
        try:
            res = sb.table(TABLE_NAME).select("brand_name").execute()
            already = {r["brand_name"].strip() for r in (res.data or [])
                       if r.get("brand_name")}
            db_ok = True
            print(f"   ℹ️ 기존 DB 브랜드 {len(already)}건 (중복 제외 대상)\n")
        except Exception as e:
            print(f"   ⚠️ Supabase 접속 실패 → DB 저장 건너뛰고 엑셀만 생성 "
                  f"({str(e)[:50]})\n")
    else:
        print("   ⚠️ Supabase 미설정 → 엑셀만 생성\n")

    # 수동 제외 목록 (기존 엑셀 등에서 넘겨받은 브랜드·도메인) — DB 죽어도 중복 방지
    already |= {b.strip() for b in args.exclude.split(",") if b.strip()}
    exclude_domains = {d.strip().replace("www.", "").lower()
                       for d in args.exclude_domains.split(",") if d.strip()}
    if args.exclude or args.exclude_domains:
        print(f"   🚫 수동 제외: 브랜드 {len(args.exclude.split(',')) if args.exclude else 0}개 · "
              f"도메인 {len(exclude_domains)}개\n")

    # [1] 검색 쿼리 구성 — 사용자 키워드 + '브랜드/공식몰' 변형으로 공식몰 노출↑
    queries = []
    for kw in user_keywords:
        queries.append(kw)
        queries.append(f"{kw} 브랜드")
        queries.append(f"{kw} 공식몰")

    results = []
    seen_domains = set(exclude_domains)   # 기존 수집 도메인 미리 차단
    processed = set()

    print("🔍 [1/3] 웹문서 검색으로 브랜드 공식몰 후보 발굴...")
    for q in queries:
        if len(results) >= target:
            break
        try:
            homepages = find_service_business_homepages(q, max_results=args.per_query)
        except Exception as e:
            print(f"   ⚠ '{q}' 검색 예외: {type(e).__name__}")
            continue

        for hp in homepages:
            if len(results) >= target:
                break
            url = hp.get("url", "")
            if not url or _is_blocked_host(url):
                continue
            dom = _domain(url)
            if not dom or dom in seen_domains:
                continue
            seen_domains.add(dom)

            hp_name = (hp.get("name") or "").strip()
            domain_name = dom.split(".")[0]
            search_name = domain_name or hp_name[:30]

            # 성인/일반 대상 브랜드 컷 (아기샴푸에 딸려오는 성인 헤어 브랜드 등)
            if _is_non_target_brand(f"{hp_name} {dom}"):
                print(f"   🚫 비타깃(성인/일반) 컷: {hp_name[:30]}")
                continue
            # 뉴스·매거진·커뮤니티 이름 컷
            if _is_media_name(hp_name):
                print(f"   🚫 미디어/커뮤니티 컷: {hp_name[:30]}")
                continue

            # [3] 홈페이지 검증 — 상품단어 + 영유아/산모단어 동시 포함
            page_text = _fetch_text(url)
            check_text = f"{hp_name} {page_text}".lower()

            if _is_non_target_brand(check_text[:2000]):
                print(f"   🚫 비타깃(성인/일반) 컷: {hp_name[:30]}")
                continue

            has_prod = _has_any(check_text, prod_tokens)
            has_market = _has_any(check_text, MOM_BABY_TOKENS)
            if not (has_prod and has_market):
                # 홈페이지 텍스트가 빈약(SPA)해도 검색제목에 둘 다 있으면 통과
                continue

            # 대기업 컷(b만) — 브랜드명+상품토큰으로 판정.
            # ⚠️ 'c'(다른시장) 컷은 쓰지 않음: market_fit 부정키워드 'IT' 등이 영어
            #    페이지의 'it' 부분문자열에 걸려 조르단/큐라프록스 같은 진짜 칫솔 브랜드를
            #    오컷했음. 펫 등 다른시장은 아래 NON_TARGET_BRAND_HINTS로 정밀 차단.
            tag, reason = market_fit_check(hp_name or search_name, " ".join(prod_tokens))
            if (not args.allow_big) and tag == "b":
                print(f"   🚫 대기업 컷: {hp_name[:30]} ({reason})")
                continue

            # [4] 연락처 수집 (이 홈페이지만 읽음 — only_hint)
            info = find_business_info_from_homepage(search_name, hint_url=url, only_hint=True)

            raw_name = (info.get("site_name") or info.get("company_name")
                        or hp_name or domain_name).strip()[:60]
            name = _clean_brand_name(raw_name) or domain_name
            if not name or name in processed or name in already or is_excluded_brand(name):
                continue
            if _is_non_target_brand(name) or _is_media_name(name):
                continue
            # 최종 이름에 대기업이 드러나면 컷 (검색제목엔 없다가 사이트명서 드러나는 케이스)
            if not args.allow_big:
                tag2, reason2 = market_fit_check(name, "")
                if tag2 == "b":
                    print(f"   🚫 대기업 컷(최종명): {name} ({reason2})")
                    continue
            processed.add(name)

            # 주력상품/카테고리 — 검색 키워드 기반
            flagship = q.replace(" 브랜드", "").replace(" 공식몰", "").strip()
            prod_cat = classify_category(f"{flagship} {name}")

            has_contact = bool(info.get("phone") or info.get("email"))
            # 연락처(전화·이메일·사업자번호) 하나도 없으면 영업 리드로 무의미 → 제외
            if not (has_contact or info.get("business_number")):
                print(f"   ⏭️ 연락처 없음 → 제외: {name}")
                continue
            print(f"   ▶ {name}  ({url})")
            print(f"       전화={info.get('phone','-') or '-'} · "
                  f"이메일={info.get('email','-') or '-'} · "
                  f"상호={info.get('company_name','-') or '-'}")

            results.append({
                "수집일":               datetime.now().strftime("%Y-%m-%d"),
                "Selpic 점수":          0,
                "발견 카테고리":        prod_cat,
                "발견 키워드":          f"{flagship} (웹검색)",
                "수집 모드":            "web",
                "브랜드명":             name,
                "스마트스토어 주소":    url,   # 공식 홈페이지 (스토어열기→공식몰)
                "주력상품명":           flagship,
                "상품 카테고리":        prod_cat,
                "가격":                 "",
                "점수 근거":            "웹검색 발굴 브랜드 (쇼핑API 대체)",
                "관심고객수 (자동)":    0,
                "상호 (자동)":          info.get("company_name", ""),
                "대표 (자동)":          info.get("ceo", ""),
                "사업자번호 (자동)":    info.get("business_number", ""),
                "전화 (자동)":          info.get("phone", ""),
                "이메일 (자동)":        info.get("email", ""),
                "사업자정보 출처 (자동)":   "공식 홈페이지" if has_contact else "연락처 미발견 — 수기 입력 필요",
                "사업자정보 신뢰도 (자동)": "중간" if has_contact else "미발견",
            })
            time.sleep(0.2)

    print(f"\n🔬 [2/3] 최종 선별: {len(results)}건 "
          f"(목표 {target}건{' — 미달' if len(results) < target else ''})")

    # [5] Supabase 저장 (살아있을 때만)
    print("💾 [3/3] 저장...")
    saved = 0
    if db_ok:
        for r in results:
            try:
                sb.table(TABLE_NAME).upsert(kor_row_to_db(r), on_conflict="brand_name").execute()
                saved += 1
            except Exception as e:
                print(f"   ⚠️ {r['브랜드명']} DB저장 실패: {str(e)[:80]}")
        print(f"   ✅ DB 저장: {saved}/{len(results)}건")
    else:
        print("   ⏭️ DB 미접속 — DB 저장 건너뜀 (엑셀로 대체)")

    # 항상 엑셀 파일로 저장 (DB 유무와 무관하게 결과 확보)
    xlsx = _write_excel(results, args.out)
    print(f"   📊 엑셀 저장: {xlsx}\n")

    # 결과 요약
    print("=" * 60)
    print(f"📌 수집 결과 {len(results)}건")
    print("=" * 60)
    for i, r in enumerate(results, 1):
        print(f"[{i}] {r['브랜드명']}  | 주력:{r['주력상품명']} | "
              f"전화:{r['전화 (자동)'] or '-'} | 이메일:{r['이메일 (자동)'] or '-'}")
    brand_csv = ",".join(r["브랜드명"] for r in results)
    try:
        with open("수집_웹_최근브랜드.txt", "w", encoding="utf-8") as f:
            f.write(brand_csv)
    except Exception:
        pass


def _write_excel(results: list, out_path: str) -> str:
    """수집 결과(한글키 dict 리스트) → 보기 좋은 xlsx. DB 없이도 동작."""
    import pandas as pd
    cols = [
        ("브랜드명", "브랜드명"), ("주력상품명", "주력상품명"),
        ("상품 카테고리", "상품 카테고리"), ("발견 키워드", "발견 키워드"),
        ("전화 (자동)", "전화"), ("이메일 (자동)", "이메일"),
        ("상호 (자동)", "상호"), ("대표 (자동)", "대표"),
        ("사업자번호 (자동)", "사업자번호"),
        ("스마트스토어 주소", "공식 홈페이지"),
        ("사업자정보 신뢰도 (자동)", "연락처 신뢰도"),
        ("수집일", "수집일"),
    ]
    rows = [{disp: r.get(src, "") for src, disp in cols} for r in results]
    df = pd.DataFrame(rows)
    with pd.ExcelWriter(out_path, engine="xlsxwriter") as xw:
        df.to_excel(xw, index=False, sheet_name="영업처")
        wb, ws = xw.book, xw.sheets["영업처"]
        hf = wb.add_format({"bold": True, "bg_color": "#DCE6F1",
                            "border": 1, "align": "center", "valign": "vcenter"})
        for c, col in enumerate(df.columns):
            ws.write(0, c, col, hf)
            width = max([len(str(col))] + [len(str(v)) for v in df[col].tolist()])
            ws.set_column(c, c, min(max(width + 2, 10), 45))
        ws.freeze_panes(1, 0)
    return out_path


if __name__ == "__main__":
    main()
