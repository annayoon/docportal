"""LibreOffice(headless)를 이용한 문서 형식 변환.

한 엔진으로 Office 문서→PDF, 위키 HTML→PDF/DOCX를 모두 처리한다.
LibreOffice가 없으면 available()이 False가 되고, 호출부는 원본 다운로드로 폴백한다.
"""

import logging
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from ..config import SOFFICE_BIN

logger = logging.getLogger(__name__)

# 흔한 설치 경로 (macOS / Linux)
_CANDIDATES = [
    "soffice",
    "libreoffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
    "/opt/libreoffice/program/soffice",
]

# LibreOffice가 안정적으로 변환할 수 있는 입력 형식
CONVERTIBLE_INPUTS = {
    ".doc", ".docx", ".odt", ".rtf", ".ppt", ".pptx", ".odp",
    ".xls", ".xlsx", ".ods", ".csv", ".html", ".htm", ".txt",
}

# 대상 형식 → LibreOffice convert-to 인자(필터 명시). 출력 확장자는 앞부분에서 취함.
_TARGET_FILTER = {
    "pdf": "pdf",
    "docx": "docx:MS Word 2007 XML",
    "html": "html:XHTML Writer File",
}


@lru_cache(maxsize=1)
def soffice_bin() -> str | None:
    if SOFFICE_BIN and Path(SOFFICE_BIN).exists():
        return SOFFICE_BIN
    for cand in _CANDIDATES:
        found = shutil.which(cand) if "/" not in cand else (cand if Path(cand).exists() else None)
        if found:
            return found
    return None


def available() -> bool:
    return soffice_bin() is not None


def can_convert(src_ext: str) -> bool:
    return available() and src_ext.lower() in CONVERTIBLE_INPUTS


def convert(data: bytes, src_ext: str, target: str) -> bytes:
    """data(src_ext 형식)를 target(예: 'pdf','docx','html') 형식으로 변환해 바이트로 반환.

    변환 실패 시 RuntimeError를 올린다.
    """
    binary = soffice_bin()
    if binary is None:
        raise RuntimeError("LibreOffice가 설치되어 있지 않습니다.")
    convert_arg = _TARGET_FILTER.get(target, target)
    out_ext = convert_arg.split(":")[0]
    src_ext = src_ext if src_ext.startswith(".") else f".{src_ext}"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / f"input{src_ext}"
        src.write_bytes(data)
        # UserInstallation을 별도 지정해 이미 떠 있는 LibreOffice 프로필과 충돌 방지
        profile = tmp_path / "profile"
        proc = subprocess.run(
            [
                binary, "--headless", "--norestore",
                f"-env:UserInstallation=file://{profile}",
                "--convert-to", convert_arg, "--outdir", str(tmp_path), str(src),
            ],
            capture_output=True, timeout=120,
        )
        outputs = list(tmp_path.glob(f"input.{out_ext}"))
        if proc.returncode != 0 or not outputs:
            raise RuntimeError(
                f"변환 실패 (rc={proc.returncode}): {proc.stderr.decode(errors='replace')[:300]}"
            )
        return outputs[0].read_bytes()
