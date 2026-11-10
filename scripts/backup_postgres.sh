#!/bin/sh
set -eu
: "${DATABASE_URL:?Set DATABASE_URL}"
: "${BACKUP_DIR:=./backups}"
mkdir -p "$BACKUP_DIR"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
pg_dump "$DATABASE_URL" --format=custom --file="$BACKUP_DIR/ncert-$timestamp.dump"
find "$BACKUP_DIR" -type f -name 'ncert-*.dump' -mtime +30 -delete
