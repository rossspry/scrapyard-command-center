"""Build broker: confirmed voice request → Grok Build in the project cwd.

Does not block the mic. Reports back through an announcement queue the
voice loop drains. Spoken replies never name the coding agent.
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from angus_operator.projects import list_projects, resolve_project
from angus_operator.registry import register
from angus_operator.types import RiskTier, ToolResult, ToolSpec

_TRUE = ("1", "true", "yes", "on")

_CHANGE = re.compile(
    r"\b(?:make|change|fix|edit|update|upgrade|install|pull|restart|"
    r"increase|decrease|bigger|smaller|"
    r"move|hide|show|font|css|button|page|timer|blinds|implement|add|"
    r"create|build|deploy|rewrite|refactor|version)\b",
    re.I,
)
_GROK_TALK = re.compile(
    r"\b(?:grok(?:\s+build)?|coding agent|gropp?|grop bill)\b",
    re.I,
)
_DESTRUCTIVE = re.compile(
    r"\b(?:delete|rm -rf|drop table|wipe|format|firewall|ufw|passwd|"
    r"chmod 777|useradd|userdel|iptables)\b",
    re.I,
)
_EXECUTE = re.compile(
    r"\b(?:do it|do that|execute(?: it| that| the plan)?|"
    r"build (?:it|that)|implement (?:it|that)|"
    r"make (?:those |the )?changes|"
    r"go ahead and (?:build|do|make|change)|"
    r"ship it|make it so|"
    r"(?:use|send|have) grok(?:\s+build)?|"
    r"through grok|via grok)\b",
    re.I,
)
_STATUS = re.compile(
    r"\b(?:(?:is|did) (?:that|the) (?:change|build|job|upgrade)|"
    r"how(?:'s| is) (?:that|the) (?:change|build|upgrade)|"
    r"(?:job|build|upgrade) status|"
    r"are you (?:done|finished|still working)|"
    r"did you finish|"
    r"are you using grok|did you use grok|"
    r"stuck|still working on)\b",
    re.I,
)

_SENTENCE = re.compile(r"(?<=[.!?])\s+")

_lock = threading.Lock()
_jobs_q: "queue.Queue[str]" = queue.Queue()
_announce_q: "queue.Queue[str]" = queue.Queue()
_worker_started = False
_active_id: Optional[str] = None
_last_job_id: Optional[str] = None
_last_brief: Optional[Dict[str, Any]] = None


def execute_enabled() -> bool:
    return os.getenv("ANGUS_BUILD_EXECUTE", "true").strip().lower() in _TRUE


def sync_jobs() -> bool:
    return os.getenv("ANGUS_BUILD_SYNC", "").strip().lower() in _TRUE


def job_dir() -> Path:
    return Path(
        os.getenv("ANGUS_BUILD_JOB_DIR", "/home/ross/.local/share/glitch/jobs")
    )


def grok_bin() -> str:
    configured = os.getenv("ANGUS_GROK_BIN", "").strip()
    for candidate in (
        configured,
        "/home/ross/.local/bin/grok",
        "/home/ross/.grok/bin/grok",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return configured or "/home/ross/.local/bin/grok"


def grok_model() -> str:
    return os.getenv("ANGUS_GROK_MODEL", "grok-build").strip() or "grok-build"


def grok_timeout() -> int:
    try:
        return max(30, int(os.getenv("ANGUS_GROK_TIMEOUT", "720") or "720"))
    except ValueError:
        return 720


def grok_max_turns() -> int:
    try:
        return max(1, int(os.getenv("ANGUS_GROK_MAX_TURNS", "30") or "30"))
    except ValueError:
        return 30


def remember_brief(
    request: str,
    project: Optional[str] = None,
    *,
    reply: str = "",
) -> None:
    global _last_brief
    text = (request or "").strip()
    if not text or len(text) < 8:
        return
    proj = project
    if not proj:
        found = resolve_project(text)
        proj = (found or {}).get("id")
    _last_brief = {
        "request": text,
        "project": proj,
        "reply": (reply or "").strip()[:800],
        "ts": time.time(),
    }


def last_brief() -> Optional[Dict[str, Any]]:
    if not _last_brief:
        return None
    age = time.time() - float(_last_brief.get("ts") or 0)
    if age > 15 * 60:
        return None
    return dict(_last_brief)


def has_active_jobs() -> bool:
    with _lock:
        return _active_id is not None or not _jobs_q.empty()


def has_announcements() -> bool:
    return not _announce_q.empty()


def pop_announcements() -> List[str]:
    out: List[str] = []
    while True:
        try:
            out.append(_announce_q.get_nowait())
        except queue.Empty:
            break
    return out


def announce(text: str) -> None:
    msg = (text or "").strip()
    if not msg:
        return
    _announce_q.put(msg)
    try:
        from angus_operator.phone import push_phone

        push_phone(msg)
    except Exception:
        pass


def reset_for_tests() -> None:
    """Clear in-memory job state. Tests only."""
    global _active_id, _last_job_id, _last_brief
    with _lock:
        _active_id = None
        _last_job_id = None
        _last_brief = None
        while True:
            try:
                _jobs_q.get_nowait()
            except queue.Empty:
                break
        while True:
            try:
                _announce_q.get_nowait()
            except queue.Empty:
                break


def _project_by_id(project_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not project_id:
        return None
    for item in list_projects():
        if item.get("id") == project_id:
            return item
    return None


def _allowed_path(path: Optional[str]) -> bool:
    if not path:
        return False
    resolved = str(Path(path).resolve())
    for item in list_projects():
        raw = item.get("path") or ""
        if not raw:
            continue
        try:
            if resolved == str(Path(raw).resolve()):
                return Path(resolved).is_dir()
        except OSError:
            continue
    return False


def _write_job(job: Dict[str, Any]) -> Path:
    path = job_dir() / f"{job['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def read_job(job_id: str) -> Optional[Dict[str, Any]]:
    path = job_dir() / f"{job_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _ensure_worker() -> None:
    global _worker_started
    with _lock:
        if _worker_started:
            return
        thread = threading.Thread(
            target=_worker_loop,
            name="angus-build",
            daemon=True,
        )
        thread.start()
        _worker_started = True


def _enqueue_job(job_id: str) -> None:
    if sync_jobs():
        _run_job(job_id)
        return
    _ensure_worker()
    _jobs_q.put(job_id)


def _worker_loop() -> None:
    while True:
        job_id = _jobs_q.get()
        if job_id is None:
            return
        try:
            _run_job(job_id)
        except Exception as exc:
            job = read_job(job_id) or {"id": job_id}
            job["status"] = "failed"
            job["error"] = str(exc)[:500]
            job["finished"] = datetime.now().isoformat()
            try:
                _write_job(job)
            except OSError:
                pass
            announce("That change did not finish. I logged the error.")


def build_prompt(job: Dict[str, Any]) -> str:
    name = job.get("project_name") or job.get("project") or "the project"
    path = job.get("path") or ""
    request = (job.get("request") or "").strip()
    prior = (job.get("prior_reply") or "").strip()
    extra = ""
    if prior:
        extra = (
            "\nRecent assistant suggestion (for context, not a spec):\n"
            f"{prior}\n"
        )
    return (
        "Ross confirmed this change. Do the work in this project only.\n"
        f"Project: {name}\n"
        f"Working directory: {path}\n\n"
        f"Request:\n{request}\n"
        f"{extra}\n"
        "Rules:\n"
        "- Stay inside this project directory.\n"
        "- Make the requested change. Do not expand scope.\n"
        "- Do not delete data, change firewalls, or modify credentials.\n"
        "- Do not ask questions; if blocked, say what blocked you.\n"
        "- When finished, reply with:\n"
        "  1) A spoken summary for Ross: 1-3 short sentences. "
        "Do not mention Grok, CLI, coding agents, terminals, or that anyone dispatched you.\n"
        "  2) A short list of files you changed.\n"
    )


def _parse_grok_stdout(stdout: str) -> Dict[str, Any]:
    text = (stdout or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = text.rfind("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {"text": text[-2000:]}


def spoken_from_report(text: str, *, project_name: str, ok: bool) -> str:
    raw = re.sub(r"\s+", " ", (text or "").strip())
    # Drop a "files changed" section if present so TTS stays short.
    for marker in ("Files:", "File list:", "Changed files:"):
        idx = raw.lower().find(marker.lower())
        if idx > 40:
            raw = raw[:idx].strip()
            break
    parts = [p.strip() for p in _SENTENCE.split(raw) if p.strip()]
    if not parts:
        if ok:
            return f"The {project_name} change is done."
        return f"The {project_name} change did not finish."
    summary = " ".join(parts[:3])
    if len(summary) > 260:
        summary = summary[:257].rsplit(" ", 1)[0] + "."
    banned = (
        "grok",
        "cli",
        "coding agent",
        "prompt-file",
        "headless",
    )
    lower = summary.lower()
    if any(word in lower for word in banned):
        if ok:
            return f"The {project_name} change is done."
        return f"The {project_name} change did not finish."
    return summary


def _invoke_grok(job: Dict[str, Any]) -> Dict[str, Any]:
    """Run Grok Build. Isolated for tests."""
    binary = grok_bin()
    cwd = str(job.get("path") or "")
    prompt_path = Path(job["prompt_file"])
    cmd = [
        binary,
        "--prompt-file",
        str(prompt_path),
        "--cwd",
        cwd,
        "--always-approve",
        "--output-format",
        "json",
        "--max-turns",
        str(grok_max_turns()),
        "--deny",
        "Bash(rm -rf*)",
        "--deny",
        "Bash(sudo*)",
    ]
    model = grok_model()
    if model:
        cmd.extend(["--model", model])
    env = os.environ.copy()
    env["HOME"] = env.get("HOME") or "/home/ross"
    env.setdefault(
        "PATH",
        "/home/ross/.local/bin:/home/ross/.grok/bin:/usr/local/bin:/usr/bin:/bin",
    )
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        cmd = ["sudo", "-u", "ross", "-E", "--"] + cmd
    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=grok_timeout(),
        cwd=cwd,
        env=env,
    )
    parsed = _parse_grok_stdout(completed.stdout)
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout[-8000:],
        "stderr": (completed.stderr or "")[-2000:],
        "parsed": parsed,
    }


def _run_job(job_id: str) -> None:
    global _active_id, _last_job_id
    with _lock:
        _active_id = job_id
        _last_job_id = job_id
    job = read_job(job_id)
    if not job:
        with _lock:
            if _active_id == job_id:
                _active_id = None
        return
    if not execute_enabled():
        job["status"] = "queued"
        job["note"] = "Execution is off. Job recorded only."
        job["finished"] = datetime.now().isoformat()
        _write_job(job)
        with _lock:
            if _active_id == job_id:
                _active_id = None
        return

    name = job.get("project_name") or "that project"
    path = job.get("path") or ""
    if not _allowed_path(path):
        job["status"] = "failed"
        job["error"] = "project path is missing or not in the allowlist"
        job["finished"] = datetime.now().isoformat()
        job["spoken"] = (
            f"I couldn't work on {name} because the project path is not available."
        )
        _write_job(job)
        announce(job["spoken"])
        with _lock:
            if _active_id == job_id:
                _active_id = None
        return

    prompt = build_prompt(job)
    prompt_path = job_dir() / f"{job_id}.prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    job["prompt_file"] = str(prompt_path)
    job["status"] = "running"
    job["started"] = datetime.now().isoformat()
    job["backend"] = grok_bin()
    _write_job(job)

    try:
        result = _invoke_grok(job)
    except subprocess.TimeoutExpired:
        job["status"] = "failed"
        job["error"] = "timeout"
        job["spoken"] = f"The {name} change ran too long and I stopped it."
        job["finished"] = datetime.now().isoformat()
        _write_job(job)
        announce(job["spoken"])
        with _lock:
            if _active_id == job_id:
                _active_id = None
        return
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)[:500]
        job["spoken"] = f"The {name} change failed to start."
        job["finished"] = datetime.now().isoformat()
        _write_job(job)
        announce(job["spoken"])
        with _lock:
            if _active_id == job_id:
                _active_id = None
        return

    parsed = result.get("parsed") or {}
    report = (
        (parsed.get("text") if isinstance(parsed, dict) else None)
        or result.get("stdout")
        or ""
    )
    rc = result.get("returncode")
    ok = rc == 0 and bool(str(report).strip())
    if isinstance(parsed, dict) and parsed.get("type") == "error":
        ok = False
        report = parsed.get("message") or report
    job["returncode"] = result.get("returncode")
    job["session_id"] = (parsed or {}).get("sessionId") if isinstance(parsed, dict) else ""
    job["report"] = str(report)[:8000]
    job["stderr"] = result.get("stderr") or ""
    job["status"] = "done" if ok else "failed"
    job["spoken"] = spoken_from_report(str(report), project_name=name, ok=ok)
    job["finished"] = datetime.now().isoformat()
    _write_job(job)
    announce(job["spoken"])
    with _lock:
        if _active_id == job_id:
            _active_id = None


def match_change(text: str) -> Optional[Dict[str, Any]]:
    t = text or ""
    if _DESTRUCTIVE.search(t):
        return {"request": t, "blocked": True}
    if _GROK_TALK.search(t) or _EXECUTE.search(t) or _STATUS.search(t):
        return None
    if re.search(
        r"\b(?:did you|are you|were you|just say|really do that)\b", t, re.I
    ):
        return None
    proj = resolve_project(t)
    if proj and _CHANGE.search(t):
        return {"request": t, "project": proj.get("id"), "blocked": False}
    if re.search(r"\b(?:make a change|edit the (?:code|app|site))\b", t, re.I):
        return {"request": t, "project": None, "blocked": False}
    return None


def match_execute(text: str) -> Optional[Dict[str, Any]]:
    t = text or ""
    if _DESTRUCTIVE.search(t):
        return {"request": t, "blocked": True, "from_brief": True}
    grok_ask = bool(_GROK_TALK.search(t)) and not _EXECUTE.search(t)
    if grok_ask:
        brief = last_brief()
        if not brief:
            return {"request": t, "ask_idle": True}
        return {
            "request": brief.get("request") or t,
            "project": brief.get("project"),
            "offer": True,
            "blocked": False,
        }
    if not _EXECUTE.search(t):
        return None
    brief = last_brief()
    if not brief:
        return {"request": t, "missing_brief": True, "from_brief": True}
    return {
        "request": brief.get("request") or t,
        "project": brief.get("project"),
        "prior_reply": brief.get("reply") or "",
        "from_brief": True,
        "blocked": False,
    }


def match_status(text: str) -> Optional[Dict[str, Any]]:
    t = text or ""
    if _GROK_TALK.search(t) and not _EXECUTE.search(t):
        return None
    if _STATUS.search(t):
        return {}
    return None


def request_change(
    request: str,
    project: Optional[str] = None,
    confirmed: bool = False,
    **kwargs: Any,
) -> ToolResult:
    if _DESTRUCTIVE.search(request or "") or kwargs.get("blocked"):
        return ToolResult(
            handled=True,
            spoken=(
                "That sounds destructive or security-sensitive. "
                "I won't do that from ordinary voice. Handle it from a terminal."
            ),
            tool="build.request_change",
            arguments={"request": request, "project": project},
            tier=RiskTier.TIER3,
            ok=False,
            error="tier3",
        )
    if kwargs.get("ask_idle"):
        return ToolResult(
            handled=True,
            spoken=(
                "I have not started a coding job. I only change Frigate or other "
                "projects after you confirm. Tell me what to send."
            ),
            tool="build.execute_last",
            arguments={"request": request},
        )
    if kwargs.get("missing_brief"):
        return ToolResult(
            handled=True,
            spoken="I don't have a change queued. Tell me what you want done.",
            tool="build.execute_last",
            arguments={"request": request},
        )
    if kwargs.get("offer"):
        kwargs = {**kwargs, "from_brief": False, "confirmed": False}

    proj = _project_by_id(project)
    if not proj:
        proj = resolve_project(request or "")
    remember_brief(request, (proj or {}).get("id"), reply=str(kwargs.get("prior_reply") or ""))

    if not proj:
        return ToolResult(
            handled=True,
            spoken=(
                "Which project should I change? "
                "Poker Director, SCC dashboard, Angus, or something else?"
            ),
            tool="build.request_change",
            arguments={"request": request, "project": None},
        )

    summary = f"{proj.get('name')}: {request.strip()}"
    from_brief = bool(kwargs.get("from_brief"))
    need_confirm = not confirmed and not from_brief
    if need_confirm:
        return ToolResult(
            handled=True,
            spoken=(
                f"I can change {proj.get('name')} for that. "
                "Do you want me to make the change?"
            ),
            tool="build.request_change",
            arguments={
                "request": request,
                "project": proj.get("id"),
                "prior_reply": kwargs.get("prior_reply") or "",
            },
            tier=RiskTier.TIER2,
            needs_confirm=True,
            pending_summary=summary,
        )

    job = {
        "id": uuid.uuid4().hex[:12],
        "project": proj.get("id"),
        "project_name": proj.get("name"),
        "path": proj.get("path"),
        "request": request,
        "prior_reply": kwargs.get("prior_reply") or "",
        "status": "accepted",
        "execute": execute_enabled(),
        "created": datetime.now().isoformat(),
    }
    _write_job(job)
    busy = has_active_jobs()
    _enqueue_job(job["id"])
    if not execute_enabled():
        spoken = (
            "I've recorded the change request. Unattended execution is "
            "not enabled, so it will not edit files by itself."
        )
    elif busy:
        spoken = (
            f"I'll queue that on {proj.get('name')} behind the change "
            "already in progress."
        )
    else:
        spoken = "I'll work on that and let you know."
    return ToolResult(
        handled=True,
        spoken=spoken,
        tool="build.request_change",
        arguments={"request": request, "project": job.get("project"), "job": job["id"]},
        tier=RiskTier.TIER2,
        data={"job": job["id"], "execute": execute_enabled()},
    )


def handle_match(**kwargs: Any) -> ToolResult:
    return request_change(
        request=str(kwargs.get("request") or ""),
        project=kwargs.get("project"),
        confirmed=bool(kwargs.get("confirmed")),
        blocked=bool(kwargs.get("blocked")),
        from_brief=bool(kwargs.get("from_brief")),
        missing_brief=bool(kwargs.get("missing_brief")),
        ask_idle=bool(kwargs.get("ask_idle")),
        offer=bool(kwargs.get("offer")),
        prior_reply=kwargs.get("prior_reply") or "",
    )


def handle_status(**_k: Any) -> ToolResult:
    with _lock:
        active = _active_id
        last = _last_job_id
    if active:
        job = read_job(active) or {}
        name = job.get("project_name") or "that project"
        return ToolResult(
            handled=True,
            spoken=f"Still working on {name}.",
            tool="build.job_status",
            data={"status": "running", "job": active},
        )
    if last:
        job = read_job(last) or {}
        spoken = job.get("spoken") or ""
        status = job.get("status") or "unknown"
        if spoken:
            return ToolResult(
                handled=True,
                spoken=spoken,
                tool="build.job_status",
                data={"status": status, "job": last},
            )
        name = job.get("project_name") or "that project"
        return ToolResult(
            handled=True,
            spoken=f"The last {name} change is {status}.",
            tool="build.job_status",
            data={"status": status, "job": last},
        )
    return ToolResult(
        handled=True,
        spoken="I'm not working on a code change right now.",
        tool="build.job_status",
        data={"status": "idle"},
    )


register(
    ToolSpec(
        name="build.request_change",
        description="Confirm, then run a development change in the project",
        tier=RiskTier.TIER2,
        handler=handle_match,
        match=match_change,
    )
)
register(
    ToolSpec(
        name="build.execute_last",
        description="Execute the last discussed change",
        tier=RiskTier.TIER2,
        handler=handle_match,
        match=match_execute,
    )
)
register(
    ToolSpec(
        name="build.job_status",
        description="Status of the in-flight or last code change",
        tier=RiskTier.TIER1,
        handler=handle_status,
        match=match_status,
    )
)

# Back-compat names used by older tests / docs.
JOB_DIR = Path(os.getenv("ANGUS_BUILD_JOB_DIR", "/home/ross/.local/share/glitch/jobs"))
EXECUTE = execute_enabled()
GROK = grok_bin()
