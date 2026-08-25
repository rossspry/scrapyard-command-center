"""SCC service / what's-running queries."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from angus_operator.registry import register
from angus_operator.types import RiskTier, ToolResult, ToolSpec

_UNITS = [
    ("scc-ui.service", "SCC dashboard"),
    ("scc-timeclock.service", "FaceTimeClock"),
    ("angus-voice.service", "Angus"),
    ("angus-chatterbox.service", "Chatterbox"),
    ("pokerlab.service", "PokerLab"),
]

_WHAT = re.compile(
    r"\bwhat(?:'s|s| is)? running\b|\bscc services\b|\bwhat(?:'s|s)? on the scc\b",
    re.I,
)


def _active(unit: str) -> bool:
    try:
        cp = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return (cp.stdout or "").strip() == "active"
    except Exception:
        return False


def match_running(text: str) -> Optional[Dict[str, Any]]:
    if _WHAT.search(text or ""):
        return {}
    return None


def handle_running(**_k: Any) -> ToolResult:
    up = []
    down = []
    for unit, label in _UNITS:
        if _active(unit):
            up.append(label)
        else:
            down.append(label)
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:5000/api/version", timeout=2) as resp:
            if resp.status == 200:
                up.append("Frigate")
            else:
                down.append("Frigate")
    except Exception:
        down.append("Frigate")
    if not down:
        spoken = "Core SCC services are running."
    else:
        spoken = (
            "Running: "
            + ", ".join(up[:4])
            + ". Not active: "
            + ", ".join(down)
            + "."
        )
    return ToolResult(
        handled=True,
        spoken=spoken,
        tool="scc.services",
        data={"up": up, "down": down},
    )


register(
    ToolSpec(
        name="scc.services",
        description="What core SCC services are running",
        tier=RiskTier.TIER1,
        handler=handle_running,
        match=match_running,
    )
)
