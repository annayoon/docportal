import difflib
import html as html_lib
from pathlib import Path

import markdown as md
import nh3
from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile,
)
from fastapi.responses import FileResponse, RedirectResponse, Response

from ..auth import get_current_user
from ..config import EXTRACT_MAX_BYTES, EXTRACT_MAX_MB, PREVIEW_DIR
from ..db import (
    fts_phrase, get_banned_words, get_conn, log_activity, notify_admins, notify_others,
    reindex_document,
)
from ..services import converter, maxkb, storage
from ..services.extractor import extract_text
from ..services.sensitive import find_banned, scan_and_mask
from ..services.summarizer import analyze, analyze_version
from ..templating import templates

router = APIRouter()

# 브라우저가 자체 렌더링할 수 있는 형식 → 미리보기 방식 결정
_PREVIEW_IFRAME = {".pdf"}
_PREVIEW_IMAGE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# 인라인 서빙을 허용하는 형식만 명시 (HTML/SVG 등 실행형 콘텐츠는 XSS 방지 위해 첨부로 강제)
_INLINE_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".txt": "text/plain; charset=utf-8",
}


def _render_markdown(text: str) -> str:
    """마크다운 → HTML 후 소독. 원문에 심어진 <script> 등 실행형 태그를 제거한다."""
    return nh3.clean(md.markdown(text, extensions=["tables", "fenced_code", "toc"]))


def _require_owner_or_admin(doc, user) -> None:
    """파일 문서의 변경(정보수정·복원·새버전)은 작성자/관리자만 — 삭제와 동일 기준."""
    if user["role"] != "admin" and doc["created_by"] != user["id"]:
        raise HTTPException(403, "이 문서의 작성자 또는 관리자만 수행할 수 있습니다.")


def _extract_masked(stored_name: str, filename: str, size: int) -> tuple[str, list[str]]:
    """저장된 파일에서 텍스트 추출 + 민감정보 마스킹. 초대형 파일은 추출 생략."""
    if size == 0 or size > EXTRACT_MAX_BYTES:
        return "", []
    data = storage.file_path(stored_name).read_bytes()
    return scan_and_mask(extract_text(data, filename))


def _preview_kind(filename: str | None, content_text: str, size: int = 0) -> str | None:
    if filename:
        suffix = Path(filename).suffix.lower()
        if suffix in _PREVIEW_IFRAME:
            return "pdf"
        if suffix in _PREVIEW_IMAGE:
            return "image"
        # 오피스 문서(docx/pptx/xlsx 등)는 PDF로 변환해 문서 모양 그대로 미리보기
        if (converter.available() and converter.can_convert(suffix)
                and suffix not in (".txt", ".html", ".htm") and 0 < size <= EXTRACT_MAX_BYTES):
            return "pdf"
    if content_text.strip():
        return "text"
    return None


def _related_docs(conn, doc_id: int, keywords: str | None, tags: str | None, limit: int = 5):
    """키워드/태그가 겹치는 다른 문서를 겹침 개수 순으로 찾는다."""
    raw = [t.strip() for t in f"{keywords or ''},{tags or ''}".split(",") if t.strip()]
    if not raw:
        return []
    # '개인정보 처리방침' 같은 복합 키워드는 단어 단위로도 매칭한다
    terms: list[str] = []
    for t in raw:
        for part in [t, *t.split()]:
            if len(part) >= 2 and part not in terms:
                terms.append(part)
    scores: dict[int, int] = {}
    for term in terms[:20]:
        if len(term) >= 3:
            # 제목/태그/키워드 컬럼만 대상으로 매칭 (본문 언급만으로는 연관 취급 안 함)
            rows = conn.execute(
                "SELECT rowid FROM fts WHERE fts MATCH ? AND rowid != ?",
                (f"{{title tags keywords}} : {fts_phrase(term)}", doc_id),
            ).fetchall()
        else:
            # 3자 미만은 trigram 인덱스를 못 타므로 LIKE로 대체
            like = f"%{term}%"
            rows = conn.execute(
                "SELECT DISTINCT d.id AS rowid FROM documents d "
                "JOIN versions v ON v.document_id = d.id AND v.version_no = "
                "  (SELECT MAX(version_no) FROM versions WHERE document_id = d.id) "
                "WHERE d.id != ? AND (d.title LIKE ? OR d.tags LIKE ? OR v.keywords LIKE ?)",
                (doc_id, like, like, like),
            ).fetchall()
        for r in rows:
            scores[r["rowid"]] = scores.get(r["rowid"], 0) + 1
    if not scores:
        return []
    top = sorted(scores.items(), key=lambda x: -x[1])[:limit]
    placeholders = ",".join("?" for _ in top)
    docs = {
        d["id"]: d
        for d in conn.execute(
            f"SELECT * FROM documents WHERE id IN ({placeholders})", [i for i, _ in top]
        ).fetchall()
    }
    return [
        {"doc": docs[i], "overlap": n}
        for i, n in top if i in docs
    ]


def _departments(conn):
    rows = conn.execute(
        "SELECT DISTINCT department FROM documents WHERE department != '' ORDER BY department"
    ).fetchall()
    return [r["department"] for r in rows]


@router.get("/")
def index(request: Request, dept: str = ""):
    conn = get_conn()
    try:
        sql = (
            "SELECT d.*, v.version_no, v.filename, v.size "
            "FROM documents d "
            "JOIN versions v ON v.document_id = d.id AND v.version_no = "
            "  (SELECT MAX(version_no) FROM versions WHERE document_id = d.id) "
        )
        params: tuple = ()
        if dept:
            sql += "WHERE d.department = ? "
            params = (dept,)
        sql += "ORDER BY d.updated_at DESC LIMIT 50"
        docs = conn.execute(sql, params).fetchall()
        return templates.TemplateResponse(
            request,
            "index.html",
            {"docs": docs, "departments": _departments(conn), "dept": dept},
        )
    finally:
        conn.close()


@router.post("/upload")
async def upload(
    background_tasks: BackgroundTasks,
    title: str = Form(""),
    department: str = Form(""),
    tags: str = Form(""),
    note: str = Form(""),
    files: list[UploadFile] = File(...),
    current_user=Depends(get_current_user),
):
    # 실제 내용이 있는 파일만 추린다 (빈 file 파트는 무시)
    uploads = [f for f in files if f.filename]
    if not uploads:
        raise HTTPException(400, "업로드할 파일을 선택해 주세요.")
    # 제목은 파일이 하나일 때만 적용 — 여러 개면 각 파일명을 제목으로 사용
    single = len(uploads) == 1

    new_ids: list[int] = []
    flagged: list[tuple[int, str, str]] = []  # (doc_id, 제목, 발견유형) — 민감정보 발견분
    conn = get_conn()
    try:
        banned = get_banned_words(conn)
        # 금칙어 검사 — 사용자가 직접 입력한 제목·태그 (발견 시 전체 차단)
        hits = find_banned(f"{title} {tags}", banned)
        if hits:
            raise HTTPException(400, f"제목/태그에 사용할 수 없는 단어가 있습니다: {', '.join(hits)}")
        for file in uploads:
            filename = file.filename or "unnamed"
            # 스트리밍 저장 — 파일 크기와 무관하게 메모리 사용 고정
            sha, stored_name, size = await storage.save_upload(file)
            if size == 0:
                continue
            # 검색·요약·챗봇에 나가는 추출 텍스트는 민감정보를 마스킹 (원본 파일은 그대로)
            text, sflags = _extract_masked(stored_name, filename, size)
            doc_title = (title.strip() if single else "") or Path(filename).stem
            cur = conn.execute(
                "INSERT INTO documents (title, doc_type, department, tags, created_by, sensitive_flags) "
                "VALUES (?, 'file', ?, ?, ?, ?)",
                (doc_title, department.strip(), tags.strip(), current_user["id"], ",".join(sflags)),
            )
            doc_id = cur.lastrowid
            version_id = conn.execute(
                "INSERT INTO versions (document_id, version_no, filename, stored_name, sha256, size, content_text, note) "
                "VALUES (?, 1, ?, ?, ?, ?, ?, ?)",
                (doc_id, filename, stored_name, sha, size, text, note.strip()),
            ).lastrowid
            reindex_document(conn, doc_id)
            log_activity(conn, current_user["id"], "upload", doc_id, filename)
            if sflags:
                log_activity(conn, current_user["id"], "sensitive", doc_id, ",".join(sflags))
                flagged.append((doc_id, doc_title, ", ".join(sflags)))
            new_ids.append(doc_id)
            background_tasks.add_task(analyze_version, version_id)
        if not new_ids:
            raise HTTPException(400, "빈 파일입니다.")
        if single:
            notify_others(
                conn, current_user["id"], new_ids[0],
                f"{current_user['email']}님이 새 문서를 업로드했습니다: "
                f"{conn.execute('SELECT title FROM documents WHERE id = ?', (new_ids[0],)).fetchone()['title']}",
            )
        else:
            # 여러 건은 각 문서마다 알림을 쏟지 않고 한 번만 요약해서 브로드캐스트
            notify_others(
                conn, current_user["id"], new_ids[0],
                f"{current_user['email']}님이 새 문서 {len(new_ids)}건을 업로드했습니다.",
            )
        # 민감정보 발견 문서는 관리자에게 경고 알림
        for doc_id, doc_title, sflags in flagged:
            notify_admins(
                conn, doc_id,
                f"⚠️ 민감정보 감지({sflags}) — 검색·챗봇에는 마스킹 처리됨: {doc_title}",
            )
        conn.commit()
    finally:
        conn.close()
    # 한 건이면 상세로, 여러 건이면 목록으로 이동
    return RedirectResponse(
        f"/documents/{new_ids[0]}" if single else "/", status_code=303
    )


@router.get("/documents/{doc_id}")
def detail(request: Request, doc_id: int, v: int | None = None):
    conn = get_conn()
    try:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if doc is None:
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        versions = conn.execute(
            "SELECT * FROM versions WHERE document_id = ? ORDER BY version_no DESC",
            (doc_id,),
        ).fetchall()
        shown = versions[0]
        if v is not None:
            shown = next((x for x in versions if x["version_no"] == v), shown)
        rendered = None
        preview = None
        if doc["doc_type"] == "wiki":
            rendered = _render_markdown(shown["content_text"])
        else:
            preview = _preview_kind(shown["filename"], shown["content_text"], shown["size"] or 0)
        related = _related_docs(conn, doc_id, versions[0]["keywords"], doc["tags"])
        # 다운로드 형식 옵션: 위키는 HTML(+변환 시 PDF/Word), 파일은 원본(+변환 시 PDF)
        src_ext = Path(shown["filename"] or "").suffix.lower() if shown["filename"] else ""
        can_pdf = doc["doc_type"] == "wiki" or (
            converter.available() and converter.can_convert(src_ext) and src_ext != ".pdf"
        )
        # 파일 문서의 변경 권한 (위키는 협업 문서라 모두 가능)
        user = request.state.user
        can_modify = doc["doc_type"] == "wiki" or (
            user is not None and (user["role"] == "admin" or user["id"] == doc["created_by"])
        )
        return templates.TemplateResponse(
            request,
            "document.html",
            {"doc": doc, "versions": versions, "shown": shown, "rendered": rendered,
             "preview": preview, "related": related,
             "convert_on": converter.available(), "can_pdf": can_pdf,
             "can_modify": can_modify},
        )
    finally:
        conn.close()


@router.get("/documents/{doc_id}/edit")
def edit_meta_form(request: Request, doc_id: int):
    conn = get_conn()
    try:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    finally:
        conn.close()
    if doc is None:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    if doc["doc_type"] == "wiki":
        return RedirectResponse(f"/wiki/{doc_id}/edit", status_code=303)
    return templates.TemplateResponse(request, "document_edit.html", {"doc": doc})


@router.post("/documents/{doc_id}/edit")
def edit_meta(
    doc_id: int,
    title: str = Form(...),
    department: str = Form(""),
    tags: str = Form(""),
    current_user=Depends(get_current_user),
):
    conn = get_conn()
    try:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if doc is None or doc["doc_type"] != "file":
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        _require_owner_or_admin(doc, current_user)
        conn.execute(
            "UPDATE documents SET title = ?, department = ?, tags = ?, "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            (title.strip() or doc["title"], department.strip(), tags.strip(), doc_id),
        )
        reindex_document(conn, doc_id)  # 제목/태그가 검색 인덱스에 들어가므로 갱신 필수
        log_activity(conn, current_user["id"], "meta_edit", doc_id, title.strip())
        conn.commit()
    finally:
        conn.close()
    maxkb.sync_async(doc_id)  # MaxKB 문서명도 새 제목으로 교체
    return RedirectResponse(f"/documents/{doc_id}", status_code=303)


@router.post("/documents/{doc_id}/revert/{version_no}")
def revert_version(doc_id: int, version_no: int, current_user=Depends(get_current_user)):
    """이력을 다시 쓰지 않고, 과거 버전의 내용을 복사한 새 버전을 만든다."""
    conn = get_conn()
    try:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if doc is None:
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        if doc["doc_type"] == "file":  # 위키는 협업 문서라 복원도 누구나 가능
            _require_owner_or_admin(doc, current_user)
        src = conn.execute(
            "SELECT * FROM versions WHERE document_id = ? AND version_no = ?",
            (doc_id, version_no),
        ).fetchone()
        if src is None:
            raise HTTPException(404, "해당 버전이 없습니다.")
        latest_no = conn.execute(
            "SELECT MAX(version_no) AS n FROM versions WHERE document_id = ?", (doc_id,)
        ).fetchone()["n"]
        if version_no == latest_no:
            raise HTTPException(400, "이미 최신 버전입니다.")
        conn.execute(
            "INSERT INTO versions (document_id, version_no, filename, stored_name, sha256, "
            "  size, content_text, note, summary, keywords) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                doc_id, latest_no + 1, src["filename"], src["stored_name"], src["sha256"],
                src["size"], src["content_text"], f"v{version_no}에서 복원",
                src["summary"], src["keywords"],
            ),
        )
        conn.execute(
            "UPDATE documents SET updated_at = datetime('now','localtime') WHERE id = ?",
            (doc_id,),
        )
        reindex_document(conn, doc_id)
        log_activity(
            conn, current_user["id"], "revert", doc_id,
            f"{doc['title']} (v{version_no} → v{latest_no + 1})",
        )
        notify_others(
            conn, current_user["id"], doc_id,
            f"{current_user['email']}님이 문서를 이전 버전으로 복원했습니다: {doc['title']} (v{version_no})",
        )
        conn.commit()
    finally:
        conn.close()
    maxkb.sync_async(doc_id)  # 복원된 내용을 지식베이스에 반영
    return RedirectResponse(f"/documents/{doc_id}", status_code=303)


@router.get("/documents/{doc_id}/diff")
def diff_versions(request: Request, doc_id: int, a: int, b: int):
    """두 버전의 텍스트 내용을 비교한다 (파일 문서는 추출 텍스트 기준)."""
    conn = get_conn()
    try:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if doc is None:
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        rows = {
            r["version_no"]: r
            for r in conn.execute(
                "SELECT * FROM versions WHERE document_id = ? AND version_no IN (?, ?)",
                (doc_id, a, b),
            ).fetchall()
        }
    finally:
        conn.close()
    if a not in rows or b not in rows:
        raise HTTPException(404, "해당 버전이 없습니다.")
    lines_a = (rows[a]["content_text"] or "").splitlines()
    lines_b = (rows[b]["content_text"] or "").splitlines()
    diff_lines = []
    for line in difflib.unified_diff(lines_a, lines_b, lineterm="", n=3):
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("@@"):
            cls = "hunk"
        elif line.startswith("+"):
            cls = "add"
        elif line.startswith("-"):
            cls = "del"
        else:
            cls = "ctx"
        diff_lines.append({"cls": cls, "text": line})
    return templates.TemplateResponse(
        request,
        "document_diff.html",
        {"doc": doc, "a": a, "b": b, "diff_lines": diff_lines,
         "va": rows[a], "vb": rows[b]},
    )


@router.post("/documents/{doc_id}/versions")
async def upload_version(
    doc_id: int,
    background_tasks: BackgroundTasks,
    note: str = Form(""),
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    filename = file.filename or "unnamed"
    conn = get_conn()
    try:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if doc is None or doc["doc_type"] != "file":
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        _require_owner_or_admin(doc, current_user)
        sha, stored_name, size = await storage.save_upload(file)
        if size == 0:
            raise HTTPException(400, "빈 파일입니다.")
        text, sflags = _extract_masked(stored_name, filename, size)
        next_no = conn.execute(
            "SELECT COALESCE(MAX(version_no), 0) + 1 AS n FROM versions WHERE document_id = ?",
            (doc_id,),
        ).fetchone()["n"]
        version_id = conn.execute(
            "INSERT INTO versions (document_id, version_no, filename, stored_name, sha256, size, content_text, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (doc_id, next_no, filename, stored_name, sha, size, text, note.strip()),
        ).lastrowid
        conn.execute(
            "UPDATE documents SET updated_at = datetime('now','localtime'), sensitive_flags = ? WHERE id = ?",
            (",".join(sflags), doc_id),
        )
        reindex_document(conn, doc_id)
        log_activity(conn, current_user["id"], "version", doc_id, f"{filename} (v{next_no})")
        if sflags:
            log_activity(conn, current_user["id"], "sensitive", doc_id, ",".join(sflags))
            notify_admins(conn, doc_id, f"⚠️ 민감정보 감지({', '.join(sflags)}) — 마스킹됨: {doc['title']}")
        notify_others(
            conn, current_user["id"], doc_id,
            f"{current_user['email']}님이 문서에 새 버전을 올렸습니다: {doc['title']} (v{next_no})",
        )
        conn.commit()
    finally:
        conn.close()
    background_tasks.add_task(analyze_version, version_id)
    return RedirectResponse(f"/documents/{doc_id}", status_code=303)


_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "html": "text/html; charset=utf-8",
}


def _wiki_html(title: str, markdown_text: str) -> bytes:
    """위키 마크다운을 변환/저장용 독립 HTML 문서로 만든다 (소독 포함)."""
    body = _render_markdown(markdown_text)
    title = html_lib.escape(title)
    return (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        f"<title>{title}</title><style>"
        "body{font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;"
        "line-height:1.7;max-width:720px;margin:24px auto;padding:0 16px;}"
        "h1,h2,h3{margin-top:1.4em;} table{border-collapse:collapse;}"
        "th,td{border:1px solid #ccc;padding:6px 12px;} "
        "pre{background:#f3f4f6;padding:12px;border-radius:6px;overflow-x:auto;}"
        f"</style></head><body><h1>{title}</h1>{body}</body></html>"
    ).encode()


def _safe_name(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in " ._-()[]가-힣" else "_" for c in name)
    return keep.strip() or "document"


@router.get("/versions/{version_id}/download")
def download(request: Request, version_id: int, format: str = "original"):
    user = request.state.user
    conn = get_conn()
    try:
        ver = conn.execute("SELECT * FROM versions WHERE id = ?", (version_id,)).fetchone()
        doc = (
            conn.execute("SELECT * FROM documents WHERE id = ?", (ver["document_id"],)).fetchone()
            if ver else None
        )
        if ver is not None and doc is not None:
            log_activity(
                conn, user["id"] if user else None, "download", doc["id"],
                f"{ver['filename'] or doc['title']} (v{ver['version_no']}, {format})",
            )
            conn.commit()
    finally:
        conn.close()
    if ver is None or doc is None:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")

    # 위키 문서: 원본 파일이 없으므로 HTML로 만들어 내보낸다 (html/pdf/docx)
    if doc["doc_type"] == "wiki":
        target = format if format in ("html", "pdf", "docx") else "html"
        html = _wiki_html(doc["title"], ver["content_text"] or "")
        base = _safe_name(doc["title"])
        if target == "html":
            data = html
        else:
            if not converter.available():
                raise HTTPException(503, "형식 변환 기능(LibreOffice)이 서버에 설정되어 있지 않습니다.")
            try:
                data = converter.convert(html, ".html", target)
            except converter.ConversionBusy:
                raise HTTPException(503, "변환 작업이 몰려 있습니다. 잠시 후 다시 시도해 주세요.")
            except Exception:
                raise HTTPException(500, "문서 변환에 실패했습니다.")
        return Response(
            content=data,
            media_type=_MEDIA_TYPES[target],
            headers={"Content-Disposition": _disposition(f"{base}.{target}")},
        )

    # 파일 문서
    if not ver["stored_name"]:
        raise HTTPException(404, "파일을 찾을 수 없습니다.")
    path = storage.file_path(ver["stored_name"])
    if not path.exists():
        raise HTTPException(404, "저장된 파일이 없습니다.")
    src_ext = Path(ver["filename"] or "").suffix.lower()

    # 원본 그대로 (또는 요청 형식이 이미 원본과 같은 경우)
    if format == "original" or src_ext == f".{format}":
        return FileResponse(path, filename=ver["filename"])

    # PDF로 변환 다운로드
    if format == "pdf":
        if not converter.can_convert(src_ext):
            raise HTTPException(400, "이 형식은 PDF 변환을 지원하지 않습니다.")
        try:
            data = converter.convert(path.read_bytes(), src_ext, "pdf")
        except converter.ConversionBusy:
            raise HTTPException(503, "변환 작업이 몰려 있습니다. 잠시 후 다시 시도해 주세요.")
        except Exception:
            raise HTTPException(500, "PDF 변환에 실패했습니다.")
        base = _safe_name(Path(ver["filename"] or "document").stem)
        return Response(
            content=data,
            media_type=_MEDIA_TYPES["pdf"],
            headers={"Content-Disposition": _disposition(f"{base}.pdf")},
        )

    raise HTTPException(400, "지원하지 않는 다운로드 형식입니다.")


def _disposition(filename: str) -> str:
    from urllib.parse import quote

    return f"attachment; filename*=UTF-8''{quote(filename)}"


@router.get("/versions/{version_id}/preview")
def preview_file(version_id: int):
    """다운로드 없이 브라우저에서 바로 열리도록 inline으로 서빙 (PDF/이미지용)."""
    conn = get_conn()
    try:
        ver = conn.execute("SELECT * FROM versions WHERE id = ?", (version_id,)).fetchone()
    finally:
        conn.close()
    if ver is None or not ver["stored_name"]:
        raise HTTPException(404, "파일을 찾을 수 없습니다.")
    path = storage.file_path(ver["stored_name"])
    if not path.exists():
        raise HTTPException(404, "저장된 파일이 없습니다.")
    ext = Path(ver["filename"] or "").suffix.lower()
    media_type = _INLINE_TYPES.get(ext)
    if media_type is not None:
        return FileResponse(
            path,
            media_type=media_type,
            filename=ver["filename"],
            content_disposition_type="inline",
        )
    # 오피스 문서: PDF로 변환해 문서 모양 그대로 미리보기 (해시 기반 캐시 — 버전당 1회 변환)
    if (converter.available() and converter.can_convert(ext)
            and ext not in (".txt", ".html", ".htm")
            and (ver["size"] or 0) <= EXTRACT_MAX_BYTES):
        cache = PREVIEW_DIR / f"{ver['sha256']}.pdf"
        if not cache.exists():
            try:
                pdf = converter.convert(path.read_bytes(), ext, "pdf")
                cache.write_bytes(pdf)
            except Exception:
                # 변환 실패(손상 파일·변환기 부하 등) → 추출 텍스트로 폴백
                return Response(
                    ver["content_text"] or "미리보기를 생성할 수 없습니다.",
                    media_type="text/plain; charset=utf-8",
                )
        return FileResponse(
            cache,
            media_type="application/pdf",
            filename=f"{Path(ver['filename'] or 'preview').stem}.pdf",
            content_disposition_type="inline",
        )
    # 그 외 형식(HTML/SVG 등)은 브라우저 실행 방지를 위해 첨부 다운로드로
    return FileResponse(
        path, filename=ver["filename"], media_type="application/octet-stream"
    )


@router.post("/documents/{doc_id}/summarize")
def summarize_document(doc_id: int, current_user=Depends(get_current_user)):
    """최신 버전 본문을 요약·키워드 추출해 캐시하고 재인덱싱. (Ollama 없으면 빈도 기반)"""
    conn = get_conn()
    try:
        latest = conn.execute(
            "SELECT * FROM versions WHERE document_id = ? ORDER BY version_no DESC LIMIT 1",
            (doc_id,),
        ).fetchone()
        if latest is None:
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        if not latest["content_text"].strip():
            raise HTTPException(400, "추출된 본문이 없어 요약할 수 없습니다.")
        summary, keywords = analyze(latest["content_text"])
        conn.execute(
            "UPDATE versions SET summary = ?, keywords = ? WHERE id = ?",
            (summary, keywords, latest["id"]),
        )
        reindex_document(conn, doc_id)
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/documents/{doc_id}", status_code=303)


@router.post("/documents/{doc_id}/delete")
def delete(doc_id: int, current_user=Depends(get_current_user)):
    conn = get_conn()
    try:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if doc is None:
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        if current_user["role"] != "admin" and doc["created_by"] != current_user["id"]:
            raise HTTPException(403, "삭제 권한이 없습니다 (작성자 또는 관리자만 삭제할 수 있습니다).")
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        conn.execute("DELETE FROM fts WHERE rowid = ?", (doc_id,))
        log_activity(conn, current_user["id"], "delete", doc_id, doc["title"])
        conn.commit()
    finally:
        conn.close()
    maxkb.delete_async(doc["maxkb_doc_id"])
    return RedirectResponse("/", status_code=303)
