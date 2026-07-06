import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DOCPORTAL_DATA", BASE_DIR / "data"))
FILES_DIR = DATA_DIR / "files"
DB_PATH = DATA_DIR / "docportal.db"

# 인덱싱할 본문 텍스트 최대 길이 (문자 수)
MAX_TEXT_LEN = 2_000_000


def ensure_dirs() -> None:
    FILES_DIR.mkdir(parents=True, exist_ok=True)
