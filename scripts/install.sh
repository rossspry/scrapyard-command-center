#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
SYSTEMD_DIR="$HOME/.config/systemd/user"
SCC_CONFIG_DIR="$HOME/.config/scc"
ENV_PATH="$SCC_CONFIG_DIR/.env"

cd "$REPO_ROOT"

mkdir -p "$SYSTEMD_DIR" "$SCC_CONFIG_DIR"
ln -sfn "$REPO_ROOT" "$SCC_CONFIG_DIR/current"

if [ ! -f "$ENV_PATH" ]; then
  cp "$REPO_ROOT/.env.example" "$ENV_PATH"
  echo "Created $ENV_PATH. Please edit SCC_UI_USERNAME/SCC_UI_PASSWORD before re-running install." >&2
  exit 1
fi

if grep -Eq '^SCC_UI_USERNAME=changeme($|\s)' "$ENV_PATH" || grep -Eq '^SCC_UI_PASSWORD=changeme($|\s)' "$ENV_PATH"; then
  echo "SCC_UI_USERNAME/SCC_UI_PASSWORD must be set to non-default values in $ENV_PATH before starting services." >&2
  exit 1
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

"$REPO_ROOT/.venv/bin/pip" install --upgrade pip
"$REPO_ROOT/.venv/bin/pip" install -e .

cp "$REPO_ROOT"/systemd/*.service "$SYSTEMD_DIR"/

systemctl --user daemon-reload
systemctl --user enable scc.service scc-watch-reolink.service
systemctl --user start scc.service scc-watch-reolink.service

echo "Installation complete. Services enabled and started."
