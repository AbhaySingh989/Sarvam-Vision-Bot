#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${DEPLOY_PATH:-}" ]]; then
  echo "DEPLOY_PATH is required"
  exit 1
fi

if [[ -z "${IMAGE_REF:-}" ]]; then
  echo "IMAGE_REF is required (set to a previous image tag)"
  exit 1
fi

cd "$DEPLOY_PATH"
export IMAGE_REF
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d --remove-orphans
docker compose -f docker-compose.prod.yml ps
