from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class Event:
    """Normalized event representation for SCC inputs."""

    source: str
    camera_id: str
    event_type: str
    ts: datetime
    confidence: Optional[float] = None
    snapshot_url: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> Dict[str, Any]:
        """Return a compact summary for logging or notifications."""
        return {
            "camera_id": self.camera_id,
            "event_type": self.event_type,
            "chosen_source": self.source,
            "confidence": self.confidence,
            "ts": self.ts.isoformat(),
        }
