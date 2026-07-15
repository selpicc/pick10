# -*- coding: utf-8 -*-
"""브랜드별 셀픽 Media Proposal(분석 리포트) PPTX 생성기
────────────────────────────────────────────────────────────────
대시보드 셀러 디테일의 '📊 분석 리포트 만들기' 버튼이 이 모듈을 호출한다.
자동화(스케줄러)가 아니라 사람이 버튼을 누른 그 브랜드에 대해서만 1건 생성한다.

만드는 것: 표지 없이 3페이지짜리 심플한 편집 가능 PPTX (폰트 Pretendard).
  1) 마케팅 전략 = 획기적 슬로건 + 브랜드 알맹이 + 핵심 타겟
  2) 니즈 · 광고 제안 = 소구/바이럴 키워드 + 셀픽 광고 상품 5종
  3) 셀픽 마케팅 방향 = 오프라인·온라인 + 셀픽 도달 규모
  ※ 사용자가 준 예시 PDF는 '내용/분석' 참고용이며 레이아웃은 새로 잡는다.

핵심 원칙 — 지어내지 않기 (brand_intro.py와 동일 철학):
  · 셀픽 고정 수치(조리원 230곳·산부인과 55곳 등)는 소개서 값 그대로 '템플릿'.
  · 브랜드/상품 부분(사용 루틴·타겟·키워드)은 그 브랜드 실제 제품정보 기반 AI 생성.
    검증 불가 주장(1위·인증·특허·기관명 등)이 섞이면 자동 폐기.
  · 창의적 마케팅 아이디어는 슬라이드에 '제안'으로 명확히 표기.
  · AI가 실패/빈약해도 절대 멈추지 않는다 → 카테고리 기반 템플릿으로 완성본 생성.

사용:
    from report_generator import make_report
    pptx_bytes, filename = make_report(row)   # row = sellers 테이블 한 행(dict)
"""
import io
import os
import re
import json

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

# Gemini 호출·홈페이지 읽기는 brand_intro의 것을 재사용 (중복 방지, 같은 안전 철학)
from brand_intro import _fetch_text, API_KEY, MODEL, API_URL
import requests


# ─────────────────────────────────────────────────────────────
# 셀픽 고정 사실 (MEDIA PROPOSAL 2026 — 지어내지 말 것. 이 값만 사용)
# ─────────────────────────────────────────────────────────────
SELPIC_FACTS = {
    "산모DB": "연간 10만명 신생아·부모 DB 확보 (월 8,000~10,000명 신규가입)",
    "누적회원": "최근 3년 누적 회원(0~3세) 41만명 · 누적 다운로드 100만+",
    "설치점": "전국 460여 대 키오스크 설치 (조리원·산부인과·마트·병원 등)",
    "제휴": "제휴 산후조리원·산부인과 전국 285곳",
    "조리원": "산후조리원 230곳 우선 설치 (시장점유 약 50%)",
    "산부인과": "분만 수 상위 산부인과 55곳 설치",
    "체류": "조리원 체류 14일 매일 접촉 · 산부인과 임신~출산 반복 노출",
    "무료인화": "임산부 1인당 무료 사진 15장 인화 (초음파~아기 사진)",
    "키오스크": "인화 대기 중 100% 노출 · 최소 일 10만 회 노출",
}

# 셀픽 영유아 시장 타겟 특성 (모든 브랜드 공통 — 소개서 기반, 고정)
TARGET_MARKET_LINES = [
    "영유아 시장 = 20대 후반~40대 초반의 여성",
    "엄마가 된다는 것은 삶의 안정기에 접어든 여성 — 좋고 안전한 제품에 아낌이 없다",
    "맘카페 빅마우스이자, 조리원 동기·산후조리원 원장의 추천에 크게 의지한다",
    "본인 경험을 맘카페로 나눠 후배 맘에게 전파 → 그 자체가 바이럴이자 새 시장 창출",
    "온라인에서 탐색·구매하지만, 오프라인의 전문가 사용·실사용 확인이 신뢰의 바탕",
    "3년이면 소비자가 교체되는 짧은 주기 → 지속 마케팅 + 빠른 신규 시장 형성",
]

# 타겟 세그먼트 후보 (예시 덱과 동일 풀)
SEGMENT_POOL = ["산모", "아기", "가족", "청소년", "아토피 환우",
                "새로운 뷰티 트렌드", "청결 강박", "미세먼지"]

# 셀픽 미디어 상품 5종 (2026 가이드 — 고정, 지어내지 말 것)
SELPIC_MEDIA = [
    ("키오스크 DA", "인화 대기 화면 100% 노출 사이니지 광고"),
    ("모바일 DA", "셀픽 앱·모바일 최적 배너 (자체 광고엔진)"),
    ("조리원 MRO·샘플링", "신생아실 사용 물품 결합 오프라인 홍보"),
    ("맘카페 체험형 바이럴", "실제 아기엄마 후기 기반 심화 바이럴"),
    ("타겟 DB LMS", "동의 기반 산모 DB 다이렉트 메시지"),
]

# 검증 불가 주장 — 리포트 문구에 하나라도 있으면 그 항목 폐기
_BANNED_CLAIM = (
    "1위", "일위", "최고", "최초", "유일", "독보", "특허", "인증", "공인",
    "수상", "fda", "식약처", "임상", "의약품", "대웅", "존슨", "공식지정",
    "검증된", "보장", "100%", "무조건 안전",
)


# ─────────────────────────────────────────────────────────────
# 1) 제품 컨텍스트 수집
# ─────────────────────────────────────────────────────────────
def _best_url(row: dict) -> str:
    """홈페이지 텍스트를 읽을 최적 URL. 공식몰 소스가 있으면 우선."""
    src = (row.get("auto_biz_sources") or "")
    m = re.search(r"https?://[^\s,]+", src)
    if m:
        return m.group(0)
    return (row.get("smartstore_url") or "").strip()


def _product_context(row: dict) -> dict:
    brand = (row.get("brand_name") or "").strip()
    category = (row.get("category") or row.get("product_category") or "").strip()
    flagship = (row.get("flagship_product") or "").strip()
    url = _best_url(row)
    site_text = _fetch_text(url) if url else ""
    return {
        "brand": brand,
        "category": category,
        "flagship": flagship,
        "site_text": site_text,
    }


# ─────────────────────────────────────────────────────────────
# 2) AI 콘텐츠 생성 (JSON) — 실패해도 {} 반환 (호출부가 템플릿 폴백)
# ─────────────────────────────────────────────────────────────
def _clean_line(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("*", "").replace("#", "").strip()).strip('"').strip("'")


def _safe_item(s: str, max_len: int = 60) -> str:
    """검증 불가 주장이 섞였으면 버린다(빈 문자열)."""
    s = _clean_line(s)
    if not (2 <= len(s) <= max_len):
        return ""
    low = s.lower()
    if any(b in low for b in _BANNED_CLAIM):
        return ""
    return s


def _clean_list(items, max_len=60, cap=10) -> list:
    out = []
    for it in (items or []):
        v = _safe_item(str(it), max_len)
        if v and v not in out:
            out.append(v)
        if len(out) >= cap:
            break
    return out


def _gemini_report_json(ctx: dict) -> dict:
    if not API_KEY:
        return {}
    if not ctx["site_text"] and not ctx["flagship"]:
        return {}

    prompt = f"""너는 영유아·임산부 시장 전문 마케팅 전략가다.
아래 '{ctx['brand']}' 브랜드의 실제 제품 정보를 바탕으로, B2B 미디어 제안서에 들어갈
브랜드 맞춤 콘텐츠를 만든다.

--- 홈페이지 텍스트 ---
{ctx['site_text'] or "(없음)"}
--- 주력상품 ---
{ctx['flagship'] or "(없음)"}
--- 카테고리 ---
{ctx['category'] or "(없음)"}

아래 JSON 형식으로만 답하라(설명·코드펜스 금지):
{{
  "slogan": "이 브랜드를 한 방에 각인시키는 획기적이고 감각적인 슬로건 한 줄 (12~24자, 카피라이터처럼)",
  "product_summary": "이 제품이 무엇이고 어떤 가치를 주는지 1문장 (홈페이지 근거)",
  "routines": [{{"title": "사용 상황 제목", "detail": "구체적 사용법 한 줄"}}],
  "targets": ["이 제품에 해당하는 타겟 세그먼트(아래 목록에서만 골라라)"],
  "usage_hashtags": ["사용 경험 해시태그 (# 없이 단어로)"],
  "appeal_points": ["소구포인트/별칭/바이럴 키워드 후보 (# 없이)"],
  "offline_ideas": ["산부인과/산후조리원/베이비스튜디오에서의 이 브랜드 활용 아이디어 한 줄"],
  "online_ideas": ["맘카페에서 퍼질 만한 이 브랜드 활용법/콘텐츠 아이디어 한 줄"],
  "lineup_ideas": [{{"axis": "용량|타입|기능", "idea": "향후 제품 확장 제안 한 줄"}}]
}}

타겟 세그먼트는 반드시 이 목록에서만 고른다: {", ".join(SEGMENT_POOL)}

반드시 지킬 것:
- 홈페이지·주력상품에 실제로 드러난 사실만 근거로 삼아라. 없는 사실을 지어내지 마라.
- 순위·수상·인증·특허·FDA·제조사명·'1위'·'최고'·구체적 수치 같은 검증 불가 주장은
  절대 넣지 마라. (이런 항목은 자동 폐기된다)
- slogan은 짧고 강렬하게 한 줄. routines 3개, usage_hashtags 6개, appeal_points 5개,
  offline_ideas 3개, online_ideas 3개, lineup_ideas 4개.
- 각 항목은 짧고 구체적으로. 존댓말체 아니어도 됨(키워드/제목형).
- 3페이지짜리 압축 제안서라, 군더더기 없이 '알맹이'만 뽑아라."""

    try:
        r = requests.post(
            API_URL.format(m=MODEL),
            headers={"x-goog-api-key": API_KEY, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.6, "maxOutputTokens": 4096},
            },
            timeout=60,
        )
        if r.status_code != 200:
            return {}
        parts = r.json()["candidates"][0]["content"]["parts"]
        raw = "".join(p.get("text", "") for p in parts).strip()
    except Exception:
        return {}

    # 코드펜스·앞뒤 잡텍스트 제거 후 최외곽 { } 파싱
    raw = re.sub(r"```(?:json)?", "", raw)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


# ─────────────────────────────────────────────────────────────
# 3) 콘텐츠 조립 (AI + 안전필터 + 템플릿 폴백)
# ─────────────────────────────────────────────────────────────
def _fallback(ctx: dict) -> dict:
    """AI가 비었을 때 쓰는 카테고리 기반 안전 템플릿 (지어낸 주장 없음)."""
    cat = ctx["category"] or "영유아 제품"
    return {
        "slogan": f"{ctx['brand']}, 엄마의 첫 순간에 함께합니다",
        "product_summary": f"{ctx['brand']}는 영유아·임산부 타깃의 {cat} 브랜드입니다.",
        "routines": [
            {"title": "출산 준비", "detail": "출산가방·신생아 준비물에 함께 챙기는 제품"},
            {"title": "매일의 육아 루틴", "detail": "아기 케어 단계에서 반복적으로 사용"},
            {"title": "온 가족 사용", "detail": "산모·아기·가족이 함께 쓰는 생활 필수템"},
        ],
        "targets": ["산모", "아기", "가족"],
        "usage_hashtags": ["출산준비물", "신생아 필수템", "육아템", "맘카페 추천", "출산가방 리스트"],
        "appeal_points": ["엄마가 고른 브랜드", "믿고 쓰는 데일리템", "선물하기 좋은"],
        "offline_ideas": [
            "산후조리원 신생아실·퇴소 교육 시 실사용·권유",
            "산부인과 대기·상담 공간에서 제품 경험 노출",
            "베이비 스튜디오 방문 고객에게 제품 각인",
        ],
        "online_ideas": [
            "맘스홀릭 등 맘카페 활용법 콘텐츠 바이럴",
            "출산가방 리스트 공유글 필수템 등극",
            "육아 선배맘 후기 기반 추천 확산",
        ],
        "lineup_ideas": [
            {"axis": "용량", "idea": "샘플링·입문용 소용량 + B2B 대용량"},
            {"axis": "타입", "idea": "휴대·여행용 등 사용 상황별 타입 확장"},
            {"axis": "기능", "idea": "인접 니즈를 묶은 라인 확장 검토"},
        ],
    }


def build_content(row: dict) -> dict:
    """브랜드 한 행 → 슬라이드에 넣을 콘텐츠 dict (항상 완성형 반환)."""
    ctx = _product_context(row)
    ai = _gemini_report_json(ctx)
    fb = _fallback(ctx)

    # slogan — 슬로건은 카피라 창의 허용, 단 검증 불가 주장·과한 길이는 폐기 후 폴백
    slogan = _clean_line(ai.get("slogan", "")) if ai else ""
    if not slogan or len(slogan) > 34 or any(b in slogan.lower() for b in _BANNED_CLAIM):
        slogan = fb["slogan"]

    # product_summary (안전 필터 후 폴백)
    summary = _clean_line(ai.get("product_summary", "")) if ai else ""
    if not summary or any(b in summary.lower() for b in _BANNED_CLAIM):
        summary = fb["product_summary"]

    # routines
    routines = []
    for r in (ai.get("routines") or []):
        if isinstance(r, dict):
            t = _safe_item(r.get("title", ""), 24)
            d = _safe_item(r.get("detail", ""), 70)
            if t and d:
                routines.append({"title": t, "detail": d})
    if len(routines) < 3:
        routines = fb["routines"]
    routines = routines[:6]

    # targets — 풀 안에 있는 것만
    targets = [t for t in (ai.get("targets") or []) if t in SEGMENT_POOL]
    if not targets:
        targets = fb["targets"]
    # 순서를 예시 덱 순서로 정렬
    targets = [s for s in SEGMENT_POOL if s in targets][:8]

    usage = _clean_list(ai.get("usage_hashtags"), 24, 10) or fb["usage_hashtags"]
    appeal = _clean_list(ai.get("appeal_points"), 24, 8) or fb["appeal_points"]
    offline = _clean_list(ai.get("offline_ideas"), 80, 4) or fb["offline_ideas"]
    online = _clean_list(ai.get("online_ideas"), 80, 5) or fb["online_ideas"]

    # lineup
    lineup = []
    for r in (ai.get("lineup_ideas") or []):
        if isinstance(r, dict):
            ax = _clean_line(r.get("axis", ""))[:6]
            idea = _safe_item(r.get("idea", ""), 80)
            if ax and idea:
                lineup.append({"axis": ax, "idea": idea})
    if len(lineup) < 3:
        lineup = fb["lineup_ideas"]
    lineup = lineup[:6]

    return {
        "brand": ctx["brand"],
        "category": ctx["category"],
        "slogan": slogan,
        "product_summary": summary,
        "routines": routines,
        "targets": targets,
        "usage_hashtags": usage,
        "appeal_points": appeal,
        "offline_ideas": offline,
        "online_ideas": online,
        "lineup_ideas": lineup,
        "ai_used": bool(ai),
    }


# ─────────────────────────────────────────────────────────────
# 4) PPTX 빌드 — 표지 없이 3페이지, 심플, Pretendard
#    (준 예시 PDF는 '내용/분석' 참고용일 뿐 레이아웃은 새로 잡는다)
# ─────────────────────────────────────────────────────────────
ORANGE = RGBColor(0xF5, 0x82, 0x1F)
ORANGE_D = RGBColor(0xD9, 0x66, 0x0A)
PEACH = RGBColor(0xF3, 0xA9, 0x7A)
PEACH_L = RGBColor(0xFD, 0xF1, 0xE8)
DARK = RGBColor(0x22, 0x22, 0x22)
GRAY = RGBColor(0x66, 0x66, 0x66)
MUTED = RGBColor(0x9A, 0x9A, 0x9A)
LINE = RGBColor(0xE6, 0xE6, 0xE6)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
FONT = "Pretendard"          # 사용자 요청 폰트 (뷰어에 Pretendard 설치 필요)

W = Inches(13.333)
H = Inches(7.5)


def _no_line(shape):
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _rect(slide, l, t, w, h, fill, shape=MSO_SHAPE.RECTANGLE):
    sp = slide.shapes.add_shape(shape, l, t, w, h)
    sp.fill.solid()
    sp.fill.fore_color.rgb = fill
    return _no_line(sp)


def _run(p, text, size, bold=False, color=DARK, font=FONT):
    r = p.add_run()
    r.text = text
    f = r.font
    f.size = Pt(size)
    f.bold = bold
    f.color.rgb = color
    f.name = font
    return r


def _textbox(slide, l, t, w, h, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(l, t, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    return tf


def _para(tf, text, size=13, bold=False, color=DARK, align=PP_ALIGN.LEFT,
          first=False, space_after=4, space_before=0, line_spacing=None):
    p = tf.paragraphs[0] if first else tf.add_paragraph()
    p.alignment = align
    p.space_after = Pt(space_after)
    p.space_before = Pt(space_before)
    if line_spacing:
        p.line_spacing = line_spacing
    if text:
        _run(p, text, size, bold, color)
    return p


def _blank(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def _eyebrow(slide, text):
    """페이지 상단 얇은 라벨 + 짧은 오렌지 악센트 바."""
    _rect(slide, Inches(0.72), Inches(0.52), Inches(0.34), Inches(0.055), ORANGE)
    tf = _textbox(slide, Inches(0.72), Inches(0.62), Inches(11.9), Inches(0.32))
    _para(tf, text, 11.5, True, MUTED, first=True)


def _title(slide, text, size=27):
    tf = _textbox(slide, Inches(0.7), Inches(0.92), Inches(11.9), Inches(0.75))
    _para(tf, text, size, True, DARK, first=True)


def _rule(slide, t, l=Inches(0.72), w=Inches(11.9)):
    _rect(slide, l, t, w, Pt(1.0), LINE)


def _note(slide, text, top=Inches(6.95)):
    tf = _textbox(slide, Inches(0.72), top, Inches(11.9), Inches(0.35))
    _para(tf, text, 9.5, False, MUTED, first=True)


def _chip(slide, l, t, text, fill=PEACH, txt=WHITE, size=12):
    w = Inches(0.42 + 0.155 * len(text))
    sp = _rect(slide, l, t, w, Inches(0.44), fill, MSO_SHAPE.ROUNDED_RECTANGLE)
    tfr = sp.text_frame
    tfr.vertical_anchor = MSO_ANCHOR.MIDDLE
    pp = tfr.paragraphs[0]
    pp.alignment = PP_ALIGN.CENTER
    _run(pp, text, size, True, txt)
    return l + w + Inches(0.14)     # 다음 칩 x


# ── 페이지 1: 마케팅 전략 = 슬로건 + 브랜드 알맹이 + 핵심 타겟 ──
def _page_strategy(prs, c):
    s = _blank(prs)
    _eyebrow(s, f"MEDIA PROPOSAL      셀픽 × {c['brand']}")
    # 히어로 슬로건 (알맹이의 핵심)
    slogan = c["slogan"]
    sz = 40 if len(slogan) <= 16 else (34 if len(slogan) <= 24 else 28)
    tf = _textbox(s, Inches(0.7), Inches(1.15), Inches(11.9), Inches(1.9))
    _para(tf, slogan, sz, True, DARK, first=True, line_spacing=1.05)
    # 브랜드 한 줄 정의
    tf2 = _textbox(s, Inches(0.72), Inches(3.0), Inches(11.9), Inches(0.9))
    _para(tf2, c["product_summary"], 15, False, GRAY, first=True, line_spacing=1.2)
    _rule(s, Inches(3.95))
    # 핵심 타겟 칩
    tf3 = _textbox(s, Inches(0.72), Inches(4.2), Inches(4.0), Inches(0.3))
    _para(tf3, "핵심 타겟", 12, True, ORANGE_D, first=True)
    x = Inches(0.72)
    for seg in c["targets"][:6]:
        x = _chip(s, x, Inches(4.55), seg)
    # 마케팅 전략 = 사용 알맹이 3줄
    tf4 = _textbox(s, Inches(0.72), Inches(5.35), Inches(11.9), Inches(0.3))
    _para(tf4, "마케팅 전략", 12, True, ORANGE_D, first=True)
    tf5 = _textbox(s, Inches(0.72), Inches(5.72), Inches(11.9), Inches(1.2))
    for i, r in enumerate(c["routines"][:3]):
        _para(tf5, f"{r['title']}  —  {r['detail']}", 12.5, False, DARK,
              first=(i == 0), space_after=5)


# ── 페이지 2: 니즈 · 광고 제안 (2단) ──
def _page_proposal(prs, c):
    s = _blank(prs)
    _eyebrow(s, "NEEDS  &  PROPOSAL")
    _title(s, "니즈에서 출발한 광고 제안")
    _rule(s, Inches(1.72))
    # 좌: 브랜드 소구 · 바이럴 키워드
    tfL = _textbox(s, Inches(0.72), Inches(2.0), Inches(6.0), Inches(4.6))
    _para(tfL, "브랜드 소구 키워드", 13, True, ORANGE_D, first=True, space_after=6)
    _para(tfL, "  ".join(f"#{a}" for a in c["appeal_points"][:5]),
          15, True, DARK, space_after=16, line_spacing=1.3)
    _para(tfL, "사용 경험 키워드", 13, True, ORANGE_D, space_after=6)
    _para(tfL, "  ".join(f"#{h}" for h in c["usage_hashtags"][:6]),
          13, False, GRAY, line_spacing=1.3)
    # 우: 셀픽 광고 상품 제안 (고정 5종)
    tfR = _textbox(s, Inches(7.1), Inches(2.0), Inches(5.5), Inches(4.6))
    _para(tfR, "셀픽 광고 상품 제안", 13, True, ORANGE_D, first=True, space_after=8)
    for name, desc in SELPIC_MEDIA:
        _para(tfR, f"● {name}", 13, True, DARK, space_after=1, space_before=3)
        _para(tfR, f"    {desc}", 11.5, False, GRAY, space_after=2)
    _note(s, "※ 키워드·상품 구성은 셀픽이 제안하는 마케팅 아이디어입니다 (집행 전 브랜드와 협의).")


# ── 페이지 3: 셀픽 마케팅 방향 ──
def _page_direction(prs, c):
    s = _blank(prs)
    _eyebrow(s, "DIRECTION")
    _title(s, "셀픽 마케팅 방향")
    _rule(s, Inches(1.72))
    # 오프라인
    tf = _textbox(s, Inches(0.72), Inches(2.0), Inches(11.9), Inches(1.9))
    _para(tf, "오프라인 — 산부인과·조리원·베이비 스튜디오에서 전문가 사용·추천",
          13.5, True, ORANGE_D, first=True, space_after=6)
    for idea in c["offline_ideas"][:3]:
        _para(tf, f"● {idea}", 12.5, False, DARK, space_after=4)
    # 온라인
    tf2 = _textbox(s, Inches(0.72), Inches(4.05), Inches(11.9), Inches(1.7))
    _para(tf2, "온라인 — 맘카페 체험형 바이럴", 13.5, True, ORANGE_D,
          first=True, space_after=6)
    for idea in c["online_ideas"][:3]:
        _para(tf2, f"● {idea}", 12.5, False, DARK, space_after=4)
    # 셀픽 도달 규모 (고정 수치 스트립)
    bar = _rect(s, Inches(0.72), Inches(5.95), Inches(11.9), Inches(0.72), PEACH_L,
                MSO_SHAPE.ROUNDED_RECTANGLE)
    btf = bar.text_frame
    btf.vertical_anchor = MSO_ANCHOR.MIDDLE
    btf.word_wrap = True
    bp = btf.paragraphs[0]
    bp.alignment = PP_ALIGN.CENTER
    _run(bp, "연 10만 신생아·부모 DB   ·   전국 460대 키오스크   ·   "
             "조리원 230곳(50% M/S)   ·   조리원 체류 14일 매일 접촉",
         12, True, ORANGE_D)
    _note(s, "※ 실제 집행 채널·물량은 브랜드와 협의 후 확정됩니다.")


def build_pptx(content: dict) -> bytes:
    prs = Presentation()
    prs.slide_width = W
    prs.slide_height = H
    _page_strategy(prs, content)
    _page_proposal(prs, content)
    _page_direction(prs, content)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────
# 5) 오케스트레이션
# ─────────────────────────────────────────────────────────────
def _safe_filename(brand: str) -> str:
    base = re.sub(r'[\\/:*?"<>|]', "", (brand or "브랜드")).strip() or "브랜드"
    return f"{base}_셀픽MediaProposal.pptx"


def make_report(row: dict):
    """브랜드 행(dict) → (pptx_bytes, filename). AI 실패해도 템플릿으로 완성."""
    content = build_content(row)
    data = build_pptx(content)
    return data, _safe_filename(content["brand"])
