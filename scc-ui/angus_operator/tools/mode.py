"""SCC STAY/AWAY/OFF — same security_mode.json the dashboard uses."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from angus_operator.registry import register
from angus_operator.types import RiskTier, ToolResult, ToolSpec

MODE_PATH = Path(
    os.getenv("SCC_SECURITY_MODE_PATH", "/srv/scc-ui/security_mode.json")
)
MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))

ALWAYS_ON = ["junkyard", "front_gate", "signpost", "facecam"]
ALL_CAMERAS = [
    "junkyard",
    "front_gate",
    "signpost",
    "backlot",
    "facetag",
    "kitchen",
    "shedview",
    "north",
    "backdoor",
    "frontcorner",
    "store",
    "shop_facing_ptz",
    "facecam",
]

_SET_STAY = re.compile(
    r"\b(?:switch|set|go|put|change)\b.+\b(?:to\s+)?stay\b"
    r"|\bstay mode\b|\bswitch us to stay\b|\bwe(?:'re| are) stay(?:ing)?\b",
    re.I,
)
_SET_AWAY = re.compile(
    r"\b(?:switch|set|go|put|change)\b.+\b(?:to\s+)?away\b"
    r"|\baway mode\b|\bswitch us to away\b|\bwe(?:'re| are) away\b",
    re.I,
)
_QUERY = re.compile(
    r"\b(?:what|which|current)\b.+\b(?:mode|stay|away)\b"
    r"|\b(?:are we|security mode)\b|\bwhat mode\b",
    re.I,
)


def get_mode() -> str:
    try:
        data = json.loads(MODE_PATH.read_text(encoding="utf-8"))
        mode = str((data or {}).get("mode") or "stay").lower()
        return mode if mode in ("stay", "away", "off") else "stay"
    except Exception:
        return "stay"


def _update_frigate(mode: str) -> None:
    try:
        import paho.mqtt.publish as publish
    except Exception:
        return
    notify_path = Path("/srv/scc-ui/notification_settings.json")
    active = set(ALL_CAMERAS if mode == "away" else ALWAYS_ON)
    try:
        settings = json.loads(notify_path.read_text(encoding="utf-8"))
        for cam, meta in (settings.get("cameras") or {}).items():
            if meta.get("enabled"):
                active.add(cam)
    except Exception:
        pass
    for camera in ALL_CAMERAS:
        state = "ON" if camera in active else "OFF"
        try:
            publish.single(
                f"frigate/{camera}/detect/set",
                payload=state,
                hostname=MQTT_HOST,
                port=MQTT_PORT,
            )
        except Exception:
            pass


def set_mode(mode: str, user: str = "angus") -> Dict[str, Any]:
    mode = (mode or "").strip().lower()
    if mode not in ("stay", "away", "off"):
        return {"ok": False, "error": "invalid_mode"}
    payload = {
        "mode": mode,
        "last_updated": datetime.now().isoformat(),
        "updated_by": user,
    }
    MODE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _update_frigate(mode)
    return {"ok": True, "mode": mode}


def match_query(text: str) -> Optional[Dict[str, Any]]:
    t = (text or "").strip()
    if _SET_STAY.search(t) or _SET_AWAY.search(t):
        return None
    if _QUERY.search(t) or t.lower() in ("security mode", "what mode"):
        return {}
    return None


def match_set(text: str) -> Optional[Dict[str, Any]]:
    t = (text or "").strip()
    if _SET_STAY.search(t):
        return {"mode": "stay"}
    if _SET_AWAY.search(t):
        return {"mode": "away"}
    return None


def handle_query(**_kwargs: Any) -> ToolResult:
    mode = get_mode()
    return ToolResult(
        handled=True,
        spoken=f"Security mode is {mode}.",
        tool="scc.get_mode",
        arguments={},
        data={"mode": mode},
    )


def handle_set(mode: str = "", **_kwargs: Any) -> ToolResult:
    result = set_mode(mode, user="angus-voice")
    if not result.get("ok"):
        return ToolResult(
            handled=True,
            spoken="I couldn't change the security mode.",
            tool="scc.set_mode",
            arguments={"mode": mode},
            ok=False,
            error=str(result.get("error") or "failed"),
        )
    return ToolResult(
        handled=True,
        spoken=f"Switching to {mode}.",
        tool="scc.set_mode",
        arguments={"mode": mode},
        data=result,
    )


register(
    ToolSpec(
        name="scc.get_mode",
        description="Query SCC STAY/AWAY/OFF",
        tier=RiskTier.TIER1,
        handler=handle_query,
        match=match_query,
    )
)
register(
    ToolSpec(
        name="scc.set_mode",
        description="Set SCC STAY or AWAY",
        tier=RiskTier.TIER1,
        handler=handle_set,
        match=match_set,
    )
)
