# -*- coding: utf-8 -*-
"""브랜드별 셀픽 Media Proposal(분석 리포트) PPTX 생성기
────────────────────────────────────────────────────────────────
대시보드 셀러 디테일의 '📊 분석 리포트 만들기' 버튼이 이 모듈을 호출한다.
자동화(스케줄러)가 아니라 사람이 버튼을 누른 그 브랜드에 대해서만 1건 생성한다.

만드는 것: 예시(셀픽_MEDIA PROPOSAL_2026)와 같은 7장짜리 편집 가능한 PPTX.
  1) 표지  2) 마케팅 전략(사용 루틴+타겟)  3) 니즈→사용법/바이럴 키워드
  4) 타겟 특징(셀픽 영유아 시장)  5) 셀픽 마케팅 방향(오프+온)
  6) Appendix 참고 레퍼런스  7) 향후 라인업 제안

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
    "조리원": "제휴 산후조리원 230곳 (시장점유 약 50%)",
    "산부인과": "제휴 산부인과 55곳",
    "체류": "산모 평균 체류 14일 — 매일 접촉하는 밀착 채널",
    "무료인화": "산모 1인당 무료 사진 15장 인화 (자연 노출)",
    "키오스크": "매장 키오스크 월 300만 노출 보장",
    "산모DB": "매월 신규 산모 DB 5,100건 유입",
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
- routines 4~6개, usage_hashtags 6~10개, appeal_points 5~8개, offline_ideas 3개,
  online_ideas 4~5개, lineup_ideas 4~6개.
- 각 항목은 짧고 구체적으로. 존댓말체 아니어도 됨(키워드/제목형)."""

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
# 4) PPTX 빌드
# ─────────────────────────────────────────────────────────────
ORANGE = RGBColor(0xF5, 0x82, 0x1F)
ORANGE_D = RGBColor(0xD9, 0x66, 0x0A)
PEACH = RGBColor(0xF3, 0xA9, 0x7A)
PEACH_L = RGBColor(0xFB, 0xE7, 0xDA)
DARK = RGBColor(0x33, 0x33, 0x33)
GRAY = RGBColor(0x60, 0x60, 0x60)
LGRAY = RGBColor(0xEF, 0xEF, 0xEF)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
FONT = "맑은 고딕"

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
          first=False, space_after=4, space_before=0):
    p = tf.paragraphs[0] if first else tf.add_paragraph()
    p.alignment = align
    p.space_after = Pt(space_after)
    p.space_before = Pt(space_before)
    if text:
        _run(p, text, size, bold, color)
    return p


def _header(slide, title, subtitle=""):
    """콘텐츠 슬라이드 상단 헤더 — 좌측 오렌지 바 + 제목 + 우측 태그라인."""
    _rect(slide, Inches(0.45), Inches(0.5), Inches(0.09), Inches(0.62), ORANGE,
          MSO_SHAPE.ROUNDED_RECTANGLE)
    tf = _textbox(slide, Inches(0.7), Inches(0.4), Inches(9.5), Inches(1.0))
    _para(tf, title, 24, True, ORANGE, first=True, space_after=2)
    if subtitle:
        _para(tf, subtitle, 14, False, GRAY)
    # 우측 상단 태그라인
    tf2 = _textbox(slide, Inches(9.8), Inches(0.42), Inches(3.2), Inches(0.4))
    _para(tf2, "당신이 사진을 찍는 이유, 셀픽", 10, True, GRAY,
          align=PP_ALIGN.RIGHT, first=True)


def _seg_box(slide, l, t, w, h, label):
    sp = _rect(slide, l, t, w, h, PEACH, MSO_SHAPE.ROUNDED_RECTANGLE)
    tf = sp.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    _run(p, label, 12, True, WHITE)


def _blank(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def _slide_cover(prs, brand):
    s = _blank(prs)
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = ORANGE
    # 좌상단 라벨
    tf = _textbox(s, Inches(0.9), Inches(2.0), Inches(8.5), Inches(0.6))
    _para(tf, "SELPIC  ·  Mom No.1 Platform", 16, True, WHITE, first=True)
    # 메인 타이틀
    tf2 = _textbox(s, Inches(0.9), Inches(2.7), Inches(9.5), Inches(2.2))
    _para(tf2, "Mom No.1 Platform 셀픽", 40, True, WHITE, first=True, space_after=6)
    _para(tf2, f"Media Proposal for  {brand}", 30, True, WHITE)
    # 하단 발신 pill
    pill = _rect(s, Inches(0.9), Inches(5.3), Inches(2.5), Inches(0.55), ORANGE_D,
                 MSO_SHAPE.ROUNDED_RECTANGLE)
    ptf = pill.text_frame
    ptf.vertical_anchor = MSO_ANCHOR.MIDDLE
    pp = ptf.paragraphs[0]
    pp.alignment = PP_ALIGN.CENTER
    _run(pp, "주식회사 에스오씨", 14, True, WHITE)
    # 우측 대형 'S' 워터마크
    tf3 = _textbox(s, Inches(9.3), Inches(2.4), Inches(3.6), Inches(3.6),
                   anchor=MSO_ANCHOR.MIDDLE)
    _para(tf3, "S", 240, True, WHITE, align=PP_ALIGN.CENTER, first=True)


def _note(slide, text, top):
    tf = _textbox(slide, Inches(0.7), top, Inches(12.0), Inches(0.35))
    _para(tf, text, 10, False, GRAY, first=True)


def _slide_strategy(prs, c):
    s = _blank(prs)
    _header(s, "마케팅 전략",
            "필요할 때만이 아닌, 일상에 늘 함께하는 생활 필수템으로")
    # 좌: 제품 요약 + 사용 루틴
    tf = _textbox(s, Inches(0.7), Inches(1.7), Inches(7.4), Inches(4.6))
    _para(tf, c["product_summary"], 14, True, ORANGE_D, first=True, space_after=10)
    for r in c["routines"]:
        _para(tf, f"● {r['title']}", 13, True, DARK, space_after=1, space_before=4)
        _para(tf, f"   └ {r['detail']}", 12, False, GRAY, space_after=2)
    # 우: 타겟 세그먼트 박스 그리드 (2행 x 최대 4열)
    segs = c["targets"][:8]
    tf_t = _textbox(s, Inches(8.4), Inches(1.6), Inches(4.4), Inches(0.4))
    _para(tf_t, "타겟 세그먼트", 13, True, ORANGE_D, first=True)
    bx, by = Inches(8.4), Inches(2.05)
    bw, bh = Inches(1.02), Inches(0.95)
    gap = Inches(0.12)
    for i, seg in enumerate(segs):
        col, rowi = i % 4, i // 4
        l = Emu_add(bx, (bw, gap), col)
        t = Emu_add(by, (bh, gap), rowi)
        _seg_box(s, l, t, bw, bh, seg)
    # 하단 오렌지 배너
    banner = _rect(s, Inches(0.7), Inches(6.55), Inches(12.0), Inches(0.6), ORANGE)
    btf = banner.text_frame
    btf.vertical_anchor = MSO_ANCHOR.MIDDLE
    bp = btf.paragraphs[0]
    bp.alignment = PP_ALIGN.CENTER
    _run(bp, "매 순간 함께하는 루틴이 곧 브랜드 충성 — 셀픽이 그 접점을 만듭니다", 13, True, WHITE)


def _slide_needs(prs, c):
    s = _blank(prs)
    _header(s, "니즈 → 사용법 → 경험 콘텐츠",
            "타겟 니즈에 맞는 사용법 제시 → 경험 콘텐츠 생성 → 바이럴")
    # 좌상: 사용 경험 해시태그
    tf = _textbox(s, Inches(0.7), Inches(1.7), Inches(8.0), Inches(2.0))
    _para(tf, "니즈에 따른 타겟 → 사용법 제시", 14, True, ORANGE_D, first=True, space_after=6)
    _para(tf, "  ".join(f"#{h}" for h in c["usage_hashtags"][:5]), 13, False, DARK, space_after=3)
    _para(tf, "  ".join(f"#{h}" for h in c["usage_hashtags"][5:10]), 13, False, DARK)
    # 좌하: 경험 콘텐츠 방향
    tf2 = _textbox(s, Inches(0.7), Inches(3.9), Inches(8.0), Inches(2.4))
    _para(tf2, "사용법에 따른 니즈 → 타겟의 경험 콘텐츠 생성", 14, True, ORANGE_D,
          first=True, space_after=6)
    for idea in c["online_ideas"][:4]:
        _para(tf2, f"● {idea}", 12, False, DARK, space_after=3)
    # 우: 소구포인트/별칭/바이럴 키워드 박스
    box = _rect(s, Inches(9.0), Inches(1.7), Inches(3.8), Inches(4.6), LGRAY,
                MSO_SHAPE.ROUNDED_RECTANGLE)
    btf = box.text_frame
    btf.word_wrap = True
    btf.margin_left = Inches(0.25)
    btf.margin_top = Inches(0.25)
    _para(btf, "소구포인트 / 별칭 / 바이럴 키워드", 13, True, DARK, first=True,
          align=PP_ALIGN.CENTER, space_after=10)
    for a in c["appeal_points"][:8]:
        _para(btf, f"#{a}", 13, True, ORANGE_D, align=PP_ALIGN.CENTER, space_after=5)
    _note(s, "※ 위 키워드·콘텐츠는 셀픽이 제안하는 마케팅 아이디어입니다 (집행 전 브랜드와 협의).",
          Inches(6.7))


def _slide_target(prs, c):
    s = _blank(prs)
    _header(s, "1단계 · 셀픽 영유아 설치점 타겟 광고 제안",
            "정밀 타겟팅 고객 대상 오프라인 제품경험 + 경험자 DB 수집 → 판매 연결")
    tf = _textbox(s, Inches(0.7), Inches(1.75), Inches(7.6), Inches(4.8))
    _para(tf, "타겟 특징", 14, True, ORANGE_D, first=True, space_after=6)
    for line in TARGET_MARKET_LINES:
        _para(tf, f"· {line}", 12.5, False, DARK, space_after=5)
    # 우: 셀픽 도달 규모 박스 (고정 수치)
    box = _rect(s, Inches(8.7), Inches(1.75), Inches(4.1), Inches(4.4), PEACH_L,
                MSO_SHAPE.ROUNDED_RECTANGLE)
    btf = box.text_frame
    btf.word_wrap = True
    btf.margin_left = Inches(0.25)
    btf.margin_top = Inches(0.22)
    _para(btf, "셀픽 도달 규모", 13, True, ORANGE_D, first=True,
          align=PP_ALIGN.CENTER, space_after=8)
    for key in ["산모DB", "조리원", "산부인과", "체류", "키오스크", "무료인화"]:
        _para(btf, f"● {SELPIC_FACTS[key]}", 12, False, DARK, space_after=6)


def _slide_direction(prs, c):
    s = _blank(prs)
    _header(s, "셀픽 마케팅 방향",
            "오프라인 제품경험 + 온라인 맘카페 바이럴의 결합")
    # 오프라인
    tf = _textbox(s, Inches(0.7), Inches(1.7), Inches(12.0), Inches(2.5))
    _para(tf, "오프라인 — 산부인과·산후조리원·베이비 스튜디오에서 전문가 사용·추천",
          14, True, ORANGE_D, first=True, space_after=6)
    for idea in c["offline_ideas"][:3]:
        _para(tf, f"● {idea}", 12.5, False, DARK, space_after=4)
    _para(tf, f"   → {SELPIC_FACTS['조리원']}, {SELPIC_FACTS['산부인과']} 접점 활용 "
              f"+ 경험자 DB 수집으로 온·오프 판매 연결", 12, True, GRAY, space_before=3)
    # 온라인
    tf2 = _textbox(s, Inches(0.7), Inches(4.3), Inches(12.0), Inches(2.2))
    _para(tf2, "온라인 — 맘카페 집중 바이럴", 14, True, ORANGE_D, first=True, space_after=6)
    for idea in c["online_ideas"][:5]:
        _para(tf2, f"● {idea}", 12.5, False, DARK, space_after=3)
    _note(s, "※ 실제 집행 채널·물량은 브랜드와 협의 후 확정됩니다.", Inches(6.85))


def _slide_appendix(prs, c):
    s = _blank(prs)
    _header(s, "Appendix · 참고 레퍼런스",
            "맘카페 바이럴이 실제로 퍼지는 방식")
    tf = _textbox(s, Inches(0.7), Inches(1.8), Inches(12.0), Inches(4.6))
    _para(tf, "맘카페에서 자연 확산되는 콘텐츠 유형", 14, True, ORANGE_D,
          first=True, space_after=8)
    ref_types = [
        "출산가방 리스트 공유글 — 매월 수백 건, 평균 조회수 높음 (필수템 등극 시 지속 노출)",
        "산후조리원 첫 샤워·신생아 배꼽·목욕 등 '단계별 관리' 후기",
        "육아 선배맘의 실사용 추천 — 조리원 동기·맘카페로 전파",
        "어린이집 하원 후 케어 루틴 등 '일상 반복 사용' 콘텐츠",
    ]
    for r in ref_types:
        _para(tf, f"· {r}", 12.5, False, DARK, space_after=6)
    _para(tf, "이 브랜드에 붙일 바이럴 키워드(제안):", 13, True, ORANGE_D,
          space_before=8, space_after=4)
    _para(tf, "   " + "   ".join(f"#{a}" for a in c["appeal_points"][:6]),
          13, True, GRAY)
    _note(s, "※ 예시는 셀픽 채널에서 관찰된 일반적 확산 패턴입니다.", Inches(6.85))


def _slide_lineup(prs, c):
    s = _blank(prs)
    _header(s, "추가 의견 · 향후 라인업 제안",
            "시장 접점에서 도출한 제품 확장 아이디어")
    # 축별 그룹핑
    groups = {"용량": [], "타입": [], "기능": []}
    for r in c["lineup_ideas"]:
        ax = r["axis"]
        key = "용량" if "용량" in ax else "타입" if "타입" in ax else "기능"
        groups[key].append(r["idea"])
    tf = _textbox(s, Inches(0.7), Inches(1.8), Inches(12.0), Inches(4.6))
    first = True
    for axis, ideas in groups.items():
        if not ideas:
            continue
        _para(tf, f"{axis} 다양화", 14, True, ORANGE_D, first=first,
              space_before=0 if first else 10, space_after=4)
        first = False
        for idea in ideas:
            _para(tf, f"   - {idea}", 12.5, False, DARK, space_after=3)
    _note(s, "※ 향후 라인업은 셀픽의 시장 관찰 기반 제안이며, 확정 계획이 아닙니다.",
          Inches(6.85))


def build_pptx(content: dict) -> bytes:
    prs = Presentation()
    prs.slide_width = W
    prs.slide_height = H
    _slide_cover(prs, content["brand"])
    _slide_strategy(prs, content)
    _slide_needs(prs, content)
    _slide_target(prs, content)
    _slide_direction(prs, content)
    _slide_appendix(prs, content)
    _slide_lineup(prs, content)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# 그리드 위치 계산 헬퍼 (EMU 덧셈)
def Emu_add(base, step_gap, n):
    step, gap = step_gap
    return base + (step + gap) * n


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
