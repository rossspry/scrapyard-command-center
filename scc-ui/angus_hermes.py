#!/usr/bin/env python3
"""Angus client for the isolated Hermes Agent API (loopback only).

Conversational/memory sidecar. Not a yard controller. Operator tools must
not import this module. Live SCC state still comes from Angus tools.

Chat Completions are stateless: Angus sends the current user text plus recent
thread history and system/context. Durable Ross preferences live in Hermes
USER.md on this host (shared by shop voice and phone). Immediate context is
the Angus thread file. No session_id or user identifiers are sent.
"""

from __future__ import annotations

import ipaddress
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

_TRUE = ("1", "true", "yes", "on")
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
_MAX_REPLY_CHARS = 4000

_circuit_lock = threading.Lock()
_consecutive_failures = 0
_opened_at = 0.0
_stats_lock = threading.Lock()
_stats: Dict[str, Any] = {
    "requests": 0,
    "successes": 0,
    "failures": 0,
    "circuit_opens": 0,
    "http_errors": 0,
    "latencies_ms": [],
}


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def enabled() -> bool:
    return _env("ANGUS_HERMES_ENABLED", "false").lower() in _TRUE


def model_name() -> str:
    return _env("ANGUS_HERMES_MODEL", "angus-hermes") or "angus-hermes"


def connect_timeout_s() -> float:
    try:
        return max(0.2, float(_env("ANGUS_HERMES_CONNECT_TIMEOUT", "1") or "1"))
    except ValueError:
        return 1.0


def total_timeout_s() -> float:
    try:
        return max(connect_timeout_s() + 0.5, float(_env("ANGUS_HERMES_TIMEOUT", "10") or "10"))
    except ValueError:
        return 10.0


def timeout_s() -> float:
    """Total request timeout (connect + read budget)."""
    return total_timeout_s()


def circuit_threshold() -> int:
    try:
        return max(1, int(_env("ANGUS_HERMES_CIRCUIT_FAILURES", "3") or "3"))
    except ValueError:
        return 3


def circuit_cooldown_s() -> float:
    try:
        return max(1.0, float(_env("ANGUS_HERMES_CIRCUIT_COOLDOWN", "60") or "60"))
    except ValueError:
        return 60.0


def reasoning_effort() -> str:
    effort = _env("ANGUS_HERMES_REASONING_EFFORT", "low").lower() or "low"
    if effort not in ("low", "medium", "high", "xhigh"):
        return "low"
    return effort


def _api_key() -> str:
    return _env("ANGUS_HERMES_API_KEY")


def redact(text: Any) -> str:
    """Strip Hermes/API credentials from loggable strings."""
    raw = "" if text is None else str(text)
    key = _api_key()
    if key and len(key) >= 8:
        raw = raw.replace(key, "[redacted]")
    if "Bearer " in raw and key and len(key) >= 8:
        raw = raw.replace(f"Bearer {key}", "Bearer [redacted]")
    # Never keep URL userinfo if it slipped through.
    if "://" in raw and "@" in raw:
        raw = raw.split("://", 1)[0] + "://[redacted]@" + raw.rsplit("@", 1)[-1]
    return raw


def configured() -> bool:
    return bool(enabled() and _api_key() and _env("ANGUS_HERMES_URL", "http://127.0.0.1:8642/v1"))


def reset_circuit() -> None:
    global _consecutive_failures, _opened_at
    with _circuit_lock:
        _consecutive_failures = 0
        _opened_at = 0.0


def snapshot_stats() -> Dict[str, Any]:
    with _stats_lock:
        lats = list(_stats["latencies_ms"])
        out = {
            "requests": int(_stats["requests"]),
            "successes": int(_stats["successes"]),
            "failures": int(_stats["failures"]),
            "circuit_opens": int(_stats["circuit_opens"]),
            "http_errors": int(_stats["http_errors"]),
            "median_ms": None,
            "max_ms": None,
        }
    if lats:
        sl = sorted(lats)
        out["median_ms"] = sl[len(sl) // 2]
        out["max_ms"] = sl[-1]
    return out


def _note_request(ok: bool, elapsed_ms: int, error: str = "") -> None:
    with _stats_lock:
        _stats["requests"] += 1
        if ok:
            _stats["successes"] += 1
            _stats["latencies_ms"].append(int(elapsed_ms))
            _stats["latencies_ms"] = _stats["latencies_ms"][-200:]
        else:
            _stats["failures"] += 1
            if str(error).startswith("http_"):
                _stats["http_errors"] += 1


def circuit_state() -> str:
    """Return closed | open | probe."""
    with _circuit_lock:
        if _consecutive_failures < circuit_threshold():
            return "closed"
        elapsed = time.monotonic() - _opened_at
        if elapsed < circuit_cooldown_s():
            return "open"
        return "probe"


def _record_success() -> None:
    global _consecutive_failures, _opened_at
    with _circuit_lock:
        _consecutive_failures = 0
        _opened_at = 0.0


def _record_failure() -> None:
    global _consecutive_failures, _opened_at
    with _circuit_lock:
        _consecutive_failures += 1
        if _consecutive_failures == circuit_threshold():
            _opened_at = time.monotonic()
            with _stats_lock:
                _stats["circuit_opens"] += 1
        elif _consecutive_failures > circuit_threshold():
            _opened_at = time.monotonic()


def _is_loopback_host(host: str) -> bool:
    name = (host or "").strip().lower()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    if name in _LOOPBACK_HOSTS or name == "127.0.0.1":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def loopback_url(raw: Optional[str] = None) -> str:
    """Return a verified http(s) loopback base URL, or raise ValueError."""
    text = (raw if raw is not None else _env("ANGUS_HERMES_URL", "http://127.0.0.1:8642/v1")).strip()
    if not text:
        raise ValueError("empty_url")
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("non_http_scheme")
    if parsed.username or parsed.password:
        raise ValueError("userinfo_forbidden")
    host = parsed.hostname or ""
    if not _is_loopback_host(host):
        raise ValueError("not_loopback")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError("url_extras_forbidden")
    path = (parsed.path or "").rstrip("/")
    if not path:
        path = "/v1"
    netloc = parsed.hostname
    if parsed.port:
        netloc = f"{parsed.hostname}:{parsed.port}"
    if parsed.scheme == "http" and parsed.hostname == "::1":
        netloc = f"[::1]:{parsed.port or 80}"
    elif parsed.hostname == "::1":
        netloc = f"[::1]:{parsed.port}" if parsed.port else "[::1]"
    return f"{parsed.scheme}://{netloc}{path}"


def base_url() -> str:
    return loopback_url()


def _fail(error: str, t0: float, record: bool = True) -> Dict[str, Any]:
    elapsed = int((time.time() - t0) * 1000)
    err = redact(error)
    if record and error != "circuit_open":
        _record_failure()
        _note_request(False, elapsed, err)
    elif error == "circuit_open":
        _note_request(False, elapsed, err)
    return {
        "ok": False,
        "error": err,
        "elapsed_ms": elapsed,
    }


def complete(
    messages: List[Dict[str, str]],
    *,
    timeout: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Non-streaming chat completion. Returns None when disabled.

    Failures return {ok: False, error} without raising. Never logs the API key.
    """
    t0 = time.time()
    if not enabled():
        return None
    if not _api_key():
        return _fail("not_configured", t0, record=False)
    state = circuit_state()
    if state == "open":
        return _fail("circuit_open", t0, record=False)
    try:
        root = loopback_url()
    except ValueError as exc:
        return _fail(str(exc) or "bad_url", t0)
    url = f"{root}/chat/completions"
    body = {
        "model": model_name(),
        "model_options": {"reasoning_effort": reasoning_effort()},
        "stream": False,
        "messages": messages,
    }
    connect = connect_timeout_s()
    total = float(timeout) if timeout is not None else total_timeout_s()
    read = max(0.5, total - connect)
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(
            url,
            json=body,
            headers=headers,
            timeout=(connect, read),
            allow_redirects=False,
        )
    except requests.exceptions.ConnectTimeout:
        return _fail("connect_timeout", t0)
    except requests.exceptions.ReadTimeout:
        return _fail("total_timeout", t0)
    except requests.exceptions.ConnectionError:
        return _fail("connection_refused", t0)
    except requests.RequestException as exc:
        return _fail(type(exc).__name__, t0)
    if 300 <= resp.status_code < 400:
        return _fail("redirect_disabled", t0)
    if resp.status_code == 401:
        return _fail("http_401", t0)
    if resp.status_code == 429:
        return _fail("http_429", t0)
    if resp.status_code >= 500:
        return _fail(f"http_{resp.status_code}", t0)
    if resp.status_code >= 400:
        return _fail(f"http_{resp.status_code}", t0)
    try:
        payload = resp.json()
    except ValueError:
        return _fail("invalid_json", t0)
    if not isinstance(payload, dict):
        return _fail("invalid_json", t0)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return _fail("missing_choices", t0)
    message = (choices[0] or {}).get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return _fail("missing_message", t0)
    text = (message.get("content") or "")
    if not isinstance(text, str) or not text.strip():
        return _fail("empty_reply", t0)
    text = text.strip()
    if len(text) > _MAX_REPLY_CHARS:
        return _fail("oversized_response", t0)
    elapsed = int((time.time() - t0) * 1000)
    _record_success()
    _note_request(True, elapsed)
    return {
        "ok": True,
        "reply": text,
        "backend": "hermes",
        "model": str(payload.get("model") or model_name()),
        "elapsed_ms": elapsed,
        "usage": payload.get("usage") or {},
        "http": resp.status_code,
    }


def request_payload_preview(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """Sanitized description of what Angus sends. No credentials."""
    return {
        "url_host_policy": "loopback_only",
        "model": model_name(),
        "model_options": {"reasoning_effort": reasoning_effort()},
        "stream": False,
        "session_id": None,
        "user_identifiers": None,
        "message_roles": [m.get("role") for m in messages],
        "has_system": any(m.get("role") == "system" for m in messages),
        "history_turns": max(0, len([m for m in messages if m.get("role") in ("user", "assistant")]) - 1),
    }
