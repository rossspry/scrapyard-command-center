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

## Fresh install on a new server
1. Clone the repository: `git clone https://github.com/<org>/scrapyard-command-center.git`
2. Change into the repository: `cd scrapyard-command-center`
3. Bootstrap the environment and services: `./scripts/install.sh`
4. Check systemd status: `./scripts/status.sh`
5. Open the UI at `http://<host>:8081`.

## How to contribute
1. Review the overview and development guide to understand scope and expectations.
2. Open an issue or draft an architectural decision record for significant changes.
3. Propose changes through a pull request with clear context and validation notes.
