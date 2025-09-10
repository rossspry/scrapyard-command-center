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

def _try_get(host: str, user: str, password: str, channel: int, timeout: float):
    endpoints = [
        f"http://{host}/cgi-bin/api.cgi?cmd=GetAiState&channel={channel}",
        f"http://{host}/cgi-bin/api.cgi?cmd=GetSmartDetect&channel={channel}",
    ]
    for url in endpoints:
        r = requests.get(url, auth=HTTPDigestAuth(user, password), timeout=timeout)
        if r.status_code == 401:
            r = requests.get(f"{url}&user={user}&password={password}", timeout=timeout)
        r.raise_for_status()
        data = r.json()
        counts = _extract_counts(data)
        if counts:
            p, v, m = counts
            cmd = url.split("?cmd=")[-1].split("&")[0]
            return {"people": p, "vehicle": v, "motion": m, "cmd": cmd}
    return None

def _try_post(host: str, user: str, password: str, channel: int, timeout: float):
    url = f"http://{host}/cgi-bin/api.cgi"
    # Reolink JSON POST format
    payloads = [
        [{"cmd": "GetAiState", "action": 0, "param": {"channel": channel}}],
        [{"cmd": "GetSmartDetect", "action": 0, "param": {"channel": channel}}],
    ]
    for body in payloads:
        r = requests.post(url, auth=HTTPDigestAuth(user, password), json=body, timeout=timeout)
        if r.status_code == 401:
            # Some firmwares allow query user/pass
            r = requests.post(f"{url}?user={user}&password={password}", json=body, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        counts = _extract_counts(data)
        if counts:
            p, v, m = counts
            return {"people": p, "vehicle": v, "motion": m, "cmd": body[0]["cmd"]}
    return None

def poll_ai_state(host: str, user: str, password: str, channel: int = 0, timeout: float = 3.0):
    last_err = None
    # 1) Try GET style
    try:
        res = _try_get(host, user, password, channel, timeout)
        if res:
            return res
    except Exception as e:
        last_err = e
    # 2) Try POST JSON style
    try:
        res = _try_post(host, user, password, channel, timeout)
        if res:
            return res
    except Exception as e:
        last_err = e
    if last_err:
        raise last_err
    raise ValueError("No valid AI state in response")

def log_event(host: str, channel: int, result: dict, logfile: Path = DEFAULT_LOG):
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
def _try_post(host: str, user: str, password: str, channel: int, timeout: float):
    url = f"http://{host}/cgi-bin/api.cgi"
    payloads = [
        [{"cmd": "GetAiState", "action": 0, "param": {"channel": channel}}],
        [{"cmd": "GetSmartDetect", "action": 0, "param": {"channel": channel}}],
    ]
    for body in payloads:
        r = requests.post(url, auth=HTTPDigestAuth(user, password), json=body, timeout=timeout)
        if r.status_code == 401:
            r = requests.post(f"{url}?user={user}&password={password}", json=body, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        counts = _extract_counts(data)
        if counts:
            p, v, m = counts
            return {"people": p, "vehicle": v, "motion": m, "cmd": body[0]["cmd"]}
    return None
def poll_ai_state(host: str, user: str, password: str, channel: int = 0, timeout: float = 3.0):
    last_err = None
    try:
        res = _try_get(host, user, password, channel, timeout)
        if res:
            return res
    except Exception as e:
        last_err = e
    try:
        res = _try_post(host, user, password, channel, timeout)
        if res:
            return res
    except Exception as e:
        last_err = e
    if last_err:
        raise last_err
    raise ValueError("No valid AI state in response")
# Override extractor to also handle "alarm_state" schema
def _extract_counts(payload: dict):
    if not isinstance(payload, list) or not payload:
        return None
    entry = payload[0]
    if entry.get("code") != 0:
        return None
    val = entry.get("value") or {}

    # Case A: flat ints {people:0, vehicle:1, motion:0}
    for key in ("state", "SmartDetect", "AIState", "AI"):
        block = val.get(key)
        if isinstance(block, dict):
            people  = block.get("people", block.get("human"))
            vehicle = block.get("vehicle", block.get("car"))
            motion  = block.get("motion")
            if all(isinstance(x, int) for x in (people, vehicle, motion)):
                return people, vehicle, motion

    # Case B: nested dicts with alarm_state {people:{alarm_state:0}, vehicle:{alarm_state:1}}
    def to_bit(x):
        if isinstance(x, dict):
            a = x.get("alarm_state")
            if isinstance(a, int):
                return 1 if a != 0 else 0
        if isinstance(x, int):
            return 1 if x != 0 else 0
        return 0

    people  = to_bit(val.get("people") or val.get("human"))
    vehicle = to_bit(val.get("vehicle") or val.get("car"))
    # Some firmwares don’t expose a standalone "motion" here; synthesize motion if any AI fired
    motion  = to_bit(val.get("motion"))
    if motion == 0 and (people == 1 or vehicle == 1):
        motion = 1

    # If at least one field is present, return it
    if any(isinstance(x, int) for x in (people, vehicle, motion)):
        return people, vehicle, motion

    return None
def _try_get(host: str, user: str, password: str, channel: int, timeout: float):
    endpoints = [
        f"http://{host}/cgi-bin/api.cgi?cmd=GetAiState&channel={channel}",
        f"http://{host}/cgi-bin/api.cgi?cmd=GetSmartDetect&channel={channel}",
    ]
    for url in endpoints:
        # 1) no-auth try
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            counts = _extract_counts(r.json())
            if counts:
                p,v,m = counts
                return {"people":p,"vehicle":v,"motion":m,"cmd":url.split("?cmd=")[-1].split("&")[0]}
        except Exception:
            pass
        # 2) digest try
        try:
            r = requests.get(url, auth=HTTPDigestAuth(user, password), timeout=timeout)
            r.raise_for_status()
            counts = _extract_counts(r.json())
            if counts:
                p,v,m = counts
                return {"people":p,"vehicle":v,"motion":m,"cmd":url.split("?cmd=")[-1].split("&")[0]}
        except Exception:
            pass
        # 3) query-creds try (works for your camera)
        try:
            r = requests.get(f"{url}&user={user}&password={password}", timeout=timeout)
            r.raise_for_status()
            counts = _extract_counts(r.json())
            if counts:
                p,v,m = counts
                return {"people":p,"vehicle":v,"motion":m,"cmd":url.split("?cmd=")[-1].split("&")[0]}
        except Exception:
            pass
    return None
