import shutil

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..auth import get_current_admin
from ..config import DATA_DIR, DB_PATH, maxkb_configured
from ..db import get_conn, log_activity
from ..services import maxkb
from ..templating import templates

router = APIRouter(prefix="/admin")

# 활동 로그 액션 → 한글 라벨
ACTION_LABELS = {
    "login": "로그인",
    "upload": "문서 업로드",
    "version": "새 버전",
    "wiki_create": "위키 작성",
    "wiki_edit": "위키 수정",
    "delete": "문서 삭제",
    "download": "다운로드",
    "meta_edit": "정보 수정",
    "revert": "버전 복원",
}


@router.get("/documents")
def manage_documents(request: Request, dept: str = "", q: str = "", admin=Depends(get_current_admin)):
    conn = get_conn()
    try:
        sql = (
            "SELECT d.*, u.email AS creator, v.version_no, v.size, "
            "  (SELECT COALESCE(SUM(size), 0) FROM versions WHERE document_id = d.id) AS total_size "
            "FROM documents d "
            "LEFT JOIN users u ON u.id = d.created_by "
            "JOIN versions v ON v.document_id = d.id AND v.version_no = "
            "  (SELECT MAX(version_no) FROM versions WHERE document_id = d.id) "
            "WHERE 1=1 "
        )
        params: list = []
        if dept:
            sql += "AND d.department = ? "
            params.append(dept)
        if q.strip():
            sql += "AND d.title LIKE ? "
            params.append(f"%{q.strip()}%")
        sql += "ORDER BY d.updated_at DESC LIMIT 500"
        docs = conn.execute(sql, params).fetchall()
        departments = [
            r["department"]
            for r in conn.execute(
                "SELECT DISTINCT department FROM documents WHERE department != '' ORDER BY department"
            ).fetchall()
        ]
        return templates.TemplateResponse(
            request,
            "admin_documents.html",
            {"docs": docs, "departments": departments, "dept": dept, "q": q.strip(),
             "maxkb_on": maxkb_configured()},
        )
    finally:
        conn.close()


@router.post("/documents/bulk-delete")
def bulk_delete(request: Request, doc_ids: list[int] = Form(...), admin=Depends(get_current_admin)):
    conn = get_conn()
    try:
        maxkb_ids = []
        for doc_id in doc_ids:
            doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if doc is None:
                continue
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            conn.execute("DELETE FROM fts WHERE rowid = ?", (doc_id,))
            log_activity(conn, admin["id"], "delete", doc_id, f"{doc['title']} (일괄 삭제)")
            maxkb_ids.append(doc["maxkb_doc_id"])
        conn.commit()
    finally:
        conn.close()
    for mid in maxkb_ids:
        maxkb.delete_async(mid)
    return RedirectResponse("/admin/documents", status_code=303)


@router.post("/maxkb-sync")
def maxkb_full_sync(admin=Depends(get_current_admin)):
    """전체 문서를 MaxKB 지식베이스로 동기화 (초기 적재/재구축용)."""
    if not maxkb_configured():
        raise HTTPException(503, "MaxKB 연동이 설정되어 있지 않습니다.")
    conn = get_conn()
    try:
        doc_ids = [r["id"] for r in conn.execute("SELECT id FROM documents").fetchall()]
    finally:
        conn.close()
    for doc_id in doc_ids:
        maxkb.sync_async(doc_id)
    return RedirectResponse("/admin/documents", status_code=303)


@router.get("/activity")
def activity(request: Request, action: str = "", admin=Depends(get_current_admin)):
    conn = get_conn()
    try:
        sql = (
            "SELECT a.*, u.email, d.title AS doc_title "
            "FROM activity_log a "
            "LEFT JOIN users u ON u.id = a.user_id "
            "LEFT JOIN documents d ON d.id = a.document_id "
        )
        params: list = []
        if action in ACTION_LABELS:
            sql += "WHERE a.action = ? "
            params.append(action)
        sql += "ORDER BY a.id DESC LIMIT 200"
        rows = conn.execute(sql, params).fetchall()
        return templates.TemplateResponse(
            request,
            "admin_activity.html",
            {"rows": rows, "action": action, "labels": ACTION_LABELS},
        )
    finally:
        conn.close()


@router.get("/users")
def list_users(request: Request, admin=Depends(get_current_admin)):
    conn = get_conn()
    try:
        users = conn.execute(
            "SELECT * FROM users ORDER BY (status = 'pending') DESC, created_at DESC"
        ).fetchall()
        return templates.TemplateResponse(request, "admin_users.html", {"users": users})
    finally:
        conn.close()


@router.get("/storage")
def storage_stats(request: Request, admin=Depends(get_current_admin)):
    conn = get_conn()
    try:
        # 논리 용량: 모든 버전 파일 크기의 합 (중복 포함)
        logical = conn.execute(
            "SELECT COALESCE(SUM(size), 0) AS n FROM versions WHERE stored_name IS NOT NULL"
        ).fetchone()["n"]
        # 실제 용량: 해시 기준 유니크 파일만 (중복 제거 반영)
        physical = conn.execute(
            "SELECT COALESCE(SUM(size), 0) AS n FROM "
            "(SELECT DISTINCT sha256, size FROM versions WHERE sha256 IS NOT NULL)"
        ).fetchone()["n"]
        totals = conn.execute(
            "SELECT COUNT(DISTINCT d.id) AS docs, COUNT(v.id) AS versions "
            "FROM documents d JOIN versions v ON v.document_id = d.id"
        ).fetchone()
        by_dept = conn.execute(
            "SELECT COALESCE(NULLIF(d.department, ''), '(미지정)') AS department, "
            "  COUNT(DISTINCT d.id) AS docs, COUNT(v.id) AS versions, "
            "  COALESCE(SUM(v.size), 0) AS size "
            "FROM documents d JOIN versions v ON v.document_id = d.id "
            "GROUP BY 1 ORDER BY size DESC"
        ).fetchall()
        # 최근 14일 업로드 추이 (버전 등록 기준 — 위키 수정 포함)
        daily_rows = {
            r["d"]: r["n"]
            for r in conn.execute(
                "SELECT date(created_at) AS d, COUNT(*) AS n FROM versions "
                "WHERE created_at >= date('now', 'localtime', '-13 days') GROUP BY 1"
            ).fetchall()
        }
        from datetime import date, timedelta

        today = date.today()
        trend = [
            {
                "day": (today - timedelta(days=offset)).strftime("%m-%d"),
                "count": daily_rows.get((today - timedelta(days=offset)).isoformat(), 0),
            }
            for offset in range(13, -1, -1)
        ]
        trend_max = max((t["count"] for t in trend), default=0) or 1
        # 최근 30일 최다 다운로드 문서 (활동 로그 기준)
        top_downloads = conn.execute(
            "SELECT a.document_id, COALESCE(d.title, '(삭제된 문서)') AS title, COUNT(*) AS n "
            "FROM activity_log a LEFT JOIN documents d ON d.id = a.document_id "
            "WHERE a.action = 'download' AND a.created_at >= date('now', 'localtime', '-30 days') "
            "GROUP BY a.document_id ORDER BY n DESC LIMIT 5"
        ).fetchall()
    finally:
        conn.close()
    db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    disk = shutil.disk_usage(DATA_DIR)
    return templates.TemplateResponse(
        request,
        "admin_storage.html",
        {
            "logical": logical, "physical": physical, "saved": logical - physical,
            "db_size": db_size, "totals": totals, "by_dept": by_dept,
            "disk_total": disk.total, "disk_free": disk.free,
            "max_size": by_dept[0]["size"] if by_dept else 0,
            "trend": trend, "trend_max": trend_max, "top_downloads": top_downloads,
        },
    )


@router.post("/users/{user_id}/approve")
def approve_user(user_id: int, admin=Depends(get_current_admin)):
    conn = get_conn()
    try:
        conn.execute("UPDATE users SET status = 'approved' WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse("/admin/users", status_code=303)


def _is_last_active_admin(conn, user) -> bool:
    if user is None or user["role"] != "admin" or user["status"] != "approved":
        return False
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND status = 'approved'"
    ).fetchone()["n"]
    return n <= 1


@router.post("/users/{user_id}/reject")
def reject_user(user_id: int, admin=Depends(get_current_admin)):
    conn = get_conn()
    try:
        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if _is_last_active_admin(conn, target):
            raise HTTPException(400, "마지막 관리자는 거부할 수 없습니다.")
        conn.execute("UPDATE users SET status = 'rejected' WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/verify")
def verify_user(user_id: int, admin=Depends(get_current_admin)):
    """인증 메일이 유실된 경우 관리자가 수동으로 인증 처리한다."""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE users SET email_verified = 1, verify_token = NULL WHERE id = ?", (user_id,)
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/toggle-admin")
def toggle_admin(user_id: int, admin=Depends(get_current_admin)):
    conn = get_conn()
    try:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            raise HTTPException(404, "사용자를 찾을 수 없습니다.")
        if user["role"] == "admin" and _is_last_active_admin(conn, user):
            raise HTTPException(400, "마지막 관리자는 해제할 수 없습니다.")
        new_role = "user" if user["role"] == "admin" else "admin"
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse("/admin/users", status_code=303)
