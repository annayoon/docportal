"""문서 업로드/변경 시 메신저 푸시 (Google Chat/Slack 수신 웹훅 호환).

DOCPORTAL_WEBHOOK_URL 미설정이면 no-op. 발송은 데몬 스레드에서 처리해
요청을 지연시키지 않고, 실패해도 본 기능(업로드/알림)에 영향을 주지 않는다.
"""

import json
import logging
import threading
import urllib.request

from ..config import WEBHOOK_URL

logger = logging.getLogger(__name__)


def push(message: str) -> None:
    if not WEBHOOK_URL:
        return
    threading.Thread(target=_send, args=(message,), daemon=True).start()


def _send(message: str) -> None:
    try:
        req = urllib.request.Request(
            WEBHOOK_URL,
            data=json.dumps({"text": message}).encode(),
            headers={"Content-Type": "application/json; charset=UTF-8"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception:
        logger.warning("웹훅 발송 실패", exc_info=True)
