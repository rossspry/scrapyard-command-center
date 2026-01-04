from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
import threading
from typing import Any, Callable, Dict, Optional

import paho.mqtt.client as mqtt

from scc_core.events import Event

logger = logging.getLogger(__name__)


@dataclass
class MqttConfig:
    host: str
    port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None
    topic: str = "frigate/events"
    client_id: Optional[str] = None


class FrigateMqttAdapter:
    """MQTT adapter that normalizes Frigate events into SCC incidents."""

    def __init__(self, mqtt_config: MqttConfig):
        self._config = mqtt_config
        self._client = mqtt.Client(client_id=self._config.client_id)
        if self._config.username:
            self._client.username_pw_set(self._config.username, self._config.password)

        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)

        self._on_event: Optional[Callable[[Event], None]] = None
        self._stop_event: Optional[threading.Event] = None

    def start(self, on_event: Callable[[Event], None], stop_event: threading.Event) -> None:
        """Start consuming MQTT events and forward them through the callback."""

        self._on_event = on_event
        self._stop_event = stop_event

        logger.info(
            "Starting Frigate MQTT adapter",
            extra={"topic": self._config.topic, "host": self._config.host, "port": self._config.port},
        )
        try:
            self._client.connect(self._config.host, self._config.port)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to connect to MQTT broker")
            raise

        self._client.loop_start()
        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(0.5)
        finally:
            logger.info("Stopping Frigate MQTT adapter")
            self._client.disconnect()
            self._client.loop_stop()

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Dict[str, Any], rc: int) -> None:
        if rc == 0:
            logger.info("Connected to MQTT broker", extra={"topic": self._config.topic})
            client.subscribe(self._config.topic)
        else:
            logger.error("MQTT connection failed", extra={"code": rc})

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, rc: int) -> None:
        logger.warning("Disconnected from MQTT broker", extra={"code": rc})

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        try:
            payload_text = msg.payload.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("Ignoring message with undecodable payload", extra={"topic": msg.topic})
            return

        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            logger.warning("Ignoring non-JSON payload", extra={"payload": payload_text})
            return

        event = self._normalize_event(payload)
        if event is None:
            logger.debug("Ignoring unrecognized Frigate event", extra={"payload": payload})
            return

        if self._on_event:
            try:
                self._on_event(event)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to handle Frigate event")

    def _normalize_event(self, payload: Dict[str, Any]) -> Optional[Event]:
        record = self._extract_event_record(payload)
        if record is None:
            return None

        camera_id = record.get("camera") or record.get("camera_id")
        label = record.get("label")
        if not camera_id or not label:
            return None

        event_type, meta_label = self._map_event_type(label)
        timestamp = self._coerce_ts(
            record.get("frame_time")
            or record.get("start_time")
            or record.get("end_time")
            or payload.get("timestamp")
            or payload.get("time")
        )
        confidence = self._coerce_confidence(
            record.get("top_score") or record.get("score") or payload.get("confidence")
        )
        snapshot_url = record.get("snapshot") or record.get("thumbnail")

        meta: Dict[str, Any] = {}
        if meta_label:
            meta["label"] = meta_label
        if "id" in record:
            meta["event_id"] = record.get("id")
        if "sub_label" in record and record.get("sub_label"):
            meta["sub_label"] = record.get("sub_label")

        return Event(
            source="frigate",
            camera_id=str(camera_id),
            event_type=event_type,
            ts=timestamp,
            confidence=confidence,
            snapshot_url=snapshot_url,
            meta=meta,
        )

    @staticmethod
    def _extract_event_record(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if isinstance(payload, dict):
            if "after" in payload and isinstance(payload["after"], dict):
                return payload["after"]
            if "before" in payload and isinstance(payload["before"], dict):
                return payload["before"]
            return payload
        return None

    @staticmethod
    def _map_event_type(label: str) -> tuple[str, Optional[str]]:
        normalized = label.lower()
        if normalized == "person":
            return "person_detected", None
        if normalized in {"car", "truck", "bus", "motorcycle"}:
            return "vehicle_detected", None
        return "object_detected", normalized

    @staticmethod
    def _coerce_ts(raw: Any) -> datetime:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw, tz=timezone.utc)
        if isinstance(raw, str):
            try:
                # Handle trailing Z
                sanitized = raw.rstrip("Z")
                return datetime.fromisoformat(sanitized).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        return datetime.now(timezone.utc)

    @staticmethod
    def _coerce_confidence(raw: Any) -> Optional[float]:
        try:
            return float(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None
