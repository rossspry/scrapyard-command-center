"""Deterministic front-door utterance classifier.

Returns LOCK | UNLOCK | STATUS | NON_ACTION | None.

None = not a front-door utterance (caller may use other tools / LLM).
NON_ACTION = mentions the door but is not a physical command.
LOCK / UNLOCK / STATUS must never be decided by the general LLM.
"""

from __future__ import annotations

import re
from typing import Optional

LOCK = "LOCK"
UNLOCK = "UNLOCK"
STATUS = "STATUS"
NON_ACTION = "NON_ACTION"

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^a-z0-9'\s]")


def normalize_door_text(text: str) -> str:
    t = (text or "").lower().strip()
    t = t.replace("'", "'").replace("'", "'")
    t = _PUNCT.sub(" ", t)
    t = _WS.sub(" ", t).strip()
    return t


def _has(t: str, phrase: str) -> bool:
    return re.search(rf"\b{re.escape(phrase)}\b", t) is not None


def classify_front_door_utterance(text: str) -> Optional[str]:
    """Classify a spoken line. Wake words may already have been stripped."""
    t = normalize_door_text(text)
    if not t:
        return None

    let_me_in = _has(t, "let me in") and not _has(t, "let me in on")
    about_door = "door" in t
    if not about_door and not let_me_in:
        return None

    if about_door and re.search(
        r"\b(camera|cam|footage|recording|snapshot|clip)\b", t
    ):
        return NON_ACTION

    if re.search(
        r"\b(?:haven't|have not|still haven't|still not)\s+locked\b"
        r"|\bnot locked\b|\bstill unlocked\b",
        t,
    ):
        return LOCK

    if re.search(
        r"\b(don't|dont|do not|never|stop|didn't|didnt|wasn't|wasnt)\b", t
    ) and re.search(r"\b(lock|unlock|open|secure)\b", t):
        if _is_status_question(t):
            return STATUS
        return NON_ACTION

    if _is_status_question(t):
        return STATUS

    if _is_unlock_command(t, let_me_in=let_me_in):
        return UNLOCK

    if _is_lock_command(t):
        return LOCK

    if about_door:
        return NON_ACTION
    return None


def _is_status_question(t: str) -> bool:
    if _has(t, "door status") or _has(t, "front door status"):
        return True
    if re.search(r"\bcheck (?:on )?the (?:front )?door\b", t):
        return True
    if re.search(
        r"\b(?:what(?:'s|s| is)|whats)(?: the)? (?:front )?door status\b", t
    ):
        return True
    if re.search(
        r"\b(?:is|was|are) the (?:front )?door (?:locked|unlocked|open|closed)\b",
        t,
    ):
        return True
    if re.search(
        r"\bdid (?:i|we|you|somebody|someone|anyone) "
        r"(?:lock|unlock|leave|open)\b",
        t,
    ) and "door" in t:
        return True
    if re.search(r"\b(?:who|why|when|how) (?:locked|unlocked|opened)\b", t):
        return True
    if re.search(r"\bwhy was the (?:front )?door\b", t):
        return True
    if re.search(r"\bdid i leave the (?:front )?door\b", t):
        return True
    if re.search(r"\b(?:is|was) (?:the )?(?:front )?door locked\b", t):
        return True
    return False


def _is_unlock_command(t: str, *, let_me_in: bool) -> bool:
    if let_me_in:
        return True
    phrases = (
        "unlock the front door",
        "unlock front door",
        "unlock the door",
        "unlock that door",
        "unlock that front door",
        "open the front door",
        "open front door",
        "open that front door",
        "please unlock the front door",
        "please unlock the door",
        "please open the front door",
        "can you unlock the door",
        "can you unlock the front door",
        "can you open the front door",
        "would you unlock the door",
        "would you unlock the front door",
    )
    if any(_has(t, p) for p in phrases):
        return True
    if re.search(r"\bunlock\b", t) and re.search(r"\b(?:front )?door\b", t):
        if re.search(r"\b(?:is|was|are|did|who|why|what|check)\b", t):
            return False
        return True
    if re.search(r"\bopen the door\b", t) and not re.search(
        r"\b(?:is|was|are|did|who|why|what)\b", t
    ):
        return True
    return False


def _is_lock_command(t: str) -> bool:
    if re.search(r"\bunlock\b", t):
        return False
    phrases = (
        "lock the front door",
        "lock front door",
        "lock the door",
        "lock that door",
        "lock that front door",
        "lock up the front door",
        "lock up the door",
        "secure the front door",
        "secure front door",
        "secure the door",
        "secure that door",
        "please lock the front door",
        "please lock the door",
        "can you lock the door",
        "can you lock the front door",
        "make sure the front door is locked",
        "make sure the door is locked",
        "make sure that the front door is locked",
    )
    if any(_has(t, p) for p in phrases):
        return True
    if re.search(
        r"\bmake sure (?:that )?(?:the )?(?:front )?door is locked\b", t
    ):
        return True
    if re.search(r"\block up\b", t) and "door" in t:
        return True
    if re.search(r"\block\b", t) and "door" in t:
        if re.search(
            r"\b(?:is|was|are|did|who|why|what|check|status|locked)\b", t
        ):
            return False
        return True
    if re.search(r"\bsecure\b", t) and "door" in t:
        if re.search(r"\b(?:is|was|are|did|who|why|what|check)\b", t):
            return False
        return True
    return False
