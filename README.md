# DocPortal — 전사 문서 포털

모든 부서가 문서를 올리면 **내용까지 인덱싱**되어 검색할 수 있고, **버전 관리**와
**마크다운 문서 작성(위키)** 을 지원하는 사내 문서 관리 시스템.

## 주요 기능

- **문서 업로드 & 전문 검색** — PDF, Word(docx), Excel(xlsx), PowerPoint(pptx),
  HWP(한글), 텍스트 파일의 본문을 추출해 SQLite FTS5(trigram)로 인덱싱.
  한국어 부분 문자열 검색 지원.
- **버전 관리** — 같은 문서에 새 버전을 업로드하면 이력이 쌓이고, 모든 이전
  버전을 다운로드할 수 있음. 파일은 내용 해시(SHA-256) 기반으로 중복 없이 저장.
- **위키 문서 작성** — 마크다운으로 문서를 작성/편집. 편집할 때마다 버전이
  기록되고 이전 버전 열람 가능.
- **부서/태그 분류** — 부서별 필터, 태그 검색.
- **로그인 & 관리자 승인** — 회사 이메일 도메인만 가입 가능, 관리자 승인 후
  로그인. 첫 가입자가 자동으로 관리자. 문서 삭제는 작성자/관리자만.
- **이메일 인증 (선택)** — SMTP를 설정하면 가입 시 인증 메일 발송, 인증 완료
  후 로그인 가능. 미설정 시 인증 절차 없이 관리자 승인만으로 동작.
- **인앱 알림** — 문서 업로드·편집 시 다른 사용자에게 알림.

## 실행

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

브라우저에서 `http://<서버주소>:8000` 접속.

## 이메일 인증 설정 (선택)

가입 인증 메일을 보내려면 SMTP 환경변수를 설정하고 실행한다.
Gmail(Google Workspace)은 2단계 인증을 켠 뒤 [앱 비밀번호](https://myaccount.google.com/apppasswords)를 발급받아야 한다.

```bash
export DOCPORTAL_SMTP_HOST=smtp.gmail.com     # 기본값
export DOCPORTAL_SMTP_PORT=587                # 기본값 (STARTTLS)
export DOCPORTAL_SMTP_USER=포털용계정@atto-research.com
export DOCPORTAL_SMTP_PASSWORD='앱 비밀번호 16자리'
export DOCPORTAL_BASE_URL=http://<서버주소>:8000   # 인증 링크에 들어갈 주소
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

USER/PASSWORD가 없으면 인증 메일 없이 기존처럼 동작한다(폐쇄망 호환).
메일이 유실된 경우 관리자가 [사용자 관리]에서 수동 인증 처리할 수 있다.

## 데이터

- `data/docportal.db` — SQLite DB (메타데이터 + 검색 인덱스)
- `data/files/` — 업로드 원본 (해시 기반 저장)
- `DOCPORTAL_DATA` 환경변수로 데이터 디렉토리 변경 가능

## 구조

```
app/
  main.py            # FastAPI 앱
  config.py          # 경로/설정
  db.py              # 스키마, FTS 인덱싱
  routers/
    documents.py     # 업로드, 상세, 버전, 다운로드
    search.py        # 전문 검색 (FTS5 trigram + 짧은 검색어 LIKE 폴백)
    wiki.py          # 마크다운 문서 작성/편집
  services/
    extractor.py     # PDF/DOCX/PPTX/XLSX/HWP 텍스트 추출
    storage.py       # 해시 기반 파일 저장
  templates/, static/
```

## 로드맵 (예정)

- [x] 로그인 / 권한 — 도메인 제한 가입 + 관리자 승인, 삭제는 작성자/관리자만
- [x] 문서 승인(결재) 워크플로우 → 결재 대신 인앱 알림으로 대체
- [x] HWP 전문 추출 — 본문 스트림 직접 파싱 (암호화 문서는 미리보기 폴백)
- [ ] PostgreSQL / 전용 검색엔진 이전 (규모 확장 시)
