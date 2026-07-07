from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..auth import get_current_admin
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
