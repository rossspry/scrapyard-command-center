#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="${HOME}/.config/scc"
ENV_FILE="${CONFIG_DIR}/.env"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
PLACEHOLDER_VALUE="changeme"
DEFAULT_BIND="127.0.0.1"
SERVICES=("scc.service" "scc-watch-reolink.service")
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() {
  echo "ERROR: $1" >&2
  exit 1
}

ensure_env_file() {
  if [[ -f "${ENV_FILE}" ]]; then
    return
  fi

  mkdir -p "${CONFIG_DIR}"
  if [[ -f "${REPO_DIR}/config/.env.example" ]]; then
    cp "${REPO_DIR}/config/.env.example" "${ENV_FILE}"
  else
    cat <<TEMPLATE > "${ENV_FILE}"
# UI authentication
SCC_UI_USERNAME=${PLACEHOLDER_VALUE}
SCC_UI_PASSWORD=${PLACEHOLDER_VALUE}

# UI bind host
SCC_UI_BIND=${DEFAULT_BIND}
TEMPLATE
  fi

  fail "Created ${ENV_FILE} with placeholder credentials. Update SCC_UI_USERNAME and SCC_UI_PASSWORD before re-running the installer."
}

load_env() {
  # shellcheck source=/dev/null
  source "${ENV_FILE}"
}

validate_credentials() {
  if [[ "${SCC_UI_USERNAME:-${PLACEHOLDER_VALUE}}" == "${PLACEHOLDER_VALUE}" || "${SCC_UI_PASSWORD:-${PLACEHOLDER_VALUE}}" == "${PLACEHOLDER_VALUE}" ]]; then
    fail "SCC_UI_USERNAME or SCC_UI_PASSWORD is still set to the placeholder value in ${ENV_FILE}. Edit the file before enabling SCC services."
  fi
}

ensure_bind_address() {
  if ! grep -q "^SCC_UI_BIND=" "${ENV_FILE}"; then
    echo "SCC_UI_BIND=${DEFAULT_BIND}" >> "${ENV_FILE}"
    echo "Defaulted SCC_UI_BIND to ${DEFAULT_BIND} to keep the UI bound locally. Update the value and use a reverse proxy if you intend to expose it."
  fi
}

install_units() {
  mkdir -p "${SYSTEMD_USER_DIR}"
  for service in "${SERVICES[@]}"; do
    local source_unit="${REPO_DIR}/config/systemd/${service}"
    [[ -f "${source_unit}" ]] || fail "Missing unit file ${source_unit}"
    sed "s|__REPO_DIR__|${REPO_DIR}|g" "${source_unit}" > "${SYSTEMD_USER_DIR}/${service}"
  done
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload
  else
    echo "systemctl not found; skipped daemon-reload."
  fi
}

enable_services() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not found; please enable services manually in ${SYSTEMD_USER_DIR}."
    return
  fi

  for service in "${SERVICES[@]}"; do
    echo "Enabling and starting ${service}..."
    systemctl --user enable --now "${service}"
  done
}

ensure_env_file
load_env
validate_credentials
ensure_bind_address
install_units
enable_services
