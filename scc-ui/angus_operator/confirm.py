"""Pending Tier-2 confirmation. Not a generic ambient yes."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

CONFIRM_YES = (
    "yes",
    "yeah",
    "yep",
    "yup",
    "ok",
    "okay",
    "do it",
    "go ahead",
    "confirm",
    "make the change",
    "please do",
    "affirmative",
)
CONFIRM_NO = (
    "no",
    "nope",
    "cancel",
    "don't",
    "do not",
    "stop",
    "never mind",
    "nevermind",
    "negative",
    "abort",
)

DEFAULT_TIMEOUT = float(
    __import__("os").getenv("ANGUS_CONFIRM_SECONDS", "25") or "25"
)


@dataclass
class PendingConfirm:
    tool: str
    arguments: Dict[str, Any]
    summary: str
    transcript: str
    expires: float
    extra: Dict[str, Any] = field(default_factory=dict)


_pending: Optional[PendingConfirm] = None


def get_pending() -> Optional[PendingConfirm]:
    global _pending
    if _pending and time.monotonic() > _pending.expires:
        _pending = None
        return None
    return _pending


def set_pending(
    tool: str,
    arguments: Dict[str, Any],
    summary: str,
    transcript: str = "",
    timeout: float = DEFAULT_TIMEOUT,
    extra: Optional[Dict[str, Any]] = None,
) -> PendingConfirm:
    global _pending
    _pending = PendingConfirm(
        tool=tool,
        arguments=dict(arguments or {}),
        summary=summary,
        transcript=transcript,
        expires=time.monotonic() + float(timeout),
        extra=dict(extra or {}),
    )
    return _pending


def clear_pending() -> None:
    global _pending
    _pending = None


def classify_confirm(text: str) -> str:
    """Return yes | no | other."""
    t = " ".join((text or "").lower().strip().split())
    t = t.replace("'", "'")
    if t in CONFIRM_YES or t.startswith("yes ") or t.startswith("ok "):
        return "yes"
    if t in CONFIRM_NO or t.startswith("no ") or t.startswith("don't"):
        return "no"
    return "other"
