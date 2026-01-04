from __future__ import annotations

import json
import logging
import os
import signal
import threading
from pathlib import Path

from scc_core.adapters import FrigateMqttAdapter
from scc_core.config import AppConfig, load_app_config
from scc_core.dedupe import DedupeAggregator
from scc_core.events import Event

DEFAULT_CONFIG_PATH = Path("config/example_frigate.yml")


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _handler(signum, _frame):
        logging.info("Received signal %s; shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def _build_app_config() -> AppConfig:
    config_path = Path(os.environ.get("SCC_CONFIG", DEFAULT_CONFIG_PATH))
    logging.info("Loading config", extra={"path": str(config_path)})
    return load_app_config(config_path)


def _on_event_factory(aggregator: DedupeAggregator):
    def _on_event(event: Event) -> None:
        decision = aggregator.process(event)
        if decision:
            print(json.dumps(decision.summary()), flush=True)

    return _on_event


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")

    app_config = _build_app_config()
    aggregator = DedupeAggregator(window_seconds=app_config.dedupe_window_seconds)
    adapter = FrigateMqttAdapter(mqtt_config=app_config.mqtt)

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)

    adapter.start(on_event=_on_event_factory(aggregator), stop_event=stop_event)


if __name__ == "__main__":
    main()
