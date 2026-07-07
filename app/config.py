import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DOCPORTAL_DATA", BASE_DIR / "data"))
FILES_DIR = DATA_DIR / "files"
DB_PATH = DATA_DIR / "docportal.db"

# 인덱싱할 본문 텍스트 최대 길이 (문자 수)
MAX_TEXT_LEN = 2_000_000

# 회원가입 허용 이메일 도메인 (폐쇄망 전제 — 실제 메일 발송 없이 도메인 검증 + 관리자 승인으로 처리)
ALLOWED_EMAIL_DOMAIN = os.environ.get("DOCPORTAL_EMAIL_DOMAIN", "atto-research.com")

SESSION_COOKIE = "docportal_session"


def ensure_dirs() -> None:
    FILES_DIR.mkdir(parents=True, exist_ok=True)
