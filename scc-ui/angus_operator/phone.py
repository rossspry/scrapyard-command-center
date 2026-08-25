"""Best-effort phone push via Home Assistant companion app."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple

import requests


def _ha_creds() -> Tuple[str, str, str]:
    url = os.getenv("HA_URL", "").strip().rstrip("/")
    token = os.getenv("HA_TOKEN", "").strip()
    notify = os.getenv(
        "HA_NOTIFY_PATH", "/api/services/notify/mobile_app_testers_iphone"
    )
    if url and token:
        return url, token, notify
    env = Path("/home/ross/.config/scc/glitch.env")
    if not env.is_file():
        return url, token, notify
    try:
        for raw in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key == "HA_URL" and not url:
                url = val.rstrip("/")
            elif key == "HA_TOKEN" and not token:
                token = val
            elif key == "HA_NOTIFY_PATH":
                notify = val
    except OSError:
        pass
    return url, token, notify


def push_phone(message: str, title: str = "Angus") -> None:
    text = (message or "").strip()
    if not text:
        return
    url, token, notify = _ha_creds()
    if not url or not token:
        return
    try:
        requests.post(
            f"{url}{notify}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"title": title, "message": text[:220]},
            timeout=6,
        )
    except Exception:
        pass
