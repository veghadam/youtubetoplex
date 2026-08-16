#!/bin/bash
set -e
DB="/home/devilke/youtubetoplex/data/archiver.db"
BACKUP_DIR="/home/devilke/youtubetoplex/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"

# Copy current DB
cp "$DB" "$BACKUP_DIR/archiver_${TIMESTAMP}.db"

# Keep only last 10 backups
ls -t "$BACKUP_DIR"/archiver_*.db 2>/dev/null | tail -n +11 | xargs -r rm

echo "Backup created: archiver_${TIMESTAMP}.db"
ls -lh "$BACKUP_DIR"/archiver_*.db | head -5
