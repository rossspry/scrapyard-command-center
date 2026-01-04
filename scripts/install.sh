#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
SYSTEMD_DIR="$HOME/.config/systemd/user"
SCC_CONFIG_DIR="$HOME/.config/scc"
ENV_PATH="$SCC_CONFIG_DIR/.env"

cd "$REPO_ROOT"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

"$REPO_ROOT/.venv/bin/pip" install --upgrade pip
"$REPO_ROOT/.venv/bin/pip" install -e .

mkdir -p "$SYSTEMD_DIR" "$SCC_CONFIG_DIR"
ln -sfn "$REPO_ROOT" "$SCC_CONFIG_DIR/current"

if [ ! -f "$ENV_PATH" ]; then
  cp "$REPO_ROOT/.env.example" "$ENV_PATH"
fi

cp "$REPO_ROOT"/systemd/*.service "$SYSTEMD_DIR"/

systemctl --user daemon-reload
systemctl --user enable scc.service scc-watch-reolink.service
systemctl --user start scc.service scc-watch-reolink.service

echo "Installation complete. Services enabled and started."
