"""Frigate camera status from the live API."""

from __future__ import annotations

import re
import subprocess
from typing import Any, Dict, List, Optional

import requests

from angus_operator.registry import register
from angus_operator.types import RiskTier, ToolResult, ToolSpec

FRIGATE_URL = "http://127.0.0.1:5000"

_DOWN = re.compile(
    r"\b(?:cameras? down|any cameras? down|camera status|how are the cameras|"
    r"are (?:the )?cameras? (?:ok|up|online|down))\b",
    re.I,
)
_VERSION = re.compile(
    r"\b(?:frigate|frigget|free gettin|free getting|nvr)\b.+"
    r"\b(?:update|updated|upgrade|version|stuck|pull)\b"
    r"|\b(?:update|updated|upgrade|version).+\b(?:frigate|frigget|free get)\b"
    r"|\bis frigate (?:up to date|updated|on)\b",
    re.I,
)


def camera_stats() -> List[Dict[str, Any]]:
    try:
        r = requests.get(f"{FRIGATE_URL}/api/stats", timeout=5)
        r.raise_for_status()
        cams = (r.json() or {}).get("cameras") or {}
    except Exception:
        return []
    out = []
    for name, cam in cams.items():
        fps = float((cam or {}).get("camera_fps") or 0)
        out.append(
            {
                "name": name,
                "camera_fps": fps,
                "detection_fps": (cam or {}).get("detection_fps"),
                "up": fps > 0,
            }
        )
    return out


def match_down(text: str) -> Optional[Dict[str, Any]]:
    if _DOWN.search(text or ""):
        return {}
    return None


def handle_down(**_k: Any) -> ToolResult:
    cams = camera_stats()
    if not cams:
        return ToolResult(
            handled=True,
            spoken="I couldn't reach Frigate for camera status.",
            tool="frigate.cameras",
            ok=False,
        )
    down = [c["name"].replace("_", " ") for c in cams if not c["up"]]
    if not down:
        return ToolResult(
            handled=True,
            spoken=f"All {len(cams)} cameras are reporting video.",
            tool="frigate.cameras",
            data={"down": [], "count": len(cams)},
        )
    if len(down) == 1:
        spoken = f"{down[0]} looks down. The others are reporting video."
    else:
        spoken = (
            ", ".join(down[:-1])
            + f", and {down[-1]} look down."
        )
    return ToolResult(
        handled=True,
        spoken=spoken,
        tool="frigate.cameras",
        data={"down": down, "count": len(cams)},
    )


def running_image() -> str:
    try:
        cp = subprocess.run(
            [
                "docker",
                "inspect",
                "frigate",
                "--format",
                "{{.Config.Image}}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return (cp.stdout or "").strip()
    except Exception:
        return ""


def match_version(text: str) -> Optional[Dict[str, Any]]:
    if _VERSION.search(text or ""):
        return {}
    return None


def handle_version(**_k: Any) -> ToolResult:
    image = running_image()
    if not image:
        return ToolResult(
            handled=True,
            spoken="I couldn't read the running Frigate container.",
            tool="frigate.version",
            ok=False,
        )
    short = image.split("/")[-1]
    spoken = (
        f"Frigate is running {short}. There is no upgrade in progress from me. "
        "I do not docker pull from chat."
    )
    return ToolResult(
        handled=True,
        spoken=spoken,
        tool="frigate.version",
        data={"image": image},
    )


register(
    ToolSpec(
        name="frigate.version",
        description="Running Frigate image; no fake pulls",
        tier=RiskTier.TIER1,
        handler=handle_version,
        match=match_version,
    )
)
register(
    ToolSpec(
        name="frigate.cameras",
        description="Which cameras are reporting video",
        tier=RiskTier.TIER1,
        handler=handle_down,
        match=match_down,
    )
)
