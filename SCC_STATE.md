# Scrapyard Command Center (SCC) — State File (Single Source of Truth)

Last updated: 2026-01-07
Owner: Ross Spry
Timezone: America/New_York

## Core principles (Ross preferences)
- ONE thing at a time (serialized workflow). No parallel “do 5 commands” instructions.
- When changing configs/code: **send the entire file** (not partial snippets).
- Keep commands paste-safe and simple (prefer bash on scc-core).
- Don’t re-ask for known credentials or paths; treat this file as the memory.

---

## Current machine + key hostnames
- Primary server: `scc-core` (Ubuntu/Linux)
- Frigate runs in Docker container named: `frigate`
- Frigate web UI: `http://192.168.1.3:5000`

---

## Storage / mounts (recordings must go to the big drive)
### Big video drive (recordings/media)
- Mount: `/mnt/video`
- Device: `/dev/sda2` (ext4)
- Size: ~1.8T
- Used: ~1% (as of 2026-01-07)

### OS drive
- Mount: `/`
- Device: `/dev/nvme0n1p2` (ext4)
- Size: ~937G

### Docker mounts for Frigate (verified via docker inspect)
- `/srv/frigate/config` -> `/config` (inside container)
- `/mnt/video/frigate/media` -> `/media/frigate` (inside container)
- `/mnt/video/frigate/cache` -> `/tmp/cache` (inside container)

**Policy:** Recordings must live on `/mnt/video/...` (the 1.8T drive), not the OS drive.

---

## Frigate config and database locations (do not “guess”)
### Frigate config file
- Host path: `/srv/frigate/config/config.yml`
- Container path: `/config/config.yml`

### Frigate DB
- Host path: `/srv/frigate/config/frigate.db`
- WAL/SHM may exist: `frigate.db-wal`, `frigate.db-shm`

### Media directories
- Recordings dir (host): `/mnt/video/frigate/media/recordings`
- Snapshots/clips are also under `/mnt/video/frigate/media` depending on config

---

## Camera credentials (do not ask again)
- Username: `scc`
- Password: `scc12345`

---

## Cameras in service
### Camera: front_gate
- IP: `192.168.1.40`
- Main stream (record): `rtsp://scc:scc12345@192.168.1.40:554/Preview_01_main`
- Sub stream (detect): `rtsp://scc:scc12345@192.168.1.40:554/Preview_01_sub`
- Detect settings: 640x360 @ 5 fps
- Track objects: person, car

### Camera: signpost
- IP: `192.168.1.31`
- Main stream (record): `rtsp://scc:scc12345@192.168.1.31:554/Preview_01_main`
- Sub stream (detect): `rtsp://scc:scc12345@192.168.1.31:554/Preview_01_sub`
- Detect settings: 640x360 @ 5 fps
- Track objects: person, car

**Note:** signpost had been temporarily removed earlier; stale references caused Frigate errors before the wipe.

---

## Codec notes (important)
- Cameras are HEVC / H.265 on main stream.
  - `ffprobe ... 192.168.1.40 ...` returned `whevc` (HEVC variant label)
  - `ffprobe ... 192.168.1.31 ...` returned `hevc`
- Frigate hardware accel currently set to NVIDIA H.265:
  - `ffmpeg: hwaccel_args: preset-nvidia-h265`

---

## GPU / RTX usage expectations
- GPU present: NVIDIA GeForce RTX 3060
- Host `nvidia-smi` works (driver 535.274.02, CUDA 12.2)
- Container can run `nvidia-smi` (`docker exec -it frigate nvidia-smi`) → means GPU is passed through.

**Goal:** Use RTX for decode/accel where possible (especially HEVC/H.265).
If logs show CUDA load errors (e.g., “Cannot load libcuda.so.1”), treat as “GPU not mounted into container correctly” or wrong hwaccel preset.

---

## Recording policy (what Ross wants)
- Record to the big drive: `/mnt/video/frigate/media`
- “Record until full then loop” (i.e., delete oldest to make room for newest)

---

## Current Frigate config highlights (known)
From `/srv/frigate/config/config.yml`:
- `ffmpeg.hwaccel_args: preset-nvidia-h265`
- `record.enabled` was set to `false` at one point (if recordings aren’t appearing, check this first).
- Snapshots enabled with short retention.
- Semantic search enabled (small model)
- Face recognition enabled (large model)
- LPR enabled

---

## Known issues encountered + resolution history
### 1) Recording maintainer errors: `'signpost'`
Symptoms:
- `frigate.record.maintainer ERROR : 'signpost'`
Cause:
- Stale camera references / DB entries when camera config changed.
Resolution:
- Ross chose to “nuke” Frigate/DB/backups because recordings were not critical yet.

### 2) Nginx auth upstream errors (502)
Symptoms:
- Nginx logs: connect() failed to upstream `127.0.0.1:5001/auth`
Meaning:
- Auth service not running / not configured (or UI hitting endpoints expecting auth).
Action:
- Treat as separate from camera ingest; fix only if required for UI/auth.

### 3) Validation error: `record -> events`
Symptoms:
- `*** Config Validation Errors *** Key : record -> events`
Meaning:
- Config contains a `record: events:` section invalid for this Frigate version (0.16-0).
Action:
- Remove/adjust invalid keys to match the active Frigate schema for 0.16-0.

---

## Repo + Git (Codex sync source of truth)
- Repo path: `/home/ross/scrapyard-command-center`
- Branch: `main`
- Goal: keep the working Frigate config committed so Codex always sees reality:
  - Copy `/srv/frigate/config/config.yml` into repo under: `infra/frigate/config.yml`
  - Commit message style: “Sync working Frigate config from scc-core”

Git identity fix (done once per machine):
- `git config --global user.name "Ross Spry"`
- `git config --global user.email "rossspry@gmail.com"`

---

## Quick commands (copy/paste friendly)
- Edit Frigate config:
  - `sudo nano /srv/frigate/config/config.yml`
- Restart Frigate (Docker):
  - `docker restart frigate`
- Tail logs:
  - `docker logs -f frigate --tail 200`
- Confirm mounts:
  - `docker inspect frigate --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'`
- Check newest recordings:
  - `sudo find /mnt/video/frigate/media/recordings -type f -name '*.mp4' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -n 10`

