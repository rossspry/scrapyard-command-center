"""Tool registry. Tools register themselves on import."""

from __future__ import annotations

from typing import Dict, List, Optional

from angus_operator.types import ToolSpec

_TOOLS: Dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    _TOOLS[spec.name] = spec
    return spec


def get(name: str) -> Optional[ToolSpec]:
    return _TOOLS.get(name)


def all_tools() -> List[ToolSpec]:
    return list(_TOOLS.values())


def names() -> List[str]:
    return sorted(_TOOLS)
