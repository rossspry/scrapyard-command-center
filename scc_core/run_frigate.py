from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from scc_core.adapters import FrigateMqttAdapter, MqttConfig
from scc_core.dedupe import DedupeAggregator

logger = logging.getLogger(__name__)


@dataclass
class AppConfig:
    mqtt: MqttConfig
    dedupe_window_seconds: int = 15


def _simple_yaml_load(text: str) -> Dict[str, Any]:
    """Very small YAML subset loader (key/value and one-level nesting)."""
    root: Dict[str, Any] = {}
    stack = [(0, root)]

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        key, _, value = line.strip().partition(":")
        value = value.strip()
        while stack and indent < stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value == "":
            current: Dict[str, Any] = {}
            parent[key] = current
            stack.append((indent + 2, current))
        else:
            parent[key] = _coerce_value(value)
    return root


def _load_config(path: Path) -> AppConfig:
    text = path.read_text()
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        data = _simple_yaml_load(text)

    mqtt_section = data.get("mqtt", {}) if isinstance(data, dict) else {}
    dedupe_section = data.get("dedupe", {}) if isinstance(data, dict) else {}

    mqtt_config = MqttConfig(
        host=str(mqtt_section.get("host", "localhost")),
        port=int(mqtt_section.get("port", 1883)),
        username=mqtt_section.get("username"),
        password=mqtt_section.get("password"),
        topic=str(mqtt_section.get("topic", "frigate/events")),
        client_id=mqtt_section.get("client_id"),
    )
    window_seconds = int(dedupe_section.get("window_seconds", 15))
    return AppConfig(mqtt=mqtt_config, dedupe_window_seconds=window_seconds)


def _coerce_value(raw: str) -> Any:
    if raw.lower() in {"true", "false"}:
        return raw.lower() == "true"
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw.strip('"')


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

    config = _load_config(args.config)
    aggregator = DedupeAggregator(window_seconds=config.dedupe_window_seconds)
    adapter = FrigateMqttAdapter(mqtt_config=config.mqtt, aggregator=aggregator)
    adapter.run()


if __name__ == "__main__":
    main()
