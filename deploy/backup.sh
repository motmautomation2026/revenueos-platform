#!/usr/bin/env bash
# Copy the Brain's customer folders out of the Docker volume into a dated .tar.gz.
#   ./deploy/backup.sh                 -> ./backups/brain-customers-YYYYmmdd-HHMM.tar.gz
# Restore (worker stopped first):
#   docker compose stop worker
#   docker run --rm -v revenueos_brain-customers:/to -v "$PWD/backups":/from alpine \
#     sh -c 'cd /to && tar xzf /from/<file>.tar.gz'
#   docker compose start worker
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
name="brain-customers-$(date +%Y%m%d-%H%M).tar.gz"
docker run --rm -v revenueos_brain-customers:/from:ro -v "$PWD/backups":/to alpine \
  sh -c "cd /from && tar czf /to/$name ."
echo "Saved backups/$name"
