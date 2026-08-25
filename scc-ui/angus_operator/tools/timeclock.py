"""FaceTimeClock operator tools. Roster/punches are authoritative."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_SCC_UI = Path(__file__).resolve().parents[2]
if str(_SCC_UI) not in sys.path:
    sys.path.insert(0, str(_SCC_UI))
import timeclock_voice as tv  # noqa: E402

from angus_operator.registry import register
from angus_operator.types import RiskTier, ToolResult, ToolSpec

_DID_TODAY = re.compile(
    r"^(?:did)\s+(?P<name>.+?)\s+(?:clock|punch)\s+in(?:\s+today)?\??$",
    re.I,
)


def _norm(text: str) -> str:
    return tv._norm(tv._WAKE_PREFIX.sub("", text or "").strip())


def match_list_in(text: str) -> Optional[Dict[str, Any]]:
    if tv.parse_who_clocked_in(text):
        return {"op": "list_in"}
    return None


def match_status(text: str) -> Optional[Dict[str, Any]]:
    name = tv.parse_today_hours_name(text)
    if name:
        return {"op": "today_hours", "name": name}
    name = tv.parse_last_shift_name(text)
    if name:
        return {"op": "last_shift", "name": name}
    name = tv.parse_current_shift_name(text)
    if name:
        return {"op": "current_shift", "name": name}
    name = tv.parse_is_clocked_in_name(text)
    if name:
        return {"op": "status", "name": name}
    name = tv.parse_when_clocked_in_name(text)
    if name:
        return {"op": "when", "name": name}
    raw = _norm(text)
    m = _DID_TODAY.match(raw)
    if m:
        return {"op": "did_today", "name": tv._norm(m.group("name"))}
    return None


def match_clock_out(text: str) -> Optional[Dict[str, Any]]:
    name = tv.parse_clock_out_name(text)
    if name:
        return {"name": name}
    return None


def match_clock_in(text: str) -> Optional[Dict[str, Any]]:
    name = tv.parse_clock_in_name(text)
    if name:
        return {"name": name}
    if tv.looks_like_clock_in(text):
        return {"name": ""}
    return None


def handle_list_in(**_k: Any) -> ToolResult:
    spoken: list[str] = []
    tv.handle_timeclock_status_utterance(
        "who is clocked in", speak=spoken.append, log=lambda *_: None
    )
    return ToolResult(
        handled=True,
        spoken=spoken[0] if spoken else "I couldn't read the timeclock.",
        tool="timeclock.list_clocked_in",
    )


def handle_status(op: str = "status", name: str = "", **_k: Any) -> ToolResult:
    tool_name = {
        "today_hours": "timeclock.get_today_hours",
        "last_shift": "timeclock.get_last_shift_duration",
        "current_shift": "timeclock.get_current_shift_duration",
    }.get(op, "timeclock.get_employee_status")
    phrase = {
        "status": f"is {name} clocked in",
        "when": f"what time did {name} clock in",
        "did_today": f"what time did {name} clock in",
        "today_hours": f"how many hours has {name} worked today",
        "last_shift": f"how long was {name} last shift",
        "current_shift": f"how long has {name} been clocked in",
    }.get(op, f"is {name} clocked in")
    spoken: list[str] = []
    tv.handle_timeclock_status_utterance(
        phrase, speak=spoken.append, log=lambda *_: None
    )
    reply = spoken[0] if spoken else "I couldn't look that up."
    return ToolResult(
        handled=True,
        spoken=reply,
        tool=tool_name,
        arguments={"op": op, "name": name},
    )


def handle_clock_in(name: str = "", **_k: Any) -> ToolResult:
    spoken: list[str] = []
    logs: list[str] = []
    handled = tv.handle_clock_in_utterance(
        f"clock {name} in" if name else "clock in",
        speak=spoken.append,
        log=logs.append,
    )
    reply = spoken[0] if spoken else ""
    if handled and name and not reply:
        reply = f"{name.title()} is clocked in."
    return ToolResult(
        handled=bool(handled),
        spoken=reply,
        tool="timeclock.clock_in",
        arguments={"name": name},
        data={"logs": logs},
    )


def handle_clock_out(name: str = "", **_k: Any) -> ToolResult:
    spoken: list[str] = []
    logs: list[str] = []
    handled = tv.handle_clock_out_utterance(
        f"clock {name} out",
        speak=spoken.append,
        log=logs.append,
        use_llm=False,
    )
    reply = spoken[0] if spoken else ""
    if handled and name and not reply:
        reply = f"{name.title()} is clocked out."
    return ToolResult(
        handled=bool(handled),
        spoken=reply,
        tool="timeclock.clock_out",
        arguments={"name": name},
        data={"logs": logs},
    )


register(
    ToolSpec(
        name="timeclock.list_clocked_in",
        description="Who is clocked in",
        tier=RiskTier.TIER1,
        handler=handle_list_in,
        match=match_list_in,
    )
)
register(
    ToolSpec(
        name="timeclock.get_employee_status",
        description="Employee IN/OUT and clock-in time",
        tier=RiskTier.TIER1,
        handler=handle_status,
        match=match_status,
    )
)
register(
    ToolSpec(
        name="timeclock.clock_out",
        description="Clock an enrolled employee out",
        tier=RiskTier.TIER1,
        handler=handle_clock_out,
        match=match_clock_out,
    )
)
register(
    ToolSpec(
        name="timeclock.clock_in",
        description="Clock an enrolled employee in",
        tier=RiskTier.TIER1,
        handler=handle_clock_in,
        match=match_clock_in,
    )
)
