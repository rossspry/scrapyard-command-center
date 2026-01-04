#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="${HOME}/.config/scc"
ENV_FILE="${CONFIG_DIR}/.env"
SERVICE_NAME="scc-ui.service"
PLACEHOLDER_VALUE="changeme"
DEFAULT_BIND="127.0.0.1"

fail() {
  echo "ERROR: $1" >&2
  exit 1
}

ensure_env_file() {
  if [[ -f "${ENV_FILE}" ]]; then
    return
  fi

  mkdir -p "${CONFIG_DIR}"
  cat <<TEMPLATE > "${ENV_FILE}"
# UI authentication
SCC_UI_USER=${PLACEHOLDER_VALUE}
SCC_UI_PASS=${PLACEHOLDER_VALUE}

# Binding
# SCC_UI_BIND=${DEFAULT_BIND}
TEMPLATE

  fail "Created ${ENV_FILE} with placeholder credentials. Update SCC_UI_USER and SCC_UI_PASS before re-running the installer."
}

load_env() {
  # shellcheck source=/dev/null
  source "${ENV_FILE}"
}

validate_credentials() {
  if [[ "${SCC_UI_USER:-${PLACEHOLDER_VALUE}}" == "${PLACEHOLDER_VALUE}" || "${SCC_UI_PASS:-${PLACEHOLDER_VALUE}}" == "${PLACEHOLDER_VALUE}" ]]; then
    fail "SCC_UI_USER or SCC_UI_PASS is still set to the placeholder value in ${ENV_FILE}. Edit the file before enabling the UI service."
  fi
}

ensure_bind_address() {
  if ! grep -q "^SCC_UI_BIND=" "${ENV_FILE}"; then
    echo "SCC_UI_BIND=${DEFAULT_BIND}" >> "${ENV_FILE}"
    echo "Defaulted SCC_UI_BIND to ${DEFAULT_BIND} to keep the UI bound locally. Update the value if you intend to expose it via reverse proxy."
  fi
}

enable_service() {
  echo "Enabling and starting ${SERVICE_NAME}..."
  if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl enable --now "${SERVICE_NAME}"
  else
    echo "systemctl not found; please enable ${SERVICE_NAME} manually."
  fi
}

ensure_env_file
load_env
validate_credentials
ensure_bind_address
enable_service
