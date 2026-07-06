import hashlib
from pathlib import Path

from ..config import FILES_DIR


def save_file(data: bytes) -> tuple[str, str, int]:
    """내용 해시 기반으로 파일을 저장한다. 같은 내용이면 한 번만 저장(중복 제거).

    Returns: (sha256, stored_name, size)
    """
    sha = hashlib.sha256(data).hexdigest()
    subdir = FILES_DIR / sha[:2]
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / sha
    if not path.exists():
        path.write_bytes(data)
    return sha, f"{sha[:2]}/{sha}", len(data)


def file_path(stored_name: str) -> Path:
    return FILES_DIR / stored_name
