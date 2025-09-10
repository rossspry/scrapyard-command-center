# app/reolink/core.py
import os, json, datetime
import os
from dotenv import load_dotenv
load_dotenv()
from pathlib import Path
from typing import Optional, Tuple
import requests
from requests.auth import HTTPDigestAuth

DEFAULT_LOG = Path(os.getenv("REOLINK_EVENT_LOG", "/data/events.jsonl"))

def _extract_counts(payload: dict) -> Optional[Tuple[int,int,int]]:
    # Accept both GetAiState and GetSmartDetect styles
    if not isinstance(payload, list) or not payload:
        return None
    entry = payload[0]
    if entry.get("code") != 0:
        return None
    val = entry.get("value") or {}
    for key in ("state", "SmartDetect", "AIState", "AI"):
        block = val.get(key)
        if isinstance(block, dict):
            people  = block.get("people", block.get("human"))
            vehicle = block.get("vehicle", block.get("car"))
            motion  = block.get("motion")
            if all(isinstance(x, int) for x in (people, vehicle, motion)):
                return people, vehicle, motion
    return None

def poll_ai_state(host: str, user: str, password: str, channel: int = 0, timeout: float = 3.0):
    """
    Returns dict: { 'people': int, 'vehicle': int, 'motion': int, 'cmd': 'GetAiState'|'GetSmartDetect' }
    Raises requests.RequestException on network issues, ValueError on response parse issues.
    """
    endpoints = [
        f"http://{host}/cgi-bin/api.cgi?cmd=GetAiState&channel={channel}",
        f"http://{host}/cgi-bin/api.cgi?cmd=GetSmartDetect&channel={channel}",
    ]
    last_err = None
    for url in endpoints:
        try:
            r = requests.get(url, auth=HTTPDigestAuth(user, password), timeout=timeout)
            if r.status_code == 401:
                # Some firmwares allow query param credentials (fallback)
                r = requests.get(f"{url}&user={user}&password={password}", timeout=timeout)
            r.raise_for_status()
            data = r.json()
            counts = _extract_counts(data)
            if counts:
                p, v, m = counts
                cmd = url.split("?cmd=")[-1].split("&")[0]
                return {"people": p, "vehicle": v, "motion": m, "cmd": cmd}
        except Exception as e:
            last_err = e
    if last_err:
        raise last_err
    raise ValueError("No valid AI state in response")

def log_event(host: str, channel: int, result: dict, logfile: Path = DEFAULT_LOG):
    """
    Append a single JSONL record, creating dirs as needed.
    """
    logfile.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "host": host,
        "channel": channel,
        **result
    }
    with logfile.open("a", buffering=1) as f:
        f.write(json.dumps(line) + "\n")
    return line
