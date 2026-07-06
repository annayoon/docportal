from fastapi import APIRouter, Request

from ..db import get_conn
from ..templating import templates

router = APIRouter()


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
                    "SELECT d.*, snippet(fts, 1, '<mark>', '</mark>', '…', 24) AS snip "
                    "FROM fts JOIN documents d ON d.id = fts.rowid "
                    "WHERE fts MATCH ? "
                )
                params: list = [f'"{query}"']
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
                    "WHERE (d.title LIKE ? OR d.tags LIKE ? OR v.content_text LIKE ?) "
                )
                params = [like, like, like]
                if dept:
                    sql += "AND d.department = ? "
                    params.append(dept)
                sql += "ORDER BY d.updated_at DESC LIMIT 100"
                results = conn.execute(sql, params).fetchall()
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
