# DocPortal — 전사 문서 포털

모든 부서가 문서를 올리면 **내용까지 인덱싱**되어 검색할 수 있고, **버전 관리**와
**마크다운 문서 작성(위키)** 을 지원하는 사내 문서 관리 시스템.

## 주요 기능

- **다중 업로드** — 여러 파일을 한 번에 선택해 각각 개별 문서로 등록.
  부서·태그·비고는 공통 적용, 각 파일은 파일명이 제목이 됨.
- **형식 변환 다운로드** — 업로드 문서를 PDF로, 위키 문서를 HTML/PDF/Word로
  변환해 다운로드. LibreOffice(headless)를 변환 엔진으로 사용하며, 미설치 시
  원본 다운로드만 제공(위키는 HTML 내보내기까지 가능).
- **문서 업로드 & 전문 검색** — PDF, Word(docx), Excel(xlsx), PowerPoint(pptx),
  HWP(한글), 텍스트 파일의 본문을 추출해 SQLite FTS5(trigram)로 인덱싱.
  한국어 부분 문자열 검색 지원.
- **버전 관리** — 같은 문서에 새 버전을 업로드하면 이력이 쌓이고, 모든 이전
  버전을 다운로드할 수 있음. **이전 버전으로 복원**(이력 보존)과 **버전 간
  내용 비교(diff)** 지원. 파일은 내용 해시(SHA-256) 기반으로 중복 없이 저장.
- **문서 정보 수정** — 파일 문서의 제목·부서·태그를 재업로드 없이 수정
  (검색 인덱스 자동 갱신).
- **위키 문서 작성** — 마크다운으로 문서를 작성/편집. 편집할 때마다 버전이
  기록되고 이전 버전 열람 가능.
- **부서/태그 분류** — 부서별 필터, 태그 검색.
- **미리보기 & AI 요약** — PDF·이미지는 브라우저에서 바로 미리보기, 그 외
  형식은 추출된 본문 텍스트 표시. 업로드하면 자동으로 요약 생성 —
  Ollama(기본 gemma4:12b)가 있으면 LLM 요약+키워드 추출, 없으면 빈도 기반
  (버전별 캐시, 수동 재생성 가능). 키워드는 검색 인덱스에 포함되어 본문에
  없는 표현으로도 문서를 찾을 수 있고, 문서 페이지에서 #칩 클릭으로 연관
  문서를 탐색할 수 있다.
- **연관 문서 추천** — 문서 페이지에서 키워드·태그가 겹치는 다른 문서를
  자동으로 찾아 추천.
- **로그인 & 관리자 승인** — 회사 이메일 도메인만 가입 가능, 관리자 승인 후
  로그인. 첫 가입자가 자동으로 관리자. 문서 삭제는 작성자/관리자만.
- **이메일 인증 (선택)** — SMTP를 설정하면 가입 시 인증 메일 발송, 인증 완료
  후 로그인 가능. 미설정 시 인증 절차 없이 관리자 승인만으로 동작.
- **인앱 알림** — 문서 업로드·편집 시 다른 사용자에게 알림.
- **민감정보 마스킹** — 주민번호·카드·계좌·휴대전화·이메일을 업로드 시 자동
  탐지해 검색·요약·챗봇에는 마스킹(원본 파일 다운로드는 그대로), 문서에 민감
  뱃지 + 관리자 알림.
- **금칙어** — 관리자가 등록한 단어가 제목·태그·위키 본문에 있으면 업로드/저장 차단.
- **관리자 화면** — 사용자 승인/권한, 문서 일괄 관리(검색·필터·일괄 삭제),
  활동 로그(로그인·업로드·다운로드·삭제 감사 추적), 저장소 통계(용량,
  업로드 추이, 최다 다운로드, 부서별 사용량).
- **메신저 푸시 (선택)** — Google Chat/Slack 수신 웹훅 URL만 설정하면 문서
  업로드·변경을 메신저로 즉시 푸시. 앱 비밀번호·메일 서버 불필요.
  (`DOCPORTAL_WEBHOOK_URL`, Google Chat: 스페이스 → 앱 및 통합 → 웹훅 추가)

## 실행 (개발/로컬)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

브라우저에서 `http://<서버주소>:8000` 접속.

## 사내 서버 배포

Ubuntu/Debian 서버에서 저장소를 받은 뒤 한 줄로 설치한다:

```bash
git clone https://github.com/annayoon/docportal.git && cd docportal
sudo bash deploy/install.sh
```

스크립트가 하는 일: 서비스 계정 생성 → `/opt/docportal`(코드)·`/var/lib/docportal`(데이터)
배치 → venv/의존성 설치 → **systemd 서비스 등록**(부팅 자동 시작, 죽으면 재시작) →
**nginx 리버스 프록시** 등록. 재실행해도 안전해서 코드 업데이트 배포에도 그대로 쓴다.

- 도메인: `deploy/nginx.conf`의 `server_name`을 사내 DNS 이름으로 수정
- 환경변수(SMTP·Ollama·BASE_URL 등): `deploy/docportal.service`에서 주석 해제
- HTTPS 운영 시: nginx에 TLS 설정 후 `DOCPORTAL_SECURE_COOKIES=1` 활성화
- 형식 변환(PDF/Word) 기능: 서버에 LibreOffice 설치 (`install.sh` 내 주석 참고)
- 백업: `/var/lib/docportal` 디렉토리 하나만 챙기면 됨 (DB + 업로드 원본)
- 상태 확인: `systemctl status docportal` / 로그: `journalctl -u docportal -f`

## MaxKB 챗봇 연동 (선택)

[MaxKB](https://github.com/1Panel-dev/MaxKB)를 세우고 환경변수를 설정하면
문서 업로드·수정·복원·삭제가 MaxKB 지식베이스에 자동 반영되어, 사내 문서
기반 RAG 챗봇을 운영할 수 있다 (HWP 포함 — 포털이 추출한 텍스트를 전송).

```bash
docker run -d --name=maxkb -p 8080:8080 -v ~/.maxkb:/opt/maxkb 1panel/maxkb
export DOCPORTAL_MAXKB_URL=http://localhost:8080
export DOCPORTAL_MAXKB_PASSWORD=<MaxKB admin 비밀번호>
export DOCPORTAL_MAXKB_KB_ID=<지식베이스 ID>
export DOCPORTAL_MAXKB_CHAT_URL=<챗봇 공유 링크>   # 상단에 'AI 챗봇' 버튼 노출
```

초기 적재는 관리자 [문서] 화면의 **MaxKB 전체 동기화** 버튼으로 실행한다.

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
