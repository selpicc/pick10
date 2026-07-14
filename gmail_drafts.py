# -*- coding: utf-8 -*-
"""
Gmail 초안 생성 + 발송·회신 추적
─────────────────────────────────────────────────────────────
- token.json 으로 인증 (최초 1회는 auth_gmail.py 로 생성)
- create_draft(): HTML + 평문 본문으로 Gmail '초안' 생성 (발송 X)
- lookup_status(): 그 초안이 발송됐는지 / 회신이 왔는지 조회
- create_followup_draft(): 같은 대화(스레드)에 답장 형태의 팔로업 초안 생성

발송은 절대 하지 않습니다. 초안만 만들고, 첨부·전송은 사용자가 Gmail에서 직접.
읽기 권한은 '보낸편지함/회신 확인' 용도로만 씁니다.

⚠ SCOPES 가 바뀌면 기존 token.json 은 무효입니다.
   → auth_gmail.py 를 다시 실행해 재승인해야 합니다 (안내 메시지가 뜹니다).
"""
import os
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# gmail.compose  = 초안 생성/수정 (발송은 코드에서 호출 안 함)
# gmail.readonly = 보낸편지함·회신 확인 (읽기 전용 — 삭제/발송 불가)
SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
]

TOKEN_PATH = "token.json"
CREDENTIALS_PATH = "credentials.json"


def get_service():
    """저장된 token.json 으로 Gmail 서비스 생성.
    token 이 없거나 권한이 부족하면 None 반환 (auth_gmail.py 먼저 실행)."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError(
            "구글 라이브러리가 없습니다. 다음을 설치하세요:\n"
            "  venv\\Scripts\\pip install google-api-python-client "
            "google-auth-httplib2 google-auth-oauthlib"
        )

    if not os.path.exists(TOKEN_PATH):
        return None  # 최초 인증 필요

    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    # ⭐ 권한(scope)이 늘어난 경우: 옛 token.json 은 compose 권한만 갖고 있다.
    #   그대로 쓰면 보낸편지함 조회에서 403이 난다 → 재승인하라고 알린다.
    granted = set(getattr(creds, "scopes", None) or [])
    if not set(SCOPES).issubset(granted):
        raise RuntimeError(
            "Gmail 권한이 부족합니다 (보낸편지함·회신 읽기 권한 없음).\n"
            "  venv\\Scripts\\python auth_gmail.py 를 다시 실행해\n"
            "  브라우저에서 권한을 한 번 더 승인해주세요."
        )

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    if not creds or not creds.valid:
        return None
    return build("gmail", "v1", credentials=creds)


def _build_message(to: str, subject: str, html_body: str,
                   plain_body: str = "", headers: dict = None) -> dict:
    msg = MIMEMultipart("alternative")
    msg["To"] = to
    msg["Subject"] = subject
    for k, v in (headers or {}).items():
        if v:
            msg[k] = v
    if plain_body:
        msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}


def create_draft(service, to: str, subject: str,
                 html_body: str, plain_body: str = "") -> dict:
    """Gmail 초안 1건 생성.

    반환: {"draft_id": "...", "thread_id": "...", "message_id": "..."}
      - draft_id  : 임시보관함의 그 초안 (발송되면 사라진다 → 발송 감지에 사용)
      - thread_id : 대화 묶음 (회신·팔로업이 같은 스레드에 붙는다)
    """
    body = {"message": _build_message(to, subject, html_body, plain_body)}
    draft = service.users().drafts().create(userId="me", body=body).execute()
    msg = draft.get("message", {}) or {}
    return {
        "draft_id": draft.get("id", ""),
        "thread_id": msg.get("threadId", ""),
        "message_id": msg.get("id", ""),
    }


# ─────────────────────────────────────────────────────────
# 발송 · 회신 추적
# ─────────────────────────────────────────────────────────
def _my_address(service) -> str:
    try:
        return (service.users().getProfile(userId="me").execute()
                .get("emailAddress", "") or "").lower()
    except Exception:
        return ""


# 회신처럼 보이지만 사람이 쓴 게 아닌 메일 (자동응답·부재중·수신확인 봇)
# → 이런 건 '회신 왔다'로 치면 안 된다. 실제로 답이 온 줄 알고 팔로업을 멈추게 된다.
_AUTO_REPLY_HINTS = (
    "auto-reply", "autoreply", "automatic reply", "out of office",
    "부재중", "자동응답", "자동 회신", "수신확인", "읽음확인",
    "mailer-daemon", "postmaster", "no-reply", "noreply", "donotreply",
)


def _is_auto_reply(headers: dict) -> bool:
    """헤더로 자동응답 판별. (100%는 아니지만 흔한 것들은 걸러진다)"""
    # RFC 3834: 자동응답은 이 헤더를 붙이는 게 표준
    if headers.get("auto-submitted", "").lower() not in ("", "no"):
        return True
    if headers.get("x-autoreply") or headers.get("x-autorespond"):
        return True
    blob = (headers.get("subject", "") + " " + headers.get("from", "")).lower()
    return any(h in blob for h in _AUTO_REPLY_HINTS)


def lookup_status(service, draft_id: str, thread_id: str) -> dict:
    """이 브랜드 메일이 지금 어떤 상태인지 Gmail에 물어본다.

    반환: {
      "sent": bool,            # 발송됨 (초안이 사라지고 보낸편지함에 있음)
      "sent_at": "ISO시각" or "",
      "replied": bool,         # 사람이 회신함 (자동응답 제외)
      "replied_at": "ISO시각" or "",
      "reply_from": "보낸사람",
    }
    조회 실패(스레드 삭제 등)는 조용히 빈 상태로 반환 — 프로그램을 멈추지 않는다.
    """
    from datetime import datetime, timezone

    out = {"sent": False, "sent_at": "", "replied": False,
           "replied_at": "", "reply_from": ""}
    if not thread_id:
        return out

    # 1) 초안이 아직 남아 있으면 = 아직 안 보냄
    if draft_id:
        try:
            service.users().drafts().get(userId="me", id=draft_id).execute()
            return out          # 초안 그대로 → 미발송
        except Exception:
            pass                # 초안 없음 → 발송됐거나 사용자가 지웠음

    try:
        thread = service.users().threads().get(
            userId="me", id=thread_id, format="metadata",
            metadataHeaders=["From", "Subject", "Auto-Submitted",
                             "X-Autoreply", "X-Autorespond"],
        ).execute()
    except Exception:
        return out              # 스레드 자체가 없음 (초안을 그냥 지운 경우)

    me = _my_address(service)

    def _ts(ms):
        try:
            return datetime.fromtimestamp(
                int(ms) / 1000, tz=timezone.utc).isoformat()
        except Exception:
            return ""

    for m in thread.get("messages", []):
        labels = m.get("labelIds", []) or []
        headers = {
            h.get("name", "").lower(): h.get("value", "")
            for h in (m.get("payload", {}).get("headers", []) or [])
        }
        sender = headers.get("from", "").lower()

        if "SENT" in labels:
            # 내가 보낸 메시지 = 발송됨 (가장 이른 것을 발송 시각으로)
            if not out["sent"]:
                out["sent"] = True
                out["sent_at"] = _ts(m.get("internalDate", 0))
            continue

        # 내가 보낸 게 아닌 메시지 = 상대방 메시지 (= 회신 후보)
        if me and me in sender:
            continue
        if _is_auto_reply(headers):
            continue            # 자동응답은 회신으로 안 침
        if not out["replied"]:
            out["replied"] = True
            out["replied_at"] = _ts(m.get("internalDate", 0))
            out["reply_from"] = headers.get("from", "")

    return out


def create_followup_draft(service, to: str, subject: str, thread_id: str,
                          html_body: str, plain_body: str = "") -> dict:
    """같은 대화(스레드)에 붙는 팔로업 초안.

    새 메일이 아니라 원래 대화의 '답장'으로 들어가야 한다.
    (새 메일로 또 보내면 '광고를 또 보냈네'가 되고, 같은 스레드면 '아, 그거'가 된다)
    """
    subj = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    body = {"message": _build_message(to, subj, html_body, plain_body)}
    if thread_id:
        body["message"]["threadId"] = thread_id
    draft = service.users().drafts().create(userId="me", body=body).execute()
    msg = draft.get("message", {}) or {}
    return {
        "draft_id": draft.get("id", ""),
        "thread_id": msg.get("threadId", ""),
        "message_id": msg.get("id", ""),
    }
