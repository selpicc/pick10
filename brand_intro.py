# -*- coding: utf-8 -*-
"""브랜드별 메일 도입부 생성 (Gemini)
─────────────────────────────────────────────────────────────
콜드메일 회신률을 실제로 가르는 건 '이 브랜드만을 위해 쓴 첫 문장'이다.
카테고리 문구는 같은 카테고리 브랜드 전부에게 똑같이 나가므로 대량발송 티가 난다.

하는 일:
  브랜드 홈페이지를 읽고 → 그 브랜드가 스스로 내세운 특징 1~2줄을 뽑아
  메일 도입부 문장으로 만든다.

핵심 원칙 — 지어내지 않기:
  LLM은 그럴듯한 거짓말("업계 1위로 알고 있습니다")을 잘 만든다. 그 한 줄이
  미팅에서 신뢰를 통째로 날린다. 그래서 3중으로 막는다.
    ① 프롬프트: 주어진 홈페이지 문구에 없는 내용은 절대 쓰지 말 것
    ② 검증(_is_safe): 숫자·순위·수상·최고 같은 검증 불가 표현이 있으면 폐기
    ③ 폐기되면 기존 카테고리 문구로 자동 복귀 (메일은 항상 정상 발송됨)

실패해도 메일 생성은 절대 안 멈춘다. 키가 없거나 네트워크가 죽어도 조용히
빈 문자열을 반환하고, 호출부는 기존 문구를 쓴다.

사용:
    from brand_intro import make_opener
    opener = make_opener(row)   # 실패 시 "" (호출부가 폴백)
"""
import os
import re

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip()
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# 검증 불가능한 주장 — 하나라도 있으면 그 문장은 버린다.
# (홈페이지에 적혀 있더라도 우리가 사실 확인을 못 하면 메일에 쓰지 않는다)
_BANNED = (
    "1위", "일위", "최고", "최대", "최초", "유일", "독보", "압도",
    "업계", "선두", "선도", "수상", "인증받", "특허", "검증된",
    "가장 많", "가장 큰", "베스트셀러", "완판", "품절대란",
    "유명", "인기", "대표적", "손꼽", "믿을 수 있는", "신뢰받",
)


def _fetch_text(url: str, limit: int = 4000) -> str:
    """홈페이지 본문 텍스트. 실패하면 빈 문자열."""
    if not url or not url.startswith("http"):
        return ""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=10)
        if r.status_code != 200:
            return ""
        r.encoding = r.apparent_encoding or r.encoding
        html = r.text
    except Exception:
        return ""

    # 스크립트·스타일 제거 후 태그 벗기기 (bs4 없이도 동작하게)
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    # 메타 description 은 브랜드가 스스로 쓴 한 줄이라 가치가 높다 → 앞에 붙인다
    meta = ""
    m = re.search(
        r'(?is)<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\']'
        r'[^>]+content=["\']([^"\']{10,300})["\']',
        html,
    )
    if m:
        meta = m.group(1).strip() + "\n"
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&[a-zA-Z#0-9]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return (meta + text)[:limit]


def _is_safe(line: str) -> bool:
    """메일에 실어도 되는 문장인지. 하나라도 걸리면 폐기."""
    s = (line or "").strip()
    if not (15 <= len(s) <= 170):
        return False
    if any(b in s for b in _BANNED):
        return False
    # 숫자가 든 주장은 검증 불가 (연 매출 100억, 후기 3만개 …)
    if re.search(r"\d", s):
        return False
    # 우리 얘기(셀픽)를 하면 도입부 역할을 못 한다
    if "셀픽" in s or "조리원" in s:
        return False
    # 모델이 지시문·따옴표째 뱉는 경우
    if s.startswith(("도입부", "문장", "-", "*", "#")) or '"' in s:
        return False
    return True


def _call_gemini(prompt: str) -> str:
    try:
        r = requests.post(
            API_URL.format(m=MODEL),
            headers={"x-goog-api-key": API_KEY, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048},
            },
            timeout=45,
        )
        if r.status_code != 200:
            return ""
        parts = r.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except Exception:
        return ""


def make_opener(row: dict) -> str:
    """브랜드 맞춤 도입부 1~2문장. 못 만들면 "" (호출부가 카테고리 문구로 폴백)."""
    if not API_KEY:
        return ""

    brand = (row.get("brand_name") or "").strip()
    if not brand:
        return ""
    category = (row.get("category") or "").strip()
    flagship = (row.get("flagship_product") or "").strip()
    url = (row.get("smartstore_url") or "").strip()

    site_text = _fetch_text(url)
    # 홈페이지를 못 읽었고 주력상품도 없으면 근거가 없다 → 지어내느니 포기
    if not site_text and not flagship:
        return ""

    prompt = f"""너는 B2B 영업메일의 첫 문단을 쓰는 카피라이터다.
아래는 '{brand}'라는 브랜드의 홈페이지에서 긁어온 텍스트다.

--- 홈페이지 텍스트 ---
{site_text if site_text else "(없음)"}
--- 주력상품 ---
{flagship if flagship else "(없음)"}
--- 카테고리 ---
{category if category else "(없음)"}

이 브랜드에 콜드메일을 보내려 한다. 도입부 1~2문장을 써라.
브랜드가 '우리를 제대로 보고 연락했구나'라고 느끼게 만드는 게 목적이다.

반드시 지킬 것:
- 위 홈페이지 텍스트에 실제로 적힌 내용만 근거로 삼아라. 없는 사실을 추측하거나
  지어내지 마라. 근거가 빈약하면 무리하지 말고 담백하게 써라.
- 숫자, 순위, 수상, '업계 1위', '유명한', '인기' 같은 표현은 절대 쓰지 마라.
- 광고 플랫폼(셀픽)이나 산후조리원 얘기는 하지 마라. 오직 이 브랜드 얘기만.
- 존댓말, 담백한 대화체. 과장·아부 금지.
- '{brand}의 ~을 보고 연락드립니다' 같은 자연스러운 관찰로 시작해라.
- 설명 없이 문장만 출력해라. 따옴표로 감싸지 마라.

예시(형식만 참고):
무향·순한 성분을 앞세우신 걸 보고 연락드립니다. 성분을 가장 깐깐하게 따지는 시기의 엄마들이 정확히 찾는 지점이라고 봤습니다."""

    out = _call_gemini(prompt)
    if not out:
        return ""

    # 여러 줄로 뱉으면 붙여서 한 덩어리로
    line = " ".join(l.strip() for l in out.splitlines() if l.strip())
    line = line.strip().strip('"').strip("'")

    if not _is_safe(line):
        return ""
    return line
