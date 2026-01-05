# Scrapyard Command Center (SCC)

Scrapyard Command Center is the future home for tooling that coordinates scrapyard operations. The repository is in an early planning stage; the goal of this baseline is to align contributors on intent and process before code lands.

## Documentation
- [Project overview](docs/overview.md)
- [Development guide](docs/development.md)
- [Architectural decisions](docs/decisions/README.md)

## Project status
- **Planning:** Baseline utilities are landing; SCC remains early-stage.
- **Architecture:** High-level components and responsibilities are captured in `docs/overview.md`.
- **Development conventions:** Contribution and review expectations are outlined in `docs/development.md`.

## Frigate MQTT runner
SCC treats detectors as inputs and makes the notification decisions itself. A Frigate MQTT runner consumes Frigate
events, applies SCC dedupe logic, and emits single-line JSON notifications to stdout.

> No fallback alerts; SCC emits only on Frigate-confirmed events.

### Run locally
1. Install dependencies: `pip install -e .`
2. Create a config file (or edit `config/example_frigate.yml`) with MQTT connection details and a dedupe window. Keep
   placeholder credentials only; do not commit secrets.
3. Point `SCC_CONFIG` at your config file (defaults to `config/example_frigate.yml`):
   `export SCC_CONFIG=config/example_frigate.yml`
4. Start the runner: `python -m scc_core.run_scc`

### Smoke test
The runner prints a single JSON line for each Frigate-confirmed decision:

```
{"camera_id": "driveway", "event_type": "person_detected", "chosen_source": "frigate", "confidence": 0.83, "ts": "2024-05-18T14:02:03+00:00"}
```

## Installer
- Fresh installs:
  - Run `scripts/install.sh` from the repo. It installs user-level systemd units (`scc.service` and `scc-watch-reolink.service`).
  - The installer **refuses to start** services if `SCC_UI_USERNAME` or `SCC_UI_PASSWORD` in `~/.config/scc/.env` are still set
    to the placeholder value (`changeme`). Update that file before re-running the installer.
  - If `~/.config/scc/.env` does not exist, the installer copies `config/.env.example` and exits so you can fill it in first.
  - The UI bind address comes from `SCC_UI_BIND` and defaults to `127.0.0.1` to keep the UI local-only. Change the value and use
    a reverse proxy if you want to expose the UI.
- Operating the services:
  - Use `systemctl --user` to manage the services (for example, `systemctl --user restart scc.service`).
  - If your distro requires it, enable lingering so the user services start without an active login session (e.g., `loginctl enable-linger $(whoami)`).
- Status and verification:
  - `scripts/status.sh` shows the systemd status for the SCC services.
  - `scripts/test_install.sh` validates the environment file and checks whether the services are enabled.

## How to contribute
1. Review the overview and development guide to understand scope and expectations.
2. Open an issue or draft an architectural decision record for significant changes.
3. Propose changes through a pull request with clear context and validation notes.
