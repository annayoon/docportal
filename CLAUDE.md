# DocPortal — 전사 문서 포털

모든 부서가 문서를 올리면 본문까지 인덱싱해 검색되고, 버전 관리와 마크다운
위키 작성을 지원하는 사내 문서 관리 시스템. 사내 서버(폐쇄망 가능) 배포 전제 —
**외부 CDN/외부 서비스 의존 금지**, 서버 렌더링(Jinja2) 유지.

## 실행

```bash
source .venv/bin/activate
uvicorn app.main:app --port 8001 --reload
```

## 아키텍처

- FastAPI + SQLite (raw sqlite3, ORM 없음) — `app/db.py`에 스키마
- 검색: FTS5 `tokenize='trigram'`, `fts.rowid == documents.id`, 문서의 **최신 버전만**
  인덱싱. 새 버전 저장 시 `reindex_document()` 호출 필수.
  3자 미만 검색어는 trigram을 못 타므로 LIKE 폴백 (`app/routers/search.py`)
- 버전: `documents` 1 : N `versions`. 위키 문서도 같은 구조
  (`doc_type='wiki'`, `content_text`에 마크다운, 파일 필드는 NULL)
- 파일 저장: SHA-256 해시 기반 중복 제거, `data/files/<해시앞2자>/<해시>`
- 텍스트 추출: `app/services/extractor.py` — PDF/DOCX/PPTX/XLSX/HWP/텍스트.
  HWP는 PrvText(미리보기) 스트림만 읽음 (전문 아님). 추출 실패는 삼키고 빈 문자열.
- `data/`는 gitignore — DB와 업로드 원본이 들어 있으니 삭제 주의

## 로드맵 (우선순위 순)

1. 로그인 / 부서별 권한 (전사 배포 전 필수)
2. 문서 승인(결재) 워크플로우
3. HWP 전문 추출 (hwp5 파싱)
4. 규모 확장 시 PostgreSQL + 전용 검색엔진 이전
