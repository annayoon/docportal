import html as html_lib

from fastapi import APIRouter, Request

from ..db import fts_phrase, get_conn
from ..templating import templates

router = APIRouter()

# 스니펫 하이라이트: 컨트롤 문자를 표지로 쓰고, HTML 이스케이프 후 <mark>로 치환
# (본문에 악성 HTML이 있어도 그대로 렌더링되지 않도록)
_SNIP_OPEN, _SNIP_CLOSE = "\x02", "\x03"


def _safe_snippet(raw: str) -> str:
    return (
        html_lib.escape(raw or "")
        .replace(_SNIP_OPEN, "<mark>")
        .replace(_SNIP_CLOSE, "</mark>")
    )


@router.get("/search")
def search(request: Request, q: str = "", dept: str = ""):
    query = q.strip()
    results = []
    conn = get_conn()
    try:
        if query:
            if len(query) >= 3:
                # trigram FTS: 제목/본문/태그 전체에서 부분 문자열 매칭
                sql = (
                    f"SELECT d.*, snippet(fts, 1, '{_SNIP_OPEN}', '{_SNIP_CLOSE}', '…', 24) AS snip "
                    "FROM fts JOIN documents d ON d.id = fts.rowid "
                    "WHERE fts MATCH ? "
                )
                params: list = [fts_phrase(query)]
                if dept:
                    sql += "AND d.department = ? "
                    params.append(dept)
                sql += "ORDER BY rank LIMIT 100"
                results = conn.execute(sql, params).fetchall()
            else:
                # 3자 미만(예: '휴가')은 trigram 인덱스를 못 타므로 LIKE로 대체
                like = f"%{query}%"
                sql = (
                    "SELECT d.*, '' AS snip FROM documents d "
                    "JOIN versions v ON v.document_id = d.id AND v.version_no = "
                    "  (SELECT MAX(version_no) FROM versions WHERE document_id = d.id) "
                    "WHERE (d.title LIKE ? OR d.tags LIKE ? OR v.content_text LIKE ? "
                    "  OR v.keywords LIKE ?) "
                )
                params = [like, like, like, like]
                if dept:
                    sql += "AND d.department = ? "
                    params.append(dept)
                sql += "ORDER BY d.updated_at DESC LIMIT 100"
                results = conn.execute(sql, params).fetchall()
        # 스니펫을 이스케이프된 안전한 HTML로 가공 (템플릿에서 | safe 사용)
        results = [dict(r) | {"snip": _safe_snippet(r["snip"])} for r in results]
        departments = [
            r["department"]
            for r in conn.execute(
                "SELECT DISTINCT department FROM documents WHERE department != '' ORDER BY department"
            ).fetchall()
        ]
        return templates.TemplateResponse(
            request,
            "search.html",
            {"q": query, "dept": dept, "results": results, "departments": departments},
        )
    finally:
        conn.close()
