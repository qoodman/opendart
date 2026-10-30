#!/usr/bin/env bash
# 서버에서 최신 코드를 받아 설치합니다. 대시보드 '설정 → 프로그램 업데이트' 버튼이 부릅니다.
# 직접 실행해도 됩니다:  ~/opendart/scripts/update_server.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "▶ $(date '+%Y-%m-%d %H:%M') 업데이트 시작"
BEFORE="$(git rev-parse --short HEAD)"
git pull --ff-only -q
AFTER="$(git rev-parse --short HEAD)"
if [ "$BEFORE" = "$AFTER" ]; then
  echo "✔ 이미 최신입니다 ($AFTER)"
  exit 0
fi
echo "▶ 바뀐 내용:"
git log --format='  · %s' "$BEFORE..$AFTER" | head -20
./scripts/install_server.sh
echo "✔ 업데이트 완료 $BEFORE → $AFTER"
