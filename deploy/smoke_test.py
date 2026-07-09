#!/usr/bin/env python3
"""DocPortal 민감정보/금칙어 스모크 테스트 — 운영 서버에서 실행.

실행 (서버):
  sudo /opt/docportal/.venv/bin/python /opt/docportal/deploy/smoke_test.py

하는 일:
  1) 임시 테스트 계정 생성 → 로그인
  2) 주민번호·전화번호가 든 문서 업로드 → 마스킹/뱃지/관리자알림 확인
  3) 원본 민감값 검색 차단 + 원본 다운로드 보존 확인
  4) 금칙어 등록 → 제목/위키 차단 확인
  5) 테스트 문서·계정·금칙어·알림 전부 자체 정리

환경변수로 대상 변경 가능: SMOKE_BASE(기본 http://127.0.0.1:8001),
SMOKE_DB(기본 /var/lib/docportal/docportal.db)
"""

import http.cookiejar
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
import uuid

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8001").rstrip("/")
DB = os.environ.get("SMOKE_DB", "/var/lib/docportal/docportal.db")
APP_DIR = os.environ.get("SMOKE_APP", "/opt/docportal")
TEST_EMAIL = "smoke-test@atto-research.com"
TEST_PW = "smoketest12345"
BANNED_WORD = f"금칙어시험{uuid.uuid4().hex[:6]}"  # 실제 문서와 충돌 없는 유일 단어

sys.path.insert(0, APP_DIR)

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
opener.addheaders = []

passed, failed = [], []
created_docs: list[int] = []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


def post_form(path: str, fields: dict):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(BASE + path, data=data)
    try:
        resp = opener.open(req, timeout=30)
        # urllib이 303 redirect를 자동 추적하므로 최종 URL을 반환
        return resp.status, resp.url, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, "", e.read().decode(errors="replace")


def post_multipart(path: str, fields: dict, filename: str, content: bytes):
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; "
             f"filename=\"{filename}\"\r\nContent-Type: text/plain\r\n\r\n").encode()
    body += content + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + path, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        resp = opener.open(req, timeout=60)
        return resp.status, resp.url, ""
    except urllib.error.HTTPError as e:
        return e.code, "", e.read().decode(errors="replace")


def get(path: str) -> str:
    return opener.open(BASE + path, timeout=30).read().decode(errors="replace")


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def main():
    print(f"대상: {BASE}  /  DB: {DB}\n")

    # ── 준비: 테스트 계정 ──
    from app.auth import hash_password  # 서버 코드의 해시 함수 재사용
    conn = db()
    conn.execute("DELETE FROM users WHERE email = ?", (TEST_EMAIL,))
    # admin 역할로 생성 — 관리자 알림 검증이 관리자 0명 환경에서도 동작하도록
    conn.execute(
        "INSERT INTO users (email, password_hash, role, status, email_verified) "
        "VALUES (?, ?, 'admin', 'approved', 1)", (TEST_EMAIL, hash_password(TEST_PW)))
    conn.commit(); conn.close()
    status, loc, _ = post_form("/login", {"email": TEST_EMAIL, "password": TEST_PW, "next": "/"})
    check("0. 테스트 계정 로그인", status in (200, 303), f"status={status}")

    # ── 1) 민감정보 문서 업로드 → 마스킹 확인 ──
    secret = "인사기록: 홍길동 850101-1234567 / 연락처 010-9876-5432"
    status, url, _ = post_multipart("/upload", {"title": "스모크-민감문서"}, "smoke_pii.txt", secret.encode())
    doc_id = int(url.rsplit("/", 1)[-1]) if url else 0
    created_docs.append(doc_id)
    check("1a. 민감정보 문서 업로드", status == 200 and doc_id > 0, f"status={status}")

    conn = db()
    row = conn.execute("SELECT content_text FROM versions WHERE document_id = ?", (doc_id,)).fetchone()
    flags = conn.execute("SELECT sensitive_flags FROM documents WHERE id = ?", (doc_id,)).fetchone()
    notif = conn.execute("SELECT COUNT(*) AS n FROM notifications WHERE message LIKE '%민감정보%' "
                         "AND document_id = ?", (doc_id,)).fetchone()["n"]
    conn.close()
    ct = row["content_text"] if row else ""
    check("1b. 인덱스 텍스트에서 주민번호 마스킹", "1234567" not in ct and "850101-1******" in ct)
    check("1c. 전화번호 마스킹", "9876" not in ct.split("850101")[0] and "010******5432" in ct)
    check("1d. 문서에 민감 플래그 기록", bool(flags and "주민등록번호" in flags["sensitive_flags"]))
    check("1e. 관리자 인앱 알림 발송", notif >= 1)

    # ── 2) 원본값 검색 차단 / 원본 다운로드 보존 ──
    body = get("/search?q=" + urllib.parse.quote("850101-1234567"))
    check("2a. 원본 주민번호로 검색 불가", f"/documents/{doc_id}" not in body)
    conn = db()
    vid = conn.execute("SELECT id FROM versions WHERE document_id = ?", (doc_id,)).fetchone()["id"]
    conn.close()
    dl = get(f"/versions/{vid}/download")
    check("2b. 원본 파일 다운로드는 마스킹 없음", "850101-1234567" in dl)

    # ── 3) 금칙어 ──
    conn = db()
    conn.execute("INSERT OR IGNORE INTO banned_words (word) VALUES (?)", (BANNED_WORD,))
    conn.commit(); conn.close()
    status, _, _ = post_multipart("/upload", {"title": f"제목에 {BANNED_WORD} 포함"}, "x.txt", b"hello")
    check("3a. 금칙어 제목 업로드 차단(400)", status == 400, f"status={status}")
    status, _, _ = post_form("/wiki/new", {"title": "정상제목", "content": f"본문에 {BANNED_WORD}"})
    check("3b. 금칙어 위키 본문 차단(400)", status == 400, f"status={status}")

    # ── 4) 위키 민감정보 마스킹 ──
    status, final_url, _ = post_form("/wiki/new", {"title": "스모크-위키", "content": "문의: 010-1111-2222"})
    wdoc = int(final_url.rsplit("/", 1)[-1]) if "/documents/" in final_url else 0
    created_docs.append(wdoc)
    conn = db()
    wct = conn.execute("SELECT content_text FROM versions WHERE document_id = ?", (wdoc,)).fetchone()
    conn.close()
    check("4. 위키 본문 전화번호 마스킹", bool(wct and "010******2222" in wct["content_text"]))

    # ── 정리 ──
    print("\n정리 중...")
    for d in created_docs:
        if d:
            post_form(f"/documents/{d}/delete", {})
    conn = db()
    conn.execute("DELETE FROM banned_words WHERE word = ?", (BANNED_WORD,))
    doc_ids = [d for d in created_docs if d]
    if doc_ids:
        marks = ",".join("?" for _ in doc_ids)
        conn.execute(f"DELETE FROM notifications WHERE document_id IN ({marks})", doc_ids)
    conn.execute("DELETE FROM users WHERE email = ?", (TEST_EMAIL,))
    conn.commit(); conn.close()
    print("테스트 문서/계정/금칙어/알림 정리 완료")

    # ── 결과 ──
    print(f"\n{'='*40}\n결과: {len(passed)}건 통과 / {len(failed)}건 실패")
    if failed:
        print("실패 항목:", ", ".join(failed))
        sys.exit(1)
    print("모든 검사 통과 ✅")


if __name__ == "__main__":
    main()
