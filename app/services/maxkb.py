"""DocPortal → MaxKB 지식베이스 자동 동기화.

문서가 업로드/수정/복원되면 추출된 본문을 MaxKB 문서로 밀어넣어
RAG 챗봇이 항상 최신 내용으로 답하게 한다. 미설정이면 전부 no-op.
발송은 데몬 스레드 — 실패해도 포털 기능에는 영향 없음.
"""

import json
import logging
import threading
import urllib.error
import urllib.request

from ..config import (
    MAXKB_KB_ID,
    MAXKB_PASSWORD,
    MAXKB_URL,
    MAXKB_USER,
    MAXKB_WORKSPACE,
    maxkb_configured,
)

logger = logging.getLogger(__name__)

_token: str | None = None
_token_lock = threading.Lock()

# MaxKB 단락 길이 제한을 고려한 청크 크기 (문자)
CHUNK_SIZE = 1200


def _login() -> str:
    req = urllib.request.Request(
        f"{MAXKB_URL}/admin/api/user/login",
        data=json.dumps({"username": MAXKB_USER, "password": MAXKB_PASSWORD}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())["data"]["token"]


def _call(method: str, path: str, body=None):
    """토큰 만료(401) 시 한 번 재로그인해서 재시도한다."""
    global _token
    for attempt in (1, 2):
        with _token_lock:
            if _token is None:
                _token = _login()
            token = _token
        req = urllib.request.Request(
            f"{MAXKB_URL}/admin/api{path}",
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 1:
                with _token_lock:
                    _token = None
                continue
            raise
    return None


def _chunk(text: str) -> list[dict]:
    """빈 줄 기준 문단을 CHUNK_SIZE 이내로 뭉친다."""
    paragraphs: list[str] = []
    buf = ""
    for part in text.split("\n\n"):
        part = part.strip()
        if not part:
            continue
        # 한 문단이 청크보다 크면 강제 분할
        while len(part) > CHUNK_SIZE:
            paragraphs.append(part[:CHUNK_SIZE])
            part = part[CHUNK_SIZE:]
        if len(buf) + len(part) + 1 > CHUNK_SIZE:
            if buf:
                paragraphs.append(buf)
            buf = part
        else:
            buf = f"{buf}\n{part}" if buf else part
    if buf:
        paragraphs.append(buf)
    return [{"title": "", "content": p} for p in paragraphs]


def sync_async(doc_id: int) -> None:
    """문서의 최신 버전 본문을 MaxKB에 반영한다 (기존 항목은 교체)."""
    if not maxkb_configured():
        return
    threading.Thread(target=_sync, args=(doc_id,), daemon=True).start()


def delete_async(maxkb_doc_id: str | None) -> None:
    if not maxkb_configured() or not maxkb_doc_id:
        return
    threading.Thread(target=_delete, args=(maxkb_doc_id,), daemon=True).start()


def _sync(doc_id: int) -> None:
    from ..db import get_conn  # 순환 import 방지

    conn = get_conn()
    try:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if doc is None:
            return
        latest = conn.execute(
            "SELECT content_text FROM versions WHERE document_id = ? "
            "ORDER BY version_no DESC LIMIT 1",
            (doc_id,),
        ).fetchone()
        text = (latest["content_text"] if latest else "") or ""
        if not text.strip():
            return  # 추출된 본문이 없으면 챗봇 근거로 쓸 게 없음
        # 교체 방식: 기존 MaxKB 문서 삭제 후 새로 생성
        if doc["maxkb_doc_id"]:
            try:
                _call("DELETE",
                      f"/workspace/{MAXKB_WORKSPACE}/knowledge/{MAXKB_KB_ID}/document/{doc['maxkb_doc_id']}")
            except Exception:
                logger.warning("MaxKB 기존 문서 삭제 실패 (계속 진행)", exc_info=True)
        result = _call(
            "POST",
            f"/workspace/{MAXKB_WORKSPACE}/knowledge/{MAXKB_KB_ID}/document",
            {"name": doc["title"][:100], "paragraphs": _chunk(text)},
        )
        data = result.get("data") if result else None
        new_id = (data[0] if isinstance(data, list) else data or {}).get("id")
        if new_id:
            conn.execute("UPDATE documents SET maxkb_doc_id = ? WHERE id = ?", (new_id, doc_id))
            conn.commit()
            logger.info("MaxKB 동기화 완료: doc %s → %s", doc_id, new_id)
    except Exception:
        logger.warning("MaxKB 동기화 실패: doc %s", doc_id, exc_info=True)
    finally:
        conn.close()


def _delete(maxkb_doc_id: str) -> None:
    try:
        _call("DELETE",
              f"/workspace/{MAXKB_WORKSPACE}/knowledge/{MAXKB_KB_ID}/document/{maxkb_doc_id}")
    except Exception:
        logger.warning("MaxKB 문서 삭제 실패: %s", maxkb_doc_id, exc_info=True)
