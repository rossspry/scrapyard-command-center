#!/usr/bin/env python3
"""
glitch_voice_hybrid.py

SCC Angus voice loop with wake word + cancel phrases + hybrid LLM.

States:
  IDLE   — only listens for wake word (does not chat)
  ACTIVE — listens for a command; cancel phrases abort without talking

Mic: ALSA USB (GLITCH_MIC_DEVICE), RTSP front-door (ANGUS_RTSP_URL), or both.
  ANGUS_AUDIO_SOURCE = alsa | rtsp | both   (default: alsa)
Wake (default / glitch.env): "Hey Angus" / "Angus" / "Hey Scrapyard"
Quiet: "nevermind", "cancel", "be quiet", "go to sleep", "quiet", "stop angus"
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import queue
import threading
import wave
from array import array
from collections import deque
from difflib import SequenceMatcher
import requests
from pathlib import Path
from typing import Iterator, List, Optional, Tuple


# Persistent Whisper model, loaded once at startup.
_WHISPER_MODEL_OBJ = None

_SYS_DIR = Path(__file__).resolve().parent
if str(_SYS_DIR) not in sys.path:
    sys.path.insert(0, str(_SYS_DIR))

from glitch_llm import (  # noqa: E402
    OLLAMA_MODEL,
    VOICE_FAST_PATH,
    VOICE_STREAM,
    ask_glitch,
    iter_glitch_sentences,
    log_line as log,
    spoken_clock_reply,
    status_dict,
)
from timeclock_voice import (  # noqa: E402
    handle_clock_in_utterance,
    handle_clock_out_utterance,
    handle_timeclock_status_utterance,
)
from angus_operator.router import handle_operator  # noqa: E402
from angus_operator.speech import correct, set_last_transcript  # noqa: E402
from angus_operator.tools import build as angus_build  # noqa: E402
from angus_operator.thread import history as thread_history  # noqa: E402
from angus_operator.thread import remember as remember_thread  # noqa: E402
from angus_operator.door_intent import (  # noqa: E402
    LOCK as DOOR_LOCK,
    NON_ACTION as DOOR_NON_ACTION,
    STATUS as DOOR_STATUS,
    UNLOCK as DOOR_UNLOCK,
    classify_front_door_utterance,
)

# -------------------------
# Audio / STT settings
# -------------------------

# Prefer stable ALSA name (CARD=Mic) so USB re-enumeration does not break capture.
MIC_DEVICE = os.getenv("GLITCH_MIC_DEVICE", "plughw:CARD=Mic,DEV=0")
SPEAKER_DEVICE = os.getenv("GLITCH_SPK_DEVICE", "plughw:CARD=PCH,DEV=0")
# Wake clips a bit longer so "Hey Glitch" fits; command clips longer
WAKE_RECORD_SECONDS = int(os.getenv("GLITCH_WAKE_RECORD_SECONDS", "4"))
COMMAND_RECORD_SECONDS = int(os.getenv("GLITCH_COMMAND_RECORD_SECONDS", "6"))


# Continuous-listening microphone / voice activity detection.
# The microphone stays open even while Whisper is processing.
MIC_SAMPLE_RATE = int(os.getenv("ANGUS_MIC_SAMPLE_RATE", "16000"))
# Dual-source mode always normalizes each producer to mono s16le.
MIC_CHANNELS = int(os.getenv("ANGUS_MIC_CHANNELS", "1"))
AUDIO_SOURCE = os.getenv("ANGUS_AUDIO_SOURCE", "alsa").strip().lower()
# Aliases: usb/mic → alsa, door/frontdoor → rtsp, dual/all → both
if AUDIO_SOURCE in ("usb", "mic", "local"):
    AUDIO_SOURCE = "alsa"
elif AUDIO_SOURCE in ("door", "frontdoor", "front_door", "camera"):
    AUDIO_SOURCE = "rtsp"
elif AUDIO_SOURCE in ("dual", "all", "alsa+rtsp", "rtsp+alsa"):
    AUDIO_SOURCE = "both"
RTSP_AUDIO_URL = os.getenv("ANGUS_RTSP_URL", "").strip()
MIC_CHUNK_MS = int(os.getenv("ANGUS_MIC_CHUNK_MS", "100"))
# Seconds to wait before restarting a dead capture process (RTSP drops, USB unplug).
MIC_RECONNECT_SEC = float(os.getenv("ANGUS_MIC_RECONNECT_SEC", "3"))
# When the USB card is missing, back off instead of retrying every 3s.
MIC_RECONNECT_MAX_SEC = float(os.getenv("ANGUS_MIC_RECONNECT_MAX", "60"))

# Minimum RMS level considered speech. USB desk vs FrontDoor RTSP are calibrated
# separately: door ambient sits ~280–400 and must not monopolize the pipeline.
VAD_MIN_RMS = int(os.getenv("ANGUS_VAD_MIN_RMS", "450"))
VAD_MIN_RMS_USB = int(os.getenv("ANGUS_VAD_MIN_RMS_USB") or VAD_MIN_RMS)
# Live door ambient sits ~50–900 RMS. Speech at the stoop is ~1200–3500.
VAD_MIN_RMS_DOOR = int(os.getenv("ANGUS_VAD_MIN_RMS_DOOR", "1400"))
MIC_HEALTH_EVERY_SEC = float(os.getenv("ANGUS_MIC_HEALTH_SEC", "10") or "10")
# Interactive first-audio budget for Chatterbox; CPU Chatterbox is skipped.
TTS_FIRST_TIMEOUT = float(os.getenv("ANGUS_TTS_FIRST_TIMEOUT", "4") or "4")

# Keep a little audio before speech so the beginning of "Hey Angus" isn't clipped.
VAD_PREROLL_SECONDS = float(os.getenv("ANGUS_VAD_PREROLL", "0.7"))

# End an utterance after this much silence.
VAD_SILENCE_SECONDS = float(os.getenv("ANGUS_VAD_SILENCE", "0.8"))

# Ignore extremely short noises.
VAD_MIN_SPEECH_SECONDS = float(os.getenv("ANGUS_VAD_MIN_SPEECH", "0.25"))

# Safety limit for a single utterance.
VAD_MAX_UTTERANCE_SECONDS = float(os.getenv("ANGUS_VAD_MAX_UTTERANCE", "15"))
# How long after Angus finishes speaking to accept follow-ups (no new wake).
# ANGUS_FOLLOWUP_SECONDS is the conversation window. GLITCH_ACTIVE_TIMEOUT is
# the older name and is only used if the new variable is unset.
FOLLOWUP_SECONDS = float(
    os.getenv("ANGUS_FOLLOWUP_SECONDS")
    or os.getenv("GLITCH_ACTIVE_TIMEOUT")
    or "10"
)
# After TTS, discard USB for this many ms (echo from analog speaker → USB mic).
# Stay in 300–500 ms. Do not wait for the room to go quiet — that ate follow-ups.
ECHO_GUARD_MS = int(os.getenv("ANGUS_ECHO_GUARD_MS", "400") or "400")
# Safety-net window: reject USB transcripts that match Angus's own last speech.
ECHO_SIM_WINDOW_S = float(os.getenv("ANGUS_ECHO_SIM_WINDOW", "4") or "4")
# Legacy alias kept for logs / door-hold comparisons.
ACTIVE_TIMEOUT_SECONDS = FOLLOWUP_SECONDS
# Soft ack after wake when no command yet in the same utterance
WAKE_ACK = os.getenv("GLITCH_WAKE_ACK", "Yes?").strip()
VOICE = os.getenv("GLITCH_TTS_VOICE", "en-AU-NatashaNeural")
ENABLE_TTS = os.getenv("GLITCH_ENABLE_TTS", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
TTS_CHUNKED = os.getenv("ANGUS_TTS_CHUNKED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
TTS_CHUNK_MIN_CHARS = int(os.getenv("ANGUS_TTS_CHUNK_MIN_CHARS", "20") or "20")
TTS_CHUNK_MAX_CHARS = int(os.getenv("ANGUS_TTS_CHUNK_MAX_CHARS", "180") or "180")
TTS_CHUNK_MAX_COUNT = int(os.getenv("ANGUS_TTS_CHUNK_MAX_COUNT", "6") or "6")
SECURITY_MODE_PATH = Path(
    os.getenv("SCC_SECURITY_MODE_PATH", "/srv/scc-ui/security_mode.json")
)


# Home Assistant device control
HA_URL = os.getenv("HA_URL", "http://127.0.0.1:8123").rstrip("/")
HA_TOKEN = os.getenv("HA_TOKEN", "").strip()

FRONT_DOOR_ENTITY = os.getenv(
    "ANGUS_FRONT_DOOR_ENTITY",
    "switch.hobk_switch_1",
).strip()
# Per-person door codes JSON: {"codes":{"1234":{"name":"Ross","allow":["unlock","lock"]}}}
DOOR_CODES_PATH = Path(
    os.getenv(
        "ANGUS_DOOR_CODES_PATH",
        str(Path.home() / ".config" / "scc" / "angus_door_codes.json"),
    )
)
# Seconds to wait for a spoken authorization code after unlock request
DOOR_AUTH_TIMEOUT = float(os.getenv("ANGUS_DOOR_AUTH_TIMEOUT", "45"))
# Require code for unlock unless ANGUS_DOOR_UNLOCK_REQUIRES_CODE=0 (temporary).
# Default is require-code so a missing env var stays locked down.
DOOR_UNLOCK_REQUIRES_CODE = os.getenv(
    "ANGUS_DOOR_UNLOCK_REQUIRES_CODE", "1"
).strip().lower() in ("1", "true", "yes", "on")
# Lock is free unless ANGUS_DOOR_LOCK_REQUIRES_CODE=1
DOOR_LOCK_REQUIRES_CODE = os.getenv(
    "ANGUS_DOOR_LOCK_REQUIRES_CODE", "0"
).strip().lower() in ("1", "true", "yes", "on")

# Pending door authorization challenge (set when unlock/lock needs a code).
_pending_door_auth: Optional[dict] = None

WHISPER_MODEL = os.getenv("GLITCH_WHISPER_MODEL", "base")
# Tiny model for wake detection = much less dead air between clips
WAKE_WHISPER_MODEL = os.getenv("GLITCH_WAKE_WHISPER_MODEL", "tiny")
WHISPER_CPP_BIN = os.getenv("GLITCH_WHISPER_CPP_BIN", "whisper-cli")
WHISPER_CPP_MODEL = os.getenv("GLITCH_WHISPER_CPP_MODEL", "")

RUNTIME_DIR = Path(os.getenv("GLITCH_RUNTIME_DIR", "/tmp/glitch_voice"))
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
STOP_FILE = RUNTIME_DIR / "stop"
LAST_USER_WAV = RUNTIME_DIR / "last_user.wav"
LAST_REPLY_MP3 = RUNTIME_DIR / "last_reply.mp3"

_DEFAULT_WAKE = (
    "hey angus,ok angus,okay angus,hi angus,hey scrapyard,angus,"
    "hey glitch,ok glitch,okay glitch,hi glitch,glitch"
)
_DEFAULT_CANCEL = (
    "nevermind,never mind,cancel,be quiet,go to sleep,shut up,"
    "stop angus,stop glitch,stop listening,quiet,false alarm,forget it,stand down,"
    "that's enough,thats enough,that's all,thats all,that is all,"
    "we're done,we are done,i'm done,im done"
)


def _parse_phrases(raw: str) -> List[str]:
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


WAKE_PHRASES = _parse_phrases(os.getenv("GLITCH_WAKE_PHRASES", _DEFAULT_WAKE))
# Match longer phrases first so "hey glitch" wins over bare "glitch"
WAKE_PHRASES = sorted(WAKE_PHRASES, key=len, reverse=True)
CANCEL_PHRASES = _parse_phrases(os.getenv("GLITCH_CANCEL_PHRASES", _DEFAULT_CANCEL))
CANCEL_PHRASES = sorted(CANCEL_PHRASES, key=len, reverse=True)


class ConversationSession:
    """Time-bounded follow-up window opened by a valid wake.

    The timer is meant to be extended *after* Angus finishes playback so TTS
    time does not eat the follow-up window.
    """

    IDLE = "IDLE"
    SPEAKING = "SPEAKING"
    PLAYBACK_COMPLETE = "PLAYBACK_COMPLETE"
    FOLLOWUP = "FOLLOWUP_LISTENING"

    def __init__(self, window_s: float = FOLLOWUP_SECONDS) -> None:
        self.window_s = float(window_s)
        self.open = False
        self.until = 0.0
        self.state = self.IDLE
        self.last_source = ""

    def is_open(self) -> bool:
        return self.open and time.monotonic() < self.until

    def in_followup(self) -> bool:
        return self.state == self.FOLLOWUP and self.is_open()

    def deadline(self) -> Optional[float]:
        if not self.open:
            return None
        return self.until

    def prefer_source(self) -> Optional[str]:
        """During follow-up, stay on the mic that heard the last turn."""
        if not self.in_followup():
            return None
        return self.last_source or "usb"

    def set_state(self, new: str, reason: str = "") -> None:
        old = self.state
        if old == new:
            return
        extra = f" reason={reason}" if reason else ""
        log(f"[state] {old} -> {new}{extra}")
        self.state = new

    def start(self, reason: str) -> None:
        was = self.open
        self.open = True
        self.until = time.monotonic() + self.window_s
        if not was:
            log(
                f"[conversation] opened reason={reason} "
                f"window={self.window_s:g}s"
            )
        else:
            log(
                f"[conversation] extended reason={reason} "
                f"window={self.window_s:g}s"
            )

    def extend(self, reason: str = "followup") -> None:
        if not self.open:
            self.start(reason)
            return
        self.until = time.monotonic() + self.window_s
        log(f"[conversation] extended reason={reason} window={self.window_s:g}s")

    def begin_speaking(self) -> None:
        self.set_state(self.SPEAKING, "tts")

    def mark_playback_complete(self, last_source: str = "") -> None:
        """TTS finished. Caller must flush USB echo, then enter_followup()."""
        if last_source:
            self.last_source = last_source
        log("[state] SPEAKING -> PLAYBACK_COMPLETE")
        self.state = self.PLAYBACK_COMPLETE
        self.open = True
        self.until = time.monotonic() + self.window_s
        log(
            f"[conversation] extended reason=after_playback "
            f"window={self.window_s:g}s prefer={self.last_source or 'usb'}"
        )

    def enter_followup(self, reason: str = "after_playback") -> None:
        self.set_state(self.FOLLOWUP, reason)

    def after_playback(self, last_source: str = "") -> None:
        """Playback finished: arm follow-up. Never return to IDLE here."""
        self.mark_playback_complete(last_source)
        self.enter_followup()

    def hold_for(self, seconds: float, reason: str) -> None:
        self.open = True
        self.until = time.monotonic() + float(seconds)
        log(f"[conversation] extended reason={reason} window={float(seconds):g}s")
        if self.state != self.SPEAKING:
            self.set_state(self.FOLLOWUP, reason)

    def expire_if_due(
        self, at_mono: Optional[float] = None, reason: str = "timeout"
    ) -> bool:
        """Close if `at_mono` (speech start) is past the window. True if expired."""
        if not self.open:
            return False
        when = time.monotonic() if at_mono is None else float(at_mono)
        if when < self.until:
            return False
        log(f"[conversation] expired reason={reason}")
        self.open = False
        self.until = 0.0
        self.set_state(self.IDLE, reason)
        return True

    def cancel(self) -> None:
        if self.open or self.state != self.IDLE:
            log("[conversation] cancelled")
        self.open = False
        self.until = 0.0
        self.set_state(self.IDLE, "cancel")

    def allows_at(self, speech_mono: float) -> bool:
        if self.expire_if_due(speech_mono, reason="speech_after_timeout"):
            return False
        return self.open


def _echo_norm(text: str) -> str:
    t = (text or "").lower().strip()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def echo_similarity(candidate: str, spoken: str) -> float:
    """0..1 SequenceMatcher ratio on punctuation-stripped text."""
    a = _echo_norm(candidate)
    b = _echo_norm(spoken)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def is_self_echo_text(candidate: str, spoken: str) -> Tuple[bool, float]:
    """True when candidate is substantially Angus's own preceding speech.

    Conservative on purpose: this is a safety net. PCM discard during SPEAKING
    is the primary echo fix. Genuine follow-ups like "What is the date today?"
    after a time reply must pass.
    """
    cand = _echo_norm(candidate)
    ref = _echo_norm(spoken)
    if not cand or not ref:
        return False, 0.0
    ratio = SequenceMatcher(None, cand, ref).ratio()
    shorter, longer = (cand, ref) if len(cand) <= len(ref) else (ref, cand)
    prefix = (
        SequenceMatcher(None, shorter, longer[: len(shorter)]).ratio()
        if shorter
        else 0.0
    )
    ct, rt = cand.split(), ref.split()
    lead = 0
    for x, y in zip(ct, rt):
        if x == y:
            lead += 1
        else:
            break
    if ratio >= 0.72:
        return True, ratio
    if prefix >= 0.82 and len(shorter) >= 24:
        return True, max(ratio, prefix)
    # Garbled start of the same sentence (Whisper of speaker bleed) is
    # typically shorter than the original TTS line.
    if (
        lead >= 3
        and prefix >= 0.55
        and len(ct) <= max(3, int(0.75 * len(rt)))
    ):
        return True, max(ratio, prefix)
    return False, ratio


class SpokenEchoFilter:
    """Remember recent TTS so USB self-echo can be rejected after playback."""

    def __init__(self) -> None:
        self.refs: List[str] = []
        self.last_play_mono = 0.0

    def remember(self, text: str, *, replace: bool = False) -> None:
        t = (text or "").strip()
        if replace:
            self.refs = []
        if not t:
            return
        self.refs.append(t)
        if len(self.refs) > 12:
            self.refs = self.refs[-12:]

    def mark_played(self) -> None:
        self.last_play_mono = time.monotonic()

    def check(self, candidate: str) -> Tuple[bool, float]:
        if not candidate or not self.refs:
            return False, 0.0
        if self.last_play_mono <= 0:
            return False, 0.0
        if time.monotonic() - self.last_play_mono > ECHO_SIM_WINDOW_S:
            return False, 0.0
        best = 0.0
        hit = False
        for ref in self.refs:
            is_hit, score = is_self_echo_text(candidate, ref)
            if score > best:
                best = score
            hit = hit or is_hit
        return hit, best


_spoken_echo = SpokenEchoFilter()
_ACTIVE_MIC: Optional["ContinuousMic"] = None


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def ensure_stop_removed() -> None:
    if STOP_FILE.exists():
        STOP_FILE.unlink()
        log("[glitch] cleared stop file")


def should_stop() -> bool:
    return STOP_FILE.exists()


def normalize_speech(text: str) -> str:
    t = text.lower().strip()
    t = t.replace("'", "'").replace("'", "'")
    t = t.replace("hey, glitch", "hey glitch")
    t = t.replace("a glitch", "glitch")
    # common STT mishears — Glitch
    t = t.replace("hey glitch.", "hey glitch")
    t = t.replace("hey rich", "hey glitch")
    t = t.replace("hey glitch,", "hey glitch")
    t = t.replace("hey which", "hey glitch")
    t = t.replace("hey bitch", "hey glitch")  # unfortunate but common STT error
    t = t.replace("hey glitch", "hey glitch")
    # common STT mishears — Angus (AIN-gus / AN-gus)
    for bad, good in (
        ("hey, angus", "hey angus"),
        ("hey angus.", "hey angus"),
        ("hey angus,", "hey angus"),
        ("hey anguess", "hey angus"),
        ("hey angas", "hey angus"),
        ("hey hangus", "hey angus"),
        ("hey and just", "hey angus"),
        ("hey and us", "hey angus"),
        ("hey angles", "hey angus"),
        ("hey angers", "hey angus"),
        ("a angus", "angus"),
        ("hey angst", "hey angus"),
    ):
        t = t.replace(bad, good)
    t = re.sub(r"[^a-z0-9'\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    from angus_light_intent import expand_stt_command

    return expand_stt_command(t)


def is_garbage_transcript(text: str) -> bool:
    if not text or not text.strip():
        return True
    raw = text.strip()
    ascii_letters = sum(1 for c in raw if "a" <= c.lower() <= "z")
    if ascii_letters < 3 and len(raw) > 0:
        return True
    if re.fullmatch(r"[\d\s.%]+", raw):
        return True
    if len(normalize_speech(raw)) < 2:
        return True
    # dots-only / punctuation garbage
    if re.fullmatch(r"[\s.]+", raw):
        return True
    return False


def find_phrase(normalized: str, phrases: List[str]) -> Optional[str]:
    for p in phrases:
        if not p:
            continue
        # word-boundary-ish: phrase as substring with care for short words
        if p in ("glitch", "angus"):
            if re.search(rf"\b{re.escape(p)}\b", normalized):
                return p
        elif p in normalized:
            return p
    return None


def strip_wake(normalized: str, wake: str) -> str:
    if wake in ("glitch", "angus"):
        out = re.sub(rf"\b{re.escape(wake)}\b", " ", normalized, count=1)
    else:
        out = normalized.replace(wake, " ", 1)
    out = re.sub(r"\s+", " ", out).strip()
    out = re.sub(r"^(please|um|uh|so)\s+", "", out).strip()
    return out


def _rms16(data: bytes) -> int:
    """Return RMS amplitude for signed 16-bit PCM audio."""
    stats = pcm_stats(data)
    return int(stats["rms"])


def pcm_stats(pcm_data: bytes) -> dict:
    """Peak/RMS/duration for a s16le PCM buffer (mono or MIC_CHANNELS)."""
    nbytes = len(pcm_data) - (len(pcm_data) % 2)
    if nbytes <= 0:
        return {"bytes": 0, "duration": 0.0, "rms": 0, "peak": 0, "samples": 0}
    samples = array("h")
    samples.frombytes(pcm_data[:nbytes])
    n = len(samples)
    if n <= 0:
        return {"bytes": nbytes, "duration": 0.0, "rms": 0, "peak": 0, "samples": 0}
    total = 0
    peak = 0
    for sample in samples:
        mag = sample if sample >= 0 else -sample
        if mag > peak:
            peak = mag
        total += sample * sample
    ch = max(1, MIC_CHANNELS)
    duration = n / float(MIC_SAMPLE_RATE * ch)
    return {
        "bytes": nbytes,
        "duration": duration,
        "rms": int((total / n) ** 0.5),
        "peak": int(peak),
        "samples": n,
    }


def stt_skip_reason(source: str, stats: dict) -> Optional[str]:
    """Do not send door-noise / tiny clips to Whisper. USB speech is kept."""
    duration = float(stats.get("duration") or 0)
    rms = int(stats.get("rms") or 0)
    peak = int(stats.get("peak") or 0)
    if duration < 0.28:
        return "too_short"
    if peak < 700 and rms < 180:
        return "too_quiet"
    # Door: skip wind/slams (loud peak, quiet average) and weak hiss.
    # Previous peak<8000 OR rms<1800 threw away real "Hey Angus" at the stoop.
    if source == "door":
        if rms < 1000 or peak < 4000:
            return "door_noise"
        if rms and peak / rms >= 10:
            return "door_noise"
    return None


def substantial_speech(source: str, stats: dict) -> bool:
    """True when VAD/PCM look like real speech, not residual echo."""
    duration = float(stats.get("duration") or 0)
    peak = int(stats.get("peak") or 0)
    rms = int(stats.get("rms") or 0)
    if source == "door":
        return duration >= 0.8 and peak >= 5000 and rms >= 1200
    if source != "usb":
        return False
    return duration >= 0.6 and peak >= 2000


def write_pcm_wav(out_path: Path, pcm_data: bytes) -> None:
    """Write captured raw PCM to a WAV file Whisper can consume."""
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(MIC_CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(MIC_SAMPLE_RATE)
        wf.writeframes(pcm_data)


def _alsa_capture_cmd() -> List[str]:
    return [
        "arecord",
        "-D",
        MIC_DEVICE,
        "-t",
        "raw",
        "-f",
        "S16_LE",
        "-r",
        str(MIC_SAMPLE_RATE),
        "-c",
        str(MIC_CHANNELS),
    ]


def _rtsp_capture_cmd() -> List[str]:
    if not RTSP_AUDIO_URL:
        raise RuntimeError(
            "RTSP audio source enabled but ANGUS_RTSP_URL is empty"
        )
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-i",
        RTSP_AUDIO_URL,
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        str(MIC_CHANNELS),
        "-ar",
        str(MIC_SAMPLE_RATE),
        "-af",
        "highpass=f=180,lowpass=f=6000,volume=10dB,aresample=16000",
        "-f",
        "s16le",
        "pipe:1",
    ]


class AudioSourceReader:
    """One capture process (ALSA or RTSP) with auto-reconnect into a chunk queue."""

    def __init__(self, name: str, cmd: List[str], chunk_bytes: int) -> None:
        self.name = name
        self.cmd = cmd
        self.chunk_bytes = chunk_bytes
        self.audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=1800)
        self.proc: Optional[subprocess.Popen] = None
        self.thread: Optional[threading.Thread] = None
        self.running = False
        self.bytes_in = 0
        self.chunks_in = 0
        self.last_chunk_mono = 0.0
        self.drops = 0
        # When True, PCM is still read from the capture pipe (so it cannot
        # block) but is not queued for VAD/STT. Used while Angus is speaking.
        self.suppress_to_vad = False
        self.suppressed_chunks = 0

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(
            target=self._run_loop,
            name=f"angus-mic-{self.name}",
            daemon=True,
        )
        self.thread.start()

    def _alsa_card_missing(self) -> bool:
        """True when this is the USB reader and CARD=Name is not in /proc/asound/cards."""
        if self.name != "usb":
            return False
        card = None
        match = re.search(r"CARD=([^,]+)", MIC_DEVICE)
        if match:
            card = match.group(1)
        try:
            text = Path("/proc/asound/cards").read_text(
                encoding="utf-8", errors="ignore"
            )
        except OSError:
            return False
        if card and f"[{card}" in text:
            return False
        if "Amazon USB Streaming Mic" in text:
            return False
        return True

    def alive(self) -> bool:
        return bool(self.thread and self.thread.is_alive() and self.running)

    def _run_loop(self) -> None:
        backoff = MIC_RECONNECT_SEC
        missing_logged = False
        try:
            while self.running:
                if self._alsa_card_missing():
                    if not missing_logged:
                        log(
                            f"[mic:{self.name}] capture device not present "
                            f"({MIC_DEVICE}); backing off retries"
                        )
                        missing_logged = True
                    time.sleep(backoff)
                    backoff = min(backoff * 2, MIC_RECONNECT_MAX_SEC)
                    continue
                if missing_logged:
                    log(f"[mic:{self.name}] capture device is back; resuming")
                    missing_logged = False
                    backoff = MIC_RECONNECT_SEC
                try:
                    self._capture_once()
                except Exception as exc:
                    if self.running:
                        log(f"[mic:{self.name}] capture error: {exc}")
                if not self.running:
                    break
                err_hint = ""
                # Missing-card failures used to retry every 3s and flood the journal.
                if self._alsa_card_missing():
                    log(
                        f"[mic:{self.name}] device gone after capture stop; "
                        f"retry in {backoff:g}s"
                    )
                    time.sleep(backoff)
                    backoff = min(backoff * 2, MIC_RECONNECT_MAX_SEC)
                    missing_logged = True
                    continue
                log(
                    f"[mic:{self.name}] reconnecting in {MIC_RECONNECT_SEC:g}s{err_hint}"
                )
                backoff = MIC_RECONNECT_SEC
                time.sleep(MIC_RECONNECT_SEC)
        except Exception as exc:
            log(f"[mic:{self.name}] reader thread crashed: {type(exc).__name__}: {exc}")
        finally:
            log(
                f"[mic:{self.name}] reader thread exit running={int(self.running)} "
                f"chunks_in={self.chunks_in}"
            )

    def _capture_once(self) -> None:
        log(
            f"[mic:{self.name}] starting "
            f"{MIC_SAMPLE_RATE}Hz/{MIC_CHANNELS}ch cmd0={self.cmd[0]}"
        )
        self.proc = subprocess.Popen(
            self.cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        assert self.proc.stdout is not None
        pending = bytearray()

        while self.running:
            data = self.proc.stdout.read(4096)
            if not data:
                break
            pending.extend(data)
            while len(pending) >= self.chunk_bytes:
                chunk = bytes(pending[: self.chunk_bytes])
                del pending[: self.chunk_bytes]
                self.bytes_in += len(chunk)
                self.chunks_in += 1
                self.last_chunk_mono = time.monotonic()
                if self.suppress_to_vad:
                    self.suppressed_chunks += 1
                    continue
                try:
                    self.audio_queue.put(chunk, timeout=0.2)
                except queue.Full:
                    self.drops += 1
                    try:
                        self.audio_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self.audio_queue.put_nowait(chunk)
                    except queue.Full:
                        pass

        err = ""
        if self.proc.stderr:
            try:
                err = self.proc.stderr.read().decode(
                    "utf-8", errors="ignore"
                ).strip()
            except Exception:
                pass
        if self.running:
            log(f"[mic:{self.name}] capture stopped: {err or 'eof'}")
        self._kill_proc()

    def _kill_proc(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None

    def clear(self) -> None:
        while True:
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    def stop(self) -> None:
        self.running = False
        self._kill_proc()


class ContinuousMic:
    """Continuous multi-source mic (ALSA USB, RTSP door, or both).

    In dual mode each source keeps its own queue. VAD locks onto whichever
    source first exceeds the speech threshold for that utterance so USB and
    door audio are never interleaved into one garble stream.
    """

    def __init__(self) -> None:
        self.chunk_frames = int(MIC_SAMPLE_RATE * MIC_CHUNK_MS / 1000)
        self.chunk_bytes = self.chunk_frames * MIC_CHANNELS * 2
        self.sources: List[AudioSourceReader] = []
        self.running = False
        self.last_speech_mono = 0.0
        self.last_source = ""
        self.last_speech_chunks = 0
        self.last_utt_chunks = 0
        self._health_mono = 0.0
        self._echo_event = threading.Event()
        self._echo_thread: Optional[threading.Thread] = None
        self._echo_dropped = 0

    def start(self) -> None:
        want_alsa = AUDIO_SOURCE in ("alsa", "both")
        want_rtsp = AUDIO_SOURCE in ("rtsp", "both")
        if not want_alsa and not want_rtsp:
            raise RuntimeError(
                f"Unknown ANGUS_AUDIO_SOURCE={AUDIO_SOURCE!r} "
                "(use alsa, rtsp, or both)"
            )
        if want_alsa:
            self.sources.append(
                AudioSourceReader(
                    "usb", _alsa_capture_cmd(), self.chunk_bytes
                )
            )
            log(
                f"[mic] ALSA USB enabled device={MIC_DEVICE} "
                f"{MIC_SAMPLE_RATE}Hz/{MIC_CHANNELS}ch"
            )
        if want_rtsp:
            self.sources.append(
                AudioSourceReader(
                    "door", _rtsp_capture_cmd(), self.chunk_bytes
                )
            )
            log(
                f"[mic] RTSP front-door enabled "
                f"{MIC_SAMPLE_RATE}Hz/{MIC_CHANNELS}ch"
            )
        self.running = True
        for src in self.sources:
            src.start()
        self.log_health(force=True)

    def clear(self) -> None:
        """Discard queued audio, mainly Angus hearing his own TTS."""
        for src in self.sources:
            src.clear()

    def _src(self, name: str) -> Optional[AudioSourceReader]:
        for src in self.sources:
            if src.name == name:
                return src
        return None

    def log_health(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and self._health_mono and now - self._health_mono < MIC_HEALTH_EVERY_SEC:
            return
        self._health_mono = now
        parts = []
        for src in self.sources:
            lag = (
                now - src.last_chunk_mono if src.last_chunk_mono else -1.0
            )
            parts.append(
                f"{src.name} chunks={src.chunks_in} bytes={src.bytes_in} "
                f"q={src.audio_queue.qsize()} drops={src.drops} "
                f"alive={int(src.alive())} lag={lag:.2f}s"
            )
            if src.name == "usb" and src.running and lag > 2.5:
                log(
                    f"[mic:usb] stale capture lag={lag:.2f}s "
                    f"alive={int(src.alive())} — USB frames not arriving"
                )
        log("[mic:health] " + " ".join(parts))

    def start_echo_suppress(self) -> None:
        """While SPEAKING: keep arecord alive, read USB, do not feed VAD."""
        usb = self._src("usb")
        if usb is not None:
            usb.suppress_to_vad = True
        if self._echo_event.is_set() and self._echo_thread and self._echo_thread.is_alive():
            return
        if usb is not None:
            usb.suppressed_chunks = 0
        self._echo_dropped = 0
        self._echo_event.set()
        self._echo_thread = threading.Thread(
            target=self._echo_discard_loop,
            name="angus-echo-discard",
            daemon=True,
        )
        self._echo_thread.start()

    def _echo_discard_loop(self) -> None:
        last_log = 0
        while self._echo_event.is_set() and self.running:
            usb = self._src("usb")
            if usb is not None:
                while True:
                    try:
                        usb.audio_queue.get_nowait()
                        self._echo_dropped += 1
                    except queue.Empty:
                        break
            total = self._echo_dropped
            if usb is not None:
                total += int(getattr(usb, "suppressed_chunks", 0) or 0)
            if total - last_log >= 25:
                log(f"[echo] usb suppressed state=speaking chunks={total}")
                last_log = total
            time.sleep(0.02)
        usb = self._src("usb")
        total = self._echo_dropped
        if usb is not None:
            total += int(getattr(usb, "suppressed_chunks", 0) or 0)
        if total:
            log(f"[echo] usb suppressed state=speaking chunks={total}")

    def _flush_usb_queue(self) -> int:
        usb = self._src("usb")
        if usb is None:
            return 0
        n = 0
        while True:
            try:
                usb.audio_queue.get_nowait()
                n += 1
            except queue.Empty:
                break
        return n

    def rearm_after_playback(self) -> None:
        """Playback done: flush every USB queue entry, short echo guard, then listen.

        Do not wait for the room to go quiet — that previously ate real
        follow-up speech. Capture processes stay up. Door backlog is cleared
        so it cannot jump the queue.
        """
        usb = self._src("usb")
        door = self._src("door")
        if usb is not None:
            usb.suppress_to_vad = True
        if door:
            door.clear()
        flushed = self._flush_usb_queue()
        log(f"[echo] playback flush chunks={flushed}")
        guard_ms = max(0, int(ECHO_GUARD_MS))
        log(f"[echo] postplay guard ms={guard_ms}")
        t_end = time.monotonic() + (guard_ms / 1000.0)
        extra = 0
        while time.monotonic() < t_end:
            extra += self._flush_usb_queue()
            time.sleep(0.02)
        extra += self._flush_usb_queue()
        self._echo_event.clear()
        if self._echo_thread is not None:
            self._echo_thread.join(timeout=0.4)
            self._echo_thread = None
        if usb is not None:
            usb.suppress_to_vad = False
            extra += self._flush_usb_queue()
        if extra:
            log(f"[echo] postplay guard discarded={extra}")
        _spoken_echo.mark_played()

    def stop(self) -> None:
        self._echo_event.clear()
        self.running = False
        if self._echo_thread is not None:
            self._echo_thread.join(timeout=0.4)
            self._echo_thread = None
        for src in self.sources:
            src.stop()

    def next_utterance(
        self,
        *,
        deadline_mono: Optional[float] = None,
        prefer: Optional[str] = None,
    ) -> bytes:
        """Wait for speech. USB always has priority over door.

        Capture processes are not restarted. Door never holds the loop while
        USB has speech-level audio.
        """
        chunk_seconds = MIC_CHUNK_MS / 1000.0
        preroll_chunks = max(1, int(VAD_PREROLL_SECONDS / chunk_seconds))
        silence_chunks_needed = max(
            1, int(VAD_SILENCE_SECONDS / chunk_seconds)
        )
        min_speech_chunks = max(
            1, int(VAD_MIN_SPEECH_SECONDS / chunk_seconds)
        )
        max_chunks = max(1, int(VAD_MAX_UTTERANCE_SECONDS / chunk_seconds))
        ignored_other = False
        usb = self._src("usb")
        door = self._src("door")

        prerolls = {
            s.name: deque(maxlen=preroll_chunks) for s in self.sources
        }
        speaking = False
        active_name: Optional[str] = None
        utterance: List[bytes] = []
        speech_chunks = 0
        silent_chunks = 0
        done = False

        def _begin(name: str, data: bytes, level: int, why: str) -> None:
            nonlocal speaking, active_name, utterance, speech_chunks, silent_chunks
            speaking = True
            active_name = name
            utterance = list(prerolls[name])
            if not utterance or utterance[-1] is not data:
                utterance.append(data)
            speech_chunks = 1
            silent_chunks = 0
            self.last_speech_mono = time.monotonic()
            self.last_source = name
            log(f"[vad] speech start source={name} rms={level} {why}")

        def _take(src: Optional[AudioSourceReader]) -> Optional[bytes]:
            if src is None:
                return None
            try:
                return src.audio_queue.get_nowait()
            except queue.Empty:
                return None

        while self.running and not should_stop() and not done:
            self.log_health()
            if deadline_mono is not None and time.monotonic() >= deadline_mono:
                if speaking:
                    log(
                        "[followup] terminated reason=timeout_during_vad "
                        f"source={active_name or prefer or '-'} speaking=1"
                    )
                return b""

            usb_data = _take(usb)
            door_data = _take(door)
            if usb_data is None and door_data is None:
                time.sleep(0.02)
                continue

            if usb_data is not None:
                prerolls["usb"].append(usb_data)
                usb_level = _rms16(usb_data)
                usb_hot = usb_level >= VAD_MIN_RMS_USB
                if usb_hot and active_name == "door":
                    log(
                        f"[vad] usb barge-in rms={usb_level} "
                        "(drop door utterance)"
                    )
                    _begin("usb", usb_data, usb_level, "barge-in")
                elif usb_hot and not speaking:
                    _begin("usb", usb_data, usb_level, "priority=usb")
                elif active_name == "usb":
                    utterance.append(usb_data)
                    if usb_hot:
                        speech_chunks += 1
                        silent_chunks = 0
                    else:
                        silent_chunks += 1

            if door_data is not None:
                prerolls["door"].append(door_data)
                door_level = _rms16(door_data)
                door_allowed = prefer in (None, "door") and active_name != "usb"
                if not door_allowed:
                    if (
                        prefer == "usb"
                        and door_level >= VAD_MIN_RMS_DOOR
                        and not ignored_other
                    ):
                        log(
                            "[followup] ignored source=door "
                            f"reason=prefer=usb rms={door_level}"
                        )
                        ignored_other = True
                elif active_name == "door":
                    utterance.append(door_data)
                    if door_level >= VAD_MIN_RMS_DOOR:
                        speech_chunks += 1
                        silent_chunks = 0
                    else:
                        silent_chunks += 1
                elif not speaking and door_level >= VAD_MIN_RMS_DOOR:
                    _begin("door", door_data, door_level, "usb-quiet")

            if not speaking:
                continue
            if len(utterance) >= max_chunks:
                log(f"[vad] maximum utterance length source={active_name}")
                done = True
            elif (
                speech_chunks >= min_speech_chunks
                and silent_chunks >= silence_chunks_needed
            ):
                done = True

        if not speaking or speech_chunks < min_speech_chunks:
            return b""

        pcm = b"".join(utterance)
        self.last_speech_chunks = speech_chunks
        self.last_utt_chunks = len(utterance)
        log(
            f"[vad] speech end source={active_name} "
            f"chunks={len(utterance)} speech_chunks={speech_chunks}"
        )
        return pcm


def get_persistent_whisper():
    """Load Whisper once and keep it resident on the GPU."""
    global _WHISPER_MODEL_OBJ

    if _WHISPER_MODEL_OBJ is not None:
        return _WHISPER_MODEL_OBJ

    import whisper
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"

    model_name = os.getenv(
        "ANGUS_WHISPER_MODEL",
        "small.en",
    ).strip()

    log(f"[stt] loading persistent Whisper model={model_name} device={device}")

    _WHISPER_MODEL_OBJ = whisper.load_model(
        model_name,
        device=device,
    )

    log(f"[stt] persistent Whisper ready model={model_name} device={device}")

    return _WHISPER_MODEL_OBJ


def transcribe_persistent_whisper(
    wav_path: Path, *, no_speech_threshold: Optional[float] = None
) -> Optional[str]:
    """Transcribe using the already-loaded Whisper model."""
    model = get_persistent_whisper()

    import torch

    kwargs = {
        "language": "en",
        "fp16": bool(torch.cuda.is_available()),
        "temperature": 0,
        "condition_on_previous_text": False,
    }
    if no_speech_threshold is not None:
        kwargs["no_speech_threshold"] = float(no_speech_threshold)

    result = model.transcribe(str(wav_path), **kwargs)

    text = (result.get("text") or "").strip()
    return text


def transcribe_whisper_cpp(wav_path: Path) -> Optional[str]:
    if not have(WHISPER_CPP_BIN) or not WHISPER_CPP_MODEL:
        return None
    txt_path = wav_path.with_suffix(".txt")
    cmd = [
        WHISPER_CPP_BIN,
        "-m",
        WHISPER_CPP_MODEL,
        "-f",
        str(wav_path),
        "-otxt",
        "-of",
        str(wav_path.with_suffix("")),
    ]
    log("[stt] using whisper.cpp")
    cp = run(cmd, check=False)
    if cp.returncode != 0:
        log(f"[stt] whisper.cpp failed: {cp.stderr.strip() or cp.stdout.strip()}")
        return None
    if txt_path.exists():
        return txt_path.read_text(encoding="utf-8", errors="ignore").strip()
    return None


def transcribe_faster_whisper(wav_path: Path) -> Optional[str]:
    try:
        from faster_whisper import WhisperModel
    except Exception:
        return None

    log("[stt] using faster-whisper")
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(wav_path), beam_size=5)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return text or None


def transcribe_whisper_cli(wav_path: Path, model_name: Optional[str] = None) -> Optional[str]:
    if not have("whisper"):
        return None
    model_name = model_name or WHISPER_MODEL
    out_dir = wav_path.parent
    log(f"[stt] whisper CLI model={model_name}")
    cmd = [
        "whisper",
        str(wav_path),
        "--model",
        model_name,
        "--language",
        "en",
        "--output_format",
        "txt",
        "--output_dir",
        str(out_dir),
        "--verbose",
        "False",
    ]
    cp = run(cmd, check=False)
    if cp.returncode != 0:
        log(f"[stt] whisper CLI failed: {cp.stderr.strip() or cp.stdout.strip()}")
        return None
    txt_path = out_dir / f"{wav_path.stem}.txt"
    if txt_path.exists():
        return txt_path.read_text(encoding="utf-8", errors="ignore").strip()
    return None


def transcribe(
    wav_path: Path,
    for_wake: bool = False,
    *,
    retry_blank: bool = False,
) -> str:
    """Return transcript using persistent GPU Whisper.

    Never launches /usr/local/bin/whisper while the resident model is loaded:
    that would allocate a second CUDA copy and OOM the 3060.
    """
    t0 = time.time()
    try:
        text = transcribe_persistent_whisper(wav_path)
        if (not text) and retry_blank:
            log("[stt] empty_transcript retry no_speech_threshold=0.2")
            text = transcribe_persistent_whisper(
                wav_path, no_speech_threshold=0.2
            )
        log(f"[stt] time={time.time() - t0:.2f}s persistent whisper -> {text}")
        return text or ""
    except Exception as exc:
        log(
            f"[stt] persistent whisper failed: {type(exc).__name__}: {exc}"
        )

    if _WHISPER_MODEL_OBJ is not None:
        log(
            "[stt] skipping Whisper CLI / extra GPU models; "
            "persistent Whisper is already loaded"
        )
        return ""

    log("[stt] persistent model not loaded; CPU fallbacks only (no whisper CLI)")
    for fn in (transcribe_whisper_cpp, transcribe_faster_whisper):
        try:
            text = fn(wav_path)
            if text is not None:
                log(f"[stt] time={time.time() - t0:.2f}s {fn.__name__} -> {text}")
                return text
        except Exception as exc:
            log(f"[stt] {fn.__name__} fallback failed: {exc}")

    return ""

_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])(?:["\')\]]*)\s+')


def split_speech_chunks(
    text: str,
    *,
    min_chars: Optional[int] = None,
    max_chars: Optional[int] = None,
    max_count: Optional[int] = None,
) -> List[str]:
    """Split spoken text into a few sentence-sized TTS chunks.

    One short sentence stays a single chunk. Tiny fragments are merged.
    """
    raw = (text or "").strip()
    if not raw:
        return []
    min_n = TTS_CHUNK_MIN_CHARS if min_chars is None else min_chars
    max_n = TTS_CHUNK_MAX_CHARS if max_chars is None else max_chars
    max_c = TTS_CHUNK_MAX_COUNT if max_count is None else max_count
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(raw) if p.strip()]
    if len(parts) <= 1:
        return [raw]

    chunks: List[str] = []
    buf = ""
    for part in parts:
        if not buf:
            buf = part
            continue
        # Only glue a fragment that is too short to speak on its own.
        if len(buf) < min_n:
            buf = f"{buf} {part}".strip()
            continue
        chunks.append(buf)
        buf = part
    if buf:
        chunks.append(buf)

    # Merge a leftover fragment into the previous chunk.
    if len(chunks) >= 2 and len(chunks[-1]) < min_n:
        chunks[-2] = f"{chunks[-2]} {chunks[-1]}".strip()
        chunks.pop()

    if max_c > 0 and len(chunks) > max_c:
        head = chunks[: max_c - 1]
        tail = " ".join(chunks[max_c - 1 :]).strip()
        chunks = head + ([tail] if tail else [])
    return chunks or [raw]


def log_command_latency(timing: dict) -> None:
    """Write one structured [latency] line covering the whole command."""
    t_eos = float(timing.get("t_eos") or 0.0)
    t_first = float(timing.get("t_first_audio") or 0.0)
    ttfa = (t_first - t_eos) if t_eos and t_first else None
    total = None
    if t_eos:
        total = time.time() - t_eos
    parts = [
        f"stt={float(timing.get('stt_s') or 0):.2f}s",
        f"route={float(timing.get('route_s') or 0):.2f}s",
        f"route_via={timing.get('route_via') or '-'}",
        f"llm={float(timing.get('llm_s') or 0):.2f}s",
        f"llm_ttft={float(timing.get('llm_ttft') or 0):.2f}s",
        f"tts_first={float(timing.get('tts_first_chunk_s') or 0):.2f}s",
        f"tts_total={float(timing.get('tts_total_s') or 0):.2f}s",
        f"play={float(timing.get('play_s') or 0):.2f}s",
        f"eos_to_first_audio={ttfa:.2f}s" if ttfa is not None else "eos_to_first_audio=-",
        f"total={total:.2f}s" if total is not None else "total=-",
        f"backend={timing.get('backend') or '-'}",
        f"model={timing.get('model') or '-'}",
        f"sanitized={int(bool(timing.get('sanitized')))}",
        f"chunks={timing.get('tts_chunks') or 0}",
        f"engine={timing.get('tts_engine') or '-'}",
    ]
    log("[latency] " + " ".join(parts))


def tts_to_mp3(text: str, out_path: Path) -> None:
    """Legacy edge-tts helper (kept for direct callers / fallback tooling)."""
    if not have("edge-tts"):
        raise RuntimeError("edge-tts is not installed")
    cmd = [
        "edge-tts",
        "--voice",
        VOICE,
        "--text",
        text,
        "--write-media",
        str(out_path),
    ]
    log(f"[tts] voice={VOICE}")
    cp = run(cmd, check=False)
    if cp.returncode != 0:
        raise RuntimeError(f"edge-tts failed: {cp.stderr.strip() or cp.stdout.strip()}")


def play_audio(path: Path) -> None:
    # Prefer PipeWire when available (shared speaker path with announcements).
    if have("pw-play"):
        env = os.environ.copy()
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        cmd = ["pw-play", str(path)]
        log("[playback] using pw-play")
        cp = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
        if cp.returncode == 0:
            return
        log(f"[playback] pw-play failed: {(cp.stderr or cp.stdout or '')[:120]}")

    if have("ffplay"):
        cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", str(path)]
        log("[playback] using ffplay")
        cp = run(cmd, check=False)
        if cp.returncode == 0:
            return

    if have("mpg123") and path.suffix.lower() == ".mp3":
        cmd = ["mpg123", "-q", str(path)]
        log("[playback] using mpg123")
        cp = run(cmd, check=False)
        if cp.returncode == 0:
            return

    if path.suffix.lower() == ".wav":
        cmd = ["aplay", "-D", SPEAKER_DEVICE, str(path)]
        log(f"[playback] using aplay -> {SPEAKER_DEVICE}")
        cp = run(cmd, check=False)
        if cp.returncode == 0:
            return

    raise RuntimeError(
        "No working playback command found. Install ffplay, mpg123, pw-play, or aplay."
    )


_cb_health_cache: Tuple[float, dict] = (0.0, {})


def _interactive_tts_kwargs() -> dict:
    """Skip CPU Chatterbox so interactive replies start in ~2s via Piper."""
    from angus_tts import engine_chain, health_chatterbox

    global _cb_health_cache
    now = time.time()
    if now - _cb_health_cache[0] > 8:
        try:
            health = health_chatterbox(timeout=0.4)
        except Exception:
            health = {"device": "unknown", "ok": False}
        _cb_health_cache = (now, health)
    else:
        health = _cb_health_cache[1]
    chain = engine_chain()
    device = str((health or {}).get("device") or "unknown").lower()
    if device != "cuda":
        log(
            f"[tts] chatterbox device={device} — piper for interactive "
            f"(first-audio target 2-3s)"
        )
        chain = [e for e in chain if e != "chatterbox"]
        return {"engines": chain, "timeout": 12}
    return {
        "engines": chain,
        "chatterbox_timeout": TTS_FIRST_TIMEOUT,
        "timeout": 16,
    }


def _synthesize_one(text: str, out_path: Path) -> Tuple[str, Path]:
    try:
        from angus_tts import synthesize as tts_synthesize

        return tts_synthesize(text, out_path, log=log, **_interactive_tts_kwargs())
    except Exception as exc:
        log(f"[tts] angus_tts failed ({exc}); legacy edge-tts path")
        tts_to_mp3(text, out_path)
        return "edge", out_path


def _speak_direct(text: str, timing: Optional[dict] = None) -> None:
    t_synth = time.time()
    engine, audio_path = _synthesize_one(text, LAST_REPLY_MP3)
    synth_s = time.time() - t_synth
    log(f"TTS engine={engine}")
    log(f"[tts] speaking with engine={engine} synth_s={synth_s:.2f}s chunks=1")
    if timing is not None:
        timing["tts_first_chunk_s"] = synth_s
        timing["tts_total_s"] = synth_s
        timing["tts_engine"] = engine
        timing["tts_chunks"] = 1
        timing["t_first_audio"] = time.time()
    t_play = time.time()
    log("[playback] start")
    play_audio(audio_path)
    play_s = time.time() - t_play
    log(f"[playback] ok play_s={play_s:.2f}s")
    if timing is not None:
        timing["play_s"] = float(timing.get("play_s") or 0) + play_s


def speak(text: str, timing: Optional[dict] = None) -> None:
    """Generate speech via angus_tts (Chatterbox → Piper → edge) and play it.

    Multi-sentence replies synthesize the first sentence first so playback can
    start while later sentences are still rendering. Short replies use the
    original one-shot path.
    """
    if not text or not ENABLE_TTS:
        return
    text = text.strip()
    if len(text) > 280:
        text = text[:277].rsplit(" ", 1)[0] + "..."

    chunks = [text]
    if TTS_CHUNKED:
        chunks = split_speech_chunks(text)
    _spoken_echo.remember(text, replace=True)
    for chunk in chunks:
        _spoken_echo.remember(chunk)
    if _ACTIVE_MIC is not None:
        _ACTIVE_MIC.start_echo_suppress()
    if len(chunks) <= 1:
        _speak_direct(text, timing)
        return

    log(f"[tts] chunked sentences={len(chunks)} first_chars={len(chunks[0])}")
    play_q: "queue.Queue[Optional[Tuple[int, str, Path, float]]]" = queue.Queue()

    def _producer() -> None:
        for idx, chunk in enumerate(chunks):
            out = RUNTIME_DIR / f"reply_chunk_{idx}.wav"
            t0 = time.time()
            try:
                engine, path = _synthesize_one(chunk, out)
            except Exception as exc:
                log(f"[tts] chunk {idx + 1}/{len(chunks)} failed: {exc}")
                play_q.put(None)
                return
            synth_s = time.time() - t0
            log(
                f"[tts] chunk {idx + 1}/{len(chunks)} engine={engine} "
                f"synth_s={synth_s:.2f}s chars={len(chunk)}"
            )
            play_q.put((idx, engine, path, synth_s))
        play_q.put(None)

    worker = threading.Thread(
        target=_producer, name="angus-tts-chunks", daemon=True
    )
    worker.start()
    play_total = 0.0
    synth_total = 0.0
    first = True
    last_engine = ""
    while True:
        item = play_q.get()
        if item is None:
            break
        _idx, engine, path, synth_s = item
        last_engine = engine
        synth_total += synth_s
        if first:
            log(f"TTS engine={engine}")
            log(
                f"[tts] first chunk ready engine={engine} "
                f"synth_s={synth_s:.2f}s"
            )
            if timing is not None:
                timing["tts_first_chunk_s"] = synth_s
                timing["tts_engine"] = engine
                timing["tts_chunks"] = len(chunks)
                timing["t_first_audio"] = time.time()
            first = False
        t_play = time.time()
        log(f"[playback] start chunk={_idx + 1}/{len(chunks)}")
        play_audio(path)
        play_s = time.time() - t_play
        play_total += play_s
        log(f"[playback] ok chunk={_idx + 1} play_s={play_s:.2f}s")
    worker.join(timeout=1)
    if timing is not None:
        timing.setdefault("tts_engine", last_engine)
        timing.setdefault("tts_chunks", len(chunks))
        timing["tts_total_s"] = synth_total
        timing["play_s"] = float(timing.get("play_s") or 0) + play_total


def speak_from_sentences(
    sentences: List[str],
    timing: Optional[dict] = None,
    sentence_iter: Optional[Iterator[str]] = None,
) -> None:
    """Play sentences in order; synthesize the next while the current plays."""
    if not ENABLE_TTS:
        return
    source = sentence_iter
    queued: List[str] = list(sentences or [])
    if source is None and not queued:
        return
    if _ACTIVE_MIC is not None:
        _ACTIVE_MIC.start_echo_suppress()
    if source is None and len(queued) <= 1:
        speak(queued[0] if queued else "", timing)
        return

    play_q: "queue.Queue[Optional[Tuple[int, str, Path, float, str]]]" = queue.Queue()
    sent_q: "queue.Queue[Optional[str]]" = queue.Queue()

    def _reader() -> None:
        """Drain the LLM stream immediately so generation is not blocked by TTS."""
        try:
            for item in queued:
                if item and item.strip():
                    sent_q.put(item.strip())
            if source is not None:
                for item in source:
                    if item and item.strip():
                        sent_q.put(item.strip())
        except Exception as exc:
            log(f"[tts] stream reader failed: {exc}")
        finally:
            sent_q.put(None)

    def _producer() -> None:
        idx = 0
        try:
            while True:
                chunk = sent_q.get()
                if chunk is None:
                    break
                _spoken_echo.remember(chunk, replace=(idx == 0))
                out = RUNTIME_DIR / f"reply_stream_{idx}.wav"
                t0 = time.time()
                engine, path = _synthesize_one(chunk, out)
                synth_s = time.time() - t0
                log(
                    f"[tts] stream-chunk {idx + 1} engine={engine} "
                    f"synth_s={synth_s:.2f}s chars={len(chunk)}"
                )
                play_q.put((idx, engine, path, synth_s, chunk))
                idx += 1
        except Exception as exc:
            log(f"[tts] stream producer failed: {exc}")
        finally:
            play_q.put(None)

    threading.Thread(target=_reader, name="angus-llm-stream", daemon=True).start()
    worker = threading.Thread(
        target=_producer, name="angus-tts-stream", daemon=True
    )
    worker.start()
    play_total = 0.0
    synth_total = 0.0
    first = True
    count = 0
    last_engine = ""
    spoken_parts: List[str] = []
    while True:
        item = play_q.get()
        if item is None:
            break
        _idx, engine, path, synth_s, _chunk = item
        if _chunk:
            spoken_parts.append(_chunk)
        last_engine = engine
        synth_total += synth_s
        count += 1
        if first:
            log(f"TTS engine={engine}")
            log(f"[tts] first chunk ready engine={engine} synth_s={synth_s:.2f}s")
            if timing is not None:
                timing["tts_first_chunk_s"] = synth_s
                timing["tts_engine"] = engine
                timing["t_first_audio"] = time.time()
            first = False
        t_play = time.time()
        log(f"[playback] start stream-chunk={_idx + 1}")
        play_audio(path)
        play_s = time.time() - t_play
        play_total += play_s
        log(f"[playback] ok stream-chunk={_idx + 1} play_s={play_s:.2f}s")
    worker.join(timeout=1)
    if spoken_parts:
        _spoken_echo.remember(" ".join(spoken_parts))
    if timing is not None:
        timing["tts_engine"] = last_engine or timing.get("tts_engine")
        timing["tts_chunks"] = count
        timing["tts_total_s"] = synth_total
        timing["play_s"] = float(timing.get("play_s") or 0) + play_total


def classify_utterance(raw: str) -> Tuple[str, str]:
    """
    Returns (kind, payload)
      kind: silence | garbage | cancel | wake | wake_command | command
    """
    if not raw or not raw.strip():
        return "silence", ""
    if is_garbage_transcript(raw):
        return "garbage", raw.strip()

    norm = normalize_speech(raw)
    if not norm:
        return "silence", ""

    if find_phrase(norm, CANCEL_PHRASES):
        return "cancel", norm

    wake = find_phrase(norm, WAKE_PHRASES)
    if wake:
        rest = strip_wake(norm, wake)
        if rest and find_phrase(rest, CANCEL_PHRASES):
            return "cancel", rest
        if rest and len(rest) >= 2:
            return "wake_command", rest
        return "wake", wake

    return "command", norm


def _ha_headers() -> dict:
    return {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    }


def ha_entity_state(entity_id: str) -> Optional[str]:
    """Return entity state string or None on error."""
    if not HA_TOKEN:
        return None
    try:
        r = requests.get(
            f"{HA_URL}/api/states/{entity_id}",
            headers=_ha_headers(),
            timeout=5,
        )
        if r.status_code == 404:
            return "missing"
        r.raise_for_status()
        return str((r.json() or {}).get("state") or "")
    except Exception as exc:
        log(f"[ha] state read failed for {entity_id}: {exc}")
        return None


def ha_call_switch_or_lock(entity_id: str, action: str) -> None:
    """action: unlock|lock. Supports switch.* (on=unlocked) or lock.* domains."""
    domain = entity_id.split(".", 1)[0] if "." in entity_id else "switch"
    if domain == "lock":
        service = "unlock" if action == "unlock" else "lock"
        url = f"{HA_URL}/api/services/lock/{service}"
    else:
        # Historical wiring: switch ON = unlocked, OFF = locked (Tuya hobk outlet/lock)
        service = "turn_on" if action == "unlock" else "turn_off"
        url = f"{HA_URL}/api/services/switch/{service}"
    r = requests.post(
        url,
        headers=_ha_headers(),
        json={"entity_id": entity_id},
        timeout=8,
    )
    r.raise_for_status()


def load_door_codes() -> dict:
    """Load per-person door authorization codes from JSON config."""
    path = DOOR_CODES_PATH
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"[door] codes file unreadable: {exc}")
        return {}
    codes = data.get("codes") if isinstance(data, dict) else None
    if not isinstance(codes, dict):
        return {}
    out: dict = {}
    for code, meta in codes.items():
        c = re.sub(r"\D", "", str(code))
        if not c:
            continue
        if isinstance(meta, str):
            out[c] = {"name": meta, "allow": ["unlock", "lock"]}
        elif isinstance(meta, dict):
            name = str(meta.get("name") or "authorized user")
            allow = meta.get("allow") or ["unlock", "lock"]
            if isinstance(allow, str):
                allow = [allow]
            out[c] = {"name": name, "allow": [str(a).lower() for a in allow]}
    return out


_WORD_DIGITS = {
    "zero": "0",
    "oh": "0",
    "o": "0",
    "one": "1",
    "won": "1",
    "two": "2",
    "to": "2",
    "too": "2",
    "three": "3",
    "four": "4",
    "for": "4",
    "fore": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "ate": "8",
    "nine": "9",
}


def extract_auth_code(spoken: str) -> str:
    """Pull a digit PIN from free-form STT (digits or spoken numbers)."""
    t = normalize_speech(spoken)
    # Direct digits first (ignore spaces/dashes)
    digits = re.sub(r"\D", "", t)
    if 3 <= len(digits) <= 8:
        return digits
    # Spoken number words → digits
    words = re.findall(r"[a-z]+", t)
    built: List[str] = []
    for w in words:
        if w in _WORD_DIGITS:
            built.append(_WORD_DIGITS[w])
        elif w in ("code", "authorization", "authorisation", "is", "my", "the", "pin"):
            continue
        else:
            # non-number word breaks a run unless we already have something
            if built and len(built) >= 3:
                break
            built = []
    if 3 <= len(built) <= 8:
        return "".join(built)
    return digits if digits else ""


def _has_phrase(text: str, phrases: Tuple[str, ...]) -> bool:
    """True if a phrase appears on word boundaries (avoids unlock⊃lock)."""
    for phrase in phrases:
        if re.search(rf"\b{re.escape(phrase)}\b", text):
            return True
    return False


def is_front_door_unlock_request(text: str) -> bool:
    return classify_front_door_utterance(text) == DOOR_UNLOCK


def is_front_door_lock_request(text: str) -> bool:
    return classify_front_door_utterance(text) == DOOR_LOCK


def is_front_door_status_request(text: str) -> bool:
    return classify_front_door_utterance(text) == DOOR_STATUS


def clear_pending_door_auth() -> None:
    global _pending_door_auth
    _pending_door_auth = None


def request_door_auth(action: str) -> None:
    """Ask for authorization code and arm the pending challenge."""
    global _pending_door_auth
    codes = load_door_codes()
    if not codes:
        log(f"[door] no codes configured at {DOOR_CODES_PATH}")
        speak(
            "No door authorization codes are configured yet. "
            "Add codes in the Angus door codes file."
        )
        clear_pending_door_auth()
        return
    _pending_door_auth = {
        "action": action,
        "expires": time.monotonic() + DOOR_AUTH_TIMEOUT,
    }
    log(f"[door] awaiting auth code for action={action} timeout={DOOR_AUTH_TIMEOUT:g}s")
    speak("What's your authorization code?")


def execute_front_door(action: str, *, person: str = "") -> None:
    """Perform HA lock/unlock and speak result. action: unlock|lock."""
    from angus_operator.tools.ha import is_locked_state
    from face_access import append_audit

    if not HA_TOKEN:
        log(f"[ha] front door {action} failed: HA_TOKEN not configured")
        speak("I can't reach Home Assistant right now.")
        append_audit(
            {
                "source": "voice",
                "action": action,
                "result": "failed",
                "reason": "no_token",
                "person": person or None,
            }
        )
        return

    st = ha_entity_state(FRONT_DOOR_ENTITY)
    log(f"[ha] front door entity={FRONT_DOOR_ENTITY} state={st}")
    locked = is_locked_state(st)
    if st in (None,):
        speak("I couldn't reach Home Assistant for the front door.")
        append_audit(
            {
                "source": "voice",
                "action": action,
                "result": "failed",
                "reason": "ha_unreachable",
                "door_state": st,
                "person": person or None,
            }
        )
        return
    if st in ("missing", "unavailable", "unknown") or locked is None:
        speak(
            "The front door lock is offline in Home Assistant. "
            "Check the Tuya integration and try again."
        )
        append_audit(
            {
                "source": "voice",
                "action": action,
                "result": "failed",
                "reason": "ha_unavailable",
                "door_state": st,
                "person": person or None,
            }
        )
        return

    if action == "lock" and locked:
        log("[ha] front door already locked")
        speak("The front door is already locked.")
        append_audit(
            {
                "source": "voice",
                "action": "lock",
                "result": "already_locked",
                "door_state": st,
                "person": person or None,
            }
        )
        return
    if action == "unlock" and locked is False:
        log("[ha] front door already unlocked")
        speak("The front door is already unlocked.")
        append_audit(
            {
                "source": "voice",
                "action": "unlock",
                "result": "already_unlocked",
                "door_state": st,
                "person": person or None,
            }
        )
        return

    try:
        ha_call_switch_or_lock(FRONT_DOOR_ENTITY, action)
        who = f" for {person}" if person else ""
        log(f"[ha] front door {action} ok entity={FRONT_DOOR_ENTITY}{who}")
        append_audit(
            {
                "source": "voice",
                "action": action,
                "result": action,
                "door_state": st,
                "person": person or None,
            }
        )
        if action == "unlock":
            if person:
                speak(f"Unlocking the front door. Welcome, {person}.")
            else:
                speak("Unlocking the front door.")
        else:
            speak("Locking the front door.")
    except Exception as exc:
        log(f"[ha] front door {action} failed: {exc}")
        append_audit(
            {
                "source": "voice",
                "action": action,
                "result": "failed",
                "reason": "ha_call_failed",
                "door_state": st,
                "person": person or None,
            }
        )
        speak(f"I couldn't {action} the front door.")


def handle_door_auth_response(user_text: str) -> bool:
    """If a door auth challenge is pending, validate spoken code. Returns True if handled."""
    global _pending_door_auth
    pending = _pending_door_auth
    if not pending:
        return False
    now = time.monotonic()
    if now > float(pending.get("expires") or 0):
        log("[door] auth challenge expired")
        clear_pending_door_auth()
        speak("Authorization timed out. Ask me again if you still need the door.")
        return True

    code = extract_auth_code(user_text)
    action = str(pending.get("action") or "unlock")
    log(f"[door] auth attempt action={action} digits={len(code)}")
    if not code:
        speak("I didn't catch a code. Please say your authorization code clearly.")
        # keep pending; refresh timeout slightly
        pending["expires"] = time.monotonic() + DOOR_AUTH_TIMEOUT
        return True

    codes = load_door_codes()
    meta = codes.get(code)
    if not meta:
        log("[door] auth denied unknown code")
        clear_pending_door_auth()
        speak("That code is not authorized.")
        return True

    allow = meta.get("allow") or []
    if action not in allow:
        log(f"[door] auth denied code for person={meta.get('name')} action={action}")
        clear_pending_door_auth()
        speak(f"You're not authorized to {action} the front door.")
        return True

    person = str(meta.get("name") or "authorized user")
    log(f"[door] auth accepted person={person} action={action}")
    clear_pending_door_auth()
    execute_front_door(action, person=person)
    return True


def is_time_query(text: str) -> Optional[str]:
    """Return 'time', 'date', or 'both' for obvious clock questions."""
    from glitch_llm import clock_query_kind

    kind = clock_query_kind(text)
    if kind:
        return kind
    t = normalize_speech(text)
    date_phrases = (
        "what is the date",
        "what's the date",
        "whats the date",
        "what date is it",
        "whats todays date",
        "todays date",
        "current date",
        "what day is it",
        "what day of the week",
        "day of the week",
        "what's today",
        "whats today",
        "what is today",
        "what's the day",
        "whats the day",
    )
    both_phrases = (
        "what time and date",
        "date and time",
        "time and date",
    )
    time_phrases = (
        "what time is it",
        "what's the time",
        "whats the time",
        "what is the time",
        "tell me the time",
        "current time",
        "got the time",
        "do you have the time",
        "what time",
    )
    if any(p in t for p in both_phrases):
        return "both"
    if any(p in t for p in date_phrases):
        return "date"
    if any(p in t for p in time_phrases):
        return "time"
    return None


def is_security_mode_query(text: str) -> bool:
    t = normalize_speech(text)
    phrases = (
        "what security mode",
        "what's the security mode",
        "whats the security mode",
        "what mode are we in",
        "security mode",
    )
    if t in ("security mode", "what mode"):
        return True
    return any(p in t for p in phrases) and any(
        w in t for w in ("what", "what's", "whats", "current", "are we")
    )


def is_porch_light_on_request(text: str) -> bool:
    from angus_light_intent import porch_light_action

    return porch_light_action(text) == "on"


def is_porch_light_off_request(text: str) -> bool:
    from angus_light_intent import porch_light_action

    return porch_light_action(text) == "off"


def read_security_mode() -> str:
    try:
        data = json.loads(SECURITY_MODE_PATH.read_text(encoding="utf-8"))
        mode = str((data or {}).get("mode") or "").strip().lower()
        return mode or "unknown"
    except Exception as exc:
        log(f"[scc] security mode read failed: {exc}")
        return ""


def handle_local_device_command(user_text: str, timing: Optional[dict] = None) -> bool:
    """Handle deterministic SCC/Home Assistant commands before the LLM.

    Returns True if handled (including when a door auth challenge was started).
    """
    text = normalize_speech(user_text)
    t_route = time.time()

    clock_kind = is_time_query(text)
    if clock_kind:
        reply = spoken_clock_reply(clock_kind)
        log(f"[local] clock query kind={clock_kind}")
        if timing is not None:
            timing["route_via"] = "local_clock"
            timing["route_s"] = time.time() - t_route
            timing["backend"] = "local"
            timing["model"] = "host-clock"
        speak(reply, timing)
        remember_thread(user_text, reply)
        return True

    if is_security_mode_query(text):
        mode = read_security_mode()
        if timing is not None:
            timing["route_via"] = "local_security_mode"
            timing["route_s"] = time.time() - t_route
            timing["backend"] = "local"
            timing["model"] = "security_mode.json"
        if mode:
            speak(f"Security mode is {mode}.", timing)
        else:
            speak("I couldn't read the current security mode.", timing)
        return True

    scrapyard_porch_on_phrases = (
        "turn on the scrapyard porch lights",
        "turn on scrapyard porch lights",
        "turn on the scrapyard porch light",
        "turn on scrapyard porch light",
    )

    scrapyard_porch_off_phrases = (
        "turn off the scrapyard porch lights",
        "turn off scrapyard porch lights",
        "turn off the scrapyard porch light",
        "turn off scrapyard porch light",
    )

    if (
        text in scrapyard_porch_on_phrases
        or any(p in text for p in scrapyard_porch_on_phrases)
        or is_porch_light_on_request(text)
    ):
        if timing is not None:
            timing["route_via"] = "local_ha_porch"
            timing["route_s"] = time.time() - t_route
            timing["backend"] = "local"
            timing["model"] = "home-assistant"
        if not HA_TOKEN:
            log("[ha] scrapyard porch lights failed: HA_TOKEN not configured")
            speak("I can't reach Home Assistant right now.", timing)
            return True

        try:
            response = requests.post(
                f"{HA_URL}/api/services/switch/turn_on",
                headers=_ha_headers(),
                json={"entity_id": "switch.scrapyard_porch_light"},
                timeout=5,
            )
            response.raise_for_status()

            log("[ha] scrapyard porch lights ON")
            speak("Turning on the scrapyard porch lights.", timing)
        except Exception as exc:
            log(f"[ha] scrapyard porch lights ON failed: {exc}")
            speak("I couldn't turn on the scrapyard porch lights.", timing)

        return True

    if (
        text in scrapyard_porch_off_phrases
        or any(p in text for p in scrapyard_porch_off_phrases)
        or is_porch_light_off_request(text)
    ):
        if timing is not None:
            timing["route_via"] = "local_ha_porch"
            timing["route_s"] = time.time() - t_route
            timing["backend"] = "local"
            timing["model"] = "home-assistant"
        if not HA_TOKEN:
            log("[ha] scrapyard porch lights failed: HA_TOKEN not configured")
            speak("I can't reach Home Assistant right now.", timing)
            return True

        try:
            response = requests.post(
                f"{HA_URL}/api/services/switch/turn_off",
                headers=_ha_headers(),
                json={"entity_id": "switch.scrapyard_porch_light"},
                timeout=5,
            )
            response.raise_for_status()

            log("[ha] scrapyard porch lights OFF")
            speak("Turning off the scrapyard porch lights.", timing)
        except Exception as exc:
            log(f"[ha] scrapyard porch lights OFF failed: {exc}")
            speak("I couldn't turn off the scrapyard porch lights.", timing)

        return True

    # Front door — classifier is deterministic; unlock PIN policy is unchanged.
    door_kind = classify_front_door_utterance(text)
    if door_kind == DOOR_STATUS:
        if timing is not None:
            timing["route_via"] = "local_door_status"
            timing["route_s"] = time.time() - t_route
            timing["backend"] = "local"
            timing["model"] = "door-status"
        from angus_operator.tools.ha import handle_door_query

        result = handle_door_query()
        speak(result.spoken or "I couldn't verify the front door lock state.", timing)
        return True

    if door_kind == DOOR_UNLOCK:
        if timing is not None:
            timing["route_via"] = "local_door"
            timing["route_s"] = time.time() - t_route
            timing["backend"] = "local"
            timing["model"] = "door-auth"
        if DOOR_UNLOCK_REQUIRES_CODE:
            log("[door] unlock requested — starting auth challenge")
            request_door_auth("unlock")
        else:
            log("[door] unlock requested — code not required, executing")
            execute_front_door("unlock")
        return True

    if door_kind == DOOR_LOCK:
        if timing is not None:
            timing["route_via"] = "local_door"
            timing["route_s"] = time.time() - t_route
            timing["backend"] = "local"
            timing["model"] = "door-auth"
        if DOOR_LOCK_REQUIRES_CODE:
            log("[door] lock requested — starting auth challenge")
            request_door_auth("lock")
        else:
            execute_front_door("lock")
        return True

    if door_kind == DOOR_NON_ACTION:
        return False

    def _speak_timed(msg: str) -> None:
        speak(msg, timing)

    # Employee clock-out — name/intent only here; timeclock punches.
    if handle_clock_out_utterance(user_text, speak=_speak_timed, log=log):
        if timing is not None:
            timing.setdefault("route_via", "local_timeclock")
            timing.setdefault("route_s", time.time() - t_route)
            timing.setdefault("backend", "local")
            timing.setdefault("model", "timeclock")
        return True

    # Clock-in is facecam-only; keep it off the general LLM.
    if handle_clock_in_utterance(user_text, speak=_speak_timed, log=log):
        if timing is not None:
            timing.setdefault("route_via", "local_timeclock")
            timing.setdefault("route_s", time.time() - t_route)
            timing.setdefault("backend", "local")
            timing.setdefault("model", "timeclock")
        return True

    if handle_timeclock_status_utterance(user_text, speak=_speak_timed, log=log):
        if timing is not None:
            timing.setdefault("route_via", "local_timeclock_status")
            timing.setdefault("route_s", time.time() - t_route)
            timing.setdefault("backend", "local")
            timing.setdefault("model", "timeclock")
        return True

    return False


def _finish_spoken_turn(
    mic: "ContinuousMic",
    session: ConversationSession,
    last_source: str = "",
) -> None:
    """SPEAKING → PLAYBACK_COMPLETE → USB flush + echo guard → FOLLOWUP."""
    src = last_source or session.last_source or mic.last_source
    session.mark_playback_complete(src)
    mic.rearm_after_playback()
    if _pending_door_auth:
        session.hold_for(DOOR_AUTH_TIMEOUT, "door_auth")
        log(f"[door] listening for auth code ({DOOR_AUTH_TIMEOUT:g}s)")
    else:
        session.enter_followup()


def handle_command(user_text: str, timing: Optional[dict] = None) -> None:
    log(f"[command] {user_text}")
    timing = timing if timing is not None else {"t_eos": time.time()}

    # Prefer completing a pending door auth over LLM chatter
    t_route = time.time()
    if _pending_door_auth and handle_door_auth_response(user_text):
        timing["route_via"] = "door_auth"
        timing["route_s"] = time.time() - t_route
        timing.setdefault("backend", "local")
        timing.setdefault("model", "door-auth")
        log_command_latency(timing)
        return

    routed, fixes = correct(user_text)
    if fixes:
        log(f"[speech] {fixes}")
    set_last_transcript(routed)

    if re.search(
        r"\b(?:new conversation|start over|forget this (?:chat|thread|conversation)|"
        r"clear (?:the )?(?:chat|thread|conversation))\b",
        routed,
        re.I,
    ):
        from angus_operator.thread import clear as clear_thread

        clear_thread()
        timing["route_via"] = "thread.clear"
        timing["backend"] = "operator"
        speak("Okay. Fresh thread.", timing)
        log_command_latency(timing)
        return

    op = handle_operator(routed, log=log)
    if op and op.handled:
        timing["route_via"] = f"operator:{op.tool}"
        timing["route_s"] = time.time() - t_route
        timing["backend"] = "operator"
        timing["model"] = op.tool
        if op.spoken:
            speak(op.spoken, timing)
            remember_thread(routed, op.spoken)
        log_command_latency(timing)
        return

    if handle_local_device_command(routed, timing):
        timing.setdefault("route_via", "local")
        timing.setdefault("route_s", time.time() - t_route)
        log_command_latency(timing)
        return

    from angus_operator.honesty import block_unmatched_yard, sanitize_llm_reply

    refuse = block_unmatched_yard(routed)
    if refuse:
        timing["route_via"] = "honesty.yard"
        timing["backend"] = "operator"
        speak(refuse, timing)
        remember_thread(routed, refuse)
        log_command_latency(timing)
        return

    timing["route_via"] = "llm"
    timing["route_s"] = time.time() - t_route

    t_llm = time.time()
    if VOICE_FAST_PATH and VOICE_STREAM and TTS_CHUNKED:
        meta: dict = {}
        sentences = iter_glitch_sentences(
            routed, voice=True, meta=meta, history=thread_history()
        )
        speak_from_sentences([], timing, sentence_iter=sentences)
        llm_done = float(meta.get("t_llm_done") or time.time())
        llm_s = llm_done - float(meta.get("t_llm_start") or t_llm)
        reply = (meta.get("reply") or "").strip()
        timing["llm_s"] = llm_s
        timing["backend"] = meta.get("backend")
        timing["model"] = meta.get("model")
        timing["sanitized"] = bool(meta.get("sanitized"))
        if meta.get("t_first_token"):
            timing["llm_ttft"] = float(meta["t_first_token"]) - float(
                meta.get("t_llm_start") or t_llm
            )
        log(
            f"[llm] time={llm_s:.2f}s ttft={float(timing.get('llm_ttft') or 0):.2f}s "
            f"backend={meta.get('backend')} model={meta.get('model')} stream=1"
        )
        if reply:
            log(f"[glitch:{meta.get('backend')}:{meta.get('model')}] {reply}")
            angus_build.remember_brief(routed, reply=reply)
            remember_thread(routed, reply)
        log_command_latency(timing)
        return

    result = ask_glitch(routed, voice=True, history=thread_history())
    llm_s = time.time() - t_llm
    reply = sanitize_llm_reply((result.get("reply") or "").strip())
    timing["llm_s"] = llm_s
    timing["backend"] = result.get("backend")
    timing["model"] = result.get("model")
    timing["sanitized"] = bool(result.get("sanitized"))
    log(f"[llm] time={llm_s:.2f}s backend={result.get('backend')} model={result.get('model')}")
    log(f"[glitch:{result.get('backend')}:{result.get('model')}] {reply}")
    if reply:
        speak(reply, timing)
        angus_build.remember_brief(routed, reply=reply)
        remember_thread(routed, reply)
    log_command_latency(timing)


def main() -> int:
    global _ACTIVE_MIC
    ensure_stop_removed()

    st = status_dict()

    log("[angus] hybrid voice loop ready (continuous listening)")
    log(
        f"[angus] mic={MIC_DEVICE} speaker={SPEAKER_DEVICE} "
        f"local_model={OLLAMA_MODEL} llm_mode={st['mode']} "
        f"xai_configured={st['xai_configured']} xai_model={st['xai_model']}"
    )
    log(
        f"[angus] voice_fast_path={int(VOICE_FAST_PATH)} "
        f"voice_model={st.get('voice_xai_model')} "
        f"voice_max_tokens={st.get('voice_max_tokens')} "
        f"voice_stream={int(bool(st.get('voice_stream')))} "
        f"tts_chunked={int(TTS_CHUNKED)} followup_s={FOLLOWUP_SECONDS:g} "
        f"door_unlock_code={int(DOOR_UNLOCK_REQUIRES_CODE)} "
        f"door_lock_code={int(DOOR_LOCK_REQUIRES_CODE)}"
    )
    log(f"[angus] wake={WAKE_PHRASES}")
    log(f"[angus] cancel={CANCEL_PHRASES}")
    log(
        f"[angus] continuous mic chunk={MIC_CHUNK_MS}ms "
        f"source={AUDIO_SOURCE} vad_usb={VAD_MIN_RMS_USB} "
        f"vad_door={VAD_MIN_RMS_DOOR} silence={VAD_SILENCE_SECONDS}s "
        f"wake_stt={WAKE_WHISPER_MODEL} cmd_stt={WHISPER_MODEL} "
        f"tts_first_timeout={TTS_FIRST_TIMEOUT:g}s"
    )
    if AUDIO_SOURCE in ("rtsp", "both"):
        log(f"[angus] rtsp_url configured={bool(RTSP_AUDIO_URL)}")
    if AUDIO_SOURCE in ("alsa", "both"):
        log(f"[angus] usb_mic device={MIC_DEVICE}")

    # Warm Whisper on GPU before opening the mic (avoids first-utterance lag).
    try:
        get_persistent_whisper()
    except Exception as exc:
        log(f"[angus] whisper preload failed (will retry on first STT): {exc}")

    session = ConversationSession(FOLLOWUP_SECONDS)
    mic = ContinuousMic()
    _ACTIVE_MIC = mic

    try:
        mic.start()
        log("[state] IDLE — listening")

        while not should_stop():
            follow = session.in_followup()
            follow_deadline = session.deadline() if follow else None
            job_poll = (
                angus_build.has_active_jobs() or angus_build.has_announcements()
            )
            poll_deadline = time.monotonic() + 1.0 if job_poll else None
            deadlines = [
                d for d in (follow_deadline, poll_deadline) if d is not None
            ]
            pcm = mic.next_utterance(
                deadline_mono=min(deadlines) if deadlines else None,
                prefer=session.prefer_source() if follow else None,
            )

            if not pcm:
                notes = angus_build.pop_announcements()
                if notes:
                    session.start("build_report")
                    session.begin_speaking()
                    mic.start_echo_suppress()
                    for note in notes:
                        speak(note)
                    _finish_spoken_turn(mic, session)
                    continue
                if session.open:
                    session.expire_if_due(reason="timeout")
                continue

            t_eos = time.time()
            speech_mono = mic.last_speech_mono or time.monotonic()
            if mic.last_source:
                session.last_source = mic.last_source
            log("[latency] recording_complete end_of_speech")
            src_name = mic.last_source or "-"
            stats = pcm_stats(pcm)
            speech_s = (mic.last_speech_chunks or 0) * (MIC_CHUNK_MS / 1000.0)
            log(
                f"[stt] source={src_name} duration={stats['duration']:.2f}s "
                f"speech={speech_s:.2f}s bytes={stats['bytes']} "
                f"peak={stats['peak']} rms={stats['rms']}"
            )
            skip = stt_skip_reason(src_name, stats)
            if skip:
                log(
                    f"[stt] skipped reason={skip} source={src_name} "
                    f"duration={stats['duration']:.2f}s peak={stats['peak']} "
                    f"rms={stats['rms']}"
                )
                continue

            write_pcm_wav(LAST_USER_WAV, pcm)

            # Session validity is based on when the user *started* speaking,
            # so finishing a sentence a moment after the window does not drop
            # a follow-up they began in time.
            is_active = session.allows_at(speech_mono)

            # While Whisper works, ContinuousMic's reader thread keeps
            # capturing audio into its queue instead of going deaf.
            t_stt = time.time()
            raw = transcribe(
                LAST_USER_WAV,
                for_wake=not is_active,
                retry_blank=substantial_speech(src_name, stats),
            ).strip()
            stt_s = time.time() - t_stt
            timing = {
                "t_eos": t_eos,
                "stt_s": stt_s,
            }
            log(f"[latency] stt={stt_s:.2f}s eos_to_stt_done={time.time() - t_eos:.2f}s")
            if not raw:
                why = (
                    "whisper_blank_substantial"
                    if substantial_speech(src_name, stats)
                    else "whisper_blank"
                )
                log(
                    f"[stt] empty_transcript reason={why} source={src_name} "
                    f"duration={stats['duration']:.2f}s peak={stats['peak']} "
                    f"rms={stats['rms']} bytes={stats['bytes']}"
                )

            kind, payload = classify_utterance(raw)

            if kind == "silence":
                log(
                    f"[user] silence source={src_name} "
                    f"duration={stats['duration']:.2f}s peak={stats['peak']} "
                    f"rms={stats['rms']}"
                )
                continue

            if kind == "garbage":
                log(f"[user] ignored garbage: {payload[:80]!r}")
                continue

            log(f"[user] ({kind}) {raw}")

            if kind == "cancel":
                log("[followup] terminated reason=cancel")
                session.cancel()
                clear_pending_door_auth()

                cancel_ack = os.getenv(
                    "GLITCH_CANCEL_ACK",
                    "",
                ).strip()

                if cancel_ack:
                    session.begin_speaking()
                    mic.start_echo_suppress()
                    speak(cancel_ack)
                    mic.rearm_after_playback()

                continue

            # Door auth PIN: accept while challenge is live (no re-wake required)
            if _pending_door_auth and kind in ("command", "wake_command", "wake"):
                auth_text = payload if kind != "wake" else raw
                if kind == "wake_command":
                    auth_text = payload
                if handle_door_auth_response(auth_text):
                    _finish_spoken_turn(mic, session)
                    continue

            if kind == "wake":
                log("[angus] wake word heard — ACTIVE")
                session.start("wake")
                session.begin_speaking()
                mic.start_echo_suppress()

                if WAKE_ACK:
                    speak(WAKE_ACK, timing)
                    timing["route_via"] = "wake_ack"
                    timing["backend"] = "local"
                    timing["model"] = "wake"
                    log_command_latency(timing)

                _finish_spoken_turn(mic, session, mic.last_source)
                continue

            if kind == "wake_command":
                log("[angus] wake + command in one utterance")
                session.start("wake_command")
                session.begin_speaking()
                mic.start_echo_suppress()

                handle_command(payload, timing)

                _finish_spoken_turn(mic, session, mic.last_source)
                continue

            if kind == "command":
                if is_active or _pending_door_auth:
                    if src_name == "usb":
                        is_echo, sim = _spoken_echo.check(raw)
                        if is_echo:
                            log(
                                f"[followup] rejected reason=self_echo "
                                f"similarity={sim:.2f}"
                            )
                            continue
                    log("[conversation] followup accepted")
                    log(f"[followup] accepted source={src_name}")
                    session.begin_speaking()
                    mic.start_echo_suppress()
                    handle_command(payload, timing)

                    _finish_spoken_turn(mic, session, mic.last_source)
                else:
                    log("[followup] skipped reason=no_session")
                    log("[angus] command ignored — no wake word")

                continue

    except KeyboardInterrupt:
        log("[angus] interrupted")
    except Exception as exc:
        log(f"[error] continuous voice loop failed: {exc}")
        return 1
    finally:
        _ACTIVE_MIC = None
        mic.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
