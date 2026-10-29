#!/usr/bin/env bash
# 맥에서 실행: 서버에 최신 코드를 받게 하고 설치·재시작합니다.
#
#   처음:   ./scripts/push_to_server.sh ubuntu@서버IP ~/.ssh/oracle.key --with-data
#   이후:   ./scripts/push_to_server.sh            (지난번 서버 정보를 기억합니다)
#
# --with-data : 맥 앱이 받아 둔 원문(dart_cache)을 서버로 복사합니다 (서버에서 처음 전체 다운로드를 건너뜀).
#               맥 앱의 API 키도 서버 .env 에 넣습니다(서버에 키가 없을 때만).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SAVED="$ROOT/.server"
REPO_URL="https://github.com/qoodman/opendart.git"
WITH_DATA=0
ARGS=()
for a in "$@"; do
  if [ "$a" = "--with-data" ]; then WITH_DATA=1; else ARGS+=("$a"); fi
done

if [ "${#ARGS[@]}" -ge 1 ]; then
  SERVER="${ARGS[0]}"
  KEY="${ARGS[1]:-$HOME/.ssh/oracle.key}"
  printf 'SERVER=%q\nKEY=%q\n' "$SERVER" "$KEY" > "$SAVED"
elif [ -f "$SAVED" ]; then
  # shellcheck disable=SC1090
  source "$SAVED"
else
  echo "사용법: $0 ubuntu@서버IP [ssh키 경로] [--with-data]"
  exit 1
fi

SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new"
echo "▶ 서버에서 최신 코드 받기 → $SERVER:~/opendart"
$SSH "$SERVER" "if [ -d ~/opendart/.git ]; then cd ~/opendart && git pull -q --ff-only; else git clone -q $REPO_URL ~/opendart; fi"

# 맥 앱 설정(저장 폴더, API 키)
SETTINGS="$HOME/.dart_simple_invest.json"
read_setting() { python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2],''))" "$SETTINGS" "$1" 2>/dev/null || true; }

if [ "$WITH_DATA" = 1 ]; then
  OUT_DIR="$(read_setting out_dir)"
  CACHE="${OUT_DIR:-$HOME/Documents/DART단순투자}/dart_cache"
  if [ -d "$CACHE" ]; then
    echo "▶ 맥에서 받아 둔 원문 복사: $CACHE ($(du -sh "$CACHE" | cut -f1))"
    $SSH "$SERVER" "sudo systemctl stop dart-daily.timer 2>/dev/null || true; mkdir -p ~/opendart/data/dart_cache"
    rsync -az --info=progress2 -e "$SSH" --exclude '*.part' "$CACHE/" "$SERVER:~/opendart/data/dart_cache/"
  else
    echo "! 맥 앱의 원문 폴더를 찾지 못했습니다: $CACHE  (서버가 처음부터 받습니다)"
  fi
fi

echo "▶ 서버에서 설치·재시작"
# -t: 처음 설치 때 DART 전용 Tailscale 로그인 주소를 보여 주고 기다리기 위해
$SSH -t "$SERVER" "cd ~/opendart && ./scripts/install_server.sh"

API_KEY="$(read_setting api_key)"
if [ -n "$API_KEY" ]; then
  # 서버에 키가 비어 있을 때만 맥 앱의 키를 넣음 (키는 명령줄이 아니라 입력으로 보냄)
  printf '%s' "$API_KEY" | $SSH "$SERVER" "cd ~/opendart && .venv/bin/python dart_server.py set-env DART_API_KEY --if-empty"
fi
echo "✔ 서버 갱신 완료"
