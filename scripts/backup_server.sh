#!/usr/bin/env bash
# 서버 백업: 결과 데이터베이스, 원문 캐시, 설정(.env), 분석·표 확인 기록을 날짜별 압축 파일로 만듭니다.
# 기본: ~/backups/ 에 만들고 최근 7개만 남깁니다.   사용: ./scripts/backup_server.sh [백업 폴더] [보관 개수]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$HOME/backups}"
KEEP="${2:-7}"
DATA="${DART_DATA_DIR:-$(grep -E '^DART_DATA_DIR=' "$ROOT/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true)}"
DATA="${DATA:-$ROOT/data}"
mkdir -p "$DEST"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST/dart-backup-$STAMP.tar.gz"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 결과 데이터베이스는 쓰는 중에도 안전하게 복사 (sqlite 백업 기능 사용)
if [ -f "$DATA/results.sqlite" ]; then
  python3 - "$DATA/results.sqlite" "$TMP/results.sqlite" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
a = sqlite3.connect(src); b = sqlite3.connect(dst)
a.backup(b); b.close(); a.close()
PY
fi
# 나머지 파일 (원문 zip·주가 캐시는 용량이 커서 같이 넣음)
mkdir -p "$TMP/data"
for f in status.json last_auto.txt last_update_day.txt update_check.json reports_last.json last_cal_notify.txt update.log; do
  [ -f "$DATA/$f" ] && cp "$DATA/$f" "$TMP/data/" || true
done
[ -f "$ROOT/.env" ] && cp "$ROOT/.env" "$TMP/env.txt"
[ -f "$TMP/results.sqlite" ] && mv "$TMP/results.sqlite" "$TMP/data/results.sqlite"
[ -d "$DATA/dart_cache" ] && cp -r "$DATA/dart_cache" "$TMP/data/" || true

tar -czf "$OUT" -C "$TMP" .
chmod 600 "$OUT"
echo "✔ 백업: $OUT ($(du -h "$OUT" | cut -f1))"

# 오래된 백업 정리 (최근 KEEP개만)
ls -1t "$DEST"/dart-backup-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do rm -f "$old"; echo "  정리: $old"; done
