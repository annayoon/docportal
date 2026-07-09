"""DocPortal → MaxKB 지식베이스 자동 동기화 (영속 큐 + 단일 워커).

문서가 업로드/수정/복원/삭제되면 작업을 DB 큐(maxkb_sync_queue)에 넣고,
백그라운드 워커 1개가 순서대로 처리한다. 이 구조의 이점:
  - 대량 업로드에도 한 번에 하나씩 처리 → MaxKB·Ollama 과부하 방지
  - 실패 시 지수 백오프로 자동 재시도
  - 서버가 재시작돼도 큐가 DB에 남아 밀린 작업을 이어서 처리
미설정(maxkb_configured=False)이면 전부 no-op.
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
# 재시도 정책
MAX_ATTEMPTS = 8              # 이 횟수 넘게 실패하면 '실패'로 남겨두고 자동 재시도 중단
_BACKOFF_BASE = 15           # 초 — 재시도 간격 = min(base * 2^(attempts-1), cap)
_BACKOFF_CAP = 3600          # 최대 1시간
_IDLE_POLL = 15              # 큐가 비었을 때 재확인 주기(초) — 백오프 대기 건도 여기서 깨움

_wake = threading.Event()
_worker_started = False
_worker_lock = threading.Lock()


# ────────────────────────── MaxKB API 호출 ──────────────────────────

def _login() -> str:
    req = urllib.request.Request(
        f"{MAXKB_URL}/admin/api/user/login",
        data=json.dumps({"username": MAXKB_USER, "password": MAXKB_PASSWORD}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())["data"]["token"]


def _call(method: str, path: str, body=None):
    """토큰 만료(401) 시 한 번 재로그인해서 재시도. 그 외 오류는 그대로 올린다(워커가 재시도)."""
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
            with urllib.request.urlopen(req, timeout=90) as resp:
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
        while len(part) > CHUNK_SIZE:  # 한 문단이 청크보다 크면 강제 분할
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


# ────────────────────────── 큐 적재 (호출부에서 사용) ──────────────────────────

def sync_async(doc_id: int) -> None:
    """문서 동기화 작업을 큐에 넣는다. 같은 문서의 대기 중 sync는 하나로 합친다."""
    if not maxkb_configured():
        return
    from ..db import get_conn

    conn = get_conn()
    try:
        conn.execute("DELETE FROM maxkb_sync_queue WHERE action = 'sync' AND doc_id = ?", (doc_id,))
        conn.execute(
            "INSERT INTO maxkb_sync_queue (action, doc_id) VALUES ('sync', ?)", (doc_id,)
        )
        conn.commit()
    finally:
        conn.close()
    _wake.set()


def delete_async(maxkb_doc_id: str | None) -> None:
    """MaxKB 문서 삭제 작업을 큐에 넣는다."""
    if not maxkb_configured() or not maxkb_doc_id:
        return
    from ..db import get_conn

    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO maxkb_sync_queue (action, maxkb_doc_id) VALUES ('delete', ?)",
            (maxkb_doc_id,),
        )
        conn.commit()
    finally:
        conn.close()
    _wake.set()


def queue_stats() -> dict:
    """관리자 화면용 — 대기/실패 건수."""
    if not maxkb_configured():
        return {"pending": 0, "failed": 0}
    from ..db import get_conn

    conn = get_conn()
    try:
        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM maxkb_sync_queue WHERE attempts < ?", (MAX_ATTEMPTS,)
        ).fetchone()["n"]
        failed = conn.execute(
            "SELECT COUNT(*) AS n FROM maxkb_sync_queue WHERE attempts >= ?", (MAX_ATTEMPTS,)
        ).fetchone()["n"]
    finally:
        conn.close()
    return {"pending": pending, "failed": failed}


# ────────────────────────── 실제 처리 (워커가 호출, 실패 시 예외) ──────────────────────────

def _process_sync(doc_id: int) -> None:
    from ..db import get_conn

    conn = get_conn()
    try:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if doc is None:
            return  # 이미 삭제된 문서 — 할 일 없음(성공 처리)
        latest = conn.execute(
            "SELECT content_text FROM versions WHERE document_id = ? "
            "ORDER BY version_no DESC LIMIT 1",
            (doc_id,),
        ).fetchone()
        text = (latest["content_text"] if latest else "") or ""
        if not text.strip():
            return  # 추출 본문 없음 — 챗봇 근거로 쓸 게 없음(성공 처리)
        # 교체 방식: 기존 MaxKB 문서를 지우고 새로 생성
        if doc["maxkb_doc_id"]:
            try:
                _call("DELETE",
                      f"/workspace/{MAXKB_WORKSPACE}/knowledge/{MAXKB_KB_ID}/document/{doc['maxkb_doc_id']}")
            except urllib.error.HTTPError as e:
                if e.code != 404:  # 이미 없는 건 무시, 그 외 오류는 재시도 유발
                    raise
        result = _call(
            "POST",
            f"/workspace/{MAXKB_WORKSPACE}/knowledge/{MAXKB_KB_ID}/document",
            {"name": doc["title"][:100], "paragraphs": _chunk(text)},
        )
        data = result.get("data") if result else None
        new_id = (data[0] if isinstance(data, list) else data or {}).get("id")
        if not new_id:
            raise RuntimeError(f"MaxKB 문서 생성 응답에 id 없음: {str(result)[:200]}")
        conn.execute("UPDATE documents SET maxkb_doc_id = ? WHERE id = ?", (new_id, doc_id))
        conn.commit()
        logger.info("MaxKB 동기화 완료: doc %s → %s", doc_id, new_id)
    finally:
        conn.close()


def _process_delete(maxkb_doc_id: str) -> None:
    try:
        _call("DELETE",
              f"/workspace/{MAXKB_WORKSPACE}/knowledge/{MAXKB_KB_ID}/document/{maxkb_doc_id}")
    except urllib.error.HTTPError as e:
        if e.code != 404:  # 이미 삭제된 건 성공으로 간주
            raise


# ────────────────────────── 워커 ──────────────────────────

def _backoff_seconds(attempts: int) -> int:
    return min(_BACKOFF_BASE * (2 ** max(0, attempts - 1)), _BACKOFF_CAP)


def _process_one() -> bool:
    """큐에서 처리할 때가 된 작업 하나를 꺼내 처리. 처리했으면 True."""
    from ..db import get_conn

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM maxkb_sync_queue "
            "WHERE attempts < ? AND next_attempt_at <= datetime('now', 'localtime') "
            "ORDER BY id LIMIT 1",
            (MAX_ATTEMPTS,),
        ).fetchone()
        if row is None:
            return False
    finally:
        conn.close()

    try:
        if row["action"] == "sync":
            _process_sync(row["doc_id"])
        else:
            _process_delete(row["maxkb_doc_id"])
    except Exception as e:
        conn = get_conn()
        try:
            attempts = row["attempts"] + 1
            conn.execute(
                "UPDATE maxkb_sync_queue SET attempts = ?, next_attempt_at = "
                "datetime('now', 'localtime', ?), last_error = ? WHERE id = ?",
                (attempts, f"+{_backoff_seconds(attempts)} seconds", str(e)[:300], row["id"]),
            )
            conn.commit()
        finally:
            conn.close()
        if attempts >= MAX_ATTEMPTS:
            logger.error("MaxKB 동기화 %d회 실패 — 자동 재시도 중단 (queue id=%s): %s",
                         attempts, row["id"], e)
        else:
            logger.warning("MaxKB 동기화 실패 (%d회차, %ds 후 재시도): %s",
                           attempts, _backoff_seconds(attempts), e)
        return True

    # 성공 → 큐에서 제거
    conn = get_conn()
    try:
        conn.execute("DELETE FROM maxkb_sync_queue WHERE id = ?", (row["id"],))
        conn.commit()
    finally:
        conn.close()
    return True


def _worker_loop() -> None:
    logger.info("MaxKB 동기화 워커 시작")
    while True:
        try:
            worked = _process_one()
        except Exception:
            logger.exception("MaxKB 워커 루프 오류")
            worked = False
        if not worked:
            # 처리할 게 없으면 대기 (새 작업이 들어오면 _wake로 즉시 깨어남)
            _wake.wait(timeout=_IDLE_POLL)
            _wake.clear()


def start_worker() -> None:
    """앱 시작 시 한 번 호출. 미설정이면 시작하지 않는다."""
    global _worker_started
    if not maxkb_configured():
        return
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
    threading.Thread(target=_worker_loop, name="maxkb-sync", daemon=True).start()
