"""Safe STT vocabulary / phrase correction. Not Whisper retraining."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

_PATH = Path(
    os.getenv(
        "ANGUS_SPEECH_MAP_PATH",
        "/home/ross/.config/scc/angus_speech.json",
    )
)
_TZ = ZoneInfo(os.getenv("GLITCH_TIMEZONE", "America/New_York"))

_DEFAULT = {
    "version": 1,
    "phrase_fixes": {
        "clark ross out": "clock ross out",
        "clark sebrina out": "clock sebrina out",
        "clark sabrina out": "clock sabrina out",
        "punch ross out": "punch ross out",
        "face time clock": "facetimeclock",
        "face timeclock": "facetimeclock",
        "poker director": "poker director",
    },
    "token_fixes": {
        "clark": "clock",
        "sabrina": "sabrina",
        "sebrina": "sebrina",
        "angus": "angus",
        "frigate": "frigate",
        "facetimeclock": "facetimeclock",
    },
    "learned": [],
}

_CLOCK_CONTEXT = re.compile(r"\b(clock|clark|punch|out|in)\b", re.I)

_last_transcript = ""


def _load() -> dict:
    if not _PATH.is_file():
        data = json.loads(json.dumps(_DEFAULT))
        save(data)
        return data
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return json.loads(json.dumps(_DEFAULT))
        data.setdefault("phrase_fixes", {})
        data.setdefault("token_fixes", {})
        data.setdefault("learned", [])
        return data
    except (OSError, json.JSONDecodeError):
        return json.loads(json.dumps(_DEFAULT))


def save(data: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(_PATH)


def seed_from_names(names: List[str]) -> None:
    """Add employee/project names as identity tokens; do not overwrite learned."""
    data = _load()
    tokens = data.setdefault("token_fixes", {})
    for name in names:
        key = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
        if not key or " " in key:
            continue
        tokens.setdefault(key, key)
    save(data)


def set_last_transcript(text: str) -> None:
    global _last_transcript
    _last_transcript = (text or "").strip()


def last_transcript() -> str:
    return _last_transcript


def correct(text: str) -> Tuple[str, List[str]]:
    """Return (corrected, list of applied fix labels). Conservative."""
    raw = (text or "").strip()
    if not raw:
        return raw, []
    data = _load()
    applied: List[str] = []
    out = raw
    low = " ".join(out.lower().split())
    for src, dst in (data.get("phrase_fixes") or {}).items():
        s = " ".join(str(src).lower().split())
        d = str(dst)
        if s and s in low and s != " ".join(d.lower().split()):
            # Don't rewrite "clark" outside clock-in/out context.
            if "clark" in s and not _CLOCK_CONTEXT.search(low):
                continue
            pattern = re.compile(re.escape(src), re.I)
            out2 = pattern.sub(d, out, count=1)
            if out2 != out:
                out = out2
                applied.append(f"phrase:{src}->{d}")
                low = " ".join(out.lower().split())
    return out, applied


def parse_correction_lesson(text: str) -> Optional[Tuple[str, str]]:
    """'No, I said clock Sabrina out' → (heard, intended)."""
    t = (text or "").strip()
    m = re.match(
        r"^(?:no[,.]?\s+)?i said\s+(.+)$",
        t,
        re.I,
    )
    if not m:
        return None
    intended = m.group(1).strip(" .")
    heard = last_transcript()
    if not intended or not heard:
        return None
    return heard, intended


def learn_phrase(heard: str, intended: str) -> bool:
    """Record a non-sensitive phrase mapping. Refuses long/secret-looking text."""
    h = " ".join((heard or "").lower().split())
    i = " ".join((intended or "").split())
    if not h or not i or h == i.lower():
        return False
    if len(h) > 80 or len(i) > 80:
        return False
    if re.search(r"\d{3,}", h + i):
        return False
    data = _load()
    data.setdefault("phrase_fixes", {})[h] = i
    data.setdefault("learned", []).append(
        {
            "ts": datetime.now(_TZ).isoformat(timespec="seconds"),
            "heard": h,
            "intended": i,
        }
    )
    data["learned"] = data["learned"][-50:]
    save(data)
    return True


def list_learned() -> List[dict]:
    return list(_load().get("learned") or [])
