from fastapi import APIRouter, Depends, Request

from ..auth import get_current_user
from ..db import get_conn
from ..templating import templates

router = APIRouter()


@router.get("/notifications")
def list_notifications(request: Request, current_user=Depends(get_current_user)):
    conn = get_conn()
    try:
        notifications = conn.execute(
            "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 100",
            (current_user["id"],),
        ).fetchall()
        conn.execute(
            "UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0",
            (current_user["id"],),
        )
        conn.commit()
        return templates.TemplateResponse(
            request, "notifications.html", {"notifications": notifications}
        )
    finally:
        conn.close()
