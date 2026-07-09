import hashlib
import os
import tempfile
from pathlib import Path

from ..config import FILES_DIR

_CHUNK = 1024 * 1024  # 1MB


async def save_upload(file) -> tuple[str, str, int]:
    """업로드 파일을 메모리에 통째로 올리지 않고 청크 단위로 저장한다.

    수 GB 파일이 올라와도 메모리 사용은 청크 크기로 고정된다.
    Returns: (sha256, stored_name, size)
    """
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    size = 0
    fd, tmp = tempfile.mkstemp(dir=FILES_DIR, prefix=".upload-")
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await file.read(_CHUNK):
                hasher.update(chunk)
                size += len(chunk)
                out.write(chunk)
        sha = hasher.hexdigest()
        subdir = FILES_DIR / sha[:2]
        subdir.mkdir(parents=True, exist_ok=True)
        path = subdir / sha
        if path.exists():  # 동일 내용은 한 번만 저장 (중복 제거)
            os.unlink(tmp)
        else:
            os.replace(tmp, path)  # 같은 볼륨 내 원자적 이동
        return sha, f"{sha[:2]}/{sha}", size
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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
