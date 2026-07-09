import sqlite3

from .config import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  doc_type TEXT NOT NULL DEFAULT 'file',          -- 'file' | 'wiki'
  department TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  version_no INTEGER NOT NULL,
  filename TEXT,                                   -- 원본 파일명 (wiki면 NULL)
  stored_name TEXT,                                -- data/files/ 내 저장 경로 (wiki면 NULL)
  sha256 TEXT,
  size INTEGER,
  content_text TEXT NOT NULL DEFAULT '',           -- 추출된 본문 / 위키 마크다운
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_versions_document ON versions(document_id, version_no);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  department TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT 'user',        -- 'admin' | 'user'
  status TEXT NOT NULL DEFAULT 'pending',   -- 'pending' | 'approved' | 'rejected'
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  message TEXT NOT NULL,
  is_read INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read);

CREATE TABLE IF NOT EXISTS activity_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  action TEXT NOT NULL,                     -- login|upload|version|wiki_create|wiki_edit|delete|download
  document_id INTEGER,                      -- 삭제된 문서 추적을 위해 FK 없이 보관
  detail TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at);

CREATE TABLE IF NOT EXISTS maxkb_sync_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  action TEXT NOT NULL,                     -- 'sync' | 'delete'
  doc_id INTEGER,                           -- sync 대상 문서
  maxkb_doc_id TEXT,                        -- delete 대상 (문서가 이미 삭제됐을 수 있어 별도 보관)
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
  last_error TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_syncq_due ON maxkb_sync_queue(next_attempt_at);

CREATE TABLE IF NOT EXISTS banned_words (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  word TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # 다중 사용자 환경에서 잠금 대기 (WAL은 init_db에서 한 번 설정되면 유지됨)
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def fts_phrase(text: str) -> str:
    """사용자 입력을 FTS5 구문 인젝션 없이 안전한 구절 쿼리로 만든다."""
    return '"' + text.replace('"', '""') + '"'


def init_db() -> None:
    ensure_dirs()
    conn = get_conn()
    try:
        conn.execute("PRAGMA journal_mode = WAL")  # 동시 읽기/쓰기 안정성
        conn.executescript(SCHEMA)
        # trigram 토크나이저: 한국어 부분 문자열 검색 지원 (SQLite 3.34+)
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5("
                "title, content, tags, keywords, tokenize='trigram')"
            )
        except sqlite3.OperationalError:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5("
                "title, content, tags, keywords, tokenize='unicode61')"
            )
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """기존 DB에 새 컬럼을 추가한다 (마이그레이션 도구 없이 직접 처리)."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
    if "created_by" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN created_by INTEGER REFERENCES users(id)")
    user_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "email_verified" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")
        # 기존 가입자는 인증 절차 없이 만들어졌으므로 인증된 것으로 간주
        conn.execute("UPDATE users SET email_verified = 1")
    if "verify_token" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN verify_token TEXT")
    if "maxkb_doc_id" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN maxkb_doc_id TEXT")
    if "sensitive_flags" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN sensitive_flags TEXT NOT NULL DEFAULT ''")
    version_cols = {row["name"] for row in conn.execute("PRAGMA table_info(versions)")}
    if "summary" not in version_cols:
        conn.execute("ALTER TABLE versions ADD COLUMN summary TEXT")
    if "keywords" not in version_cols:
        conn.execute("ALTER TABLE versions ADD COLUMN keywords TEXT")
    # FTS에 keywords 컬럼이 없던 기존 DB는 인덱스를 재생성한다
    fts_cols = {row["name"] for row in conn.execute("PRAGMA table_info(fts)")}
    if "keywords" not in fts_cols:
        conn.execute("DROP TABLE fts")
        conn.execute(
            "CREATE VIRTUAL TABLE fts USING fts5("
            "title, content, tags, keywords, tokenize='trigram')"
        )
        for row in conn.execute("SELECT id FROM documents").fetchall():
            reindex_document(conn, row["id"])


def reindex_document(conn: sqlite3.Connection, doc_id: int) -> None:
    """문서의 최신 버전 내용으로 검색 인덱스를 갱신한다. fts.rowid == documents.id"""
    doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    conn.execute("DELETE FROM fts WHERE rowid = ?", (doc_id,))
    if doc is None:
        return
    latest = conn.execute(
        "SELECT content_text, keywords FROM versions WHERE document_id = ? "
        "ORDER BY version_no DESC LIMIT 1",
        (doc_id,),
    ).fetchone()
    content = latest["content_text"] if latest else ""
    keywords = (latest["keywords"] or "") if latest else ""
    conn.execute(
        "INSERT INTO fts(rowid, title, content, tags, keywords) VALUES (?, ?, ?, ?, ?)",
        (doc_id, doc["title"], content, doc["tags"], keywords),
    )


def get_banned_words(conn: sqlite3.Connection) -> list[str]:
    return [r["word"] for r in conn.execute("SELECT word FROM banned_words ORDER BY word")]


def notify_admins(conn: sqlite3.Connection, doc_id: int, message: str) -> None:
    """관리자 전원에게만 인앱 알림을 남긴다 (민감정보 경고용)."""
    admins = conn.execute(
        "SELECT id FROM users WHERE role = 'admin' AND status = 'approved'"
    ).fetchall()
    conn.executemany(
        "INSERT INTO notifications (user_id, document_id, message) VALUES (?, ?, ?)",
        [(a["id"], doc_id, message) for a in admins],
    )


def log_activity(
    conn: sqlite3.Connection, user_id: int | None, action: str,
    doc_id: int | None = None, detail: str = "",
) -> None:
    """감사용 활동 기록. 호출부 트랜잭션에 편승한다 (commit은 호출부 책임)."""
    conn.execute(
        "INSERT INTO activity_log (user_id, action, document_id, detail) VALUES (?, ?, ?, ?)",
        (user_id, action, doc_id, detail),
    )


def notify_others(conn: sqlite3.Connection, actor_id: int, doc_id: int, message: str) -> None:
    """업로드/편집 행위자 본인을 제외한 모든 승인된 사용자에게 알림을 남긴다.

    웹훅(DOCPORTAL_WEBHOOK_URL)이 설정돼 있으면 메신저로도 푸시한다.
    """
    from .config import BASE_URL
    from .services.webhook import push

    recipients = conn.execute(
        "SELECT id FROM users WHERE status = 'approved' AND id != ?", (actor_id,)
    ).fetchall()
    conn.executemany(
        "INSERT INTO notifications (user_id, document_id, message) VALUES (?, ?, ?)",
        [(r["id"], doc_id, message) for r in recipients],
    )
    push(f"{message}\n{BASE_URL}/documents/{doc_id}")
