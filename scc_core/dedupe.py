from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple

from .events import Event


@dataclass
class _Incident:
    best_event: Event
    last_updated: datetime


class DedupeAggregator:
    """Aggregate noisy detector events into single SCC incidents."""

    def __init__(self, window_seconds: int = 15):
        self.window = timedelta(seconds=window_seconds)
        self._incidents: Dict[Tuple[str, str], _Incident] = {}

    def process(self, event: Event) -> Optional[Event]:
        """
        Consume an event and decide whether to emit a notification-worthy incident.

        Returns the chosen Event when a notify decision should be emitted, otherwise None.
        """

        now = event.ts if event.ts.tzinfo else datetime.now(timezone.utc)
        self._purge(now)

        key = (event.camera_id, event.event_type)
        incident = self._incidents.get(key)
        if incident is None or (now - incident.best_event.ts > self.window):
            self._incidents[key] = _Incident(best_event=event, last_updated=now)
            return event

        incident.last_updated = now
        if self._is_preferred(event, incident.best_event):
            incident.best_event = event
        return None

    def _purge(self, now: datetime) -> None:
        expired_keys = [
            key
            for key, inc in self._incidents.items()
            if now - inc.last_updated > self.window
        ]
        for key in expired_keys:
            del self._incidents[key]

    @staticmethod
    def _is_preferred(candidate: Event, current: Event) -> bool:
        if candidate.source == current.source:
            return candidate.ts >= current.ts
        if candidate.source == "frigate":
            return True
        if current.source == "frigate":
            return False
        return candidate.ts >= current.ts
