from __future__ import annotations

import argparse
import logging
import threading
from pathlib import Path

from scc_core.adapters import FrigateMqttAdapter
from scc_core.config import load_app_config
from scc_core.dedupe import DedupeAggregator

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the SCC Frigate MQTT adapter")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/example_frigate.yml"),
        help="Path to YAML config file",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")

    config = load_app_config(args.config)
    aggregator = DedupeAggregator(window_seconds=config.dedupe_window_seconds)
    adapter = FrigateMqttAdapter(mqtt_config=config.mqtt)

    stop_event = threading.Event()

    def _on_event(event):
        decision = aggregator.process(event)
        if decision:
            logger.info("Decision emitted", extra=decision.summary())

    adapter.start(on_event=_on_event, stop_event=stop_event)


if __name__ == "__main__":
    main()
