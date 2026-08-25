"""STT-tolerant porch-light matching. Fail-closed: no porch+light+on/off → None."""

from __future__ import annotations

import re
from typing import Optional

_GLUED_ON_OFF = re.compile(r"\b(turn|switch|shut)(off|on)\b")
_SCRAP_YARD = re.compile(r"\bscrap\s+yard\b")
_NON_WORD = re.compile(r"[^a-z0-9'\s]")
_SPACES = re.compile(r"\s+")

_OFF_CUES = (
    "turn off",
    "switch off",
    "shut off",
    "lights off",
    "light off",
    "porch lights off",
    "porch light off",
)
_ON_CUES = (
    "turn on",
    "switch on",
    "lights on",
    "light on",
    "porch lights on",
    "porch light on",
)


def expand_stt_command(text: str) -> str:
    """Lowercase, strip punctuation, split glued turnoff/turnon, join scrap yard."""
    t = (text or "").lower().strip()
    t = _NON_WORD.sub(" ", t)
    t = _SPACES.sub(" ", t).strip()
    t = _GLUED_ON_OFF.sub(r"\1 \2", t)
    t = _SCRAP_YARD.sub("scrapyard", t)
    return t


def porch_light_action(text: str) -> Optional[str]:
    """Return 'on' or 'off' only when this is confidently a porch-light command."""
    t = expand_stt_command(text)
    if "porch" not in t or "light" not in t:
        return None
    if any(cue in t for cue in _OFF_CUES):
        return "off"
    if any(cue in t for cue in _ON_CUES):
        return "on"
    return None
