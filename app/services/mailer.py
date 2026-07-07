"""가입 인증 메일 발송. SMTP 미설정이면 호출하지 않는다 (config.smtp_configured 확인)."""

import smtplib
from email.message import EmailMessage

from ..config import (
    BASE_URL,
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_STARTTLS,
    SMTP_USER,
)


def send_verification_email(to_email: str, token: str) -> None:
    """실패 시 예외를 그대로 올린다 — 호출부에서 사용자에게 안내."""
    link = f"{BASE_URL}/verify?token={token}"
    msg = EmailMessage()
    msg["Subject"] = "[DocPortal] 이메일 인증을 완료해 주세요"
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg.set_content(
        "DocPortal 가입 신청이 접수되었습니다.\n\n"
        "아래 링크를 열어 이메일 인증을 완료해 주세요:\n"
        f"{link}\n\n"
        "인증 후 관리자 승인이 완료되면 로그인할 수 있습니다.\n"
        "본인이 신청하지 않았다면 이 메일을 무시하세요."
    )
    _send(msg)


def _send(msg: EmailMessage) -> None:
    if SMTP_PORT == 465:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            _login_and_send(server, msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.ehlo()
            if SMTP_STARTTLS:
                server.starttls()
                server.ehlo()
            _login_and_send(server, msg)


def _login_and_send(server: smtplib.SMTP, msg: EmailMessage) -> None:
    if SMTP_PASSWORD and server.has_extn("auth"):
        server.login(SMTP_USER, SMTP_PASSWORD)
    server.send_message(msg)
