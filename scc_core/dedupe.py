from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from .events import Event


@dataclass
class _Incident:
    best_event: Event
    first_seen: datetime
    last_updated: datetime


class DedupeAggregator:
    """Aggregate noisy detector events into single SCC incidents."""

    def __init__(self, window_seconds: int = 15):
        self.window = timedelta(seconds=window_seconds)
        self._incidents: Dict[Tuple[str, str], _Incident] = {}

    def process(self, event: Event) -> Optional[Event]:
        """
        Consume an event and decide whether to emit a notification-worthy incident.

        Rules:
        - Reolink is early-signal only (NO fallback alerts).
        - Frigate is authoritative; SCC emits only when a Frigate event arrives.
        - Within the dedupe window, prefer Frigate as the incident representative.
        """
        ts = event.ts if event.ts.tzinfo else datetime.now(timezone.utc)
        self._purge(ts)

        key = (event.camera_id, event.event_type)
        inc = self._incidents.get(key)

        expired = inc is None or (ts - inc.last_updated > self.window)
        if expired:
            inc = _Incident(best_event=event, first_seen=ts, last_updated=ts)
            self._incidents[key] = inc
        else:
            inc.last_updated = ts
            if self._is_preferred(event, inc.best_event):
                inc.best_event = event

        # NO fallback alerts: never emit based on Reolink-only signals.
        if event.source != "frigate":
            return None

        # Frigate confirms; emit the preferred representative (frigate-preferred).
        return inc.best_event

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
