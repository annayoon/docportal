from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..auth import get_current_user
from ..db import get_conn, log_activity, notify_others, reindex_document
from ..services.summarizer import analyze_version
from ..templating import templates

router = APIRouter(prefix="/wiki")


@router.get("/new")
def new_page(request: Request):
    return templates.TemplateResponse(request, "wiki_edit.html", {"doc": None, "content": ""})


@router.post("/new")
def create_page(
    background_tasks: BackgroundTasks,
    title: str = Form(...),
    department: str = Form(""),
    tags: str = Form(""),
    content: str = Form(""),
    current_user=Depends(get_current_user),
):
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO documents (title, doc_type, department, tags, created_by) "
            "VALUES (?, 'wiki', ?, ?, ?)",
            (title.strip(), department.strip(), tags.strip(), current_user["id"]),
        )
        doc_id = cur.lastrowid
        version_id = conn.execute(
            "INSERT INTO versions (document_id, version_no, content_text, note) "
            "VALUES (?, 1, ?, '최초 작성')",
            (doc_id, content),
        ).lastrowid
        reindex_document(conn, doc_id)
        log_activity(conn, current_user["id"], "wiki_create", doc_id, title.strip())
        notify_others(
            conn, current_user["id"], doc_id,
            f"{current_user['email']}님이 새 위키 문서를 작성했습니다: {title.strip()}",
        )
        conn.commit()
    finally:
        conn.close()
    background_tasks.add_task(analyze_version, version_id)
    return RedirectResponse(f"/documents/{doc_id}", status_code=303)


@router.get("/{doc_id}/edit")
def edit_page(request: Request, doc_id: int):
    conn = get_conn()
    try:
        doc = conn.execute(
            "SELECT * FROM documents WHERE id = ? AND doc_type = 'wiki'", (doc_id,)
        ).fetchone()
        if doc is None:
            raise HTTPException(404, "위키 문서를 찾을 수 없습니다.")
        latest = conn.execute(
            "SELECT content_text FROM versions WHERE document_id = ? ORDER BY version_no DESC LIMIT 1",
            (doc_id,),
        ).fetchone()
        return templates.TemplateResponse(
            request,
            "wiki_edit.html",
            {"doc": doc, "content": latest["content_text"] if latest else ""},
        )
    finally:
        conn.close()


@router.post("/{doc_id}/edit")
def save_page(
    doc_id: int,
    background_tasks: BackgroundTasks,
    title: str = Form(...),
    department: str = Form(""),
    tags: str = Form(""),
    content: str = Form(""),
    note: str = Form(""),
    current_user=Depends(get_current_user),
):
    conn = get_conn()
    try:
        doc = conn.execute(
            "SELECT * FROM documents WHERE id = ? AND doc_type = 'wiki'", (doc_id,)
        ).fetchone()
        if doc is None:
            raise HTTPException(404, "위키 문서를 찾을 수 없습니다.")
        next_no = conn.execute(
            "SELECT COALESCE(MAX(version_no), 0) + 1 AS n FROM versions WHERE document_id = ?",
            (doc_id,),
        ).fetchone()["n"]
        conn.execute(
            "UPDATE documents SET title = ?, department = ?, tags = ?, "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            (title.strip(), department.strip(), tags.strip(), doc_id),
        )
        version_id = conn.execute(
            "INSERT INTO versions (document_id, version_no, content_text, note) VALUES (?, ?, ?, ?)",
            (doc_id, next_no, content, note.strip()),
        ).lastrowid
        reindex_document(conn, doc_id)
        log_activity(conn, current_user["id"], "wiki_edit", doc_id, f"{title.strip()} (v{next_no})")
        notify_others(
            conn, current_user["id"], doc_id,
            f"{current_user['email']}님이 위키 문서를 수정했습니다: {title.strip()}",
        )
        conn.commit()
    finally:
        conn.close()
    background_tasks.add_task(analyze_version, version_id)
    return RedirectResponse(f"/documents/{doc_id}", status_code=303)
