#!/usr/bin/env bash
set -euo pipefail

SERVICES=("scc.service" "scc-watch-reolink.service")

for service in "${SERVICES[@]}"; do
  echo "=== ${service} ==="
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user status "${service}" --no-pager --lines=20 || true
  else
    echo "systemctl not available; cannot show status for ${service}."
  fi
  echo
done
