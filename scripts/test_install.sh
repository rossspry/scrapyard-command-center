#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="${HOME}/.config/scc"
ENV_FILE="${CONFIG_DIR}/.env"
PLACEHOLDER_VALUE="changeme"
SERVICES=("scc.service" "scc-watch-reolink.service")

[[ -f "${ENV_FILE}" ]] || { echo "${ENV_FILE} is missing."; exit 1; }
# shellcheck source=/dev/null
source "${ENV_FILE}"

if [[ "${SCC_UI_USERNAME:-${PLACEHOLDER_VALUE}}" == "${PLACEHOLDER_VALUE}" || "${SCC_UI_PASSWORD:-${PLACEHOLDER_VALUE}}" == "${PLACEHOLDER_VALUE}" ]]; then
  echo "SCC_UI_USERNAME or SCC_UI_PASSWORD is still set to '${PLACEHOLDER_VALUE}'."
  exit 1
fi

if [[ -z "${SCC_UI_BIND:-}" ]]; then
  echo "SCC_UI_BIND is not set."
  exit 1
fi

echo "Environment file looks good."

if command -v systemctl >/dev/null 2>&1; then
  for service in "${SERVICES[@]}"; do
    echo "Checking ${service}..."
    if systemctl --user is-enabled "${service}" >/dev/null 2>&1; then
      echo "${service} is enabled."
    else
      echo "${service} is not enabled."
    fi
  done
else
  echo "systemctl not available; skipping service checks."
fi
