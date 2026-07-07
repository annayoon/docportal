import shutil

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..auth import get_current_admin
from ..config import DATA_DIR, DB_PATH
from ..db import get_conn
from ..templating import templates

router = APIRouter(prefix="/admin")


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


@router.post("/users/{user_id}/reject")
def reject_user(user_id: int, admin=Depends(get_current_admin)):
    conn = get_conn()
    try:
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
        new_role = "user" if user["role"] == "admin" else "admin"
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse("/admin/users", status_code=303)
