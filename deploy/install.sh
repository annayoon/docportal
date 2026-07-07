#!/usr/bin/env bash
# DocPortal 사내 서버 설치 스크립트 (Ubuntu/Debian 기준, root 또는 sudo로 실행)
#
#   sudo bash deploy/install.sh
#
# 하는 일:
#   1) 시스템 패키지 설치 (python3-venv, nginx; LibreOffice는 선택)
#   2) 서비스 계정(docportal) + /opt/docportal(코드) + /var/lib/docportal(데이터) 준비
#   3) venv 생성 + 의존성 설치
#   4) systemd 서비스 등록·기동, nginx 사이트 등록
#
# 재실행해도 안전(멱등)하게 작성됨. 코드 업데이트 시에도 이 스크립트를 다시 실행하면 됨.

set -euo pipefail

APP_DIR=/opt/docportal
DATA_DIR=/var/lib/docportal
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "root 권한이 필요합니다: sudo bash deploy/install.sh" >&2
  exit 1
fi

echo "==> 1/5 시스템 패키지 설치"
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip nginx rsync
# 형식 변환(PDF/Word) 기능을 쓰려면 주석 해제:
# apt-get install -y -qq libreoffice --no-install-recommends

echo "==> 2/5 서비스 계정·디렉토리 준비"
id docportal &>/dev/null || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin docportal
mkdir -p "$APP_DIR" "$DATA_DIR"

echo "==> 3/5 코드 배치 + 의존성 설치"
rsync -a --delete \
  --exclude '.git' --exclude '.venv' --exclude 'data' --exclude '__pycache__' \
  "$REPO_DIR/" "$APP_DIR/"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
chown -R docportal:docportal "$APP_DIR" "$DATA_DIR"

echo "==> 4/5 systemd 서비스 등록"
cp "$APP_DIR/deploy/docportal.service" /etc/systemd/system/docportal.service
systemctl daemon-reload
systemctl enable --now docportal
systemctl restart docportal

echo "==> 5/5 nginx 설정"
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/docportal
ln -sf /etc/nginx/sites-available/docportal /etc/nginx/sites-enabled/docportal
nginx -t && systemctl reload nginx

echo
echo "설치 완료!"
echo "  - 서비스 상태 : systemctl status docportal"
echo "  - 로그 확인   : journalctl -u docportal -f"
echo "  - 접속 주소   : http://<이 서버 주소>/  (server_name은 deploy/nginx.conf에서 수정)"
echo "  - 데이터 위치 : $DATA_DIR  (백업은 이 디렉토리만 챙기면 됩니다)"
echo "  - 첫 가입자가 자동으로 관리자가 됩니다."
