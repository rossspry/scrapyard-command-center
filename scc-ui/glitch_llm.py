#!/usr/bin/env python3
"""
Shared Glitch LLM router.

Voice (short spoken replies): local Ollama qwen3:1.7b with thinking disabled,
then xAI/Grok fallback. Hermes 64K is not used for the voice fast path.

Non-voice agent/memory: isolated Hermes (angus-hermes / angus-local 64K)
when enabled, then xAI fallback.

Device/tool routing happens before this module. External xAI calls are logged.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

# ---------------------------------------------------------------------------
# Env loading
# ---------------------------------------------------------------------------

_ENV_CANDIDATES = (
    Path("/home/ross/.config/scc/glitch.env"),
    Path("/srv/scc-ui/glitch.env"),
    Path("/home/ross/.config/scc/.env"),
)


def _load_env_files() -> None:
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
                # File is source of truth for voice/LLM config (quoted values supported).
                if key:
                    os.environ[key] = val
        except OSError:
            pass


_load_env_files()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "dolphin-llama3:8b")

XAI_API_KEY = os.getenv("XAI_API_KEY", "").strip()
XAI_BASE_URL = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/")
# grok-3 remains the general default; voice can override via GLITCH_VOICE_XAI_MODEL
XAI_MODEL = os.getenv("XAI_MODEL", "grok-3").strip()
# auto | local | xai  — default xai (Grok) for general usefulness
GLITCH_LLM_MODE = os.getenv("GLITCH_LLM_MODE", "xai").strip().lower()
XAI_ENABLED = os.getenv("XAI_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# Spoken-conversation fast path. Does not change the general XAI_MODEL default.
VOICE_FAST_PATH = _env_bool("GLITCH_VOICE_FAST_PATH", "true")
# auto | local | xai | inherit  — inherit follows GLITCH_LLM_MODE
VOICE_LLM_MODE = os.getenv("GLITCH_VOICE_LLM_MODE", "inherit").strip().lower()
# Non-reasoning xAI model used for short spoken replies. grok-3 stays available.
VOICE_XAI_MODEL = os.getenv(
    "GLITCH_VOICE_XAI_MODEL",
    "grok-4.20-0309-non-reasoning",
).strip()
VOICE_MAX_TOKENS = int(os.getenv("GLITCH_VOICE_MAX_TOKENS", "80") or "80")
VOICE_STREAM = _env_bool("GLITCH_VOICE_STREAM", "true")
VOICE_TEMPERATURE = float(os.getenv("GLITCH_VOICE_TEMPERATURE", "0.4") or "0.4")


def voice_local_model() -> str:
    """Fast spoken-reply model. Hermes 64K / angus-local is not this path."""
    return os.getenv("GLITCH_VOICE_LOCAL_MODEL", "qwen3:1.7b").strip() or "qwen3:1.7b"


def voice_local_think() -> bool:
    """Thinking/reasoning for the fast voice model. Default off."""
    return _env_bool("GLITCH_VOICE_LOCAL_THINK", "false")


def voice_local_timeout_s() -> float:
    """Ollama timeout for the fast voice path only. General Ollama stays at 180s."""
    try:
        return max(1.0, float(os.getenv("GLITCH_VOICE_LOCAL_TIMEOUT", "12") or "12"))
    except ValueError:
        return 12.0

TIMEZONE = os.getenv("GLITCH_TIMEZONE", "America/New_York")

_DEFAULT_SYSTEM = (
    "You are Angus, Ross's virtual intelligence agent for Accelerated Salvage and Metalworks "
    "and the Scrapyard Command Center (SCC). You are helpful, direct, and conversational. "
    "Keep voice answers to 1-3 short sentences unless he asks for detail. "
    "You can answer general knowledge, date/time, weather concepts, tech, business, and yard ops. "
    "Do NOT claim you only answer scrapyard questions — you are a full assistant. "
    "If you lack live device status, say so briefly rather than inventing it. "
    "Never call yourself Glitch. "
    "You cannot run docker, compose, apt, systemctl, or Home Assistant from this chat. "
    "Never say you started an upgrade, image pull, restart, or light change. "
    "Never invent logs, digests, download progress, or error lines. "
    "If Ross wants Frigate or code changed, tools handle it after he confirms — you only talk."
)

_VOICE_STYLE = (
    "Spoken reply rules: answer in one or two short sentences unless the user "
    "explicitly asks for more detail. No lists, no preamble, no markdown. "
    "Sound like Angus speaking aloud, not writing an essay."
)

SYSTEM_PROMPT = os.getenv("GLITCH_SYSTEM_PROMPT", _DEFAULT_SYSTEM)

CONTEXT_PATH = Path(
    os.getenv(
        "GLITCH_CONTEXT_PATH",
        "/home/ross/.config/scc/glitch_context.json",
    )
)
_STANDING_FILES = (
    Path("/opt/angus/ANGUS.md"),
    Path("/opt/angus/CAPABILITIES.md"),
    Path("/home/ross/.config/scc/angus_capabilities.md"),
    Path("/opt/angus/USER_PREFERENCES.md"),
)
LOG_PATH = Path(
    os.getenv(
        "GLITCH_EXTERNAL_LOG",
        "/home/ross/.local/share/glitch/logs/external_llm.log",
    )
)

# Force-local phrases (strip optional) when in auto mode
_LOCAL_PHRASES = (
    r"\bask local\b",
    r"\buse local\b",
    r"\bollama\b",
    r"\boffline\b",
)

# Prefer local only in auto mode for private device-control style intents
_LOCAL_KEYWORDS = (
    "restart frigate",
    "restart home assistant",
    "systemctl",
    "security mode",
)


def log_line(msg: str) -> None:
    print(msg, flush=True)


def _now_local() -> datetime:
    try:
        return datetime.now(ZoneInfo(TIMEZONE))
    except Exception:
        return datetime.now()


def _clock_block() -> str:
    """Authoritative SCC host clock, rebuilt on every request."""
    now = _now_local()
    tzname = now.tzname() or TIMEZONE
    spoken_time = format_spoken_time_phrase(now)
    spoken_date = format_spoken_date(now)
    return (
        "SCC HOST CLOCK (authoritative — do not use training-data dates or any "
        "other remembered date):\n"
        f"{spoken_date}\n"
        f"Local time is {spoken_time} {tzname}.\n"
        f"Timezone: {TIMEZONE}.\n"
        "When asked the date, day, or time, answer from this clock only. "
        "Speak times in words (five oh four AM). Never write 5:04, 5.04, or 504."
    )


def _user_with_clock(user_text: str) -> str:
    now = _now_local()
    tzname = now.tzname() or TIMEZONE
    return (
        f"[Host clock: {format_spoken_date(now)[:-1]}; "
        f"{format_spoken_time_phrase(now)} {tzname} / {TIMEZONE}]\n"
        f"{user_text}"
    )


def _load_context_block() -> str:
    if not CONTEXT_PATH.is_file():
        return ""
    try:
        data = json.loads(CONTEXT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    parts = []
    loc = data.get("location") or {}
    user = data.get("user") or {}
    if user:
        parts.append(
            f"User: {user.get('name', 'Ross')} ({user.get('business', 'Scrapyard')})."
        )
    if loc:
        parts.append(
            f"Location: {loc.get('city', '')}, {loc.get('state', '')} "
            f"{loc.get('zip', '')} ({loc.get('timezone', TIMEZONE)})."
        )
    # Skip old reminders that force scrapyard-only / 1-2 sentence if they conflict —
    # still include non-conflicting ones
    skip_bits = ("scrapyard only", "only answer", "never give long")
    for r in data.get("reminders") or []:
        rs = str(r).lower()
        if any(s in rs for s in skip_bits):
            continue
        parts.append(str(r))
    return " ".join(parts).strip()


def _load_standing_files() -> str:
    chunks: List[str] = []
    seen = set()
    for path in _STANDING_FILES:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            continue
        if not text:
            continue
        if len(text) > 6000:
            text = text[:6000].rsplit("\n", 1)[0] + "\n…"
        chunks.append(f"[{path.name}]\n{text}")
    return "\n\n".join(chunks)


def build_system_prompt(for_xai: bool = False, *, voice: bool = False) -> str:
    # Clock first so it is not buried after personality text.
    parts = [_clock_block(), SYSTEM_PROMPT]
    parts.append(
        "HARD RULE: You cannot run docker, compose, apt, systemctl, or Home Assistant "
        "from this chat. Never say you started an upgrade, image pull, restart, or "
        "light change. Never invent logs, digests, download progress, or error lines. "
        "Frigate and code changes only happen if a tool already confirmed this turn."
    )
    standing = _load_standing_files()
    if standing:
        parts.append("STANDING FILES (always in force):\n" + standing)
    if voice:
        parts.append(_VOICE_STYLE)
    ctx = _load_context_block()
    if ctx:
        parts.append(f"Context: {ctx}")
    if for_xai:
        parts.append(
            "You have up-to-date world knowledge via Grok. "
            "The SCC host clock above is still the only source for today's date and local time. "
            "FaceTimeClock, door lock, porch lights, and cameras are live SCC tools. "
            "If this request includes earlier turns, treat them as this same conversation — "
            "do not start over, and do not claim you lack memory or telemetry of those turns. "
            "If you were not given a live roster this turn, do not invent punches; "
            "just answer from the thread you have. "
            "Never continue a fake background job. If no tool result is in this turn, "
            "you did not start Docker, Frigate, or Home Assistant work."
        )
    return "\n\n".join(parts)


_HOUR_WORDS = (
    "twelve",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
)
_ONES = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
)
_TEENS = (
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
_TENS = {
    20: "twenty",
    30: "thirty",
    40: "forty",
    50: "fifty",
}


_ORDS_1_19 = (
    "",
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "eleventh",
    "twelfth",
    "thirteenth",
    "fourteenth",
    "fifteenth",
    "sixteenth",
    "seventeenth",
    "eighteenth",
    "nineteenth",
)


def format_spoken_minutes(minute: int) -> str:
    """TTS-safe minute words. Never returns digits."""
    m = int(minute)
    if m <= 0:
        return ""
    if m < 10:
        return f"oh {_ONES[m]}"
    if m < 20:
        return _TEENS[m - 10]
    tens = (m // 10) * 10
    ones = m % 10
    if ones == 0:
        return _TENS[tens]
    return f"{_TENS[tens]}-{_ONES[ones]}"


def format_spoken_ordinal_day(day: int) -> str:
    d = int(day)
    if 1 <= d <= 19:
        return _ORDS_1_19[d]
    if d == 20:
        return "twentieth"
    if d == 30:
        return "thirtieth"
    tens = (d // 10) * 10
    ones = d % 10
    return f"{_TENS[tens]}-{_ORDS_1_19[ones]}"


def format_spoken_year(year: int) -> str:
    """2026 -> twenty twenty-six. Never returns digits."""
    y = int(year)
    if 2000 <= y <= 2099:
        rest = y % 100
        if rest == 0:
            return "two thousand"
        if rest < 10:
            return f"twenty oh {_ONES[rest]}"
        if rest < 20:
            return f"twenty {_TEENS[rest - 10]}"
        tens = (rest // 10) * 10
        ones = rest % 10
        if ones == 0:
            return f"twenty {_TENS[tens]}"
        return f"twenty {_TENS[tens]}-{_ONES[ones]}"
    return " ".join(_ONES[int(ch)] if ch.isdigit() else ch for ch in str(y))


def format_spoken_time(dt: datetime) -> str:
    """12-hour spoken English for Chatterbox. No digits, colons, or tz abbrevs.

    00:05 -> It's twelve oh five AM
    03:25 -> It's three twenty-five AM
    15:00 -> It's three PM
    """
    hour24 = int(dt.hour)
    minute = int(dt.minute)
    ampm = "AM" if hour24 < 12 else "PM"
    hour_word = _HOUR_WORDS[hour24 % 12]
    minute_words = format_spoken_minutes(minute)
    if not minute_words:
        return f"It's {hour_word} {ampm}."
    return f"It's {hour_word} {minute_words} {ampm}."


def format_spoken_date(dt: datetime) -> str:
    """Full spoken date. No ISO or numeric year/day."""
    weekday = dt.strftime("%A")
    month = dt.strftime("%B")
    return (
        f"Today is {weekday}, {month} {format_spoken_ordinal_day(dt.day)}, "
        f"{format_spoken_year(dt.year)}."
    )


def get_spoken_time(dt: datetime | None = None) -> str:
    return format_spoken_time(dt or _now_local())


def get_spoken_date(dt: datetime | None = None) -> str:
    return format_spoken_date(dt or _now_local())


def format_spoken_time_phrase(dt: datetime) -> str:
    """Time-of-day phrase without 'It's', for embedding in other sentences."""
    text = format_spoken_time(dt).rstrip(".")
    if text.startswith("It's "):
        return text[len("It's ") :]
    return text


_CLOCK_HM = re.compile(
    r"\b(?P<h>\d{1,2})[:.](?P<m>\d{2})(?:\s*(?P<p>a\.?m\.?|p\.?m\.?))?\b",
    re.I,
)
_CLOCK_H_AMPM = re.compile(
    r"\b(?P<h>\d{1,2})\s*(?P<p>a\.?m\.?|p\.?m\.?)\b",
    re.I,
)


def _phrase_from_hour_minute(hour: int, minute: int, meridiem: str = "") -> str:
    hour24 = int(hour)
    minute = int(minute)
    p = (meridiem or "").lower().replace(".", "").strip()
    if p.startswith("p"):
        if hour24 < 12:
            hour24 = hour24 + 12
        elif hour24 == 12:
            hour24 = 12
    elif p.startswith("a"):
        if hour24 == 12:
            hour24 = 0
        elif hour24 > 12:
            hour24 = hour24 % 12
    elif hour24 > 23:
        return ""
    if hour24 > 23 or minute > 59:
        return ""
    dummy = datetime(2000, 1, 1, hour24, minute)
    phrase = format_spoken_time_phrase(dummy)
    if not p:
        phrase = re.sub(r"\s+(AM|PM)$", "", phrase, flags=re.I)
    return phrase


def rewrite_digits_for_tts(text: str) -> str:
    """Stop Chatterbox saying 5:04 as 'five thousand four'."""
    raw = text or ""

    def hm(match: re.Match) -> str:
        phrase = _phrase_from_hour_minute(
            int(match.group("h")),
            int(match.group("m")),
            match.group("p") or "",
        )
        return phrase or match.group(0)

    def ham(match: re.Match) -> str:
        phrase = _phrase_from_hour_minute(
            int(match.group("h")),
            0,
            match.group("p") or "",
        )
        return phrase or match.group(0)

    out = _CLOCK_HM.sub(hm, raw)
    out = _CLOCK_H_AMPM.sub(ham, out)
    return out


def clock_query_kind(text: str) -> Optional[str]:
    """Return 'time', 'date', or 'both' for obvious clock questions."""
    t = (text or "").lower().replace("'", "").replace("'", "")
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = " ".join(t.split())
    if not t:
        return None
    if any(
        p in t
        for p in ("what time and date", "date and time", "time and date")
    ):
        return "both"
    if any(
        p in t
        for p in (
            "what is the date",
            "whats the date",
            "what date is it",
            "todays date",
            "current date",
            "what day is it",
            "what day of the week",
            "day of the week",
            "whats today",
            "what is today",
            "whats the day",
        )
    ):
        return "date"
    if any(
        p in t
        for p in (
            "what time is it",
            "whats the time",
            "what is the time",
            "tell me the time",
            "current time",
            "got the time",
            "do you have the time",
            "what time",
        )
    ):
        return "time"
    return None


def spoken_clock_reply(kind: str = "time") -> str:
    """Local spoken date/time so simple clock questions skip the LLM."""
    now = _now_local()
    log_line(
        f"[local] wall={now.strftime('%Y-%m-%d %I:%M %p %Z')} tz={TIMEZONE}"
    )
    if kind == "date":
        return get_spoken_date(now)
    spoken_time = get_spoken_time(now)
    if kind == "both":
        date_tail = get_spoken_date(now)
        if date_tail.startswith("Today is "):
            date_tail = date_tail[len("Today is ") :].rstrip(".")
        return f"{spoken_time.rstrip('.')} on {date_tail}."
    return spoken_time


def _log_external(event: Dict[str, Any]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        event = dict(event)
        event.setdefault("ts", _now_local().isoformat())
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as e:
        log_line(f"[llm] external log failed: {e}")


def xai_available() -> bool:
    return bool(XAI_ENABLED and XAI_API_KEY)


def detect_force_local(user_text: str) -> bool:
    t = user_text.lower()
    if any(re.search(p, t, flags=re.IGNORECASE) for p in _LOCAL_PHRASES):
        return True
    return any(k in t for k in _LOCAL_KEYWORDS)


def strip_route_phrases(user_text: str) -> str:
    text = user_text.strip()
    for pat in _LOCAL_PHRASES:
        text = re.sub(pat, " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" ,:.-")
    return text or user_text.strip()


def _effective_mode(voice: bool = False) -> str:
    if voice and VOICE_FAST_PATH and VOICE_LLM_MODE in ("auto", "local", "xai"):
        return VOICE_LLM_MODE
    return GLITCH_LLM_MODE if GLITCH_LLM_MODE in ("auto", "local", "xai") else "xai"


def choose_backend(user_text: str, *, voice: bool = False) -> Tuple[str, str]:
    """
    Returns (backend, cleaned_user_text).
    backend: 'local' | 'xai'
    """
    cleaned = strip_route_phrases(user_text)
    mode = _effective_mode(voice)

    if mode == "local":
        return "local", user_text.strip()

    if mode == "xai":
        if xai_available():
            return "xai", user_text.strip()
        log_line("[llm] GLITCH_LLM_MODE=xai but no XAI_API_KEY; falling back to local")
        return "local", user_text.strip()

    # auto: prefer xAI for everything except explicit local force
    if detect_force_local(user_text):
        return "local", cleaned
    if xai_available():
        return "xai", user_text.strip()
    return "local", user_text.strip()


def _xai_model_for(voice: bool = False, model: Optional[str] = None) -> str:
    if model:
        return model.strip()
    if voice and VOICE_FAST_PATH and VOICE_XAI_MODEL:
        return VOICE_XAI_MODEL
    return XAI_MODEL


_SENTENCE_END = re.compile(r'(?<=[.!?])(?:["\')\]]*)(?:\s+|$)')


def pop_complete_sentences(buf: str) -> Tuple[List[str], str]:
    """Split completed spoken sentences off the front of a streaming buffer."""
    done: List[str] = []
    while True:
        match = _SENTENCE_END.search(buf)
        if not match:
            break
        piece = buf[: match.end()].strip()
        buf = buf[match.end() :]
        if piece:
            done.append(piece)
    return done, buf


def _chat_messages(
    user_text: str,
    system_prompt: Optional[str],
    *,
    for_xai: bool,
    voice: bool,
    history: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": system_prompt or build_system_prompt(for_xai, voice=voice),
        }
    ]
    for turn in history or []:
        role = str(turn.get("role") or "")
        content = str(turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": _user_with_clock(user_text)})
    return messages


def _ollama_payload(
    user_text: str,
    system_prompt: Optional[str],
    *,
    voice: bool,
    max_tokens: Optional[int],
    stream: bool,
    history: Optional[List[Dict[str, str]]] = None,
    model: Optional[str] = None,
    think: Optional[bool] = None,
) -> Dict[str, Any]:
    options: Dict[str, Any] = {}
    if max_tokens:
        options["num_predict"] = int(max_tokens)
    if voice:
        options["temperature"] = VOICE_TEMPERATURE
    payload: Dict[str, Any] = {
        "model": (model or OLLAMA_MODEL),
        "stream": bool(stream),
        "messages": _chat_messages(
            user_text, system_prompt, for_xai=False, voice=voice, history=history
        ),
    }
    if think is not None:
        payload["think"] = bool(think)
    if options:
        payload["options"] = options
    return payload


def ask_ollama(
    user_text: str,
    system_prompt: Optional[str] = None,
    *,
    voice: bool = False,
    max_tokens: Optional[int] = None,
    history: Optional[List[Dict[str, str]]] = None,
    model: Optional[str] = None,
    think: Optional[bool] = None,
    timeout: Optional[float] = None,
) -> str:
    use_model = (model or OLLAMA_MODEL)
    payload = _ollama_payload(
        user_text,
        system_prompt,
        voice=voice,
        max_tokens=max_tokens,
        stream=False,
        history=history,
        model=use_model,
        think=think,
    )
    think_s = "n/a" if think is None else str(int(bool(think)))
    use_timeout = 180.0 if timeout is None else float(timeout)
    log_line(
        f"[ollama] model={use_model} voice={int(voice)} "
        f"think={think_s} max_tokens={max_tokens or '-'} timeout={use_timeout:g}"
    )
    r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=use_timeout)
    r.raise_for_status()
    data = r.json()
    return (data.get("message") or {}).get("content", "").strip()


def _iter_ollama_tokens(
    user_text: str,
    system_prompt: Optional[str],
    *,
    voice: bool,
    max_tokens: Optional[int],
    history: Optional[List[Dict[str, str]]] = None,
    model: Optional[str] = None,
    think: Optional[bool] = None,
) -> Iterator[str]:
    use_model = (model or OLLAMA_MODEL)
    payload = _ollama_payload(
        user_text,
        system_prompt,
        voice=voice,
        max_tokens=max_tokens,
        stream=True,
        history=history,
        model=use_model,
        think=think,
    )
    log_line(f"[ollama] stream model={use_model} voice={int(voice)}")
    r = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json=payload,
        timeout=180,
        stream=True,
    )
    r.raise_for_status()
    for raw in r.iter_lines(decode_unicode=True):
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        piece = ((data.get("message") or {}).get("content")) or ""
        if piece:
            yield piece
        if data.get("done"):
            break


def _xai_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json",
    }


def _xai_payload(
    user_text: str,
    system_prompt: Optional[str],
    *,
    model: str,
    voice: bool,
    max_tokens: Optional[int],
    stream: bool,
    history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model,
        "messages": _chat_messages(
            user_text, system_prompt, for_xai=True, voice=voice, history=history
        ),
        "temperature": VOICE_TEMPERATURE if voice else 0.6,
        "stream": bool(stream),
    }
    if max_tokens:
        payload["max_tokens"] = int(max_tokens)
    return payload


def ask_xai(
    user_text: str,
    system_prompt: Optional[str] = None,
    *,
    voice: bool = False,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    if not xai_available():
        raise RuntimeError("xAI not configured (set XAI_API_KEY in ~/.config/scc/glitch.env)")

    use_model = _xai_model_for(voice, model)
    payload = _xai_payload(
        user_text,
        system_prompt,
        model=use_model,
        voice=voice,
        max_tokens=max_tokens,
        stream=False,
        history=history,
    )
    log_line(
        f"[xai] model={use_model} voice={int(voice)} max_tokens={max_tokens or '-'}"
    )
    t0 = time.time()
    r = requests.post(
        f"{XAI_BASE_URL}/chat/completions",
        headers=_xai_headers(),
        json=payload,
        timeout=120,
    )
    elapsed_ms = int((time.time() - t0) * 1000)
    if r.status_code >= 400:
        _log_external(
            {
                "provider": "xai",
                "model": use_model,
                "ok": False,
                "status": r.status_code,
                "error": r.text[:500],
                "elapsed_ms": elapsed_ms,
                "user_preview": user_text[:200],
                "voice": voice,
            }
        )
        r.raise_for_status()
    data = r.json()
    try:
        content = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError) as e:
        raise RuntimeError(f"Unexpected xAI response: {e}") from e

    _log_external(
        {
            "provider": "xai",
            "model": use_model,
            "ok": True,
            "status": r.status_code,
            "elapsed_ms": elapsed_ms,
            "user_preview": user_text[:200],
            "reply_preview": content[:200],
            "voice": voice,
        }
    )
    return content


def _iter_xai_tokens(
    user_text: str,
    system_prompt: Optional[str],
    *,
    model: str,
    voice: bool,
    max_tokens: Optional[int],
    history: Optional[List[Dict[str, str]]] = None,
) -> Iterator[str]:
    if not xai_available():
        raise RuntimeError("xAI not configured (set XAI_API_KEY in ~/.config/scc/glitch.env)")
    payload = _xai_payload(
        user_text,
        system_prompt,
        model=model,
        voice=voice,
        max_tokens=max_tokens,
        stream=True,
        history=history,
    )
    log_line(f"[xai] stream model={model} voice={int(voice)} max_tokens={max_tokens or '-'}")
    t0 = time.time()
    r = requests.post(
        f"{XAI_BASE_URL}/chat/completions",
        headers=_xai_headers(),
        json=payload,
        timeout=120,
        stream=True,
    )
    if r.status_code >= 400:
        body = r.text[:500]
        _log_external(
            {
                "provider": "xai",
                "model": model,
                "ok": False,
                "status": r.status_code,
                "error": body,
                "elapsed_ms": int((time.time() - t0) * 1000),
                "user_preview": user_text[:200],
                "voice": voice,
                "stream": True,
            }
        )
        r.raise_for_status()
    collected: List[str] = []
    for raw in r.iter_lines(decode_unicode=True):
        if not raw:
            continue
        if raw.startswith("data:"):
            raw = raw[5:].strip()
        if raw == "[DONE]":
            break
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        try:
            piece = data["choices"][0].get("delta", {}).get("content") or ""
        except (KeyError, IndexError, TypeError, AttributeError):
            piece = ""
        if piece:
            collected.append(piece)
            yield piece
    _log_external(
        {
            "provider": "xai",
            "model": model,
            "ok": True,
            "status": r.status_code,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "user_preview": user_text[:200],
            "reply_preview": "".join(collected)[:200],
            "voice": voice,
            "stream": True,
        }
    )


CONVERSE_UNAVAILABLE = (
    "I'm having trouble reaching my conversational service right now."
)


def _hermes_mode() -> bool:
    try:
        import angus_hermes

        return angus_hermes.enabled()
    except Exception:
        return False


def _sanitize_reply(text: str) -> Tuple[str, bool]:
    raw = (text or "").strip()
    try:
        from angus_operator.honesty import sanitize_llm_reply
    except Exception:
        return raw, False
    safe = sanitize_llm_reply(raw)
    return safe, bool(raw) and safe != raw


def _sanitized_nonstream_fallback(
    user_text: str,
    *,
    voice: bool,
    history: Optional[List[Dict[str, str]]] = None,
    fallback_from: str = "hermes",
) -> Dict[str, Any]:
    """Non-streaming xAI fallback. Sanitize the full reply before any speech."""
    cleaned = (user_text or "").strip()
    xai_model = _xai_model_for(voice, None)
    reply = ""
    used_model = xai_model
    try:
        if not xai_available():
            raise RuntimeError("xai_unavailable")
        try:
            reply = ask_xai(
                cleaned,
                voice=voice,
                model=xai_model,
                max_tokens=None,
                history=history,
            )
        except Exception as exc:
            if voice and xai_model != XAI_MODEL:
                log_line(f"[llm] fallback voice model failed ({type(exc).__name__}); retry {XAI_MODEL}")
                reply = ask_xai(
                    cleaned,
                    voice=voice,
                    model=XAI_MODEL,
                    max_tokens=None,
                    history=history,
                )
                used_model = XAI_MODEL
            else:
                raise
    except Exception as exc:
        log_line(f"[llm] fallback xAI failed ({type(exc).__name__})")
        _log_external(
            {
                "provider": "xai",
                "ok": False,
                "error": type(exc).__name__,
                "fallback": "unavailable",
                "fallback_from": fallback_from,
                "user_preview": cleaned[:200],
                "voice": voice,
            }
        )
        return {
            "reply": CONVERSE_UNAVAILABLE,
            "backend": "failure",
            "model": "",
            "cleaned_user": cleaned,
            "sanitized": False,
        }
    safe, hit = _sanitize_reply(reply)
    _log_external(
        {
            "provider": "xai",
            "model": used_model,
            "ok": True,
            "user_preview": cleaned[:200],
            "reply_preview": safe[:200],
            "voice": voice,
            "stream": False,
            "fallback_from": fallback_from,
            "sanitized": hit,
        }
    )
    return {
        "reply": safe,
        "backend": "xai",
        "model": used_model,
        "cleaned_user": cleaned,
        "sanitized": hit,
    }


def _voice_token_limit(voice: bool, max_tokens: Optional[int]) -> Optional[int]:
    if max_tokens is not None:
        return max_tokens
    if voice and VOICE_FAST_PATH:
        return VOICE_MAX_TOKENS
    return None


def _try_hermes(
    user_text: str,
    *,
    voice: bool,
    history: Optional[List[Dict[str, str]]] = None,
) -> Optional[Dict[str, Any]]:
    """Call isolated Hermes if enabled. None means caller should use xAI/Ollama.

    The full Hermes reply is sanitized here, before ask_glitch returns or
    iter_glitch_sentences yields a sentence, so speak()/TTS never see a
    claimed SCC action.
    """
    try:
        import angus_hermes
    except Exception as exc:
        log_line(f"[hermes] import failed ({type(exc).__name__}); using existing LLM path")
        return None
    if not angus_hermes.enabled() or not angus_hermes._api_key():
        return None
    from angus_operator.honesty import sanitize_llm_reply

    messages = _chat_messages(
        user_text, None, for_xai=True, voice=voice, history=history
    )
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = (
            (messages[0].get("content") or "")
            + "\n\nDurable preferences are already available. "
            "Do not rewrite them unless Ross states a new lasting preference. "
            "Never claim you performed or verified a live SCC action."
        )
    log_line(
        f"[hermes] model={angus_hermes.model_name()} "
        f"effort={angus_hermes.reasoning_effort()} voice={int(voice)}"
    )
    result = angus_hermes.complete(messages)
    if not result:
        return None
    if not result.get("ok"):
        err = angus_hermes.redact(result.get("error") or "error")
        log_line(
            f"[hermes] failed ({err}); "
            f"{result.get('elapsed_ms')}ms; falling back"
        )
        _log_external(
            {
                "provider": "hermes",
                "model": angus_hermes.model_name(),
                "ok": False,
                "error": err[:200],
                "elapsed_ms": result.get("elapsed_ms"),
                "fallback": "xai",
                "user_preview": user_text[:200],
                "voice": voice,
            }
        )
        return None
    raw_reply = (result.get("reply") or "").strip()
    safe = sanitize_llm_reply(raw_reply)
    result = dict(result)
    result["reply"] = safe
    result["sanitized"] = safe != raw_reply
    _log_external(
        {
            "provider": "hermes",
            "model": result.get("model"),
            "ok": True,
            "elapsed_ms": result.get("elapsed_ms"),
            "user_preview": user_text[:200],
            "reply_preview": safe[:200],
            "voice": voice,
            "stream": False,
            "sanitized": result["sanitized"],
        }
    )
    return result


def _try_voice_local(
    user_text: str,
    *,
    history: Optional[List[Dict[str, str]]] = None,
    max_tokens: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Fast spoken path: local Ollama with thinking disabled. None = use xAI."""
    model = voice_local_model()
    think = voice_local_think()
    token_limit = _voice_token_limit(True, max_tokens)
    timeout_s = voice_local_timeout_s()
    log_line(
        f"[voice-local] model={model} think={int(bool(think))} "
        f"max_tokens={token_limit or '-'} timeout={timeout_s:g}"
    )
    try:
        reply = ask_ollama(
            user_text,
            voice=True,
            max_tokens=token_limit,
            history=history,
            model=model,
            think=think,
            timeout=timeout_s,
        )
    except Exception as exc:
        log_line(f"[voice-local] failed ({type(exc).__name__}); falling back")
        _log_external(
            {
                "provider": "ollama",
                "model": model,
                "ok": False,
                "error": type(exc).__name__,
                "fallback": "xai",
                "user_preview": user_text[:200],
                "voice": True,
                "think": think,
            }
        )
        return None
    raw_reply = (reply or "").strip()
    if not raw_reply:
        log_line("[voice-local] empty reply; falling back")
        return None
    safe, hit = _sanitize_reply(raw_reply)
    _log_external(
        {
            "provider": "ollama",
            "model": model,
            "ok": True,
            "user_preview": user_text[:200],
            "reply_preview": safe[:200],
            "voice": True,
            "stream": False,
            "think": think,
            "sanitized": hit,
        }
    )
    return {
        "reply": safe,
        "backend": "local",
        "model": model,
        "cleaned_user": user_text,
        "sanitized": hit,
    }


def ask_glitch(
    user_text: str,
    *,
    voice: bool = False,
    backend: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    text = (user_text or "").strip()
    if not text:
        return {"reply": "", "backend": "none", "model": "", "cleaned_user": ""}

    if voice:
        local = _try_voice_local(text, history=history, max_tokens=max_tokens)
        if local:
            return local
        return _sanitized_nonstream_fallback(
            text, voice=True, history=history, fallback_from="local"
        )

    if _hermes_mode():
        hermes = _try_hermes(text, voice=voice, history=history)
        if hermes:
            return {
                "reply": hermes.get("reply") or "",
                "backend": "hermes",
                "model": hermes.get("model") or "",
                "cleaned_user": text,
                "sanitized": bool(hermes.get("sanitized")),
            }
        return _sanitized_nonstream_fallback(text, voice=voice, history=history)

    chosen, cleaned = choose_backend(text, voice=voice)
    use_backend = backend or chosen
    token_limit = _voice_token_limit(voice, max_tokens)
    xai_model = _xai_model_for(voice, model)
    try:
        if use_backend == "xai":
            reply = ask_xai(
                cleaned,
                voice=voice,
                model=xai_model,
                max_tokens=token_limit,
                history=history,
            )
            used_model = xai_model
        else:
            reply = ask_ollama(
                cleaned, voice=voice, max_tokens=token_limit, history=history
            )
            used_model = OLLAMA_MODEL
    except Exception as e:
        if use_backend == "xai":
            # Voice fast-path model may 404; retry configured XAI_MODEL once.
            if voice and xai_model != XAI_MODEL:
                log_line(f"[llm] voice model {xai_model} failed ({e}); retry {XAI_MODEL}")
                try:
                    reply = ask_xai(
                        cleaned,
                        voice=voice,
                        model=XAI_MODEL,
                        max_tokens=token_limit,
                        history=history,
                    )
                    return {
                        "reply": reply,
                        "backend": "xai",
                        "model": XAI_MODEL,
                        "cleaned_user": cleaned,
                    }
                except Exception as e2:
                    e = e2
            log_line(f"[llm] xAI failed ({e}); falling back to local Ollama")
            _log_external(
                {
                    "provider": "xai",
                    "model": xai_model,
                    "ok": False,
                    "error": str(e)[:500],
                    "fallback": "ollama",
                    "user_preview": cleaned[:200],
                    "voice": voice,
                }
            )
            reply = ask_ollama(
                cleaned, voice=voice, max_tokens=token_limit, history=history
            )
            use_backend = "local_fallback"
            used_model = OLLAMA_MODEL
        else:
            raise

    return {
        "reply": reply,
        "backend": use_backend,
        "model": used_model,
        "cleaned_user": cleaned,
    }


def iter_glitch_sentences(
    user_text: str,
    *,
    voice: bool = True,
    backend: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    meta: Optional[Dict[str, Any]] = None,
    history: Optional[List[Dict[str, str]]] = None,
) -> Iterator[str]:
    """Yield spoken sentences as the model produces them (streaming)."""
    text = (user_text or "").strip()
    info = meta if meta is not None else {}
    if not text:
        info.update({"reply": "", "backend": "none", "model": "", "cleaned_user": ""})
        return

    if voice:
        packed = _try_voice_local(
            text, history=history, max_tokens=max_tokens
        ) or _sanitized_nonstream_fallback(
            text, voice=True, history=history, fallback_from="local"
        )
        reply = (packed.get("reply") or "").strip()
        info.update(
            {
                "backend": packed.get("backend") or "failure",
                "model": packed.get("model") or "",
                "cleaned_user": text,
                "t_llm_start": time.time(),
                "t_first_token": time.time(),
                "reply": reply,
                "sanitized": bool(packed.get("sanitized")),
            }
        )
        buf = reply
        ready, buf = pop_complete_sentences(buf)
        for sent in ready:
            yield sent
        tail = buf.strip()
        if tail:
            yield tail
        elif not ready and reply:
            yield reply
        info["t_llm_done"] = time.time()
        return

    if _hermes_mode():
        hermes = _try_hermes(text, voice=voice, history=history)
        packed = hermes or _sanitized_nonstream_fallback(
            text, voice=voice, history=history
        )
        reply = (packed.get("reply") or "").strip()
        info.update(
            {
                "backend": packed.get("backend") or "failure",
                "model": packed.get("model") or "",
                "cleaned_user": text,
                "t_llm_start": time.time(),
                "t_first_token": time.time(),
                "reply": reply,
                "sanitized": bool(packed.get("sanitized")),
            }
        )
        buf = reply
        ready, buf = pop_complete_sentences(buf)
        for sent in ready:
            yield sent
        tail = buf.strip()
        if tail:
            yield tail
        elif not ready and reply:
            yield reply
        info["t_llm_done"] = time.time()
        return

    chosen, cleaned = choose_backend(text, voice=voice)
    use_backend = backend or chosen
    token_limit = _voice_token_limit(voice, max_tokens)
    xai_model = _xai_model_for(voice, model)
    used_model = xai_model if use_backend == "xai" else OLLAMA_MODEL
    info.update({"backend": use_backend, "model": used_model, "cleaned_user": cleaned})

    def _token_source() -> Iterator[str]:
        nonlocal use_backend, used_model
        try:
            if use_backend == "xai":
                yield from _iter_xai_tokens(
                    cleaned,
                    None,
                    model=xai_model,
                    voice=voice,
                    max_tokens=token_limit,
                    history=history,
                )
            else:
                yield from _iter_ollama_tokens(
                    cleaned,
                    None,
                    voice=voice,
                    max_tokens=token_limit,
                    history=history,
                )
        except Exception as exc:
            if use_backend != "xai":
                raise
            if voice and xai_model != XAI_MODEL:
                log_line(f"[llm] voice stream {xai_model} failed ({exc}); retry {XAI_MODEL}")
                try:
                    used_model = XAI_MODEL
                    info["model"] = used_model
                    yield from _iter_xai_tokens(
                        cleaned,
                        None,
                        model=XAI_MODEL,
                        voice=voice,
                        max_tokens=token_limit,
                        history=history,
                    )
                    return
                except Exception as exc2:
                    exc = exc2
            log_line(f"[llm] xAI stream failed ({exc}); falling back to local Ollama")
            use_backend = "local_fallback"
            used_model = OLLAMA_MODEL
            info["backend"] = use_backend
            info["model"] = used_model
            yield from _iter_ollama_tokens(
                cleaned,
                None,
                voice=voice,
                max_tokens=token_limit,
                history=history,
            )

    buf = ""
    full: List[str] = []
    info["t_llm_start"] = time.time()
    for piece in _token_source():
        if "t_first_token" not in info:
            info["t_first_token"] = time.time()
        buf += piece
        ready, buf = pop_complete_sentences(buf)
        for sent in ready:
            full.append(sent)
            yield sent
    tail = buf.strip()
    if tail:
        full.append(tail)
        yield tail
    info["t_llm_done"] = time.time()
    info["reply"] = " ".join(full).strip()


def status_dict() -> Dict[str, Any]:
    return {
        "mode": GLITCH_LLM_MODE,
        "ollama_url": OLLAMA_URL,
        "ollama_model": OLLAMA_MODEL,
        "xai_enabled": XAI_ENABLED,
        "xai_configured": bool(XAI_API_KEY),
        "xai_model": XAI_MODEL,
        "xai_base_url": XAI_BASE_URL,
        "voice_fast_path": VOICE_FAST_PATH,
        "voice_llm_mode": VOICE_LLM_MODE,
        "voice_xai_model": VOICE_XAI_MODEL,
        "voice_local_model": voice_local_model(),
        "voice_local_think": voice_local_think(),
        "voice_local_timeout_s": voice_local_timeout_s(),
        "voice_max_tokens": VOICE_MAX_TOKENS,
        "voice_stream": VOICE_STREAM,
        "hermes_enabled": os.getenv("ANGUS_HERMES_ENABLED", "false").strip().lower()
        in ("1", "true", "yes", "on"),
        "hermes_url": os.getenv("ANGUS_HERMES_URL", "http://127.0.0.1:8642/v1"),
        "hermes_model": os.getenv("ANGUS_HERMES_MODEL", "angus-hermes"),
        "external_log": str(LOG_PATH),
        "timezone": TIMEZONE,
        "local_now": _now_local().isoformat(),
    }
