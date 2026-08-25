
- Filesystem: ext4
- Mount options: `defaults,noatime`
- `/mnt/video` is persistent via `/etc/fstab`

This layout is **canonical** for SCC v1.0.

---

## 6. GPU STACK (CONFIRMED WORKING)

- NVIDIA Driver: **535.xx**
- CUDA: via container images only
- Secure Boot: Off
- `nvidia-smi`: working on host
- GPU passthrough: confirmed inside Docker containers
- RTX 3060 remains available for Frigate + Ollama
- Desired Chatterbox TTS policy: CPU (`CHATTERBOX_DEVICE=cpu`) so it does not consume VRAM needed by Ollama

**Important:**  
TensorRT detector is **no longer supported** by Frigate on amd64 systems.

---

## 7. DOCKER STACK (CONFIRMED WORKING)

- Docker Engine: 29.x (official Docker repo)
- Docker Compose plugin: installed
- User `ross`: member of `docker` group
- NVIDIA Container Toolkit: installed and configured
- GPU-in-container test: **PASSED**

Docker is the **only** supported runtime for SCC services.

---

## 8. FRIGATE STATUS (v1.0)

### Deployment
- Runs in Docker
- Config path: `/srv/frigate/config`
- Media path: `/mnt/video/frigate`
- UI: `http://192.168.1.3:5000`
- MQTT broker: local Mosquitto

### Cameras
- Total on site: ~21
- Managed by Frigate v1.0: **10 Reolink cameras**
- Current test cameras:
  - `Frontgate` (IP .40)
  - `Signpost` (IP .31)

### Recording Policy
- Camera DVRs: record **24/7**
- Frigate: **events only** (motion / person / vehicle)
- Frigate recordings retained for review & automation

### Detection (IMPORTANT)
- ❌ TensorRT: REMOVED / NOT SUPPORTED
- ✅ Current detector: **CPU** (temporary, stable)
- 🔜 Planned detector: **ONNX (GPU)**

ONNX migration is a **planned v1.1 task**, not a failure.

---

## 9. BUSINESS RULES (CURRENT)

- Business hours (as of 2026-01-08):
  - Thursday–Saturday
  - 10:00 AM – 4:30 PM
- Outside business hours:
  - Cameras are notify-only by default
  - Announcements are suppressed unless manually enabled

---

## 10. SECURITY & PRIVACY POLICY (v1.0)

### Explicitly NOT implemented
- ❌ Facial recognition
- ❌ Identity matching
- ❌ Biometric identification
- ❌ Staff/person databases tied to faces

**Reason:** Legal, ethical, and operational risk.

### Planned for v2.0 (DEFERRED)
- Optional facial recognition
- Explicit consent model
- Separate enablement layer
- Clear audit controls

Nothing biometric may be added without updating this file.

---

## 11. VOICE & LLM INTEGRATION

### Assistant (Local LLM) — WORKING (localhost only)

- Runtime: Hermes Agent (`hermes-agent.service`)
- Gateway: `127.0.0.1:8642` (localhost only)
- API model name: `angus-hermes`
- Backend: Ollama `http://localhost:11434/v1`
- Local model: `angus-local` (based on `qwen3:4b`, `PARAMETER num_ctx 65536`)
- Hermes `context_length`: `65536`
- Git source: `infra/hermes/`
- Secrets: `/var/lib/hermes/.env` stays on the host; `API_SERVER_KEY` is not in Git
- Isolation: `hermes` user, `ProtectSystem=strict`, `ReadWritePaths=/var/lib/hermes`
- Isolation drop-in blocks `/opt/angus`, SCC UI, Frigate, timeclock, SCC config, Grok, and Docker
- Disabled toolsets include terminal, file, web, browser, and other non-memory tools
- End-to-end gateway test succeeded with: `HERMES LOCAL 64K OK`

Policy remains local-first. This setup does not grant Hermes Docker, Frigate, SCC filesystem, web, or terminal access.

### Voice Interface

- Full SCC voice assistant (wake phrase / yard commands) is still deferred
- Voice is an interface layer, not the decision engine
- Chatterbox TTS (`angus-chatterbox.service`) is in use on the host
- Desired deterministic policy: systemd drop-in `CHATTERBOX_DEVICE=cpu` (tracked in `infra/hermes/systemd/angus-chatterbox.service.d/override.conf`)
- Live unit currently still has `CHATTERBOX_DEVICE=cuda`; CPU is happening via low-VRAM fallback until that drop-in is installed
- Do not move Chatterbox back to CUDA

### Goals (unchanged)
- Local (“in-house”) LLM
- Voice wake phrase: “Hey Scrapyard”
- Commands such as:
  - “What’s today’s price for irony aluminum?”
  - “Clock Crystal in”
  - “Announce yard closing in 10 minutes”

---

## 12. PROJECT GOVERNANCE

### Golden Rules
1. This file is authoritative.
2. If reality changes, this file must change.
3. Experimental changes must be labeled as such.
4. Stable checkpoints are preferred over constant refactors.

### Tooling Philosophy
- ChatGPT: planning, architecture, review
- Codex / coding agents: multi-file edits, refactors, PR-style changes
- Git repo: source of truth
- Server: pull & deploy only

---

## 13. CURRENT CHECKPOINT

**SCC v1.0 Foundation: COMPLETE**

Hermes local (64k, localhost-only) is captured in `infra/hermes`.

Next planned milestones:
1. ONNX GPU detector migration
2. Expand Frigate from 2 → 10 cameras
3. Rule engine formalization
4. Voice interface on top of the local assistant (v1.1)
5. Facial recognition review (v2.0 only)

---

_End of SCC_STATE.md_
