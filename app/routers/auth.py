import logging
import secrets

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from ..auth import (
    create_session, destroy_session, hash_password, is_allowed_email,
    purge_expired_sessions, verify_password,
)
from ..config import ALLOWED_EMAIL_DOMAIN, SECURE_COOKIES, SESSION_COOKIE, smtp_configured
from ..db import get_conn, log_activity
from ..services.mailer import send_verification_email
from ..templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/signup")
def signup_form(request: Request):
    if getattr(request.state, "user", None):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "signup.html",
        {"domain": ALLOWED_EMAIL_DOMAIN, "error": None, "verify_enabled": smtp_configured()},
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
    elif not 8 <= len(password) <= 128:
        error = "비밀번호는 8자 이상 128자 이하여야 합니다."

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
                {"domain": ALLOWED_EMAIL_DOMAIN, "error": error, "verify_enabled": smtp_configured()},
                status_code=400,
            )

        is_first = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
        role = "admin" if is_first else "user"
        status = "approved" if is_first else "pending"
        need_verify = smtp_configured()
        token = secrets.token_urlsafe(32) if need_verify else None
        conn.execute(
            "INSERT INTO users (email, password_hash, department, role, status, email_verified, verify_token) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                email, hash_password(password), department.strip(), role, status,
                0 if need_verify else 1, token,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    mail_error = None
    if need_verify:
        try:
            send_verification_email(email, token)
        except Exception:
            logger.exception("인증 메일 발송 실패: %s", email)
            mail_error = (
                "가입은 접수됐지만 인증 메일 발송에 실패했습니다. "
                "관리자에게 문의하면 수동으로 인증 처리할 수 있습니다."
            )

    if mail_error:
        notice = None
    elif need_verify:
        notice = (
            f"인증 메일을 {email}(으)로 보냈습니다. 메일의 링크를 열어 인증을 완료해 주세요."
            + ("" if is_first else " 이후 관리자 승인이 끝나면 로그인할 수 있습니다.")
        )
    elif is_first:
        notice = "관리자 계정이 생성되었습니다. 로그인해 주세요."
    else:
        notice = "가입 신청이 접수되었습니다. 관리자 승인 후 로그인할 수 있습니다."
    return templates.TemplateResponse(
        request, "login.html", {"error": mail_error, "notice": notice}
    )


@router.get("/verify")
def verify_email(request: Request, token: str = ""):
    error = None
    notice = None
    conn = get_conn()
    try:
        user = (
            conn.execute("SELECT * FROM users WHERE verify_token = ?", (token,)).fetchone()
            if token
            else None
        )
        if user is None:
            error = "유효하지 않거나 이미 사용된 인증 링크입니다."
        else:
            conn.execute(
                "UPDATE users SET email_verified = 1, verify_token = NULL WHERE id = ?",
                (user["id"],),
            )
            conn.commit()
            notice = (
                "이메일 인증이 완료되었습니다. 로그인해 주세요."
                if user["status"] == "approved"
                else "이메일 인증이 완료되었습니다. 관리자 승인이 끝나면 로그인할 수 있습니다."
            )
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "login.html", {"error": error, "notice": notice}, status_code=400 if error else 200
    )


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
        if smtp_configured() and not user["email_verified"]:
            return templates.TemplateResponse(
                request,
                "login.html",
                {"error": "이메일 인증이 완료되지 않았습니다. 받은 메일함에서 인증 링크를 확인해 주세요.", "notice": None},
                status_code=403,
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
        purge_expired_sessions(conn)
        log_activity(conn, user["id"], "login")
        conn.commit()
    finally:
        conn.close()

    # 오픈 리다이렉트 방지: 사이트 내부 경로만 허용
    if not next.startswith("/") or next.startswith("//"):
        next = "/"
    resp = RedirectResponse(next or "/", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax",
        secure=SECURE_COOKIES, max_age=60 * 60 * 24 * 30,
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
