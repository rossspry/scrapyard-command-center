
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
- ✅ Current detector: **ONNX (GPU)**
- Execution providers available in-container: **CUDAExecutionProvider**, **TensorrtExecutionProvider** (CPU fallback present)
- Model: **yolox_tiny.onnx** (loaded by detector)

ONNX is **active now** (not just planned). Keep changes deterministic: manage Frigate config under `infra/frigate` and deploy to `/srv/frigate`.

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

## 11. VOICE & LLM INTEGRATION (PLANNED)

### Assistant (Local LLM)

- Experimental runtime: Dolphin Llama 3 8B (non-binding)
- Policy: local-first assistant
- Web fallback allowed only when explicitly enabled
- All external queries must be logged

The local assistant is **v1.1+**, not part of v1.0.

### Voice Interface

- Voice input/output is deferred
- Will integrate only after assistant logic is stable
- Voice is an interface layer, not the decision engine

### Goals
- Local (“in-house”) LLM
- Voice wake phrase: “Hey Scrapyard”
- Commands such as:
  - “What’s today’s price for irony aluminum?”
  - “Clock Crystal in”
  - “Announce yard closing in 10 minutes”

### Status
- Infrastructure planned
- No LLM runtime deployed yet
- No voice listeners active yet

Voice + LLM is **v1.1+**, not v1.0.

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

Next planned milestones:
1. ONNX GPU detector migration
2. Expand Frigate from 2 → 10 cameras
3. Rule engine formalization
4. Voice + LLM integration (v1.1)
5. Facial recognition review (v2.0 only)

---

_End of SCC_STATE.md_
