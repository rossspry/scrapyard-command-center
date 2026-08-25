"""Deterministic operator router. Fast; no extra LLM round-trip."""

from __future__ import annotations

import re
from typing import Any, Optional

from angus_operator import audit, confirm
from angus_operator.registry import all_tools, get
from angus_operator.speech import (
    correct,
    learn_phrase,
    parse_correction_lesson,
    set_last_transcript,
)
from angus_operator.types import RiskTier, ToolResult

# Import tools for side-effect registration.
import angus_operator.tools  # noqa: F401

_TIER3 = re.compile(
    r"\b(?:delete|drop table|wipe the database|format the disk|firewall|"
    r"open the firewall|change (?:the )?pin|door codes?|rm -rf|"
    r"payroll (?:edit|change)|iptables|userdel|chmod 777)\b",
    re.I,
)


def handle_operator(text: str, *, log=print) -> Optional[ToolResult]:
    """Return ToolResult if handled, else None (caller may use LLM)."""
    raw = (text or "").strip()
    if not raw:
        return None

    lesson = parse_correction_lesson(raw)
    if lesson:
        heard, intended = lesson
        ok = learn_phrase(heard, intended)
        spoken = (
            "I'll remember that phrasing."
            if ok
            else "I won't store that as a correction."
        )
        log(f"[speech] learned={ok} heard={heard[:80]!r} intended={intended[:80]!r}")
        result = ToolResult(
            handled=True,
            spoken=spoken,
            tool="speech.learn",
            arguments={"heard": heard, "intended": intended},
        )
        audit.log_action(
            {
                "transcript": raw,
                "tool": result.tool,
                "tier": result.tier.value,
                "ok": ok,
            }
        )
        return result

    corrected, fixes = correct(raw)
    if fixes:
        log(f"[speech] corrected {fixes} -> {corrected!r}")
    set_last_transcript(corrected)

    pending = confirm.get_pending()
    if pending:
        decision = confirm.classify_confirm(corrected)
        if decision == "yes":
            spec = get(pending.tool)
            confirm.clear_pending()
            if not spec:
                result = ToolResult(
                    handled=True,
                    spoken="That request is no longer available.",
                    tool=pending.tool,
                    ok=False,
                )
            else:
                args = dict(pending.arguments)
                args["confirmed"] = True
                result = spec.handler(**args)
            audit.log_action(
                {
                    "transcript": corrected,
                    "tool": pending.tool,
                    "arguments": pending.arguments,
                    "tier": RiskTier.TIER2.value,
                    "confirm": "yes",
                    "ok": result.ok,
                    "spoken": result.spoken[:160],
                }
            )
            return result
        if decision == "no":
            confirm.clear_pending()
            audit.log_action(
                {
                    "transcript": corrected,
                    "tool": pending.tool,
                    "tier": RiskTier.TIER2.value,
                    "confirm": "no",
                    "ok": True,
                }
            )
            return ToolResult(
                handled=True,
                spoken="Okay, I won't make that change.",
                tool=pending.tool,
                arguments=pending.arguments,
                tier=RiskTier.TIER2,
            )
        # Ambiguous follow-up while something is pending: do not treat as yes.

    if _TIER3.search(corrected):
        result = ToolResult(
            handled=True,
            spoken=(
                "That needs stronger authorization than ordinary voice. "
                "I won't do it from here."
            ),
            tool="operator.refuse",
            tier=RiskTier.TIER3,
            ok=False,
            error="tier3",
        )
        audit.log_action(
            {
                "transcript": corrected,
                "tool": result.tool,
                "tier": result.tier.value,
                "ok": False,
                "error": "tier3",
            }
        )
        return result

    for spec in all_tools():
        if not spec.match:
            continue
        args = spec.match(corrected)
        if args is None:
            continue
        if spec.tier is RiskTier.TIER3:
            result = ToolResult(
                handled=True,
                spoken="That is protected. I won't do it from ordinary voice.",
                tool=spec.name,
                arguments=args,
                tier=RiskTier.TIER3,
                ok=False,
            )
            audit.log_action(
                {
                    "transcript": corrected,
                    "tool": spec.name,
                    "arguments": args,
                    "tier": spec.tier.value,
                    "ok": False,
                }
            )
            return result
        result = spec.handler(**args)
        if result.needs_confirm:
            confirm.set_pending(
                tool=spec.name,
                arguments={**args, **result.arguments},
                summary=result.pending_summary or result.spoken,
                transcript=corrected,
            )
            log(f"[operator] confirm required tool={spec.name}")
        audit.log_action(
            {
                "transcript": corrected,
                "tool": result.tool or spec.name,
                "arguments": result.arguments or args,
                "tier": result.tier.value,
                "confirm_required": result.needs_confirm,
                "ok": result.ok,
                "spoken": (result.spoken or "")[:160],
                "error": result.error,
            }
        )
        if result.handled:
            if not result.needs_confirm:
                from angus_operator.honesty import record

                record(result.tool or spec.name, result.spoken or "", result.ok)
            log(
                f"[operator] tool={result.tool or spec.name} "
                f"tier={result.tier.value} ok={int(result.ok)}"
            )
            return result
    return None
