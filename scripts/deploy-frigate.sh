#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: run as root: sudo ./scripts/deploy-frigate.sh" >&2
  exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)

FRIGATE_ROOT=/srv/frigate
CONFIG_TEMPLATE="${REPO_ROOT}/infra/frigate/config/config.yml"
COMPOSE_SRC="${REPO_ROOT}/infra/frigate/docker-compose.yml"

# Model cache
MODEL_CACHE_DIR="${FRIGATE_ROOT}/config/model_cache"
MODEL_PATH="${MODEL_CACHE_DIR}/yolox_tiny.onnx"
MODEL_URL="https://huggingface.co/unstructuredio/yolo_x_layout/resolve/main/yolox_tiny.onnx"

# Host env file (secrets live here, not in git)
ENV_FILE="${FRIGATE_ROOT}/.env"

# --- Preflight ---
if [[ ! -f "${ENV_FILE}" ]]; then
  cat >&2 <<'MSG'
ERROR: /srv/frigate/.env not found.

Create it like this (example):
  sudo bash -c 'cat > /srv/frigate/.env <<EOF
FRIGATE_RTSP_USER=scc
FRIGATE_RTSP_PASSWORD=YOUR_PASSWORD_HERE
EOF'
MSG
  exit 1
fi

if ! command -v envsubst >/dev/null 2>&1; then
  echo "Installing envsubst (gettext-base)..." >&2
  apt-get update -y
  apt-get install -y gettext-base
fi

# --- Ensure directories ---
mkdir -p "${FRIGATE_ROOT}/config"
mkdir -p "${MODEL_CACHE_DIR}"

# --- Download model if missing ---
if [[ ! -f "${MODEL_PATH}" ]]; then
  curl -fL "${MODEL_URL}" -o "${MODEL_PATH}"
fi

# --- Install compose + render config ---
cp -a "${COMPOSE_SRC}" "${FRIGATE_ROOT}/docker-compose.yml"

# Load variables from /srv/frigate/.env and render the template into /srv/frigate/config/config.yml
set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a
envsubst < "${CONFIG_TEMPLATE}" > "${FRIGATE_ROOT}/config/config.yml"

# --- Deploy ---
docker compose -f "${FRIGATE_ROOT}/docker-compose.yml" pull
docker compose -f "${FRIGATE_ROOT}/docker-compose.yml" up -d
