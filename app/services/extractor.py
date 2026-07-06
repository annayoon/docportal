"""업로드된 문서에서 검색용 텍스트를 추출한다.

지원 형식: PDF, DOCX, PPTX, XLSX, HWP(미리보기 텍스트), TXT/MD/CSV 등 텍스트 파일.
추출 실패는 검색 불가일 뿐 업로드 실패는 아니므로 예외를 삼키고 빈 문자열을 돌려준다.
"""

import io
import logging
from pathlib import Path

from ..config import MAX_TEXT_LEN

logger = logging.getLogger(__name__)

TEXT_SUFFIXES = {".txt", ".md", ".csv", ".log", ".json", ".xml", ".yaml", ".yml"}


def extract_text(data: bytes, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".pdf":
            text = _from_pdf(data)
        elif suffix == ".docx":
            text = _from_docx(data)
        elif suffix == ".pptx":
            text = _from_pptx(data)
        elif suffix == ".xlsx":
            text = _from_xlsx(data)
        elif suffix == ".hwp":
            text = _from_hwp(data)
        elif suffix in TEXT_SUFFIXES:
            text = data.decode("utf-8", errors="replace")
        else:
            text = ""
    except Exception:
        logger.exception("텍스트 추출 실패: %s", filename)
        text = ""
    return text[:MAX_TEXT_LEN]


def _from_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _from_docx(data: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(data))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append("\t".join(cell.text for cell in row.cells))
    return "\n".join(parts)


def _from_pptx(data: bytes) -> str:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(data))
    parts = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                parts.append(shape.text_frame.text)
    return "\n".join(parts)


def _from_xlsx(data: bytes) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets:
        parts.append(f"[{ws.title}]")
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None]
            if cells:
                parts.append("\t".join(cells))
            if sum(len(p) for p in parts) > MAX_TEXT_LEN:
                return "\n".join(parts)
    return "\n".join(parts)


def _from_hwp(data: bytes) -> str:
    """HWP 5.x의 PrvText(미리보기) 스트림에서 텍스트를 꺼낸다. 전문은 아니지만 검색에는 충분."""
    import olefile

    ole = olefile.OleFileIO(io.BytesIO(data))
    try:
        if ole.exists("PrvText"):
            return ole.openstream("PrvText").read().decode("utf-16-le", errors="replace")
        return ""
    finally:
        ole.close()
