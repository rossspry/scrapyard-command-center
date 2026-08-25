"""SCC project registry discovered from live paths and services_catalog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

_CATALOG = Path("/srv/scc-ui/services_catalog.json")

_KNOWN = [
    {
        "id": "angus",
        "name": "Angus",
        "aliases": ["angus", "voice assistant", "glitch"],
        "path": "/srv/scc-ui",
        "docs": ["/srv/scc/docs/ANGUS_TTS.md", "/opt/angus/ANGUS.md"],
        "agents": ["/srv/scc/AGENTS.md"],
        "changelog": "/srv/scc/CHANGELOG.md",
        "services": ["angus-voice.service", "angus-chatterbox.service"],
        "risk": "tier2",
    },
    {
        "id": "scc-dashboard",
        "name": "SCC dashboard",
        "aliases": [
            "scc dashboard",
            "command center",
            "scc ui",
            "dashboard",
            "camera page",
        ],
        "path": "/srv/scc-ui",
        "docs": ["/srv/scc/ARCHITECTURE.md"],
        "agents": ["/srv/scc/AGENTS.md"],
        "changelog": "/srv/scc/CHANGELOG.md",
        "services": ["scc-ui.service"],
        "risk": "tier2",
    },
    {
        "id": "facetimeclock",
        "name": "FaceTimeClock",
        "aliases": ["facetimeclock", "face time clock", "timeclock", "time clock"],
        "path": "/srv/timeclock",
        "docs": ["/srv/scc/docs/FACE_PIPELINE.md"],
        "agents": ["/srv/scc/AGENTS.md"],
        "changelog": "/srv/scc/CHANGELOG.md",
        "services": ["scc-timeclock.service"],
        "risk": "tier2",
    },
    {
        "id": "frigate",
        "name": "Frigate",
        "aliases": [
            "frigate",
            "frigget",
            "frig it",
            "free get",
            "free gettin",
            "cameras",
            "nvr",
        ],
        "path": "/srv/frigate",
        "docs": ["/srv/scc/docs/FACE_PIPELINE.md"],
        "agents": ["/srv/scc/AGENTS.md"],
        "changelog": "/srv/scc/CHANGELOG.md",
        "services": [],
        "risk": "tier2",
    },
    {
        "id": "poker-director",
        "name": "Poker Director",
        "aliases": ["poker director", "pokerforge", "tournament timer", "blinds timer"],
        "path": "/home/ross/pokerforge",
        "docs": ["/home/ross/pokerforge-deploy/README.md"],
        "agents": [],
        "changelog": "",
        "services": [],
        "risk": "tier2",
    },
    {
        "id": "pokerlab",
        "name": "PokerLab",
        "aliases": ["pokerlab", "poker lab"],
        "path": "/home/ross/PokerLab-AI",
        "docs": ["/home/ross/PokerLab-AI/README.md"],
        "agents": [],
        "changelog": "",
        "services": ["pokerlab.service"],
        "risk": "tier2",
    },
    {
        "id": "osint",
        "name": "OSINT Lab",
        "aliases": ["osint", "skiptracer", "osint lab"],
        "path": "/home/ross/scc-osint-lab",
        "docs": ["/srv/scc/docs/OSINT_LAB.md"],
        "agents": ["/home/ross/scc-osint-lab/AGENTS.md"],
        "changelog": "",
        "services": [],
        "risk": "tier2",
    },
    {
        "id": "scanner",
        "name": "NC VIPER Scanner",
        "aliases": ["scanner", "viper", "icecast"],
        "path": "/srv/scc",
        "docs": ["/srv/scc/docs/SCANNER_SDR.md"],
        "agents": ["/srv/scc/AGENTS.md"],
        "changelog": "/srv/scc/CHANGELOG.md",
        "services": ["icecast2.service"],
        "risk": "tier2",
    },
    {
        "id": "sdr",
        "name": "SDR Intercept",
        "aliases": ["sdr", "intercept"],
        "path": "/opt/sdr/intercept",
        "docs": ["/srv/scc/docs/SCANNER_SDR.md"],
        "agents": ["/opt/sdr/intercept/AGENTS.md"],
        "changelog": "/opt/sdr/intercept/CHANGELOG.md",
        "services": [],
        "risk": "tier2",
    },
]


def _exists(path: str) -> bool:
    return bool(path) and Path(path).exists()


def list_projects() -> List[Dict[str, Any]]:
    out = []
    for proj in _KNOWN:
        item = dict(proj)
        item["present"] = _exists(str(proj.get("path") or ""))
        out.append(item)
    return out


def resolve_project(text: str) -> Optional[Dict[str, Any]]:
    t = (text or "").lower()
    best = None
    best_len = 0
    for proj in list_projects():
        for alias in proj.get("aliases") or []:
            a = str(alias).lower()
            if a and a in t and len(a) > best_len:
                best = proj
                best_len = len(a)
    return best
