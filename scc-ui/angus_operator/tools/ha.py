"""Home Assistant tools. Writable allowlist only; door stays PIN-protected."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from angus_operator.registry import register
from angus_operator.types import RiskTier, ToolResult, ToolSpec

HA_URL = os.getenv("HA_URL", "http://127.0.0.1:8123").rstrip("/")
HA_TOKEN = os.getenv("HA_TOKEN", "").strip()
if not HA_TOKEN:
    env = Path("/home/ross/.config/scc/glitch.env")
    if env.is_file():
        for raw in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if raw.startswith("HA_TOKEN="):
                HA_TOKEN = raw.split("=", 1)[1].strip().strip('"').strip("'")
            if raw.startswith("HA_URL="):
                HA_URL = raw.split("=", 1)[1].strip().strip('"').strip("'").rstrip("/")
            if raw.startswith("ANGUS_FRONT_DOOR_ENTITY="):
                os.environ.setdefault(
                    "ANGUS_FRONT_DOOR_ENTITY",
                    raw.split("=", 1)[1].strip().strip('"').strip("'"),
                )

FRONT_DOOR = os.getenv("ANGUS_FRONT_DOOR_ENTITY", "switch.hobk_switch_1")

# Only these entities may be toggled from speech without extra confirmation.
WRITABLE = {
    "switch.scrapyard_porch_light": {
        "aliases": (
            "scrapyard porch",
            "scrapyard porch light",
            "porch lights",
            "porch light",
        ),
        "actions": ("on", "off"),
    },
    "switch.small_porch_light": {
        "aliases": ("small porch", "small porch light"),
        "actions": ("on", "off"),
    },
}


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    }


def list_entities() -> List[Dict[str, Any]]:
    if not HA_TOKEN:
        return []
    try:
        r = requests.get(f"{HA_URL}/api/states", headers=_headers(), timeout=6)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []
    out = []
    for item in data or []:
        eid = str(item.get("entity_id") or "")
        if not eid.startswith(("light.", "switch.", "lock.")):
            continue
        fn = str((item.get("attributes") or {}).get("friendly_name") or eid)
        out.append(
            {
                "entity_id": eid,
                "friendly_name": fn,
                "state": item.get("state"),
                "writable": eid in WRITABLE,
                "protected": eid == FRONT_DOOR,
            }
        )
    return out


def entity_state(entity_id: str) -> Optional[str]:
    if not HA_TOKEN:
        return None
    try:
        r = requests.get(
            f"{HA_URL}/api/states/{entity_id}", headers=_headers(), timeout=5
        )
        if r.status_code == 404:
            return "missing"
        r.raise_for_status()
        return str((r.json() or {}).get("state") or "")
    except Exception:
        return None


def call_switch(entity_id: str, action: str) -> None:
    service = "turn_on" if action == "on" else "turn_off"
    r = requests.post(
        f"{HA_URL}/api/services/switch/{service}",
        headers=_headers(),
        json={"entity_id": entity_id},
        timeout=6,
    )
    r.raise_for_status()


def is_locked_state(state: Optional[str]) -> Optional[bool]:
    """True if locked, False if unlocked, None if unknown/offline."""
    if state is None:
        return None
    st = str(state).strip().lower()
    if st in ("", "missing", "unavailable", "unknown"):
        return None
    if st in ("off", "locked"):
        return True
    if st in ("on", "unlocked"):
        return False
    return None


def call_unlock(entity_id: str) -> None:
    """Unlock a switch.* (ON=unlocked) or lock.* entity."""
    domain = entity_id.split(".", 1)[0] if "." in entity_id else "switch"
    if domain == "lock":
        r = requests.post(
            f"{HA_URL}/api/services/lock/unlock",
            headers=_headers(),
            json={"entity_id": entity_id},
            timeout=8,
        )
        r.raise_for_status()
        return
    call_switch(entity_id, "on")


def call_lock(entity_id: str) -> None:
    """Lock a switch.* (OFF=locked) or lock.* entity."""
    domain = entity_id.split(".", 1)[0] if "." in entity_id else "switch"
    if domain == "lock":
        r = requests.post(
            f"{HA_URL}/api/services/lock/lock",
            headers=_headers(),
            json={"entity_id": entity_id},
            timeout=8,
        )
        r.raise_for_status()
        return
    call_switch(entity_id, "off")


def unlock_if_locked(entity_id: str = "") -> Dict[str, Any]:
    """If the front door is locked, unlock it. Never locks. Silent (no TTS).

    Returns a dict: action is unlocked | already_unlocked | skipped | failed.
    """
    eid = (entity_id or FRONT_DOOR).strip() or FRONT_DOOR
    result: Dict[str, Any] = {
        "ok": False,
        "entity_id": eid,
        "state": None,
        "action": "skipped",
        "locked": None,
    }
    if not HA_TOKEN:
        result["error"] = "no_token"
        return result
    st = entity_state(eid)
    result["state"] = st
    locked = is_locked_state(st)
    result["locked"] = locked
    if locked is None:
        result["error"] = "offline" if st else "unreachable"
        return result
    if not locked:
        result["ok"] = True
        result["action"] = "already_unlocked"
        return result
    try:
        call_unlock(eid)
    except Exception as exc:
        result["action"] = "failed"
        result["error"] = str(exc)[:160]
        return result
    result["ok"] = True
    result["action"] = "unlocked"
    return result


def lock_if_unlocked(entity_id: str = "") -> Dict[str, Any]:
    """If the front door is unlocked, lock it. Silent (no TTS)."""
    eid = (entity_id or FRONT_DOOR).strip() or FRONT_DOOR
    result: Dict[str, Any] = {
        "ok": False,
        "entity_id": eid,
        "state": None,
        "action": "skipped",
        "locked": None,
    }
    if not HA_TOKEN:
        result["error"] = "no_token"
        return result
    st = entity_state(eid)
    result["state"] = st
    locked = is_locked_state(st)
    result["locked"] = locked
    if locked is None:
        result["error"] = "offline" if st else "unreachable"
        return result
    if locked:
        result["ok"] = True
        result["action"] = "already_locked"
        return result
    try:
        call_lock(eid)
    except Exception as exc:
        result["action"] = "failed"
        result["error"] = str(exc)[:160]
        return result
    result["ok"] = True
    result["action"] = "locked"
    return result


_last_light = "switch.scrapyard_porch_light"


def match_porch(text: str) -> Optional[Dict[str, Any]]:
    global _last_light
    from angus_light_intent import expand_stt_command, porch_light_action

    t = expand_stt_command(text)
    if re.search(
        r"\b(?:turn|switch|shut|get) (?:them|it|those) (?:back )?(?:off|on)\b"
        r"|\b(?:those |the |them )?lights? (?:back )?(?:off|on)\b"
        r"|\bneed them (?:on|off)\b|\bget them off\b",
        t,
    ) and not re.search(r"\b(?:what|which|are the) lights?\b", t):
        action = "off" if re.search(r"\boff\b", t) else "on"
        return {"entity_id": _last_light, "action": action}
    action = porch_light_action(t)
    if action is None:
        return None
    entity = "switch.scrapyard_porch_light"
    if "small" in t:
        entity = "switch.small_porch_light"
    _last_light = entity
    return {"entity_id": entity, "action": action}


def match_door_query(text: str) -> Optional[Dict[str, Any]]:
    from angus_operator.door_intent import STATUS, classify_front_door_utterance

    if classify_front_door_utterance(text) == STATUS:
        return {"entity_id": FRONT_DOOR}
    return None


def match_lights_on(text: str) -> Optional[Dict[str, Any]]:
    t = (text or "").lower()
    if re.search(r"\b(?:what|which) lights?\b.+\bon\b|\blights? (?:are )?on\b", t):
        return {}
    return None


def handle_switch(entity_id: str = "", action: str = "", **_k: Any) -> ToolResult:
    if entity_id == FRONT_DOOR or entity_id not in WRITABLE:
        return ToolResult(
            handled=True,
            spoken="That device is protected. Use the door command if you need the front door.",
            tool="ha.switch",
            arguments={"entity_id": entity_id, "action": action},
            tier=RiskTier.TIER3,
            ok=False,
            error="protected",
        )
    if action not in ("on", "off"):
        return ToolResult(
            handled=True,
            spoken="I can only turn that device on or off.",
            tool="ha.switch",
            ok=False,
        )
    if not HA_TOKEN:
        return ToolResult(
            handled=True,
            spoken="I can't reach Home Assistant right now.",
            tool="ha.switch",
            ok=False,
        )
    try:
        call_switch(entity_id, action)
    except Exception as exc:
        return ToolResult(
            handled=True,
            spoken="I couldn't change that light.",
            tool="ha.switch",
            arguments={"entity_id": entity_id, "action": action},
            ok=False,
            error=str(exc)[:160],
        )
    label = "the porch lights"
    if "small" in entity_id:
        label = "the small porch light"
    verb = "on" if action == "on" else "off"
    st = entity_state(entity_id)
    if st in ("on", "off") and st != action:
        return ToolResult(
            handled=True,
            spoken=(
                f"Home Assistant still shows {label} {st}. "
                "I sent the command but the switch did not match."
            ),
            tool="ha.switch",
            arguments={"entity_id": entity_id, "action": action, "ha_state": st},
            ok=False,
        )
    return ToolResult(
        handled=True,
        spoken=f"Turning {verb} {label}.",
        tool="ha.switch",
        arguments={"entity_id": entity_id, "action": action, "ha_state": st},
    )


def handle_door_query(**_k: Any) -> ToolResult:
    st = entity_state(FRONT_DOOR)
    if st in (None,):
        return ToolResult(
            handled=True,
            spoken="I couldn't reach Home Assistant for the front door.",
            tool="ha.door_status",
            ok=False,
        )
    if st in ("missing", "unavailable", "unknown"):
        return ToolResult(
            handled=True,
            spoken="The front door lock is offline in Home Assistant.",
            tool="ha.door_status",
            data={"state": st},
            ok=False,
        )
    locked = is_locked_state(st)
    if locked is None:
        return ToolResult(
            handled=True,
            spoken="I couldn't verify the front door lock state.",
            tool="ha.door_status",
            data={"state": st},
            ok=False,
        )
    spoken = (
        "The front door is locked." if locked else "The front door is unlocked."
    )
    return ToolResult(
        handled=True,
        spoken=spoken,
        tool="ha.door_status",
        data={"state": st, "locked": locked},
    )


def handle_lights_on(**_k: Any) -> ToolResult:
    entities = list_entities()
    on = [
        e["friendly_name"]
        for e in entities
        if e.get("state") == "on"
        and e["entity_id"] in WRITABLE
        or (
            e.get("state") == "on"
            and e["entity_id"].startswith("light.")
            and "infrared" not in e["entity_id"]
            and "flood" in e["entity_id"]
        )
    ]
    # Keep spoken list to allowlisted lights plus known flood.
    names = []
    for e in entities:
        if e.get("state") != "on":
            continue
        eid = e["entity_id"]
        if eid in WRITABLE or eid == "light.shopfacingptz_floodlight":
            names.append(e["friendly_name"])
    if not names:
        return ToolResult(
            handled=True,
            spoken="None of the yard lights I can see are on.",
            tool="ha.lights_on",
        )
    if len(names) == 1:
        spoken = f"{names[0]} is on."
    else:
        spoken = ", ".join(names[:-1]) + f", and {names[-1]} are on."
    return ToolResult(
        handled=True,
        spoken=spoken,
        tool="ha.lights_on",
        data={"on": names},
    )


register(
    ToolSpec(
        name="ha.switch",
        description="Turn an allowlisted HA switch on or off",
        tier=RiskTier.TIER1,
        handler=handle_switch,
        match=match_porch,
    )
)
register(
    ToolSpec(
        name="ha.door_status",
        description="Query front door lock state (no unlock)",
        tier=RiskTier.TIER1,
        handler=handle_door_query,
        match=match_door_query,
    )
)
register(
    ToolSpec(
        name="ha.lights_on",
        description="List allowlisted lights that are on",
        tier=RiskTier.TIER1,
        handler=handle_lights_on,
        match=match_lights_on,
    )
)
