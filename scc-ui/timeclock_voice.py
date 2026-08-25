#!/usr/bin/env python3
"""Angus → SCC timeclock voice clock-out.

The voice loop / LLM may only extract intent + a spoken name.
This module calls the existing timeclock API; it never writes the punch DB.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

import requests

_ENV_CANDIDATES = (
    Path("/home/ross/.config/scc/glitch.env"),
    Path("/srv/scc-ui/glitch.env"),
)


def _load_env() -> None:
    for path in _ENV_CANDIDATES:
        if not path.is_file():
            continue
        try:
            for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
        except OSError:
            pass


_load_env()

def _timeclock_url() -> str:
    return os.getenv("TIMECLOCK_URL", "http://127.0.0.1:8095").rstrip("/")


def _timeclock_key() -> str:
    return (
        os.getenv("TIMECLOCK_VOICE_TOKEN") or os.getenv("TIMECLOCK_SECRET") or ""
    ).strip()

# Strip leftover wake words if classify_utterance did not.
_WAKE_PREFIX = re.compile(
    r"^(?:hey\s+)?(?:angus|glitch)\b[\s,]*",
    re.I,
)
_LEAD_IN = re.compile(
    r"^(?:please|can you|could you|would you|go ahead and|"
    r"you have access to (?:the )?(?:face )?timeclock[,.]?)\s+",
    re.I,
)
_TRAIL = re.compile(r"\s+(?:please|now|thanks|thank you)$", re.I)

_CLOCK_OUT_RES = (
    re.compile(
        r"^(?:clock|punch)\s+out\s+(?P<name>.+)$",
        re.I,
    ),
    re.compile(
        r"^(?:clock|punch)\s+(?P<name>.+?)\s+out$",
        re.I,
    ),
)

_CLOCK_IN_RES = (
    re.compile(
        r"^(?:clock|punch)\s+in\s+(?P<name>.+)$",
        re.I,
    ),
    re.compile(
        r"^(?:clock|punch)\s+(?P<name>.+?)\s+in$",
        re.I,
    ),
    re.compile(
        r"^(?:clock|punch)\s+me\s+in$",
        re.I,
    ),
    re.compile(
        r"^(?:clock|punch)\s+in$",
        re.I,
    ),
)

_LOOKS_LIKE = re.compile(
    r"\b(?:clock|punch)\b.+\bout\b|\bout\b.+\b(?:clock|punch)\b",
    re.I,
)

_LOOKS_LIKE_IN = re.compile(
    r"\b(?:clock|punch)\b.+\bin\b|\bin\b.+\b(?:clock|punch)\b",
    re.I,
)

_BANNED_NAMES = frozenset(
    {
        "me",
        "us",
        "everyone",
        "everybody",
        "all",
        "them",
        "somebody",
        "someone",
        "the",
        "a",
        "an",
    }
)


def _norm(text: str) -> str:
    t = (text or "").strip().lower()
    t = t.replace("'", "'")
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return " ".join(t.split())


def parse_clock_out_name(text: str) -> Optional[str]:
    """Return spoken employee name for a clock-out command, else None."""
    raw = _norm(text)
    if not raw:
        return None
    raw = _WAKE_PREFIX.sub("", raw).strip()
    for _ in range(3):
        nxt = _LEAD_IN.sub("", raw).strip()
        if nxt == raw:
            break
        raw = nxt
    raw = _TRAIL.sub("", raw).strip()
    if not raw:
        return None
    for cre in _CLOCK_OUT_RES:
        match = cre.match(raw)
        if not match:
            continue
        name = _norm(match.group("name"))
        if not name or name in _BANNED_NAMES:
            return None
        if name in {"out", "clock", "punch"}:
            return None
        return name
    return None


def looks_like_clock_out(text: str) -> bool:
    return bool(_LOOKS_LIKE.search(_norm(text)))


def looks_like_clock_in(text: str) -> bool:
    raw = _norm(text)
    if looks_like_clock_out(raw):
        return False
    return bool(_LOOKS_LIKE_IN.search(raw))


def parse_clock_in_name(text: str) -> Optional[str]:
    """Return spoken employee name for a clock-in command, or '' if nameless."""
    raw = _norm(text)
    if not raw:
        return None
    raw = _WAKE_PREFIX.sub("", raw).strip()
    for _ in range(3):
        nxt = _LEAD_IN.sub("", raw).strip()
        if nxt == raw:
            break
        raw = nxt
    raw = _TRAIL.sub("", raw).strip()
    if not raw:
        return None
    for cre in _CLOCK_IN_RES:
        match = cre.match(raw)
        if not match:
            continue
        if "name" not in (match.groupdict() or {}):
            return ""
        name = _norm(match.group("name") or "")
        if not name or name in _BANNED_NAMES or name in {"in", "clock", "punch"}:
            return ""
        return name
    return None


def extract_clock_out_name_llm(text: str) -> Optional[str]:
    """Ask the existing Angus LLM for intent+name only. Never punches."""
    try:
        from glitch_llm import ask_glitch
    except Exception:
        return None
    prompt = (
        "Extract a timeclock voice command. Reply with ONLY JSON, no markdown.\n"
        'If the user wants to clock/punch one named person out, reply: '
        '{"intent":"clock_out","employee":"<spoken name only>"}\n'
        'Otherwise reply: {"intent":"none"}\n'
        "Do not invent a last name. Do not pick a person if the name is missing.\n"
        f"Utterance: {text}"
    )
    try:
        result = ask_glitch(prompt, voice=True, max_tokens=40)
        reply = (result.get("reply") or "").strip()
    except Exception:
        return None
    if not reply:
        return None
    start = reply.find("{")
    end = reply.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(reply[start : end + 1])
    except json.JSONDecodeError:
        return None
    if str(data.get("intent") or "").strip().lower() != "clock_out":
        return None
    name = _norm(str(data.get("employee") or data.get("name") or ""))
    if not name or name in _BANNED_NAMES:
        return None
    return name


def request_clock_in(name: str, timeout: float = 8.0) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    key = _timeclock_key()
    if key:
        headers["X-Timeclock-Key"] = key
    response = requests.post(
        f"{_timeclock_url()}/api/voice/clock-in",
        json={"name": name},
        headers=headers,
        timeout=timeout,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {"ok": False, "error": f"http_{response.status_code}"}
    if not isinstance(payload, dict):
        payload = {"ok": False, "error": "bad_response"}
    payload.setdefault("http_status", response.status_code)
    return payload


def request_clock_out(name: str, timeout: float = 8.0) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    key = _timeclock_key()
    if key:
        headers["X-Timeclock-Key"] = key
    response = requests.post(
        f"{_timeclock_url()}/api/voice/clock-out",
        json={"name": name},
        headers=headers,
        timeout=timeout,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {"ok": False, "error": f"http_{response.status_code}"}
    if not isinstance(payload, dict):
        payload = {"ok": False, "error": "bad_response"}
    payload.setdefault("http_status", response.status_code)
    return payload


def _voice_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = _timeclock_key()
    if key:
        headers["X-Timeclock-Key"] = key
    return headers


def request_timeclock_status(timeout: float = 5.0) -> dict[str, Any]:
    response = requests.get(
        f"{_timeclock_url()}/api/voice/status",
        headers=_voice_headers(),
        timeout=timeout,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {"ok": False, "error": f"http_{response.status_code}"}
    if not isinstance(payload, dict):
        payload = {"ok": False, "error": "bad_response"}
    payload.setdefault("http_status", response.status_code)
    return payload


def request_employee_lookup(name: str, timeout: float = 5.0) -> dict[str, Any]:
    response = requests.get(
        f"{_timeclock_url()}/api/voice/lookup",
        params={"name": name},
        headers=_voice_headers(),
        timeout=timeout,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {"ok": False, "error": f"http_{response.status_code}"}
    if not isinstance(payload, dict):
        payload = {"ok": False, "error": "bad_response"}
    payload.setdefault("http_status", response.status_code)
    return payload


_WHO_IN = re.compile(
    r"\bwho(?:\s*s|\s*is)?(?:\s+else)?(?:\s+is)?\s+"
    r"(?:clocked in|on the clock|on shift|punched in|(?:still )?in)\b"
    r"|\banyone\b.+\b(?:clocked in|on the clock|on shift|punched in)\b",
    re.I,
)
_IS_IN = re.compile(
    r"^(?:is|did)\s+(?P<name>.+?)\s+"
    r"(?:clocked in|clocked out|clock in|punch in|punched in|on the clock|on shift)\??$",
    re.I,
)
_WHEN_IN = re.compile(
    r"^(?:what time|when)(?: did)?\s+(?P<name>.+?)\s+"
    r"(?:clock|punch)\s+in\??$"
    r"|^(?:what time|when)(?: did)?\s+(?P<name2>.+?)\s+clock in\??$",
    re.I,
)


def parse_who_clocked_in(text: str) -> bool:
    raw = _norm(text)
    raw = _WAKE_PREFIX.sub("", raw).strip()
    if not raw:
        return False
    return bool(_WHO_IN.search(raw))


def parse_is_clocked_in_name(text: str) -> Optional[str]:
    raw = _norm(text)
    raw = _WAKE_PREFIX.sub("", raw).strip()
    match = _IS_IN.match(raw)
    if not match:
        return None
    name = _norm(match.group("name") or "")
    if not name or name in _BANNED_NAMES:
        return None
    return name


_TODAY_HOURS = re.compile(
    r"^(?:how many hours has|how many hours have|how long has|how long have)\s+"
    r"(?P<name>.+?)\s+worked(?:\s+today)?\??$"
    r"|^(?:how many hours did|how long did)\s+(?P<name2>.+?)\s+work(?:\s+today)?\??$",
    re.I,
)
_LAST_SHIFT = re.compile(
    r"^(?:how long was|how long were|what was|how many hours was)\s+"
    r"(?P<name>.+?)\s+(?:s\s+)?last shift\??$",
    re.I,
)
_CURRENT_SHIFT = re.compile(
    r"^(?:how long has|how long have)\s+(?P<name>.+?)\s+"
    r"(?:been clocked in|been on the clock|been here|been in)\??$",
    re.I,
)


def parse_today_hours_name(text: str) -> Optional[str]:
    raw = _WAKE_PREFIX.sub("", _norm(text)).strip()
    match = _TODAY_HOURS.match(raw)
    if not match:
        return None
    name = _norm(match.group("name") or match.group("name2") or "")
    if not name or name in _BANNED_NAMES:
        return None
    return name


def parse_last_shift_name(text: str) -> Optional[str]:
    raw = _WAKE_PREFIX.sub("", _norm(text)).strip()
    match = _LAST_SHIFT.match(raw)
    if not match:
        return None
    name = _norm(match.group("name") or "")
    parts = name.split()
    if parts and parts[-1] in {"s"}:
        parts = parts[:-1]
    name = " ".join(parts)
    if not name or name in _BANNED_NAMES:
        return None
    return name


def parse_current_shift_name(text: str) -> Optional[str]:
    raw = _WAKE_PREFIX.sub("", _norm(text)).strip()
    match = _CURRENT_SHIFT.match(raw)
    if not match:
        return None
    name = _norm(match.group("name") or "")
    if not name or name in _BANNED_NAMES:
        return None
    return name


def request_employee_hours(name: str, timeout: float = 5.0) -> dict[str, Any]:
    response = requests.get(
        f"{_timeclock_url()}/api/voice/hours",
        params={"name": name},
        headers=_voice_headers(),
        timeout=timeout,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {"ok": False, "error": f"http_{response.status_code}"}
    if not isinstance(payload, dict):
        payload = {"ok": False, "error": "bad_response"}
    payload.setdefault("http_status", response.status_code)
    return payload


def parse_when_clocked_in_name(text: str) -> Optional[str]:
    raw = _norm(text)
    raw = _WAKE_PREFIX.sub("", raw).strip()
    match = _WHEN_IN.match(raw)
    if not match:
        # Simpler fallbacks after normalization (apostrophes already stripped).
        m2 = re.match(
            r"^(?:what time|when)(?: did)?\s+(.+?)\s+(?:clock|punch)\s+in$",
            raw,
            re.I,
        )
        if not m2:
            return None
        name = _norm(m2.group(1))
    else:
        name = _norm(match.group("name") or match.group("name2") or "")
    if not name or name in _BANNED_NAMES:
        return None
    return name


def _spoken_list(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _spoken_clock_time(iso_ts: str) -> str:
    raw = (iso_ts or "").strip()
    if not raw:
        return ""
    try:
        from datetime import datetime

        from glitch_llm import format_spoken_time_phrase

        dt = datetime.fromisoformat(raw)
        return format_spoken_time_phrase(dt)
    except Exception:
        return raw


def handle_timeclock_status_utterance(
    text: str,
    *,
    speak: Callable[[str], Any],
    log: Callable[[str], None],
) -> bool:
    """Local FaceTimeClock roster/status questions. Never punches."""
    if parse_who_clocked_in(text):
        log("[timeclock] status roster query")
        try:
            result = request_timeclock_status()
        except Exception as exc:
            log(f"[timeclock] status api error: {exc}")
            speak("I couldn't reach the timeclock.")
            return True
        people = result.get("clocked_in") or []
        names = [
            str(p.get("display") or p.get("person") or "").strip()
            for p in people
            if isinstance(p, dict)
        ]
        names = [n for n in names if n]
        if not names:
            speak("Nobody is clocked in.")
        else:
            speak(f"{_spoken_list(names)} {_are(len(names))} clocked in.")
        return True

    today_name = parse_today_hours_name(text)
    if today_name:
        log(f"[timeclock] today-hours query name={today_name!r}")
        try:
            result = request_employee_hours(today_name)
        except Exception as exc:
            log(f"[timeclock] hours api error: {exc}")
            speak("I couldn't reach the timeclock.")
            return True
        return _speak_hours(result, today_name, speak, want="today")

    last_name = parse_last_shift_name(text)
    if last_name:
        log(f"[timeclock] last-shift query name={last_name!r}")
        try:
            result = request_employee_hours(last_name)
        except Exception as exc:
            log(f"[timeclock] hours api error: {exc}")
            speak("I couldn't reach the timeclock.")
            return True
        return _speak_hours(result, last_name, speak, want="last")

    cur_name = parse_current_shift_name(text)
    if cur_name:
        log(f"[timeclock] current-shift query name={cur_name!r}")
        try:
            result = request_employee_hours(cur_name)
        except Exception as exc:
            log(f"[timeclock] hours api error: {exc}")
            speak("I couldn't reach the timeclock.")
            return True
        return _speak_hours(result, cur_name, speak, want="current")

    when_name = parse_when_clocked_in_name(text)
    if when_name:
        log(f"[timeclock] when-in query name={when_name!r}")
        try:
            result = request_employee_lookup(when_name)
        except Exception as exc:
            log(f"[timeclock] lookup api error: {exc}")
            speak("I couldn't reach the timeclock.")
            return True
        return _speak_lookup(result, when_name, speak, want="when")

    is_name = parse_is_clocked_in_name(text)
    if is_name:
        log(f"[timeclock] is-in query name={is_name!r}")
        try:
            result = request_employee_lookup(is_name)
        except Exception as exc:
            log(f"[timeclock] lookup api error: {exc}")
            speak("I couldn't reach the timeclock.")
            return True
        return _speak_lookup(result, is_name, speak, want="is")

    return False


def _are(n: int) -> str:
    return "is" if n == 1 else "are"


def _speak_lookup(
    result: dict[str, Any],
    spoken: str,
    speak: Callable[[str], Any],
    *,
    want: str,
) -> bool:
    if not result.get("ok"):
        reason = str(result.get("reason") or result.get("error") or "failed")
        if reason == "ambiguous_employee":
            speak("That name matches more than one employee. Please say the full name.")
        else:
            speak(f"I don't have an employee named {spoken}.")
        return True
    display = str(result.get("display") or result.get("person") or spoken.title())
    clocked_in = bool(result.get("clocked_in"))
    since = _spoken_clock_time(str(result.get("since") or result.get("last_in") or ""))
    if want == "when":
        last_in = _spoken_clock_time(str(result.get("last_in") or ""))
        if last_in and clocked_in:
            speak(f"{display} clocked in at {last_in} and is still on the clock.")
        elif last_in:
            speak(f"{display} last clocked in at {last_in} and is now clocked out.")
        else:
            speak(f"I don't have a clock-in time for {display}.")
        return True
    if clocked_in:
        if since:
            speak(f"Yes, {display} is clocked in. They clocked in at {since}.")
        else:
            speak(f"Yes, {display} is clocked in.")
    else:
        speak(f"No, {display} is clocked out.")
    return True


def _speak_hours(
    result: dict[str, Any],
    spoken: str,
    speak: Callable[[str], Any],
    *,
    want: str,
) -> bool:
    if not result.get("ok"):
        reason = str(result.get("reason") or result.get("error") or "failed")
        if reason == "ambiguous_employee":
            speak("That name matches more than one employee. Please say the full name.")
        else:
            speak(f"I don't have an employee named {spoken}.")
        return True
    display = str(result.get("display") or result.get("person") or spoken.title())
    if want == "today":
        spoken_dur = result.get("today_spoken") or "zero minutes"
        extra = " and is still on the clock" if result.get("clocked_in") else ""
        speak(f"{display} has worked {spoken_dur} today{extra}.")
        return True
    if want == "last":
        last = result.get("last_shift") or {}
        spoken_dur = last.get("spoken")
        if not spoken_dur:
            speak(f"I don't have a completed shift for {display}.")
        else:
            speak(f"{display}'s last shift was {spoken_dur}.")
        return True
    # current open shift
    if not result.get("clocked_in"):
        speak(f"{display} is not clocked in.")
        return True
    cur = result.get("current_shift") or {}
    spoken_dur = cur.get("spoken") or result.get("today_spoken") or "a short time"
    speak(f"{display} has been clocked in for {spoken_dur}.")
    return True


def handle_clock_out_utterance(
    text: str,
    *,
    speak: Callable[[str], Any],
    log: Callable[[str], None],
    use_llm: bool = True,
) -> bool:
    """Handle a clock-out command. True if this utterance was a clock-out attempt."""
    name = parse_clock_out_name(text)
    if not name and looks_like_clock_out(text) and use_llm:
        log("[timeclock] regex miss — asking LLM for intent+name only")
        name = extract_clock_out_name_llm(text)
        if name:
            log(f"[timeclock] llm employee={name!r}")
    if not name:
        if looks_like_clock_out(text):
            log("[timeclock] clock-out intent but no usable name")
            speak("Who should I clock out?")
            return True
        return False

    log(f"[timeclock] clock-out requested name={name!r}")
    try:
        result = request_clock_out(name)
    except Exception as exc:
        log(f"[timeclock] api error: {exc}")
        speak("I couldn't reach the timeclock.")
        return True

    if result.get("ok"):
        person = result.get("person") or result.get("resolved") or name
        log(f"[timeclock] PUNCH_OUT person={person} id={result.get('id')}")
        # Timeclock already dings and says "{Name} clocked out."
        return True

    reason = str(result.get("reason") or result.get("error") or "failed")
    person = result.get("person") or result.get("resolved")
    spoken = result.get("spoken") or name
    display = person or spoken.title()
    log(f"[timeclock] SKIPPED reason={reason} person={display}")

    if reason == "not_clocked_in":
        speak(f"{display} is already clocked out.")
    elif reason == "unknown_employee":
        speak(f"I don't have an employee named {spoken}.")
    elif reason == "ambiguous_employee":
        speak("That name matches more than one employee. Please say the full name.")
    elif reason == "not_employee":
        speak(f"{display} is not enrolled on the timeclock.")
    else:
        speak(f"I couldn't clock {display} out.")
    return True


def handle_clock_in_utterance(
    text: str,
    *,
    speak: Callable[[str], Any],
    log: Callable[[str], None],
) -> bool:
    """Handle a clock-in command. True if this utterance was a clock-in attempt."""
    name = parse_clock_in_name(text)
    if name is None and not looks_like_clock_in(text):
        return False
    who = (name or "").strip()
    if not who or who in {"me"}:
        log("[timeclock] clock-in intent but no usable name")
        speak("Who should I clock in?")
        return True

    log(f"[timeclock] clock-in requested name={who!r}")
    try:
        result = request_clock_in(who)
    except Exception as exc:
        log(f"[timeclock] api error: {exc}")
        speak("I couldn't reach the timeclock.")
        return True

    if result.get("ok"):
        person = result.get("person") or result.get("resolved") or who
        log(f"[timeclock] PUNCH_IN person={person} id={result.get('id')}")
        speak(f"{person} is clocked in.")
        return True

    reason = str(result.get("reason") or result.get("error") or "failed")
    person = result.get("person") or result.get("resolved")
    display = person or who.title()
    log(f"[timeclock] SKIPPED reason={reason} person={display}")
    if "already" in reason.replace("_", " ") and "in" in reason:
        speak(f"{display} is already clocked in.")
    elif reason == "unknown_employee":
        speak(f"I don't have an employee named {who}.")
    elif reason == "ambiguous_employee":
        speak("That name matches more than one employee. Please say the full name.")
    elif reason == "not_employee":
        speak(f"{display} is not enrolled on the timeclock.")
    else:
        speak(f"I couldn't clock {display} in.")
    return True
