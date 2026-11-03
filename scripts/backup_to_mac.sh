#!/usr/bin/env bash
# 맥에서 실행: 서버의 최신 백업을 맥으로 내려받습니다. (맥을 켜 둔 때만 동작)
#   사용: ./scripts/backup_to_mac.sh ubuntu@서버IP ~/.ssh/oracle.key [받을 폴더]
set -euo pipefail
SERVER="${1:?사용: $0 ubuntu@서버IP ssh키경로 [받을 폴더]}"
KEY="${2:-$HOME/.ssh/oracle.key}"
DEST="${3:-$HOME/opendart-backup}"
mkdir -p "$DEST"
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new"
# 서버에서 백업을 새로 만든 뒤 가장 최근 것을 받음
$SSH "$SERVER" "~/opendart/scripts/backup_server.sh >/dev/null"
LATEST="$($SSH "$SERVER" "ls -1t ~/backups/dart-backup-*.tar.gz | head -1")"
NAME="$(basename "$LATEST")"
rsync -az -e "$SSH" "$SERVER:$LATEST" "$DEST/$NAME"
chmod 600 "$DEST/$NAME"
# 맥 쪽도 최근 30개만 유지
ls -1t "$DEST"/dart-backup-*.tar.gz | tail -n +31 | xargs -r rm -f
echo "✔ 맥으로 받음: $DEST/$NAME"
