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
  읽음 처리한다. 이메일 발송은 안 하지만, `DOCPORTAL_WEBHOOK_URL` 설정 시
  `notify_others()`가 Google Chat/Slack 호환 웹훅({"text": ...} POST)으로도
  푸시한다(`app/services/webhook.py`, 데몬 스레드 fire-and-forget — 실패해도
  업로드에 영향 없음). 링크 호스트는 `DOCPORTAL_BASE_URL`.
  Gmail 앱 비밀번호가 회사 규정상 불가라 웹훅이 사실상의 푸시 수단.

- 미리보기/요약: 상세 페이지에서 PDF·이미지는 `/versions/{id}/preview`(inline)로
  브라우저 렌더링, 그 외는 `content_text` 표시. 요약은 `versions.summary`에
  캐시 — `app/services/summarizer.py`가 Ollama(`DOCPORTAL_OLLAMA_URL`, 기본
  localhost:11434, 모델 `DOCPORTAL_OLLAMA_MODEL` 기본 gemma4:12b) 시도 후 실패하면
  빈도 기반 추출 요약으로 폴백. 사내 서버에 Ollama 없어도 동작.
  업로드/위키 저장 시 FastAPI BackgroundTasks로 **자동 요약+키워드 추출**
  (`analyze_version`) — 응답은 즉시 반환되고 결과는 뒤에서 채워진다.
  키워드는 `versions.keywords`(콤마 구분)에 저장되고 FTS의 `keywords` 컬럼으로
  인덱싱되어 **본문에 없는 표현도 키워드로 검색**된다. 문서 페이지에 클릭
  가능한 #키워드 칩으로 표시(클릭 → 검색). LLM 응답은 Ollama `format: json`
  구조화 출력 사용. [요약 생성/다시 생성] 버튼은 수동 재시도용.

- 저장소 통계: `/admin/storage` (관리자 전용) — 실제/논리 용량, 중복 제거
  절약분, DB 크기, 디스크 여유, 부서별 사용량. 업로드 용량 제한은 **의도적으로
  없음** (사용자 결정) — 대신 이 화면으로 모니터링.

- 연관 문서: 문서 상세에서 현재 문서의 키워드+태그가 다른 문서의 제목/태그/
  keywords와 겹치는 정도로 추천(`_related_docs` in `app/routers/documents.py`).
  복합 키워드는 단어 단위로도 분해해 매칭, 겹침 개수 순 상위 5건 표시.
  본문 언급만으로는 연관 처리 안 함(제목/태그/키워드 컬럼만 대상).

- 업로드: `/upload`는 `files: list[UploadFile]`로 다중 파일 수신. 파일 1개면
  `title` 적용 후 상세로 리다이렉트, 여러 개면 각 파일명을 제목으로 개별 문서
  등록 후 홈으로. 부서·태그·비고는 공통. 각 문서마다 `analyze_version`
  백그라운드 태스크, 알림은 다건일 때 'N건' 한 번만 브로드캐스트.

- 형식 변환: `app/services/converter.py`가 LibreOffice(headless)로 변환.
  `/versions/{id}/download?format=original|pdf|docx|html`. 파일 문서는
  original(그대로)/pdf(변환), 위키는 html(순수 파이썬)/pdf/docx(LibreOffice).
  soffice 경로는 자동 탐색 + `DOCPORTAL_SOFFICE`로 지정. 미설치면 원본/HTML만.

- 배포: `deploy/` — install.sh(멱등 설치 스크립트, Ubuntu 기준),
  docportal.service(systemd, 데이터는 /var/lib/docportal 분리),
  nginx.conf(리버스 프록시, client_max_body_size 0 = 업로드 무제한 정책).
  HTTPS 뒤에서는 `DOCPORTAL_SECURE_COOKIES=1`로 세션 쿠키 Secure 플래그.

- 관리자 화면: `/admin/users`(승인/권한), `/admin/documents`(검색·필터·일괄 삭제),
  `/admin/activity`(감사 로그 — `db.log_activity()`로 login/upload/version/
  wiki_create/wiki_edit/delete/download 기록, 최근 200건 표시),
  `/admin/storage`(용량·14일 업로드 추이·30일 최다 다운로드·부서별 사용량).
  새 사용자 행동을 추가하면 log_activity 호출과 ACTION_LABELS 갱신을 잊지 말 것.

- 리비전 편집: 파일 문서 메타데이터 수정 `/documents/{id}/edit`(reindex 필수),
  복원 `/documents/{id}/revert/{version_no}` — 이력을 다시 쓰지 않고 과거 버전
  내용을 복사한 새 버전 생성(summary/keywords도 복사, 최신 버전 복원은 400),
  비교 `/documents/{id}/diff?a=&b=` — content_text 기준 unified diff.

- MaxKB 연동(`app/services/maxkb.py`): `DOCPORTAL_MAXKB_*` 설정 시 문서 최신
  본문을 지식베이스에 자동 동기화(교체 방식 — 기존 MaxKB 문서 삭제 후 재생성,
  매핑은 `documents.maxkb_doc_id`). 훅: analyze_version(업로드/위키), meta_edit,
  revert, delete(단건/일괄). 빈 본문(스캔 PDF 등)은 스킵. 전체 재적재는
  `/admin/maxkb-sync`. MaxKB는 로컬 Docker(colima)로 8080에서 구동,
  임베딩/LLM은 host.docker.internal로 호스트 Ollama 사용.

## 보안 수칙 (2026-07-08 자체 점검에서 적용)

- 사용자 입력 HTML은 반드시 소독: 위키 렌더링은 `_render_markdown()`(nh3),
  검색 스니펫은 컨트롤 문자 표지 + escape 후 `<mark>` 치환. 새 `| safe` 사용 금지.
- FTS MATCH에 사용자 입력을 넣을 땐 반드시 `db.fts_phrase()` 경유.
- `/versions/{id}/preview`는 `_INLINE_TYPES` 화이트리스트만 인라인 — HTML/SVG 등
  실행형 콘텐츠는 octet-stream 첨부 강제. 전 응답에 X-Content-Type-Options: nosniff.
- 로그인 `next`는 내부 경로만 허용(오픈 리다이렉트 방지). 세션은 30일 만료 +
  로그인 시 만료분 정리. 마지막 관리자는 강등/거부 불가(잠금 방지).
- HWP 압축 해제는 섹션당 64MB 상한(압축 폭탄 방지). SQLite는 WAL + busy_timeout.
- 변환 동시 실행 상한: `DOCPORTAL_MAX_CONVERSIONS`(기본 3) — 세마포어로 제한,
  초과분은 60초 대기 후 503(ConversionBusy). LibreOffice 프로세스 폭주 방지.
- **미적용(알려진 한계)**: CSRF 토큰 없음(SameSite=Lax로 완화), 로그인 레이트리밋
  없음. 필요 시 로드맵에 추가할 것.

## 로드맵 (우선순위 순)

1. ~~로그인 / 부서별 권한~~ — 완료. 로그인 필수 + 이메일 도메인 가입 + 관리자
   승인. 단, 조회/업로드는 부서 제한 없음(삭제만 작성자/관리자 제한)으로 구현
2. ~~문서 승인(결재) 워크플로우~~ — 결재 단계는 필요 없다고 판단, 대신 업로드·
   편집 시 인앱 알림(브로드캐스트)으로 대체 구현
3. ~~HWP 전문 추출~~ — 완료. HWP 5.x 레코드 직접 파싱 (extractor.py)
4. 규모 확장 시 PostgreSQL + 전용 검색엔진 이전
