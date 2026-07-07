import hashlib
import hmac
import secrets
import sqlite3

from fastapi import HTTPException, Request

from .config import ALLOWED_EMAIL_DOMAIN

PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, hashhex = stored.split("$", 1)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return hmac.compare_digest(dk.hex(), hashhex)


def is_allowed_email(email: str) -> bool:
    email = email.strip().lower()
    if "@" not in email:
        return False
    return email.rsplit("@", 1)[1] == ALLOWED_EMAIL_DOMAIN


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user_id))
    return token


def destroy_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def user_from_token(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    if not token:
        return None
    return conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?",
        (token,),
    ).fetchone()


def get_current_user(request: Request) -> sqlite3.Row:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "로그인이 필요합니다.")
    return user


def get_current_admin(request: Request) -> sqlite3.Row:
    user = get_current_user(request)
    if user["role"] != "admin":
        raise HTTPException(403, "관리자만 접근할 수 있습니다.")
    return user
