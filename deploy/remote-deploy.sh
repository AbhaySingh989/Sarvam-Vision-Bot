#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${DEPLOY_PATH:-}" ]]; then
  echo "DEPLOY_PATH is required"
  exit 1
fi

if [[ -z "${IMAGE_REF:-}" ]]; then
  echo "IMAGE_REF is required"
  exit 1
fi

mkdir -p "$DEPLOY_PATH"
cd "$DEPLOY_PATH"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not installed on target host"
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose plugin is not available on target host"
  exit 1
fi

if [[ ! -f ".env" ]]; then
  echo ".env missing in $DEPLOY_PATH. Create it before deployment."
  exit 1
fi

if [[ ! -f "docker-compose.prod.yml" ]]; then
  echo "docker-compose.prod.yml missing in $DEPLOY_PATH"
  exit 1
fi

echo "Deploying image: $IMAGE_REF"
export IMAGE_REF
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d --remove-orphans
docker image prune -f
docker compose -f docker-compose.prod.yml ps
