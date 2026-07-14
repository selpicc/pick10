# -*- coding: utf-8 -*-
"""셀픽이앤에스 영업메일 템플릿 엔진
─────────────────────────────────────────────────────────────
브랜드 1건(DB row dict) → 제목 + HTML 본문 + 평문 본문 생성.

설계 원칙 (2026-07 개편 — 콜드메일 회신률 중심):
1. 제목에 회사명·"제안" 안 씀 → 광고메일로 걸러지지 않게
2. 첫 문단은 우리 소개가 아니라 '상대 브랜드 얘기'
3. 우리 자산(230곳·10만 DB)을 상대 이익(첫 접점·지역 타겟·리드)으로 번역
4. 가격 미노출 — 첫 메일에서 금액은 거절 트리거. 견적은 회신·미팅에서
5. CTA는 하나 — 셀픽 소개서를 함께 첨부하고, 유선 통화 또는 미팅을 요청한다.
   ("편하신 시간만 한 줄 회신 주세요" — 상대가 할 일을 최소로)
6. 소개서는 사람이 Gmail 임시보관함에서 직접 첨부해 발송 (본문이 첨부를 전제로 함)
7. 근거 수치는 전부 셀픽 MEDIA PROPOSAL(2026.07)에 실제로 있는 값만 사용.
   성과 수치(전환율 등)는 소개서에 없으므로 절대 지어내지 않는다.

제품형 / 서비스형(출산 서비스) 자동 분기.

build_email(row) → {
    "to", "subject", "html", "plain",
    "template", "skip"(bool), "skip_reason"(str)
}
사용처: dashboard 버튼 / 메일초안_생성.py 가 이 함수로 본문을 만들고 초안 생성.
"""
import html as _html


# ─────────────────────────────────────────────────────────
# 확정 서명 (2026-06 사용자 확정 — 영업팀)
# ─────────────────────────────────────────────────────────
SIG_HTML = (
    '<div style="border-top:2px solid #ddd;padding-top:12px;margin-top:8px;'
    'color:#444;font-size:13px;line-height:1.6;">'
    '<b>셀픽이앤에스</b> / 영업팀<br>'
    '<b>서민지</b> 선임 매니저<br>'
    'M. 010-2421-6905<br>'
    'E. selpic100@gmail.com<br>'
    '셀픽 홈페이지 <a href="https://selpic.kr/" '
    'style="color:#1a73e8;text-decoration:none;">www.selpic.kr</a>'
    '</div>'
)
SIG_PLAIN = (
    "============================================\n"
    "셀픽이앤에스 / 영업팀\n"
    "서민지 선임 매니저\n"
    "M. 010-2421-6905\n"
    "E. selpic100@gmail.com\n"
    "셀픽 홈페이지 selpic.kr\n"
    "============================================"
)

# ─────────────────────────────────────────────────────────
# 플랫폼 설명 — 소개서 실측치만 사용
#   산후조리원 230곳(M/S 50%) · 산부인과 55곳 · 조리원 체류 14일 매일 접촉
#   1인당 15장 무료 인화 · 키오스크 DA 월 300만 노출 보장(일 약 10만회)
# ─────────────────────────────────────────────────────────
PLATFORM_HTML = (
    '셀픽은 전국 산후조리원 <b>230곳(전체의 약 절반)</b>과 산부인과 55곳에서<br>'
    '사진 인화 키오스크를 운영합니다.<br>'
    '산모는 조리원에 머무는 <b>14일 내내</b> 초음파·아기 사진을 뽑으러 이 화면 앞에 섭니다.<br>'
    '(1인당 15장 무료 인화 / 키오스크 광고는 <b>월 300만 회 노출 보장</b>)'
)
PLATFORM_PLAIN = (
    "셀픽은 전국 산후조리원 230곳(전체의 약 절반)과 산부인과 55곳에서\n"
    "사진 인화 키오스크를 운영합니다.\n"
    "산모는 조리원에 머무는 14일 내내 초음파·아기 사진을 뽑으러 이 화면 앞에 섭니다.\n"
    "(1인당 15장 무료 인화 / 키오스크 광고는 월 300만 회 노출 보장)"
)

# 레퍼런스 (소개서 Appendix 실제 집행 사례 — 지금 집행 중인 게 아니라 '진행했던' 캠페인.
#            현재형으로 쓰면 미팅에서 바로 들통난다. 성과 수치는 소개서에 없어 언급 안 함)
REFERENCE_HTML = (
    '<b>네슬레 일루맘클럽</b>, <b>리안 드림콧</b>, 프리미엄 분유 <b>퓨어락</b>도 '
    '이 구조로 셀픽에서 캠페인을 진행했습니다.'
)
REFERENCE_PLAIN = (
    "네슬레 일루맘클럽, 리안 드림콧, 프리미엄 분유 퓨어락도 "
    "이 구조로 셀픽에서 캠페인을 진행했습니다."
)

# 매월 신규 산모 DB (소개서: 매월 신규 DB 8,000건 / 그중 산모 5,100건)
DB_MONTHLY_MOM = "5,100"


# ─────────────────────────────────────────────────────────
# 카테고리별 프로필 (제품형)
#   hook_noun : 도입부에서 부를 제품 표현
#   insight   : "왜 지금 이 브랜드인가" — 한 줄 (길면 안 읽힘)
#   sampling  : 조리원 MRO·샘플링 구좌를 이 카테고리에 맞게 표현
#   lms       : 타겟 LMS 발송 타이밍
# ─────────────────────────────────────────────────────────
CATEGORY_PROFILE = {
    "분유·이유식": {
        "hook_noun": "제품",
        "insight": (
            "이유식은 조리원에서 각인된 브랜드가 생후 4~6개월 첫 구매로 그대로 이어지는 "
            "카테고리라, 이 시점 접점이 {brand}에 특히 크게 작용한다고 봤습니다."
        ),
        "sampling": "입실 키트·맘박스로 산모가 직접 먹여보게",
        "lms": "이유식 시작 시점(생후 4~6개월) 부모만 골라 발송",
    },
    "베이비 스킨케어": {
        "hook_noun": "베이비 스킨케어 제품",
        "insight": (
            "신생아 스킨케어는 '조리원에서 처음 바른 제품'이 그대로 반복 구매로 굳는 "
            "카테고리라, 이 시점 접점이 {brand}에 특히 크게 작용한다고 봤습니다."
        ),
        "sampling": "입실 키트·맘박스로 산모가 직접 발라보게",
        "lms": "생후 1개월 이내 신생아 부모만 골라 발송",
    },
    "기저귀·물티슈": {
        "hook_noun": "제품",
        "insight": (
            "기저귀·물티슈는 조리원에서 처음 쓴 제품이 그대로 정기 구매로 굳는 소모품이라, "
            "이 시점 접점이 {brand}에 특히 크게 작용한다고 봤습니다."
        ),
        "sampling": (
            "신생아실 사용 물품(MRO) 입점 + 신생아실 유리에 사용 인증 마크 부착"
        ),
        "lms": "생후 1개월 이내 신생아 부모만 골라 발송",
    },
    "수유용품": {
        "hook_noun": "수유용품",
        "insight": (
            "수유용품은 출산 직후부터 바로 쓰는 품목이라 조리원 체류 기간이 가장 강력한 "
            "첫 접점이고, 이 시점 경험이 {brand}의 재구매로 이어진다고 봤습니다."
        ),
        "sampling": "입실 키트·맘박스로 산모가 직접 써보게",
        "lms": "생후 1개월 이내 신생아 부모만 골라 발송",
    },
    "임산부 뷰티": {
        "hook_noun": "제품",
        "insight": (
            "임산부 뷰티는 조리원 체류 시점이 산모가 제품을 직접 경험하기 가장 좋은 "
            "타이밍이고, 그 경험이 {brand}의 재구매로 이어진다고 봤습니다."
        ),
        "sampling": "입실 키트·맘박스로 산모가 직접 써보게",
        "lms": "출산 직후 산모만 골라 발송",
    },
    "임산부 영양제": {
        "hook_noun": "제품",
        "insight": (
            "임산부·수유 영양제는 구매 주기가 길어, 조리원에서 브랜드를 각인시키면 "
            "수유기 재구매까지 연결된다고 봤습니다."
        ),
        "sampling": "입실 키트·맘박스로 산모가 직접 써보게",
        "lms": "출산 직후~수유기 산모만 골라 발송",
    },
    "이동·외출": {
        "hook_noun": "제품",
        "insight": (
            "유모차·카시트는 출산 전후 한 번에 결정되는 고관여 품목이라, 산모가 정보를 "
            "가장 활발히 찾는 조리원 체류 시점의 노출이 {brand}의 구매 결정에 직접 닿는다고 봤습니다."
        ),
        "sampling": "실제 산모 체험단으로 실사용 콘텐츠 확보",
        "lms": "출산 예정·신생아 부모만 골라 발송",
    },
    "임산부 의류": {
        "hook_noun": "제품",
        "insight": (
            "임부복·수유복은 주수에 따라 반복 구매가 일어나는 카테고리라, 출산 직후 산모 "
            "접점에서의 노출이 {brand}의 산후복·수유복 구매로 이어진다고 봤습니다."
        ),
        "sampling": "맘박스로 제품 경험 + 산모 체험단 운영",
        "lms": "출산 직후 산모만 골라 발송",
    },
    "유아 의류": {
        "hook_noun": "제품",
        "insight": (
            "신생아 의류는 '처음 입힌 브랜드'가 돌·유아동복까지 반복 구매로 이어지는 "
            "구조라, 조리원 체류 기간이 {brand}에 가장 강력한 첫 접점이라고 봤습니다."
        ),
        "sampling": (
            "신생아실 사용 물품(MRO) 입점 + 맘박스로 소품 체험"
        ),
        "lms": "생후 1개월 이내 신생아 부모만 골라 발송",
    },
}

# 카테고리를 못 찾았을 때 쓰는 일반 제품 프로필
DEFAULT_PROFILE = {
    "hook_noun": "제품",
    "insight": (
        "영유아·임산부 카테고리는 출산 직후 각인된 브랜드가 반복 구매로 이어지는 구조라, "
        "이 시점 접점이 {brand}에 특히 크게 작용한다고 봤습니다."
    ),
    "sampling": "조리원 입실 키트·맘박스 샘플링 — 산모가 직접 써보게",
    "lms": "생후 1개월 이내 신생아 부모만 골라 발송",
}


def _esc(s: str) -> str:
    return _html.escape(s or "", quote=False)


def _josa(word: str, with_batchim: str, without_batchim: str) -> str:
    """앞 단어의 받침 유무에 따라 조사 선택.
    예: _josa("제품","을","를")→"을", _josa("베이비","을","를")→"를"
    한글이면 받침 계산, 영문은 모음(a/e/i/o/u/y)으로 끝나면 받침 없음 취급.
    """
    w = (word or "").strip()
    if not w:
        return without_batchim
    ch = w[-1]
    code = ord(ch)
    if 0xAC00 <= code <= 0xD7A3:          # 한글 음절
        return with_batchim if (code - 0xAC00) % 28 != 0 else without_batchim
    if ch.isalpha():                       # 영문
        return without_batchim if ch.lower() in "aeiouy" else with_batchim
    if ch.isdigit():                       # 숫자 발음 받침 (1일/3삼/6육/7칠/8팔/0영)
        return with_batchim if ch in "0136780" else without_batchim
    return with_batchim                    # 기타 기호 → 받침 있는 것처럼


# 스마트스토어 상품명 꼬리에 붙는 검색 키워드 (메일에 그대로 넣으면 지저분)
_PROMO_TOKENS = (
    "선물", "세트선물", "출산선물", "무료배송", "당일발송", "정품", "본품",
    "1+1", "행사", "특가", "할인", "사은품", "증정", "기획", "기획전",
)


def _short_flagship(flagship: str, brand: str) -> str:
    """주력상품명 정리 — 상품명이 아니라 '검색 키워드 나열'인 경우가 많다.
    앞쪽 3어절만 남기고, 꼬리의 판촉 키워드는 떼어낸다.
    예) '마미투비 임산부화장품 스킨로션세트 무향 순한성분 기초화장품 산모크림선물'
        → '마미투비 임산부화장품 스킨로션세트'
    """
    f = (flagship or "").strip()
    if not f:
        return ""
    words = [w for w in f.split() if w]
    # 꼬리에서부터 판촉성 단어 제거
    while words and any(words[-1].endswith(t) for t in _PROMO_TOKENS):
        words.pop()
    if not words:
        return ""
    f = " ".join(words[:3])
    if len(f) > 30:
        f = f[:30].rstrip() + "…"
    return f


# ─────────────────────────────────────────────────────────
# 지역 사업자 판별
#   서비스형은 두 부류가 섞여 들어온다.
#     - 지역형: 사진관·산후도우미처럼 상권 안 산모만 고객 (예: 김해 스튜디오)
#     - 전국형: 온라인·배송 기반 서비스 (전국 조리원 동시 노출이 맞음)
#   지역형에 "전국 캠페인" 얘기를 하거나, 전국형에 "상권" 얘기를 하면 바로 어색해진다.
#   판별 근거는 수집된 사실만 사용: ① 유선 지역번호 ② 주소 ③ 상호에 든 지역명
# ─────────────────────────────────────────────────────────
_AREA_CODE_REGION = {
    "02": "서울", "031": "경기", "032": "인천", "033": "강원",
    "041": "충남", "042": "대전", "043": "충북", "044": "세종",
    "051": "부산", "052": "울산", "053": "대구", "054": "경북",
    "055": "경남", "061": "전남", "062": "광주", "063": "전북",
    "064": "제주",
}

# 상호·주소에서 바로 집어낼 수 있는 시·군 이름 (있으면 광역명보다 이걸 우선)
_CITY_NAMES = (
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
    "수원", "성남", "분당", "용인", "고양", "일산", "화성", "동탄", "부천",
    "안산", "안양", "평택", "시흥", "김포", "광명", "군포", "하남", "오산",
    "이천", "안성", "의정부", "남양주", "파주", "구리", "포천", "양주",
    "춘천", "원주", "강릉", "속초",
    "청주", "충주", "제천", "천안", "아산", "서산", "당진",
    "전주", "익산", "군산", "목포", "여수", "순천", "광양",
    "포항", "구미", "경주", "안동", "김천", "경산",
    "창원", "김해", "진주", "양산", "거제", "통영", "제주", "서귀포",
    "강남", "서초", "송파", "강동", "마포", "용산", "노원", "은평",
)


def _detect_region(row: dict):
    """(지역형 여부, 지역명) 반환. 지역명은 못 찾으면 빈 문자열.
    지역형이라도 지역명이 비면 '상권'이라는 일반 표현으로 쓴다.
    """
    brand = (row.get("brand_name") or "")
    addr = (row.get("auto_address") or "") or ""
    phone = (row.get("auto_phone") or row.get("manual_phone") or "").strip()

    # ① 상호·주소에 시·군 이름이 박혀 있으면 그게 가장 확실하다
    for city in _CITY_NAMES:
        if city in brand or city in addr:
            return True, city

    # ② 유선 지역번호 → 광역 (010 휴대폰 / 1544·1588 대표번호 / 070 인터넷전화는 지역 근거 못 됨)
    digits = "".join(ch for ch in phone if ch.isdigit())
    if digits.startswith("02"):
        return True, _AREA_CODE_REGION["02"]
    if digits[:3] in _AREA_CODE_REGION:
        return True, _AREA_CODE_REGION[digits[:3]]

    # ③ 근거 없음 → 전국형으로 본다 (틀린 지역명을 지어내는 것보다 안전)
    return False, ""


def _wrap_html(inner: str) -> str:
    return (
        '<div style="font-family:\'Apple SD Gothic Neo\',\'Malgun Gothic\','
        'sans-serif;font-size:14px;line-height:1.75;color:#333;max-width:600px;">'
        + inner + '</div>'
    )


def _bullets_html(items) -> str:
    """· 로 시작하는 짧은 목록 (제안 구조를 표 대신 가볍게)"""
    out = ""
    for label, desc in items:
        out += (
            '<div style="margin:0 0 8px;">'
            f'<span style="font-weight:700;color:#e8590c;">· {_esc(label)}</span> — '
            f'<span style="color:#555;">{_esc(desc)}</span></div>'
        )
    return out


def _bullets_plain(items) -> str:
    return "\n".join(f"· {label} — {desc}" for label, desc in items)


# ─────────────────────────────────────────────────────────
# 제품형
# ─────────────────────────────────────────────────────────
def _build_product(row: dict) -> dict:
    brand = (row.get("brand_name") or "").strip()
    found_cat = (row.get("category") or "").strip()
    flagship = _short_flagship(row.get("flagship_product", ""), brand)

    prof = CATEGORY_PROFILE.get(found_cat) or DEFAULT_PROFILE
    insight = prof["insight"].format(brand=brand)
    hook_noun = prof["hook_noun"]

    subject = f"{brand}, 산모가 조리원에서 처음 쓰는 제품이 되려면"

    # 도입 — 상대 얘기부터.
    # ① AI가 홈페이지를 읽고 쓴 브랜드 전용 문장이 있으면 그걸 최우선 (진짜 개인화)
    # ② 없으면 주력상품명을 짚고 카테고리 인사이트로 (기존 방식)
    ai = (row.get("ai_opener") or "").strip()
    if ai:
        opener_html = _esc(ai)
        opener_plain = ai
    elif flagship:
        opener_html = (
            f'<b>[{_esc(flagship)}]</b> 보고 연락드립니다.<br>'
            f'{_esc(insight)}'
        )
        opener_plain = (
            f"[{flagship}] 보고 연락드립니다.\n{insight}"
        )
    else:
        opener_html = (
            f'{_esc(brand)}의 {hook_noun}{_josa(hook_noun, "을", "를")} 보고 연락드립니다.<br>'
            f'{_esc(insight)}'
        )
        opener_plain = (
            f"{brand}의 {hook_noun}{_josa(hook_noun, '을', '를')} 보고 연락드립니다.\n{insight}"
        )

    slots = (
        ("키오스크·모바일 광고", "조리원 체류 14일 동안 반복 노출"),
        ("조리원 샘플링", prof["sampling"]),
        ("타겟 LMS",
         f"매월 새로 쌓이는 산모 DB {DB_MONTHLY_MOM}건 중 {prof['lms']}"),
        ("맘카페 체험단", "실제로 써본 산모의 후기 콘텐츠 확보"),
    )

    inner = (
        f'안녕하세요, <b>{_esc(brand)}</b> 담당자님.<br>'
        '셀픽이앤에스 서민지입니다.<br><br>'
        f'{opener_html}<br><br>'
        f'{PLATFORM_HTML}<br><br>'
        f'{REFERENCE_HTML}<br><br>'
        f'<b>{_esc(brand)}라면 이렇게 붙일 수 있습니다.</b><br>'
        f'{_bullets_html(slots)}<br>'
        '자세한 내용은 <b>셀픽 소개서</b>에 정리해 함께 첨부드립니다.<br>'
        f'가볍게 살펴보신 뒤, <b>유선으로 짧게 통화하거나 직접 찾아뵙고</b> '
        f'{_esc(brand)}에 맞는 운영안을 말씀드리고 싶습니다.<br>'
        '편하신 시간만 한 줄 회신 주시면 그때 맞춰 연락드리겠습니다.<br><br>'
        f'{SIG_HTML}'
    )

    plain = (
        f"안녕하세요, {brand} 담당자님.\n"
        "셀픽이앤에스 서민지입니다.\n\n"
        f"{opener_plain}\n\n"
        f"{PLATFORM_PLAIN}\n\n"
        f"{REFERENCE_PLAIN}\n\n"
        f"[ {brand}라면 이렇게 붙일 수 있습니다 ]\n"
        f"{_bullets_plain(slots)}\n\n"
        "자세한 내용은 셀픽 소개서에 정리해 함께 첨부드립니다.\n"
        f"가볍게 살펴보신 뒤, 유선으로 짧게 통화하거나 직접 찾아뵙고 "
        f"{brand}에 맞는 운영안을 말씀드리고 싶습니다.\n"
        "편하신 시간만 한 줄 회신 주시면 그때 맞춰 연락드리겠습니다.\n\n"
        f"{SIG_PLAIN}"
    )

    return {
        "subject": subject,
        "html": _wrap_html(inner),
        "plain": plain,
        "template": "product",
    }


# ─────────────────────────────────────────────────────────
# 서비스형 (출산 서비스 — 사진관·산후도우미·산후케어 등)
#   지역형(사진관 등)  → "상권 안 조리원만 골라 노출" 이 최대 무기
#   전국형(온라인·배송) → "전국 230곳 동시 노출 + 월령 타겟" 이 최대 무기
#   이 둘을 섞으면 바로 어색해지므로 _detect_region 으로 갈라 쓴다.
# ─────────────────────────────────────────────────────────
def _build_service(row: dict) -> dict:
    brand = (row.get("brand_name") or "").strip()
    is_local, region = _detect_region(row)

    if is_local:
        # 지역명을 못 집으면 '상권'이라는 일반 표현으로 (틀린 지역명 쓰지 않기)
        where = f"{region} 권역" if region else "상권 안"
        subject = f"{brand}, {where} 조리원 산모에게만 노출할 수 있습니다"
        headline = f"{brand}에 맞을 것 같은 건 지역 타겟입니다."
        slots = (
            ("키오스크 광고", f"{where} 조리원·산부인과에만 노출 (지역 단위 선택)"),
            ("타겟 LMS",
             f"매월 새로 쌓이는 산모 DB {DB_MONTHLY_MOM}건 중 지역으로 골라 발송"),
            ("맘카페 체험단", "실제 이용한 산모의 후기 콘텐츠 확보"),
        )
        closer_html = (
            f'전국 캠페인까지 안 가도, <b>{_esc(where)} 산모만 골라</b> '
            '작게 시작할 수 있는 구조입니다.'
        )
        closer_plain = (
            f"전국 캠페인까지 안 가도, {where} 산모만 골라 작게 시작할 수 있는 구조입니다."
        )
    else:
        subject = f"{brand}, 조리원에 있는 산모에게 먼저 닿는 방법"
        headline = f"{brand}라면 이렇게 붙일 수 있습니다."
        slots = (
            ("키오스크·모바일 광고", "전국 조리원 230곳에서 체류 14일 동안 반복 노출"),
            ("타겟 LMS",
             f"매월 새로 쌓이는 산모 DB {DB_MONTHLY_MOM}건 중 월령·지역으로 골라 발송"),
            ("맘카페 체험단", "실제 이용한 산모의 후기 콘텐츠 확보"),
        )
        closer_html = (
            '조리원에서 <b>서비스를 처음 알게 되는 자리</b>를 선점하는 구조입니다.'
        )
        closer_plain = (
            "조리원에서 서비스를 처음 알게 되는 자리를 선점하는 구조입니다."
        )

    # AI가 홈페이지를 읽고 쓴 브랜드 전용 문장이 있으면 도입부에 한 줄 얹는다
    ai = (row.get("ai_opener") or "").strip()
    ai_html = f'{_esc(ai)}<br><br>' if ai else ""
    ai_plain = f"{ai}\n\n" if ai else ""

    inner = (
        f'안녕하세요, <b>{_esc(brand)}</b> 담당자님.<br>'
        '셀픽이앤에스 서민지입니다.<br><br>'
        f'{ai_html}'
        '출산 관련 서비스는 산모가 조리원에 있는 2주 사이에 거의 결정된다고 알고 있습니다.<br>'
        f'그 2주 동안 산모가 <b>매일</b> 보는 화면에 {_esc(brand)}{_josa(brand, "을", "를")} '
        '띄울 수 있어 연락드립니다.<br><br>'
        f'{PLATFORM_HTML}<br><br>'
        f'{REFERENCE_HTML}<br><br>'
        f'<b>{_esc(headline)}</b><br>'
        f'{_bullets_html(slots)}'
        f'{closer_html}<br><br>'
        '자세한 내용은 <b>셀픽 소개서</b>에 정리해 함께 첨부드립니다.<br>'
        f'가볍게 살펴보신 뒤, <b>유선으로 짧게 통화하거나 직접 찾아뵙고</b> '
        f'{_esc(brand)}에 맞는 운영안을 말씀드리고 싶습니다.<br>'
        '편하신 시간만 한 줄 회신 주시면 그때 맞춰 연락드리겠습니다.<br><br>'
        f'{SIG_HTML}'
    )

    plain = (
        f"안녕하세요, {brand} 담당자님.\n"
        "셀픽이앤에스 서민지입니다.\n\n"
        f"{ai_plain}"
        "출산 관련 서비스는 산모가 조리원에 있는 2주 사이에 거의 결정된다고 알고 있습니다.\n"
        f"그 2주 동안 산모가 매일 보는 화면에 {brand}{_josa(brand, '을', '를')} "
        "띄울 수 있어 연락드립니다.\n\n"
        f"{PLATFORM_PLAIN}\n\n"
        f"{REFERENCE_PLAIN}\n\n"
        f"[ {headline} ]\n"
        f"{_bullets_plain(slots)}\n\n"
        f"{closer_plain}\n\n"
        "자세한 내용은 셀픽 소개서에 정리해 함께 첨부드립니다.\n"
        f"가볍게 살펴보신 뒤, 유선으로 짧게 통화하거나 직접 찾아뵙고 "
        f"{brand}에 맞는 운영안을 말씀드리고 싶습니다.\n"
        "편하신 시간만 한 줄 회신 주시면 그때 맞춰 연락드리겠습니다.\n\n"
        f"{SIG_PLAIN}"
    )

    return {
        "subject": subject,
        "html": _wrap_html(inner),
        "plain": plain,
        "template": "service",
    }


def build_email(row: dict) -> dict:
    """브랜드 row → 영업메일. 이메일 없으면 skip=True."""
    brand = (row.get("brand_name") or "").strip()
    email = (row.get("auto_email") or row.get("manual_email") or "").strip()

    base = {"to": email, "subject": "", "html": "", "plain": "",
            "template": "", "skip": False, "skip_reason": ""}

    if not brand:
        base.update(skip=True, skip_reason="브랜드명 없음")
        return base
    if not email or "@" not in email:
        base.update(skip=True, skip_reason="이메일 없음 (수기 입력 필요)")
        return base

    # 신뢰도 미발견이면 보류 (대원칙: 공식홈 정보만)
    conf = (row.get("auto_biz_confidence") or "").strip()
    if conf == "미발견":
        base.update(skip=True, skip_reason="공식홈 미발견 — 수기 확인 필요")
        return base

    found_cat = (row.get("category") or "").strip()
    prod_cat = (row.get("product_category") or "").strip()
    is_service = ("서비스" in found_cat) or ("서비스" in prod_cat)

    built = _build_service(row) if is_service else _build_product(row)
    base.update(built)
    return base
