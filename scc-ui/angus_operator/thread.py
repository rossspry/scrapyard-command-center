"""Conversation thread for Angus (phone + yard).

Persists to disk so a new PTT is not a new assistant. Idle timeout is days,
not minutes. Say “new conversation” to start over.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, List

_IDLE_SECONDS = float(os.getenv("ANGUS_THREAD_IDLE_SECONDS", str(7 * 24 * 3600)))
_MAX_MESSAGES = int(os.getenv("ANGUS_THREAD_MAX_MESSAGES", "80") or "80")

_turns: List[Dict[str, str]] = []
_touched = 0.0
_loaded = False
_file_mtime = 0.0


def _path() -> Path:
    return Path(
        os.getenv(
            "ANGUS_THREAD_PATH",
            "/home/ross/.local/share/glitch/angus_thread.json",
        )
    )


def _load() -> None:
    global _turns, _touched, _loaded, _file_mtime
    path = _path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
        if _loaded:
            return
        _loaded = True
        return
    if _loaded and mtime <= _file_mtime:
        return
    _loaded = True
    _file_mtime = mtime
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return
    if not isinstance(data, dict):
        return
    turns = data.get("turns") or []
    if isinstance(turns, list):
        _turns = [
            {"role": str(t.get("role")), "content": str(t.get("content") or "")[:800]}
            for t in turns
            if isinstance(t, dict) and t.get("role") in ("user", "assistant")
        ]
    try:
        _touched = float(data.get("touched_mono") or 0)
    except (TypeError, ValueError):
        _touched = 0.0
    # File mtime is wall clock; idle uses monotonic. Use saved idle_at wall time.
    try:
        idle_at = float(data.get("idle_at") or 0)
    except (TypeError, ValueError):
        idle_at = 0.0
    if idle_at:
        age = time.time() - idle_at
        if age > _IDLE_SECONDS:
            _turns = []
            _touched = 0.0
        else:
            _touched = time.monotonic() - max(0.0, age)


def _save() -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "turns": _turns,
                    "idle_at": time.time(),
                    "touched_mono": _touched,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    else:
        global _file_mtime
        try:
            _file_mtime = path.stat().st_mtime
        except OSError:
            pass


def _expire() -> None:
    global _turns, _touched
    _load()
    if _touched and (time.monotonic() - _touched) > _IDLE_SECONDS:
        _turns = []
        _touched = 0.0
        _save()


def history() -> List[Dict[str, str]]:
    _expire()
    return list(_turns)


def snapshot() -> Dict[str, object]:
    _expire()
    return {"ok": True, "turns": list(_turns), "count": len(_turns)}


def remember(user_text: str, assistant_text: str) -> None:
    global _touched
    user = (user_text or "").strip()
    assistant = (assistant_text or "").strip()
    if not user and not assistant:
        return
    _expire()
    if user:
        _turns.append({"role": "user", "content": user[:800]})
    if assistant:
        _turns.append({"role": "assistant", "content": assistant[:800]})
    overflow = len(_turns) - _MAX_MESSAGES
    if overflow > 0:
        del _turns[:overflow]
    _touched = time.monotonic()
    _save()


def clear() -> None:
    global _turns, _touched, _loaded
    _load()
    _turns = []
    _touched = 0.0
    _loaded = True
    _save()
