# -*- coding: utf-8 -*-
"""
셀픽이앤에스 영업메일 템플릿 엔진
─────────────────────────────────────────────────────────────
브랜드 1건(DB row dict) → 제목 + HTML 본문 + 평문 본문 생성.

- 제품형 / 서비스형(출산 서비스) 자동 분기
- 카테고리별 인사이트·샘플링·LMS 타이밍 치환
- 브랜드 훅 + 주력상품 치환
- 확정 서명(영업팀 / 서민지 / nate / selpic.kr)

build_email(row) → {
    "to", "subject", "html", "plain",
    "template", "skip"(bool), "skip_reason"(str)
}
사용처: dashboard 버튼이 이 함수로 본문을 만들고 gmail_drafts 로 초안 생성.
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
    'E. ondayon1@nate.com<br>'
    '셀픽 홈페이지 <a href="https://selpic.kr/" '
    'style="color:#1a73e8;text-decoration:none;">www.selpic.kr</a>'
    '</div>'
)
SIG_PLAIN = (
    "============================================\n"
    "셀픽이앤에스 / 영업팀\n"
    "서민지 선임 매니저\n"
    "M. 010-2421-6905\n"
    "E. ondayon1@nate.com\n"
    "셀픽 홈페이지 selpic.kr\n"
    "============================================"
)

# 셀픽 소개 (모든 메일 공통)
SELPIC_INTRO_HTML = (
    '셀픽은 전국 산후조리원·산부인과에 설치된 사진 인화 플랫폼을 기반으로<br>'
    '<b style="color:#e8590c;">연간 10만 명의 신생아·부모 DB</b>를 확보하고 있습니다.<br>'
    '브랜드 노출 → 맘박스 샘플링 → 생후 개월 타겟 LMS 전환까지<br>'
    '<b>"조리원 체류 기간 → 경험 → 전환"</b> 흐름으로 연결하는 마케팅을 운영합니다.'
)
SELPIC_INTRO_PLAIN = (
    "셀픽은 전국 산후조리원·산부인과에 설치된 사진 인화 플랫폼을 기반으로\n"
    "연간 10만 명의 신생아·부모 DB를 확보하고 있습니다.\n"
    "브랜드 노출 → 맘박스 샘플링 → 생후 개월 타겟 LMS 전환까지\n"
    '"조리원 체류 기간 → 경험 → 전환" 흐름으로 연결하는 마케팅을 운영합니다.'
)


# ─────────────────────────────────────────────────────────
# 카테고리별 프로필 (제품형)
#   hook_noun : 도입부 훅에서 부를 제품 표현
#   insight   : "왜 이 브랜드에 제안하는가" (카테고리 인사이트)
#   sampling  : ② 신뢰 구축 단계의 샘플링 문구
#   lms       : ③ 전환 마케팅 단계의 LMS 타이밍 문구
# ─────────────────────────────────────────────────────────
CATEGORY_PROFILE = {
    "분유·이유식": {
        "hook_noun": "제품",
        "insight": (
            "이유식 카테고리는 출산 직후 형성된 브랜드 인지가 "
            "생후 4~6개월 이유식 시작 시점의 첫 구매로 이어지는 구조가 강한 시장입니다. "
            "특히 정기식단·구독 모델은 한 번 시작되면 반복 구매로 연결되기 때문에, "
            "이 초기 접점 설계가 {brand} 성장에 중요한 요소라 판단했습니다."
        ),
        "sampling": "셀픽 운영 맘박스를 통한 이유식 체험 샘플링 (월 2,000~5,000개 규모)",
        "lms": "이유식 시작 시점(생후 4~6개월) 부모 대상 타겟 LMS",
    },
    "베이비 스킨케어": {
        "hook_noun": "베이비 스킨케어 제품",
        "insight": (
            "신생아 스킨케어는 '조리원에서 처음 사용한 제품'이 "
            "이후 반복 구매로 이어지는 구조가 강한 카테고리입니다. "
            "보습·태열·마사지 루틴이 출산 직후 형성되기 때문에, "
            "초기 접점 설계가 {brand} 성장에 중요한 요소라 판단했습니다."
        ),
        "sampling": "셀픽 운영 맘박스를 통한 스킨케어 체험 샘플링 (월 2,000~5,000개 규모)",
        "lms": "생후 1개월 이내 신생아 부모 대상 타겟 LMS",
    },
    "기저귀·물티슈": {
        "hook_noun": "제품",
        "insight": (
            "기저귀·물티슈는 신생아기에 매일 사용하는 소모품으로, "
            "조리원에서 처음 접한 제품이 그대로 정기 구매로 굳어지는 카테고리입니다. "
            "출산 직후 첫 사용 경험이 {brand}의 브랜드 충성도로 직결된다고 판단했습니다."
        ),
        "sampling": "제휴 조리원 신생아실 사용분 지원 / 맘박스를 통한 제품 샘플링",
        "lms": "생후 1개월 이내 신생아 부모 대상 타겟 LMS",
    },
    "수유용품": {
        "hook_noun": "수유용품",
        "insight": (
            "수유용품은 출산 직후부터 바로 사용하는 품목으로, "
            "조리원 체류 기간이 가장 강력한 첫 접점입니다. "
            "이 시점의 첫 사용 경험이 {brand}의 추가 구매·재구매로 이어진다고 판단했습니다."
        ),
        "sampling": "셀픽 운영 맘박스를 통한 수유용품 체험 샘플링",
        "lms": "생후 1개월 이내 신생아 부모 대상 타겟 LMS",
    },
    "임산부 뷰티": {
        "hook_noun": "제품",
        "insight": (
            "임산부 뷰티는 임신 중부터 산후까지 꾸준히 사용하는 카테고리로, "
            "산후조리원 체류 시점이 산모가 제품을 직접 경험하기 가장 좋은 타이밍입니다. "
            "이 시점의 경험이 {brand}의 재구매로 이어진다고 판단했습니다."
        ),
        "sampling": "셀픽 운영 맘박스를 통한 제품 체험 샘플링",
        "lms": "출산 직후 산모 대상 타겟 LMS",
    },
    "임산부 영양제": {
        "hook_noun": "제품",
        "insight": (
            "임산부·수유 영양제는 임신 중부터 수유기까지 이어지는, 구매 주기가 긴 카테고리입니다. "
            "산후조리원 체류 시점에 브랜드를 각인시키면 수유기 재구매로 연결된다고 판단했습니다."
        ),
        "sampling": "셀픽 운영 맘박스를 통한 제품 샘플링",
        "lms": "출산 직후~수유기 산모 대상 타겟 LMS",
    },
    "이동·외출": {
        "hook_noun": "제품",
        "insight": (
            "유모차·카시트 등 이동·외출용품은 출산 전후 한 번에 결정되는 고관여 품목입니다. "
            "산모가 정보를 가장 활발히 탐색하는 조리원 체류 시점의 노출이 "
            "{brand}의 구매 결정에 직접 영향을 준다고 판단했습니다."
        ),
        "sampling": "키오스크 집중 노출 / 맘카페 체험단을 통한 실사용 콘텐츠",
        "lms": "출산 예정·신생아 부모 대상 타겟 LMS",
    },
    "임산부 의류": {
        "hook_noun": "제품",
        "insight": (
            "임부복·수유복은 임신 주수에 따라 반복 구매가 일어나는 카테고리입니다. "
            "출산 직후 산모 접점에서의 브랜드 노출이 {brand}의 산후복·수유복 구매로 이어진다고 판단했습니다."
        ),
        "sampling": "맘박스·체험단을 통한 제품 경험",
        "lms": "출산 직후 산모 대상 타겟 LMS",
    },
    "유아 의류": {
        "hook_noun": "제품",
        "insight": (
            "신생아 의류는 출산 직후 '처음 입히는 브랜드'가 "
            "이후 돌·유아동복까지 반복 구매로 이어지는 구조가 강한 시장입니다. "
            "배냇저고리·턱받이처럼 신생아기에 바로 쓰는 품목은 "
            "조리원 체류 기간이 가장 강력한 첫 접점이라 판단했습니다."
        ),
        "sampling": "제휴 조리원 신생아실 사용분 지원 / 맘박스를 통한 소품 샘플링",
        "lms": "생후 1개월 이내 신생아 부모 대상 타겟 LMS",
    },
}

# 카테고리를 못 찾았을 때 쓰는 일반 제품 프로필
DEFAULT_PROFILE = {
    "hook_noun": "제품",
    "insight": (
        "영유아·임산부 카테고리는 출산 직후 형성된 브랜드 인지가 "
        "이후 반복 구매로 이어지는 구조가 강한 시장입니다. "
        "이 초기 접점 설계가 {brand} 성장에 중요한 요소라 판단했습니다."
    ),
    "sampling": "셀픽 운영 맘박스를 통한 제품 체험 샘플링 (월 2,000~5,000개 규모)",
    "lms": "생후 1개월 이내 신생아 부모 대상 타겟 LMS",
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


def _short_flagship(flagship: str, brand: str) -> str:
    """주력상품명이 너무 길면 핵심만. 브랜드명 중복 제거."""
    f = (flagship or "").strip()
    if not f:
        return ""
    # 브랜드명이 상품명 맨 앞에 있으면 그대로 두되, 너무 길면 자름
    if len(f) > 40:
        f = f[:40].rstrip() + "…"
    return f


def _wrap_html(inner: str) -> str:
    return (
        '<div style="font-family:\'Apple SD Gothic Neo\',\'Malgun Gothic\','
        'sans-serif;font-size:14px;line-height:1.75;color:#333;max-width:600px;">'
        + inner + '</div>'
    )


def _build_product(row: dict) -> dict:
    brand = (row.get("brand_name") or "").strip()
    found_cat = (row.get("category") or "").strip()
    prod_cat = (row.get("product_category") or "").strip()
    flagship = _short_flagship(row.get("flagship_product", ""), brand)

    prof = CATEGORY_PROFILE.get(found_cat) or DEFAULT_PROFILE
    insight = prof["insight"].format(brand=_esc(brand))
    hook_noun = prof["hook_noun"]
    sampling = prof["sampling"]
    lms = prof["lms"]

    subject = f"[셀픽이앤에스] {brand} × 산후조리원 신생아 부모 DB 마케팅 제안"

    # ── HTML ──
    steps = (
        ('① 브랜드 인지', '셀픽 키오스크 광고 / 모바일 배너 (아웃링크 가능)'),
        ('② 신뢰 구축', sampling),
        ('③ 전환 마케팅', lms),
        ('④ 바이럴 마케팅', '맘카페 중심 실사용 후기 축적 / 셀픽 회원 기반 체험단 운영'),
    )
    steps_html = ""
    for i, (label, desc) in enumerate(steps):
        mb = "18px" if i == len(steps) - 1 else "12px"
        steps_html += (
            f'<div style="margin:0 0 {mb};">'
            f'<span style="font-weight:700;color:#e8590c;">{label}</span><br>'
            f'<span style="color:#555;">{_esc(desc)}</span></div>'
        )

    flagship_html = ""
    if flagship:
        flagship_html = (
            f'주력상품인 <b>[{_esc(flagship)}]</b> 같은 경우,<br>'
            "출산 직후 '첫 경험'을 설계하는 전략이 특히 중요하다고 판단됩니다.<br><br>"
        )

    inner = (
        f'안녕하세요, <b>{_esc(brand)}</b> 담당자님.<br>'
        '셀픽이앤에스 영업팀 서민지입니다.<br><br>'
        f'{_esc(brand)}의 {hook_noun}{_josa(hook_noun, "을", "를")} 인상 깊게 보고 연락드립니다.<br>'
        f'출산 직후 부모가 브랜드를 <b>처음 만나는 시점</b>에 '
        f'{_esc(brand)}{_josa(brand, "을", "를")} 알릴 수 있는 방법이 있어,<br>'
        '짧게 제안드리고자 메일 드립니다.<br><br>'
        f'{SELPIC_INTRO_HTML}<br><br>'
        f'{insight}<br><br>'
        '<div style="font-size:16px;font-weight:700;color:#1a1a1a;'
        'border-left:4px solid #e8590c;padding-left:12px;margin:6px 0 16px;">'
        f'{_esc(brand)} 맞춤 제안 구조</div>'
        f'{steps_html}'
        f'{flagship_html}'
        '자세한 내용은 회사소개서에 정리해 함께 첨부드립니다.<br>'
        '편하게 살펴보신 뒤, 관심 있으시면 메일로 회신 주시거나 전화 한 통 주셔도 좋습니다.<br>'
        f'필요하시면 미팅으로 {_esc(brand)}에 맞는 운영안을 더 구체적으로 말씀드리겠습니다.<br>'
        '감사합니다.<br><br>'
        f'{SIG_HTML}'
    )

    # ── 평문 ──
    plain_steps = (
        f"① 브랜드 인지 — 셀픽 키오스크 광고 / 모바일 배너 (아웃링크 가능)\n"
        f"② 신뢰 구축 — {sampling}\n"
        f"③ 전환 마케팅 — {lms}\n"
        f"④ 바이럴 마케팅 — 맘카페 중심 실사용 후기 축적 / 셀픽 회원 기반 체험단 운영"
    )
    plain_flagship = ""
    if flagship:
        plain_flagship = (
            f"\n주력상품인 [{flagship}] 같은 경우,\n"
            "출산 직후 '첫 경험'을 설계하는 전략이 특히 중요하다고 판단됩니다.\n"
        )
    plain = (
        f"안녕하세요, {brand} 담당자님.\n"
        "셀픽이앤에스 영업팀 서민지입니다.\n\n"
        f"{brand}의 {hook_noun}{_josa(hook_noun, '을', '를')} 인상 깊게 보고 연락드립니다.\n"
        f"출산 직후 부모가 브랜드를 처음 만나는 시점에 "
        f"{brand}{_josa(brand, '을', '를')} 알릴 수 있는 방법이 있어,\n"
        "짧게 제안드리고자 메일 드립니다.\n\n"
        f"{SELPIC_INTRO_PLAIN}\n\n"
        f"{insight}\n\n"
        f"[ {brand} 맞춤 제안 구조 ]\n\n"
        f"{plain_steps}\n"
        f"{plain_flagship}\n"
        "자세한 내용은 회사소개서에 정리해 함께 첨부드립니다.\n"
        "편하게 살펴보신 뒤, 관심 있으시면 메일로 회신 주시거나 전화 한 통 주셔도 좋습니다.\n"
        f"필요하시면 미팅으로 {brand}에 맞는 운영안을 더 구체적으로 말씀드리겠습니다.\n"
        "감사합니다.\n\n"
        f"{SIG_PLAIN}"
    )

    return {
        "subject": subject,
        "html": _wrap_html(inner),
        "plain": plain,
        "template": "product",
    }


def _build_service(row: dict) -> dict:
    brand = (row.get("brand_name") or "").strip()
    flagship = _short_flagship(row.get("flagship_product", ""), brand)

    subject = f"[셀픽이앤에스] {brand} × 산후조리원 산모 타겟 마케팅 제안"

    steps = (
        ('① 키오스크 광고',
         '산후조리원(230여 곳)·산부인과·베이비스튜디오 키오스크를 활용한 '
         '출산 직후 산모 및 임산부 타겟 집중 노출'),
        ('② 타겟 LMS', '타겟 맞춤 LMS 발송을 통한 노출 및 직접 도달 강화'),
        ('③ 온라인 바이럴', '맘카페 바이럴 운영을 통한 실제 육아맘 대상 체험 기반 콘텐츠 확산'),
    )
    steps_html = ""
    for i, (label, desc) in enumerate(steps):
        mb = "18px" if i == len(steps) - 1 else "12px"
        steps_html += (
            f'<div style="margin:0 0 {mb};">'
            f'<span style="font-weight:700;color:#e8590c;">{label}</span><br>'
            f'<span style="color:#555;">{_esc(desc)}</span></div>'
        )

    price_html = (
        '<div style="background:#fff6f0;border:1px solid #ffd8bf;border-radius:6px;'
        'padding:12px 14px;margin:4px 0 16px;color:#444;">'
        '통상 조리원 광고 + 바이럴 포함 시 <b>월 1,000만원 이상</b> 집행되는 구조입니다.<br>'
        f'다만 {_esc(brand)}의 경우 초기 테스트 부담 없이 검증하실 수 있도록<br>'
        '<b style="color:#e8590c;">월 500만원(3개월, 신규 광고주 대상) 프로모션 패키지</b>로 '
        '제안드립니다.<br>'
        '<span style="font-size:12px;color:#888;">※ 본 메일을 수신하신 분에 한해 적용되는 '
        '견적이며, 성과 확인 후 확대 여부를 판단하실 수 있도록 설계했습니다.</span>'
        '</div>'
    )

    inner = (
        f'안녕하세요, <b>{_esc(brand)}</b> 담당자님.<br>'
        '셀픽이앤에스 영업팀 서민지입니다.<br><br>'
        f'{_esc(brand)} 서비스를 인상 깊게 보고 연락드립니다.<br>'
        '산모가 조리원에서 서비스를 인지한 뒤 <b>퇴소 직후 예약으로 이어지는</b> 흐름을 '
        '함께 설계해보고 싶어 제안드립니다.<br><br>'
        f'{SELPIC_INTRO_HTML}<br><br>'
        f'조리원 체류 중 인지부터 퇴소 후 예약 전환까지 연결되는 구조로 {_esc(brand)}에 맞춰 제안드립니다.<br><br>'
        '<div style="font-size:16px;font-weight:700;color:#1a1a1a;'
        'border-left:4px solid #e8590c;padding-left:12px;margin:6px 0 16px;">'
        f'{_esc(brand)} 맞춤 제안 구조</div>'
        f'{steps_html}'
        f'{price_html}'
        '①광고견적서 ②셀픽소개서 파일을 함께 첨부드립니다.<br>'
        '검토 후 궁금하신 부분은 메일로 회신 주시거나 전화 한 통 주셔도 좋습니다.<br>'
        f'필요하시면 미팅으로 {_esc(brand)}에 맞는 운영안을 더 구체적으로 말씀드리겠습니다.<br>'
        '감사합니다.<br><br>'
        f'{SIG_HTML}'
    )

    plain = (
        f"안녕하세요, {brand} 담당자님.\n"
        "셀픽이앤에스 영업팀 서민지입니다.\n\n"
        f"{brand} 서비스를 인상 깊게 보고 연락드립니다.\n"
        "산모가 조리원에서 서비스를 인지한 뒤 퇴소 직후 예약으로 이어지는 흐름을\n"
        "함께 설계해보고 싶어 제안드립니다.\n\n"
        f"{SELPIC_INTRO_PLAIN}\n\n"
        f"조리원 체류 중 인지부터 퇴소 후 예약 전환까지 연결되는 구조로 {brand}에 맞춰 제안드립니다.\n\n"
        f"[ {brand} 맞춤 제안 구조 ]\n\n"
        "① 키오스크 광고 — 산후조리원(230여 곳)·산부인과·베이비스튜디오 키오스크를 활용한 출산 직후 산모 및 임산부 타겟 집중 노출\n"
        "② 타겟 LMS — 타겟 맞춤 LMS 발송을 통한 노출 및 직접 도달 강화\n"
        "③ 온라인 바이럴 — 맘카페 바이럴 운영을 통한 실제 육아맘 대상 체험 기반 콘텐츠 확산\n\n"
        "통상 조리원 광고 + 바이럴 포함 시 월 1,000만원 이상 집행되는 구조입니다.\n"
        f"다만 {brand}의 경우 초기 테스트 부담 없이 검증하실 수 있도록\n"
        "월 500만원(3개월, 신규 광고주 대상) 프로모션 패키지로 제안드립니다.\n"
        "※ 본 메일을 수신하신 분에 한해 적용되는 견적이며, 성과 확인 후 확대 여부를 판단하실 수 있도록 설계했습니다.\n\n"
        "①광고견적서 ②셀픽소개서 파일을 함께 첨부드립니다.\n"
        "검토 후 궁금하신 부분은 메일로 회신 주시거나 전화 한 통 주셔도 좋습니다.\n"
        f"필요하시면 미팅으로 {brand}에 맞는 운영안을 더 구체적으로 말씀드리겠습니다.\n"
        "감사합니다.\n\n"
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
