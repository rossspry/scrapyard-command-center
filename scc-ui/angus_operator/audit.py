"""JSONL audit log. Never write secrets or door PINs."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from zoneinfo import ZoneInfo

_LOG = Path(
    os.getenv(
        "ANGUS_OPERATOR_AUDIT",
        "/home/ross/.local/share/glitch/logs/operator_audit.jsonl",
    )
)
_TZ = ZoneInfo(os.getenv("GLITCH_TIMEZONE", "America/New_York"))
_SECRET_KEYS = {
    "token",
    "api_key",
    "secret",
    "password",
    "pin",
    "code",
    "authorization",
    "ha_token",
    "xai_api_key",
    "angus_hermes_api_key",
    "api_server_key",
    "bearer",
}


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, val in value.items():
            lk = str(key).lower()
            if any(s in lk for s in _SECRET_KEYS):
                out[key] = "[redacted]"
            else:
                out[key] = _scrub(val)
        return out
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def log_action(event: Dict[str, Any]) -> None:
    record = {"ts": _now(), **_scrub(event)}
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        with _LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
