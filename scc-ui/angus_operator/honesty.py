"""Yard-action honesty: LLM must not claim tools ran."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from angus_operator.registry import register
from angus_operator.types import RiskTier, ToolResult, ToolSpec

_YARD = re.compile(
    r"\b(?:lights?|porch|door|lock|unlock|frigate|frigget|free get|"
    r"clocked in|clock in|home assistant|security mode|away mode|"
    r"upgrade|docker|compose|systemctl|pulling|porch-lights)\b",
    re.I,
)
_CLAIM = re.compile(
    r"\b(?:lights? are (?:on|off)|turning (?:them |it )?(?:on|off)|"
    r"porch lights? (?:are )?(?:on|off)|"
    r"front door locked|i(?:'ve| have) (?:locked|unlocked|started)|"
    r"upgrading|pulling frigate|docker (?:logs|pull|compose)|"
    r"systemctl|porch-lights|"
    r"directly through the scc|not using home assistant)\b",
    re.I,
)
# First-person completed SCC actions / claimed live checks. Not mere topic words.
_DONE_VERB = re.compile(
    r"\bi(?:['’]ve| have)?(?: just)? "
    r"(?:locked|unlocked|clocked|turned|changed|checked|restarted|"
    r"upgraded|edited|started|stopped|punched|completed|set|ran)\b",
    re.I,
)
_DONE_OBJECT = re.compile(
    r"\b(?:doors?|locks?|unlock|porch|lights?|frigate|cameras?|"
    r"timeclock|clock(?:ed| in| out)|sebrina|punch(?:ed)?|"
    r"away|stay|mode|services?|systemctl|docker|files?|config|"
    r"grok build|upgrade)\b",
    re.I,
)
_STATE_DONE = re.compile(
    r"\b(?:porch )?lights? are now (?:on|off)\b|"
    r"\bgrok build (?:completed|finished) (?:the )?(?:change|job|work)\b|"
    r"\bevery camera is healthy\b",
    re.I,
)
# Promises / future-tense device actions. LLM talk is never authoritative.
_PROMISE = re.compile(
    r"\b(?:the )?(?:scrapyard |scrap yard )?(?:porch )?lights? will "
    r"(?:be )?(?:turned |switched |shut )?(?:off|on)\b|"
    r"\b(?:the )?(?:front )?doors? will (?:be )?(?:locked|unlocked)\b|"
    r"\b(?:i(?:['’]ll| will)|we(?:['’]ll| will)|going to|gonna)\b.{0,48}"
    r"\b(?:turn(?:ing)?|lock(?:ing)?|unlock(?:ing)?|switch(?:ing)?|shut(?:ting)?)\b.{0,48}"
    r"\b(?:lights?|porch|doors?|lock|frigate|cameras?)\b|"
    r"\bwill (?:turn|lock|unlock|switch|shut)\b.{0,40}\b(?:lights?|porch|doors?)\b|"
    r"\bturning (?:the )?(?:scrapyard |scrap yard )?(?:porch )?lights? (?:off|on)\b",
    re.I | re.DOTALL,
)
UNVERIFIED_ACTION_REFUSE = "I didn't perform or verify that action."
_DID_YOU = re.compile(
    r"\b(?:did you really|did you actually|did you just say|"
    r"or did you just|are you lying)\b",
    re.I,
)

_last: Optional[Dict[str, Any]] = None

REFUSE = (
    "I didn't run a yard tool for that sentence. "
    "Say turn on the scrapyard porch lights, lock the front door, "
    "who is clocked in, or camera status."
)


def record(tool: str, spoken: str, ok: bool = True) -> None:
    global _last
    _last = {"tool": tool, "spoken": (spoken or "")[:200], "ok": ok}


def last_action() -> Optional[Dict[str, Any]]:
    return dict(_last) if _last else None


def is_yard_utterance(text: str) -> bool:
    return bool(_YARD.search(text or ""))


def claims_yard_action(reply: str) -> bool:
    return bool(_CLAIM.search(reply or ""))


def claims_unverified_action(reply: str) -> bool:
    """True when the model claims or promises an SCC/device action."""
    text = reply or ""
    if _STATE_DONE.search(text) or _PROMISE.search(text):
        return True
    if _DONE_VERB.search(text) and _DONE_OBJECT.search(text):
        return True
    return False


def block_unmatched_yard(user_text: str) -> Optional[str]:
    if is_yard_utterance(user_text):
        return REFUSE
    return None


def sanitize_llm_reply(reply: str) -> str:
    text = reply or ""
    if claims_unverified_action(text) or claims_yard_action(text):
        return UNVERIFIED_ACTION_REFUSE
    return text


def match_did_you(text: str) -> Optional[Dict[str, Any]]:
    if _DID_YOU.search(text or ""):
        return {}
    return None


def handle_did_you(**_k: Any) -> ToolResult:
    last = last_action()
    if not last:
        spoken = "No yard tool ran. That last answer would have been talk only."
    elif last.get("ok"):
        spoken = f"Yes. {last.get('spoken') or 'A tool ran.'}"
    else:
        spoken = f"I tried ({last.get('tool')}) but it did not succeed. {last.get('spoken')}"
    return ToolResult(
        handled=True,
        spoken=spoken,
        tool="honesty.did_you",
        data=last or {},
    )


register(
    ToolSpec(
        name="honesty.did_you",
        description="Whether the last yard action was a real tool",
        tier=RiskTier.TIER1,
        handler=handle_did_you,
        match=match_did_you,
    )
)
