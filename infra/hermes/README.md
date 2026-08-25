# Hermes local (localhost-only)

Git copy of the working local Hermes setup. This does not vendor `/opt/hermes`.

- Gateway: `127.0.0.1:8642`
- API model name: `angus-hermes`
- Backend: Ollama `http://localhost:11434/v1`
- Local model: `angus-local` (`qwen3:4b`, `num_ctx 65536`)
- Hermes `context_length`: `65536`
- Secrets live only in `/var/lib/hermes/.env` (`API_SERVER_KEY` is not in Git)

Hermes stays localhost-only with the existing sandbox. Do not add Docker, Frigate, SCC filesystem, web, or terminal access.

## Chatterbox CPU policy

Desired deterministic policy (this repo):

```
infra/hermes/systemd/angus-chatterbox.service.d/override.conf
Environment=CHATTERBOX_DEVICE=cpu
```

The live unit currently still has `CHATTERBOX_DEVICE=cuda`. CPU on the host is happening via low-VRAM fallback. Install the drop-in to make CPU explicit. Do not move Chatterbox back to CUDA. RTX 3060 stays available for Frigate + Ollama.

## Install / deploy

Do not run these as part of a Git-only change. Operator-owned; requires root for systemd and `/var/lib/hermes`.

Prerequisites already true on the working host:

- `hermes` user/group, `HOME=/var/lib/hermes`
- Hermes installed at `/opt/hermes` with `.venv`
- Ollama running; `qwen3:4b` pulled
- Chatterbox unit `angus-chatterbox.service` already installed

```bash
REPO=/home/ross/scrapyard-command-center-hermes
SRC="$REPO/infra/hermes"

# Config (no secrets in this file)
sudo install -o hermes -g hermes -m 640 "$SRC/config.yaml" /var/lib/hermes/config.yaml

# Secrets: copy example only if missing. Never overwrite a live key from Git.
if [ ! -f /var/lib/hermes/.env ]; then
  sudo install -o hermes -g hermes -m 600 "$SRC/.env.example" /var/lib/hermes/.env
  echo "Edit /var/lib/hermes/.env and set API_SERVER_KEY (openssl rand -hex 32)"
fi

# Ollama model
ollama create angus-local -f "$SRC/Modelfile"

# systemd: Hermes unit + isolation, Chatterbox CPU override
sudo install -m 644 "$SRC/systemd/hermes-agent.service" /etc/systemd/system/hermes-agent.service
sudo install -d /etc/systemd/system/hermes-agent.service.d
sudo install -m 644 "$SRC/systemd/hermes-agent.service.d/isolation.conf" \
  /etc/systemd/system/hermes-agent.service.d/isolation.conf
sudo install -d /etc/systemd/system/angus-chatterbox.service.d
sudo install -m 644 "$SRC/systemd/angus-chatterbox.service.d/override.conf" \
  /etc/systemd/system/angus-chatterbox.service.d/override.conf
sudo systemctl daemon-reload
# restart is operator-owned; this runbook does not start or restart services
```

## Verify

Source `API_SERVER_KEY` from the host env file. Do not paste it into Git, shell history notes, or this README.

```bash
systemctl is-active hermes-agent.service
curl -sS http://127.0.0.1:8642/health
# expect {"status": "ok", "platform": "hermes-agent", ...}

KEY=$(sudo awk -F= '/^API_SERVER_KEY=/{print substr($0,index($0,"=")+1)}' /var/lib/hermes/.env)
curl -sS -H "Authorization: Bearer $KEY" http://127.0.0.1:8642/v1/models
curl -sS -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  http://127.0.0.1:8642/v1/chat/completions \
  -d '{"model":"angus-hermes","messages":[{"role":"user","content":"Reply with exactly: HERMES LOCAL 64K OK"}]}'

ollama ps
# expect angus-local with CONTEXT 65536 after the chat call

curl -sS http://127.0.0.1:10210/health
# expect device=cpu, engine=chatterbox, ok=true
```
