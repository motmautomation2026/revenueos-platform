#!/usr/bin/env bash
# Build and (re)start RevenueOS on the VPS. Safe to run again for every update.
#   ./deploy/deploy.sh            pull main, rebuild what changed, restart
#   ./deploy/deploy.sh --no-pull  rebuild the code as it is in this folder
set -euo pipefail
cd "$(dirname "$0")/.."

for file in .env brain/.env; do
  [ -f "$file" ] || { echo "Missing $file: copy it to the VPS first (see deploy/README.md)."; exit 1; }
done

if [ "${1:-}" != "--no-pull" ]; then
  git pull --ff-only
fi

docker compose up -d --build --remove-orphans
docker image prune -f >/dev/null

docker compose ps
echo
echo "App:    curl -sI http://127.0.0.1:3000/login | head -1"
echo "Logs:   docker compose logs -f worker     (or: app)"
