#!/usr/bin/env bash
set -euo pipefail

SYSTEMCTL_CMD="systemctl --user"

$SYSTEMCTL_CMD daemon-reload >/dev/null 2>&1 || true

$SYSTEMCTL_CMD status scc.service scc-watch-reolink.service --no-pager --lines=20
