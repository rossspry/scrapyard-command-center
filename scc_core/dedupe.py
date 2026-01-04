from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from .events import Event

logger = logging.getLogger(__name__)


@dataclass
class _Incident:
    best_event: Event
    last_updated: datetime
    first_seen: datetime
    has_frigate: bool = False


class DedupeAggregator:
    """Aggregate noisy detector events into single SCC incidents."""

    def __init__(self, window_seconds: int = 15):
        self.window = timedelta(seconds=window_seconds)
        self._incidents: Dict[Tuple[str, str], _Incident] = {}

    def process(self, event: Event) -> Optional[Event]:
        """
        Consume an event and decide whether to emit a notification-worthy incident.

        Returns the chosen Event when a Frigate event confirms an incident within the
        dedupe window; otherwise None.
        """

        now = event.ts if event.ts.tzinfo else datetime.now(timezone.utc)
        self._purge(now)

        key = (event.camera_id, event.event_type)
        incident = self._incidents.get(key)
        if incident is None or (now - incident.last_updated > self.window):
            incident = _Incident(
                best_event=event,
                last_updated=now,
                first_seen=now,
                has_frigate=event.source == "frigate",
            )
            self._incidents[key] = incident
        else:
            incident.last_updated = now
            if self._is_preferred(event, incident.best_event):
                incident.best_event = event
            if event.source == "frigate":
                incident.has_frigate = True

        if event.source == "frigate":
            return incident.best_event
        return None

    def _purge(self, now: datetime) -> None:
        expired_keys = []
        for key, inc in self._incidents.items():
            inactive = now - inc.last_updated > self.window
            pending_expired = not inc.has_frigate and now - inc.first_seen >= self.window
            if pending_expired:
                logger.debug(
                    "Dropping pending incident without Frigate confirmation",
                    extra={"camera_id": key[0], "event_type": key[1]},
                )
            if inactive or pending_expired:
                expired_keys.append(key)

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
