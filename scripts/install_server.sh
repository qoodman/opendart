#!/usr/bin/env bash
# 리눅스 서버(오라클 클라우드 우분투 등) 설치 스크립트. 서버 안에서 실행합니다.
#   ./scripts/install_server.sh      설치 + 매일 자동 수집 + 대시보드 상시 실행 (+ Tailscale 있으면 공개)
# 여러 번 실행해도 안전합니다 (업데이트 후 다시 실행하면 됨).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"
RE_ENV="$HOME/realestate/.env"      # 부동산 앱 설정 (있으면 알림·주소를 가져옴)
cd "$ROOT"

echo "▶ 시간대를 한국으로"
sudo timedatectl set-timezone Asia/Seoul || true

# 무료 서버(메모리 1GB)는 분석·엑셀 저장 중 메모리가 모자랄 수 있어 스왑 2GB를 만듭니다
if [ "$(free -m | awk '/Mem:/{print $2}')" -lt 2000 ] && ! swapon --show | grep -q .; then
  echo "▶ 스왑 2GB 만들기"
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  echo "▶ uv 설치"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"

echo "▶ 파이썬 패키지 설치"
[ -x .venv/bin/python ] || "$UV" venv --python 3.12 .venv
"$UV" pip install --python .venv/bin/python -q -r requirements.txt
PY="$ROOT/.venv/bin/python"
mkdir -p data

envget() { [ -f "$1" ] && grep -E "^$2=" "$1" | tail -1 | cut -d= -f2- || true; }
envset() {  # envset KEY VALUE : .env 의 KEY 값을 바꿈 (없으면 추가)
  if grep -qE "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "▶ .env 를 만들었습니다: $ROOT/.env"
  # 부동산 앱과 같은 ntfy 주제·주소를 그대로 씀
  TOPIC="$(envget "$RE_ENV" NTFY_TOPIC)"
  RE_URL="$(envget "$RE_ENV" DASHBOARD_URL)"
  [ -n "$TOPIC" ] && envset NTFY_TOPIC "$TOPIC" && echo "  · 부동산 앱의 ntfy 주제를 같이 씁니다"
  [ -n "$RE_URL" ] && envset REALESTATE_URL "$RE_URL"
fi

echo "▶ 자동 실행 등록 (systemd)"
for f in dart-web.service dart-daily.service dart-tick.service dart-tick.timer; do
  sed -e "s|__ROOT__|$ROOT|g" -e "s|__PY__|$PY|g" -e "s|__USER__|$USER_NAME|g" \
    "scripts/systemd/$f" | sudo tee "/etc/systemd/system/$f" >/dev/null
done
sudo systemctl daemon-reload
# 예전 방식(고정 20:13 타이머)은 끄고, 설정 화면의 시각을 따르는 10분 확인 타이머로
sudo systemctl disable --now dart-daily.timer >/dev/null 2>&1 || true
sudo rm -f /etc/systemd/system/dart-daily.timer
sudo systemctl daemon-reload
sudo systemctl enable --now dart-web.service dart-tick.timer
sudo systemctl restart dart-web.service

# ---- 대시보드 주소: DART 전용 Tailscale 연결로 https://dart.xxxx.ts.net ----
TS_NAME="$(envget .env DART_TS_HOSTNAME)"; TS_NAME="${TS_NAME:-dart}"
TSD="/run/tailscale-dart/tailscaled.sock"
TS2() { sudo tailscale --socket="$TSD" "$@"; }
ts_dns() { "$PY" -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true; }
URL=""
if command -v tailscale >/dev/null 2>&1 && command -v tailscaled >/dev/null 2>&1; then
  echo "▶ DART 전용 Tailscale 연결 ($TS_NAME)"
  sed -e "s|__TAILSCALED__|$(command -v tailscaled)|g" scripts/systemd/tailscaled-dart.service \
    | sudo tee /etc/systemd/system/tailscaled-dart.service >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable --now tailscaled-dart.service
  for _ in 1 2 3 4 5 6 7 8 9 10; do [ -S "$TSD" ] && break; sleep 1; done
  if ! TS2 status >/dev/null 2>&1; then
    echo "  처음 한 번 로그인이 필요합니다. 아래 주소를 브라우저에서 열고 폰과 같은 계정으로 로그인하세요."
    TS2 up --hostname="$TS_NAME" --timeout=10m || true
  fi
  if TS2 status >/dev/null 2>&1; then
    TS2 serve --bg 8766 >/dev/null
    NAME="$(TS2 status --json | ts_dns)"
    [ -n "$NAME" ] && URL="https://$NAME"
    # 예전 방식(부동산 주소:8443)으로 열어 둔 것은 닫음
    sudo tailscale serve --https=8443 off >/dev/null 2>&1 || true
  else
    echo "! 로그인이 끝나지 않았습니다. 다시 실행하면 이어서 진행합니다: ./scripts/install_server.sh"
  fi
  # 부동산 대시보드 주소 (기본 Tailscale)
  if [ -z "$(envget .env REALESTATE_URL)" ] && tailscale status >/dev/null 2>&1; then
    RE_NAME="$(tailscale status --json | ts_dns)"
    [ -n "$RE_NAME" ] && envset REALESTATE_URL "https://$RE_NAME"
  fi
  if [ -n "$URL" ]; then
    CUR="$(envget .env DASHBOARD_URL)"
    # 비어 있거나 예전 :8443 주소면 새 주소로
    if [ -z "$CUR" ] || [[ "$CUR" == *":8443"* ]]; then envset DASHBOARD_URL "$URL"; fi
  fi
else
  echo "! Tailscale이 설치되어 있지 않습니다. 부동산 앱 안내(docs/SERVER.md 4번)대로 먼저 설치하고 이 스크립트를 다시 실행하세요."
fi

echo
echo "✔ 설치 완료"
[ -z "$(envget .env DART_API_KEY)" ] && echo "  ✖ $ROOT/.env 의 DART_API_KEY 를 채워주세요:  nano $ROOT/.env"
[ -n "$URL" ] && echo "  폰 대시보드:     $URL"
echo "  접속 확인:       $PY $ROOT/dart_server.py net-check"
echo "  지금 한 번 수집: sudo systemctl start --no-block dart-daily && journalctl -u dart-daily -f"
echo "  자동 수집 시각:  대시보드 → 설정 (기본 20:13)"
