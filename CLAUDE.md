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
  HWP는 BodyText/Section* 스트림을 직접 파싱해 전문 추출 (표·각주·머리말 포함),
  암호화/배포용 문서는 PrvText(미리보기)로 폴백. 추출 실패는 삼키고 빈 문자열.
- `data/`는 gitignore — DB와 업로드 원본이 들어 있으니 삭제 주의
- 인증: `app/auth.py` + `app/routers/auth.py`. 외부 서비스 없이 stdlib만 사용
  (비밀번호는 PBKDF2-SHA256, 세션은 DB `sessions` 테이블 + 랜덤 토큰 쿠키).
  `AuthMiddleware`(`app/main.py`)가 `/login`, `/signup`, `/verify`, `/static` 외
  모든 경로에 로그인을 강제하고 `request.state.user`를 채워준다.
  - 가입: `/signup`에서 회사 이메일 도메인(`DOCPORTAL_EMAIL_DOMAIN`, 기본
    `atto-research.com`)만 허용. 가입 시 `users.status='pending'`으로 생성되고
    관리자가 `/admin/users`에서 승인/거부해야 로그인 가능.
    **최초 가입자는 자동으로 관리자 계정으로 승인**됨 (부트스트랩용).
  - 이메일 인증(선택): `DOCPORTAL_SMTP_USER/PASSWORD`가 설정된 경우에만 활성화
    (`app/services/mailer.py`, 기본 smtp.gmail.com:587 — Gmail은 앱 비밀번호 필요).
    가입 → `/verify?token=` 링크 메일 → 인증 → 관리자 승인 → 로그인 순서.
    SMTP 미설정이면 인증 절차 생략(폐쇄망 호환). 메일 유실 시 관리자가
    `/admin/users`에서 수동 인증 처리 가능. 인증 링크의 호스트는
    `DOCPORTAL_BASE_URL`로 지정.
  - 권한 범위는 의도적으로 넓다: 로그인한 사용자는 부서 무관하게 모든 문서를
    조회/업로드/편집할 수 있다. `department`는 분류용 태그일 뿐 접근 제어에
    쓰이지 않는다. 유일한 제한은 문서 삭제 — 작성자 본인(`documents.created_by`)
    또는 `role='admin'`만 가능.
- 알림: 결재 워크플로우 없이, 업로드/편집 시 브로드캐스트 알림만 제공
  (`app/db.py`의 `notify_others()`, `app/routers/notifications.py`).
  새 문서 업로드·새 버전 업로드·위키 작성/수정마다 행위자 본인을 제외한
  `status='approved'` 사용자 전원에게 `notifications` 테이블에 행이 생긴다.
  헤더의 🔔 뱃지는 `AuthMiddleware`가 매 요청마다 채우는
  `request.state.unread_count`로 표시하고, `/notifications` 조회 시 전체
  읽음 처리한다. 실제 이메일/외부 푸시는 사용하지 않음(폐쇄망 전제).

- 미리보기/요약: 상세 페이지에서 PDF·이미지는 `/versions/{id}/preview`(inline)로
  브라우저 렌더링, 그 외는 `content_text` 표시. 요약은 `versions.summary`에
  캐시 — `app/services/summarizer.py`가 Ollama(`DOCPORTAL_OLLAMA_URL`, 기본
  localhost:11434, 모델 `DOCPORTAL_OLLAMA_MODEL` 기본 gemma4:12b) 시도 후 실패하면
  빈도 기반 추출 요약으로 폴백. 사내 서버에 Ollama 없어도 동작.
  업로드/위키 저장 시 FastAPI BackgroundTasks로 **자동 요약** — 응답은 즉시
  반환되고 요약은 뒤에서 채워진다. [요약 생성/다시 생성] 버튼은 수동 재시도용.

## 로드맵 (우선순위 순)

1. ~~로그인 / 부서별 권한~~ — 완료. 로그인 필수 + 이메일 도메인 가입 + 관리자
   승인. 단, 조회/업로드는 부서 제한 없음(삭제만 작성자/관리자 제한)으로 구현
2. ~~문서 승인(결재) 워크플로우~~ — 결재 단계는 필요 없다고 판단, 대신 업로드·
   편집 시 인앱 알림(브로드캐스트)으로 대체 구현
3. ~~HWP 전문 추출~~ — 완료. HWP 5.x 레코드 직접 파싱 (extractor.py)
4. 규모 확장 시 PostgreSQL + 전용 검색엔진 이전
