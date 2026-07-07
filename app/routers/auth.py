from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from ..auth import create_session, destroy_session, hash_password, is_allowed_email, verify_password
from ..config import ALLOWED_EMAIL_DOMAIN, SESSION_COOKIE
from ..db import get_conn
from ..templating import templates

router = APIRouter()


@router.get("/signup")
def signup_form(request: Request):
    if getattr(request.state, "user", None):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request, "signup.html", {"domain": ALLOWED_EMAIL_DOMAIN, "error": None}
    )


@router.post("/signup")
def signup(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    department: str = Form(""),
):
    email = email.strip().lower()
    error = None
    if not is_allowed_email(email):
        error = f"회사 이메일(@{ALLOWED_EMAIL_DOMAIN})만 가입할 수 있습니다."
    elif len(password) < 8:
        error = "비밀번호는 8자 이상이어야 합니다."

    conn = get_conn()
    try:
        if error is None and conn.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ).fetchone():
            error = "이미 가입된 이메일입니다."
        if error:
            return templates.TemplateResponse(
                request,
                "signup.html",
                {"domain": ALLOWED_EMAIL_DOMAIN, "error": error},
                status_code=400,
            )

        is_first = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
        role = "admin" if is_first else "user"
        status = "approved" if is_first else "pending"
        conn.execute(
            "INSERT INTO users (email, password_hash, department, role, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (email, hash_password(password), department.strip(), role, status),
        )
        conn.commit()
    finally:
        conn.close()

    notice = (
        "관리자 계정이 생성되었습니다. 로그인해 주세요."
        if is_first
        else "가입 신청이 접수되었습니다. 관리자 승인 후 로그인할 수 있습니다."
    )
    return templates.TemplateResponse(request, "login.html", {"error": None, "notice": notice})


@router.get("/login")
def login_form(request: Request, next: str = "/"):
    if getattr(request.state, "user", None):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "notice": None, "next": next}
    )


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    email = email.strip().lower()
    conn = get_conn()
    try:
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user is None or not verify_password(password, user["password_hash"]):
            return templates.TemplateResponse(
                request,
                "login.html",
                {"error": "이메일 또는 비밀번호가 올바르지 않습니다.", "notice": None},
                status_code=400,
            )
        if user["status"] == "pending":
            return templates.TemplateResponse(
                request,
                "login.html",
                {"error": "관리자 승인 대기 중입니다.", "notice": None},
                status_code=403,
            )
        if user["status"] == "rejected":
            return templates.TemplateResponse(
                request,
                "login.html",
                {"error": "가입이 거부된 계정입니다. 관리자에게 문의하세요.", "notice": None},
                status_code=403,
            )
        token = create_session(conn, user["id"])
        conn.commit()
    finally:
        conn.close()

    resp = RedirectResponse(next or "/", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30
    )
    return resp


@router.post("/logout")
def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    conn = get_conn()
    try:
        if token:
            destroy_session(conn, token)
            conn.commit()
    finally:
        conn.close()
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp
