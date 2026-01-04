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

## Frigate MQTT adapter
SCC treats detectors as inputs and makes the notification decisions itself. A minimal Frigate MQTT adapter is available for
local testing without real cameras.

1. Install dependencies: `pip install -e .`
2. Copy and edit MQTT settings in `config/example_frigate.yml` (host, port, credentials, topic, client ID, dedupe window).
3. Run the adapter: `python -m scc_core.run_frigate --config config/example_frigate.yml`

Incoming MQTT events are normalized, de-duplicated, and emitted as single-line JSON summaries (one per incident) to stdout.

## How to contribute
1. Review the overview and development guide to understand scope and expectations.
2. Open an issue or draft an architectural decision record for significant changes.
3. Propose changes through a pull request with clear context and validation notes.
