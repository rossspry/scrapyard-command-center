from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from scc_core.adapters.frigate_mqtt import MqttConfig

logger = logging.getLogger(__name__)

yaml_spec = importlib.util.find_spec("yaml")
if yaml_spec:
    import yaml  # type: ignore  # noqa: F401
else:
    yaml = None  # type: ignore


def load_yaml(path: Path) -> Dict[str, Any]:
    """Load a YAML config file, falling back to a tiny parser if PyYAML is absent."""

    text = path.read_text()

    if yaml:
        try:
            data = yaml.safe_load(text)  # type: ignore[attr-defined]
            return data or {}
        except Exception:  # noqa: BLE001
            logger.warning("Failed to parse YAML with PyYAML; falling back to simple loader", exc_info=True)

    return _simple_yaml_load(text)


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


def _coerce_value(raw: str) -> Any:
    if raw.lower() in {"true", "false"}:
        return raw.lower() == "true"
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw.strip('"')


@dataclass
class AppConfig:
    mqtt: MqttConfig
    dedupe_window_seconds: int = 15


def load_app_config(path: Path) -> AppConfig:
    data = load_yaml(path)

    mqtt_section = data.get("mqtt", {}) if isinstance(data, dict) else {}
    dedupe_section = data.get("dedupe", {}) if isinstance(data, dict) else {}

    mqtt_config = MqttConfig(
        host=str(mqtt_section.get("host", "localhost")),
        port=int(mqtt_section.get("port", 1883)),
        username=mqtt_section.get("username"),
        password=mqtt_section.get("password"),
        topic=str(mqtt_section.get("topic", "frigate/events")),
        client_id=mqtt_section.get("client_id", "scc-frigate"),
    )
    window_seconds = int(dedupe_section.get("window_seconds", 15))
    return AppConfig(mqtt=mqtt_config, dedupe_window_seconds=window_seconds)

