#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: run as root: sudo ./scripts/deploy-frigate.sh" >&2
  exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)

FRIGATE_ROOT=/srv/frigate
CONFIG_SRC="${REPO_ROOT}/infra/frigate/config"
COMPOSE_SRC="${REPO_ROOT}/infra/frigate/docker-compose.yml"

# Deploy from git (authoritative).
# Secrets live in ${FRIGATE_ROOT}/.env and must not be committed or overwritten.
mkdir -p "${FRIGATE_ROOT}/config"
rsync -a --delete --exclude=".env" "${CONFIG_SRC}/" "${FRIGATE_ROOT}/config/"
cp -a "${COMPOSE_SRC}" "${FRIGATE_ROOT}/docker-compose.yml"

docker compose -f "${FRIGATE_ROOT}/docker-compose.yml" pull
docker compose -f "${FRIGATE_ROOT}/docker-compose.yml" up -d

docker ps --filter name=frigate --format "table {{.Names}}\t{{.Status}}"
