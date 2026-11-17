#!/bin/sh
# Daily database backup (app + n8n). Run on the server from the project folder, e.g. via cron:
#   0 3 * * *  cd /opt/wa-agent && sh deploy/backup.sh >> /var/log/wa-agent-backup.log 2>&1
# Restore one:  gunzip -c backups/wa_agent-<date>.sql.gz | docker exec -i wa-agent-postgres psql -U <user> -d wa_agent
set -e
cd "$(dirname "$0")/.."
. ./.env
mkdir -p backups
STAMP=$(date +%Y-%m-%d_%H%M)
for DB in "$POSTGRES_DB" n8n; do
  docker exec wa-agent-postgres pg_dump -U "$POSTGRES_USER" -d "$DB" --no-owner | gzip > "backups/${DB}-${STAMP}.sql.gz"
  echo "backup written: backups/${DB}-${STAMP}.sql.gz"
done
# Keep 14 days
find backups -name '*.sql.gz' -mtime +14 -delete
