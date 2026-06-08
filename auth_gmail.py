# -*- coding: utf-8 -*-
"""
Gmail 최초 인증 (딱 한 번만 실행)
─────────────────────────────────────────────────────────────
credentials.json (구글 클라우드에서 발급) 이 같은 폴더에 있어야 합니다.
실행하면 브라우저가 열리고, 로그인+허용하면 token.json 이 만들어집니다.
이후로는 대시보드가 token.json 으로 자동 작동합니다.

실행:  venv\\Scripts\\python auth_gmail.py
"""
import sys
import io
import os

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]

if not os.path.exists("credentials.json"):
    print("❌ credentials.json 이 없습니다.")
    print("   구글 클라우드 콘솔에서 OAuth 클라이언트(데스크톱 앱)를 만들어")
    print("   credentials.json 을 이 폴더에 두고 다시 실행하세요.")
    print("   (자세한 순서는 Gmail_연동_설정가이드.md 참고)")
    sys.exit(1)

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("❌ 구글 라이브러리가 없습니다. 먼저 설치하세요:")
    print("   venv\\Scripts\\pip install google-api-python-client "
          "google-auth-httplib2 google-auth-oauthlib")
    sys.exit(1)

flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
creds = flow.run_local_server(port=0)

with open("token.json", "w", encoding="utf-8") as f:
    f.write(creds.to_json())

print("\n✅ 인증 완료! token.json 이 생성되었습니다.")
print("   이제 대시보드에서 '영업메일 초안 일괄 생성' 버튼을 쓸 수 있어요.")
