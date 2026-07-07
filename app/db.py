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
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    ensure_dirs()
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        # trigram 토크나이저: 한국어 부분 문자열 검색 지원 (SQLite 3.34+)
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5("
                "title, content, tags, tokenize='trigram')"
            )
        except sqlite3.OperationalError:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5("
                "title, content, tags, tokenize='unicode61')"
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


def reindex_document(conn: sqlite3.Connection, doc_id: int) -> None:
    """문서의 최신 버전 내용으로 검색 인덱스를 갱신한다. fts.rowid == documents.id"""
    doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    conn.execute("DELETE FROM fts WHERE rowid = ?", (doc_id,))
    if doc is None:
        return
    latest = conn.execute(
        "SELECT content_text FROM versions WHERE document_id = ? "
        "ORDER BY version_no DESC LIMIT 1",
        (doc_id,),
    ).fetchone()
    content = latest["content_text"] if latest else ""
    conn.execute(
        "INSERT INTO fts(rowid, title, content, tags) VALUES (?, ?, ?, ?)",
        (doc_id, doc["title"], content, doc["tags"]),
    )


def notify_others(conn: sqlite3.Connection, actor_id: int, doc_id: int, message: str) -> None:
    """업로드/편집 행위자 본인을 제외한 모든 승인된 사용자에게 알림을 남긴다."""
    recipients = conn.execute(
        "SELECT id FROM users WHERE status = 'approved' AND id != ?", (actor_id,)
    ).fetchall()
    conn.executemany(
        "INSERT INTO notifications (user_id, document_id, message) VALUES (?, ?, ?)",
        [(r["id"], doc_id, message) for r in recipients],
    )
