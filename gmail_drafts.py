# -*- coding: utf-8 -*-
"""
Gmail API 초안 생성 모듈 (대시보드 '완전자동' 버튼용)
─────────────────────────────────────────────────────────────
- token.json 으로 인증 (최초 1회는 auth_gmail.py 로 생성)
- create_draft(): HTML + 평문 본문으로 Gmail '초안' 생성 (발송 X)

발송은 하지 않습니다. 초안만 만들고, 첨부·전송은 사용자가 Gmail에서 직접.
"""
import os
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# gmail.compose = 초안 생성/수정 권한 (발송은 코드에서 호출 안 함)
SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]

TOKEN_PATH = "token.json"
CREDENTIALS_PATH = "credentials.json"


def get_service():
    """저장된 token.json 으로 Gmail 서비스 생성.
    token 이 없으면 안내 메시지와 함께 None 반환 (auth_gmail.py 먼저 실행)."""
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
    # 만료 시 자동 갱신
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    if not creds or not creds.valid:
        return None
    return build("gmail", "v1", credentials=creds)


def create_draft(service, to: str, subject: str,
                 html_body: str, plain_body: str = "") -> str:
    """Gmail 초안 1건 생성 → draft id 반환."""
    msg = MIMEMultipart("alternative")
    msg["To"] = to
    msg["Subject"] = subject
    if plain_body:
        msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    draft = (
        service.users()
        .drafts()
        .create(userId="me", body={"message": {"raw": raw}})
        .execute()
    )
    return draft.get("id", "")
