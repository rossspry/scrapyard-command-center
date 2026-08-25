"""Shared operator types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional


class RiskTier(str, Enum):
    TIER1 = "tier1"  # execute now
    TIER2 = "tier2"  # spoken confirmation
    TIER3 = "tier3"  # refuse / escalate


@dataclass
class ToolResult:
    handled: bool
    spoken: str = ""
    tool: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    tier: RiskTier = RiskTier.TIER1
    ok: bool = True
    needs_confirm: bool = False
    pending_summary: str = ""
    error: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolSpec:
    name: str
    description: str
    tier: RiskTier
    handler: Callable[..., ToolResult]
    match: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None
