import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DOCPORTAL_DATA", BASE_DIR / "data"))
FILES_DIR = DATA_DIR / "files"
DB_PATH = DATA_DIR / "docportal.db"

# 인덱싱할 본문 텍스트 최대 길이 (문자 수)
MAX_TEXT_LEN = 2_000_000

# 텍스트 추출을 시도할 파일 크기 상한 (MB) — 이보다 크면 저장만 하고 추출 생략
# (추출 라이브러리가 파일을 메모리에 올리므로, 초대형 파일의 메모리 폭주 방지)
EXTRACT_MAX_MB = int(os.environ.get("DOCPORTAL_EXTRACT_MAX_MB", "64"))
EXTRACT_MAX_BYTES = EXTRACT_MAX_MB * 1024 * 1024

# 회원가입 허용 이메일 도메인 (폐쇄망 전제 — 실제 메일 발송 없이 도메인 검증 + 관리자 승인으로 처리)
ALLOWED_EMAIL_DOMAIN = os.environ.get("DOCPORTAL_EMAIL_DOMAIN", "atto-research.com")

SESSION_COOKIE = "docportal_session"
# HTTPS(nginx TLS 종단) 뒤에서 운영할 때 1로 설정 — 세션 쿠키에 Secure 플래그를 붙인다
SECURE_COOKIES = os.environ.get("DOCPORTAL_SECURE_COOKIES", "0") == "1"

# 문서 형식 변환(LibreOffice headless) — 미설치면 원본 다운로드만 제공.
# 실행 파일 경로를 직접 지정하려면 DOCPORTAL_SOFFICE 설정.
SOFFICE_BIN = os.environ.get("DOCPORTAL_SOFFICE", "")
# 동시 변환 상한 — LibreOffice 프로세스 폭주로 서버가 느려지는 것을 방지
MAX_CONVERSIONS = max(1, int(os.environ.get("DOCPORTAL_MAX_CONVERSIONS", "3")))

# 문서 업로드/변경 푸시 알림 — Google Chat/Slack 수신 웹훅 URL ({"text": ...} POST 호환)
# Google Chat: 스페이스 → 앱 및 통합 → 웹훅 추가 (앱 비밀번호 불필요)
WEBHOOK_URL = os.environ.get("DOCPORTAL_WEBHOOK_URL", "")

# MaxKB 연동 — 설정 시 문서 본문을 MaxKB 지식베이스로 자동 동기화 (RAG 챗봇용)
MAXKB_URL = os.environ.get("DOCPORTAL_MAXKB_URL", "").rstrip("/")
MAXKB_USER = os.environ.get("DOCPORTAL_MAXKB_USER", "admin")
MAXKB_PASSWORD = os.environ.get("DOCPORTAL_MAXKB_PASSWORD", "")
MAXKB_KB_ID = os.environ.get("DOCPORTAL_MAXKB_KB_ID", "")
MAXKB_WORKSPACE = os.environ.get("DOCPORTAL_MAXKB_WORKSPACE", "default")
# 포털 화면에 노출할 챗봇 주소 (MaxKB 애플리케이션 공유 링크)
MAXKB_CHAT_URL = os.environ.get("DOCPORTAL_MAXKB_CHAT_URL", "")


def maxkb_configured() -> bool:
    return bool(MAXKB_URL and MAXKB_PASSWORD and MAXKB_KB_ID)

# SMTP 설정 — USER/PASSWORD가 모두 있으면 가입 인증 메일을 발송한다.
# Gmail(Google Workspace)은 2단계 인증 + 앱 비밀번호 필요: https://myaccount.google.com/apppasswords
SMTP_HOST = os.environ.get("DOCPORTAL_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("DOCPORTAL_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("DOCPORTAL_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("DOCPORTAL_SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("DOCPORTAL_SMTP_FROM", SMTP_USER)
SMTP_STARTTLS = os.environ.get("DOCPORTAL_SMTP_STARTTLS", "1") == "1"
# 인증 메일 링크에 들어갈 이 서비스의 주소
BASE_URL = os.environ.get("DOCPORTAL_BASE_URL", "http://localhost:8001").rstrip("/")


def smtp_configured() -> bool:
    return bool(SMTP_USER and SMTP_PASSWORD)


# 문서 요약 — Ollama가 있으면 LLM 요약, 없으면 빈도 기반 추출 요약으로 폴백
OLLAMA_URL = os.environ.get("DOCPORTAL_OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("DOCPORTAL_OLLAMA_MODEL", "gemma4:12b")
SUMMARY_INPUT_LEN = 8_000   # 요약에 넣을 본문 최대 길이 (문자)


# 오피스 문서 미리보기용 변환 PDF 캐시
PREVIEW_DIR = DATA_DIR / "previews"


def ensure_dirs() -> None:
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
