from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse

from .auth import user_from_token
from .config import SESSION_COOKIE
from .db import get_conn, init_db
from .routers import admin, auth, chatproxy, documents, notifications, search, wiki
from .services import maxkb

app = FastAPI(title="DocPortal — 전사 문서 포털")

init_db()
# MaxKB 동기화 워커 시작 (미설정이면 no-op). 서버 재시작 시 밀린 큐를 이어서 처리.
maxkb.start_worker()

PUBLIC_PATHS = {"/login", "/signup", "/verify"}
PUBLIC_PREFIXES = ("/static",)


class AuthMiddleware(BaseHTTPMiddleware):
    """모든 페이지는 로그인이 필요하다 — 부서 구분 없이 로그인 여부만 검사한다."""

    async def dispatch(self, request: Request, call_next):
        token = request.cookies.get(SESSION_COOKIE)
        user = None
        unread_count = 0
        if token:
            conn = get_conn()
            try:
                user = user_from_token(conn, token)
                if user is not None:
                    unread_count = conn.execute(
                        "SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND is_read = 0",
                        (user["id"],),
                    ).fetchone()["n"]
            finally:
                conn.close()
        request.state.user = user
        request.state.unread_count = unread_count

        path = request.url.path
        is_public = path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)
        if not is_public and user is None:
            next_q = f"?next={path}" if request.method == "GET" else ""
            return RedirectResponse(f"/login{next_q}", status_code=303)
        response = await call_next(request)
        # 브라우저의 콘텐츠 타입 추측(스니핑) 금지 — 업로드 파일 XSS 방어 보강
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response


app.add_middleware(AuthMiddleware)

app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).resolve().parent / "static")),
    name="static",
)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(documents.router)
app.include_router(notifications.router)
app.include_router(search.router)
app.include_router(wiki.router)
app.include_router(chatproxy.router)
