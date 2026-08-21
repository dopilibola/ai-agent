#!/usr/bin/env bash
# Nightly dump of the ai-sales Postgres.
#
# The conversation corpus (`conversation_events`) is append-only and is the one
# thing here that cannot be regenerated: catalogue and prices can be re-seeded
# from the Maskan production DB, but what a customer actually typed in July
# exists nowhere else. Everything else in the dump comes along for free.
#
# Two dumps per run:
#   full-*.sql.gz    the whole database (restore target)
#   corpus-*.sql.gz  conversation_events only — small, so it can be kept far
#                    longer than the full dumps on a disk that is already 78% used
#
# Install (once):
#   sudo cp deploy/ai-sales-backup.{sh,service,timer} /etc/systemd/system/  # .sh -> /usr/local/bin
#   sudo systemctl enable --now ai-sales-backup.timer

set -euo pipefail

CONTAINER="${BACKUP_CONTAINER:-ai-sales-postgres-1}"
OUT_DIR="${BACKUP_DIR:-$HOME/backups/ai-sales}"
KEEP_FULL_DAYS="${KEEP_FULL_DAYS:-10}"
KEEP_CORPUS_DAYS="${KEEP_CORPUS_DAYS:-90}"
STAMP="$(date -u +%Y%m%d-%H%M)"

# Credentials come from the running container, not from a copy that can drift.
DB_USER="$(docker exec "$CONTAINER" printenv POSTGRES_USER)"
DB_NAME="$(docker exec "$CONTAINER" printenv POSTGRES_DB)"

mkdir -p "$OUT_DIR"

full="$OUT_DIR/full-$STAMP.sql.gz"
corpus="$OUT_DIR/corpus-$STAMP.sql.gz"

docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" | gzip -9 > "$full.part"
mv "$full.part" "$full"

docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" \
    --table=conversation_events --data-only | gzip -9 > "$corpus.part"
mv "$corpus.part" "$corpus"

# A zero-length dump is worse than none: it looks like a backup and restores to
# nothing. Fail loudly so the watchdog's unit-failure alert fires.
for f in "$full" "$corpus"; do
    if [ ! -s "$f" ] || [ "$(stat -c%s "$f")" -lt 200 ]; then
        echo "backup produced an empty file: $f" >&2
        exit 1
    fi
done

find "$OUT_DIR" -name 'full-*.sql.gz'   -mtime "+$KEEP_FULL_DAYS"   -delete
find "$OUT_DIR" -name 'corpus-*.sql.gz' -mtime "+$KEEP_CORPUS_DAYS" -delete

rows="$(docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t -A \
        -c 'select count(*) from conversation_events')"
echo "backup ok: $(basename "$full") $(du -h "$full" | cut -f1), corpus rows=$rows"
