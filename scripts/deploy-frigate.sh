#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)

FRIGATE_ROOT=/srv/frigate
CONFIG_SRC="${REPO_ROOT}/infra/frigate/config"
COMPOSE_SRC="${REPO_ROOT}/infra/frigate/docker-compose.yml"
MODEL_CACHE_DIR="${FRIGATE_ROOT}/config/model_cache"
MODEL_PATH="${MODEL_CACHE_DIR}/yolox_tiny.onnx"
MODEL_URL="https://huggingface.co/unstructuredio/yolo_x_layout/resolve/main/yolox_tiny.onnx"

mkdir -p "${MODEL_CACHE_DIR}"

if [[ ! -f "${MODEL_PATH}" ]]; then
  curl -fL "${MODEL_URL}" -o "${MODEL_PATH}"
fi

mkdir -p "${FRIGATE_ROOT}/config"
cp -a "${CONFIG_SRC}/." "${FRIGATE_ROOT}/config/"
cp -a "${COMPOSE_SRC}" "${FRIGATE_ROOT}/docker-compose.yml"

docker compose -f "${FRIGATE_ROOT}/docker-compose.yml" pull

docker compose -f "${FRIGATE_ROOT}/docker-compose.yml" up -d
