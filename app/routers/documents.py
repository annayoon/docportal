import mimetypes
from pathlib import Path

import markdown as md
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from ..auth import get_current_user
from ..db import get_conn, notify_others, reindex_document
from ..services import storage
from ..services.extractor import extract_text
from ..services.summarizer import summarize
from ..templating import templates

router = APIRouter()

# 브라우저가 자체 렌더링할 수 있는 형식 → 미리보기 방식 결정
_PREVIEW_IFRAME = {".pdf"}
_PREVIEW_IMAGE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _preview_kind(filename: str | None, content_text: str) -> str | None:
    if filename:
        suffix = Path(filename).suffix.lower()
        if suffix in _PREVIEW_IFRAME:
            return "pdf"
        if suffix in _PREVIEW_IMAGE:
            return "image"
    if content_text.strip():
        return "text"
    return None


def _departments(conn):
    rows = conn.execute(
        "SELECT DISTINCT department FROM documents WHERE department != '' ORDER BY department"
    ).fetchall()
    return [r["department"] for r in rows]


@router.get("/")
def index(request: Request, dept: str = ""):
    conn = get_conn()
    try:
        sql = (
            "SELECT d.*, v.version_no, v.filename, v.size "
            "FROM documents d "
            "JOIN versions v ON v.document_id = d.id AND v.version_no = "
            "  (SELECT MAX(version_no) FROM versions WHERE document_id = d.id) "
        )
        params: tuple = ()
        if dept:
            sql += "WHERE d.department = ? "
            params = (dept,)
        sql += "ORDER BY d.updated_at DESC LIMIT 50"
        docs = conn.execute(sql, params).fetchall()
        return templates.TemplateResponse(
            request,
            "index.html",
            {"docs": docs, "departments": _departments(conn), "dept": dept},
        )
    finally:
        conn.close()


@router.post("/upload")
async def upload(
    title: str = Form(""),
    department: str = Form(""),
    tags: str = Form(""),
    note: str = Form(""),
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    data = await file.read()
    filename = file.filename or "unnamed"
    if not data:
        raise HTTPException(400, "빈 파일입니다.")
    sha, stored_name, size = storage.save_file(data)
    text = extract_text(data, filename)
    doc_title = title.strip() or Path(filename).stem
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO documents (title, doc_type, department, tags, created_by) "
            "VALUES (?, 'file', ?, ?, ?)",
            (doc_title, department.strip(), tags.strip(), current_user["id"]),
        )
        doc_id = cur.lastrowid
        conn.execute(
            "INSERT INTO versions (document_id, version_no, filename, stored_name, sha256, size, content_text, note) "
            "VALUES (?, 1, ?, ?, ?, ?, ?, ?)",
            (doc_id, filename, stored_name, sha, size, text, note.strip()),
        )
        reindex_document(conn, doc_id)
        notify_others(
            conn, current_user["id"], doc_id,
            f"{current_user['email']}님이 새 문서를 업로드했습니다: {doc_title}",
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/documents/{doc_id}", status_code=303)


@router.get("/documents/{doc_id}")
def detail(request: Request, doc_id: int, v: int | None = None):
    conn = get_conn()
    try:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if doc is None:
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        versions = conn.execute(
            "SELECT * FROM versions WHERE document_id = ? ORDER BY version_no DESC",
            (doc_id,),
        ).fetchall()
        shown = versions[0]
        if v is not None:
            shown = next((x for x in versions if x["version_no"] == v), shown)
        rendered = None
        preview = None
        if doc["doc_type"] == "wiki":
            rendered = md.markdown(
                shown["content_text"], extensions=["tables", "fenced_code", "toc"]
            )
        else:
            preview = _preview_kind(shown["filename"], shown["content_text"])
        return templates.TemplateResponse(
            request,
            "document.html",
            {"doc": doc, "versions": versions, "shown": shown, "rendered": rendered,
             "preview": preview},
        )
    finally:
        conn.close()


@router.post("/documents/{doc_id}/versions")
async def upload_version(
    doc_id: int,
    note: str = Form(""),
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    data = await file.read()
    filename = file.filename or "unnamed"
    if not data:
        raise HTTPException(400, "빈 파일입니다.")
    conn = get_conn()
    try:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if doc is None or doc["doc_type"] != "file":
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        sha, stored_name, size = storage.save_file(data)
        text = extract_text(data, filename)
        next_no = conn.execute(
            "SELECT COALESCE(MAX(version_no), 0) + 1 AS n FROM versions WHERE document_id = ?",
            (doc_id,),
        ).fetchone()["n"]
        conn.execute(
            "INSERT INTO versions (document_id, version_no, filename, stored_name, sha256, size, content_text, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (doc_id, next_no, filename, stored_name, sha, size, text, note.strip()),
        )
        conn.execute(
            "UPDATE documents SET updated_at = datetime('now','localtime') WHERE id = ?",
            (doc_id,),
        )
        reindex_document(conn, doc_id)
        notify_others(
            conn, current_user["id"], doc_id,
            f"{current_user['email']}님이 문서에 새 버전을 올렸습니다: {doc['title']} (v{next_no})",
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/documents/{doc_id}", status_code=303)


@router.get("/versions/{version_id}/download")
def download(version_id: int):
    conn = get_conn()
    try:
        ver = conn.execute("SELECT * FROM versions WHERE id = ?", (version_id,)).fetchone()
    finally:
        conn.close()
    if ver is None or not ver["stored_name"]:
        raise HTTPException(404, "파일을 찾을 수 없습니다.")
    path = storage.file_path(ver["stored_name"])
    if not path.exists():
        raise HTTPException(404, "저장된 파일이 없습니다.")
    return FileResponse(path, filename=ver["filename"])


@router.get("/versions/{version_id}/preview")
def preview_file(version_id: int):
    """다운로드 없이 브라우저에서 바로 열리도록 inline으로 서빙 (PDF/이미지용)."""
    conn = get_conn()
    try:
        ver = conn.execute("SELECT * FROM versions WHERE id = ?", (version_id,)).fetchone()
    finally:
        conn.close()
    if ver is None or not ver["stored_name"]:
        raise HTTPException(404, "파일을 찾을 수 없습니다.")
    path = storage.file_path(ver["stored_name"])
    if not path.exists():
        raise HTTPException(404, "저장된 파일이 없습니다.")
    media_type, _ = mimetypes.guess_type(ver["filename"] or "")
    return FileResponse(
        path,
        media_type=media_type or "application/octet-stream",
        filename=ver["filename"],
        content_disposition_type="inline",
    )


@router.post("/documents/{doc_id}/summarize")
def summarize_document(doc_id: int, current_user=Depends(get_current_user)):
    """최신 버전 본문을 요약해 버전에 캐시한다. (Ollama 없으면 추출 요약)"""
    conn = get_conn()
    try:
        latest = conn.execute(
            "SELECT * FROM versions WHERE document_id = ? ORDER BY version_no DESC LIMIT 1",
            (doc_id,),
        ).fetchone()
        if latest is None:
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        if not latest["content_text"].strip():
            raise HTTPException(400, "추출된 본문이 없어 요약할 수 없습니다.")
        summary = summarize(latest["content_text"])
        conn.execute("UPDATE versions SET summary = ? WHERE id = ?", (summary, latest["id"]))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/documents/{doc_id}", status_code=303)


@router.post("/documents/{doc_id}/delete")
def delete(doc_id: int, current_user=Depends(get_current_user)):
    conn = get_conn()
    try:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if doc is None:
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        if current_user["role"] != "admin" and doc["created_by"] != current_user["id"]:
            raise HTTPException(403, "삭제 권한이 없습니다 (작성자 또는 관리자만 삭제할 수 있습니다).")
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        conn.execute("DELETE FROM fts WHERE rowid = ?", (doc_id,))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse("/", status_code=303)
