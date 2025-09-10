import os, subprocess, shlex, uuid, pathlib, json
import os
from dotenv import load_dotenv
load_dotenv()
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from datetime import datetime

router = APIRouter(prefix="/api/camera", tags=["camera"])

DATA_FILE = pathlib.Path("/data/cameras.json")
EXPORT_DIR = pathlib.Path(os.getenv("EXPORT_DIR", "/data/exports"))
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

# global state for cameras
CAMERAS = {}

def load_cameras():
    """Load cameras.json into the global CAMERAS dict."""
    global CAMERAS
    if DATA_FILE.exists():
        try:
            CAMERAS = json.loads(DATA_FILE.read_text())
            if not isinstance(CAMERAS, dict):
                CAMERAS = {}
        except Exception:
            CAMERAS = {}
    else:
        CAMERAS = {}
    return CAMERAS

# initial load at import
load_cameras()

def _ffmpeg_installed() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return True
    except FileNotFoundError:
        return False

@router.get("/list")
def list_cameras():
    return {"cameras": list(CAMERAS.keys())}

@router.post("/reload")
def reload_cameras():
    cams = load_cameras()
    return {"ok": True, "count": len(cams), "cameras": list(cams.keys())}

@router.get("/snapshot")
def snapshot(camera: str = Query(..., description="Camera name from /api/camera/list")):
    if camera not in CAMERAS:
        raise HTTPException(status_code=404, detail="Unknown camera")
    if not _ffmpeg_installed():
        raise HTTPException(status_code=500, detail="ffmpeg not installed")
    url = CAMERAS[camera]
    outfile = EXPORT_DIR / f"snapshot_{camera}_{uuid.uuid4().hex}.jpg"
    cmd = f'ffmpeg -y -rtsp_transport tcp -i "{url}" -frames:v 1 -q:v 2 "{outfile}"'
    subprocess.run(shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if not outfile.exists():
        raise HTTPException(status_code=500, detail="Snapshot failed. Check URL/credentials.")
    return FileResponse(path=str(outfile), media_type="image/jpeg", filename=outfile.name)

@router.get("/export")
def export_clip(
    camera: str,
    start: str = Query(..., description="UTC ISO8601, e.g., 2025-09-09T20:00:00Z"),
    duration_sec: int = Query(20, ge=1, le=3600)
):
    if camera not in CAMERAS:
        raise HTTPException(status_code=404, detail="Unknown camera")
    if not _ffmpeg_installed():
        raise HTTPException(status_code=500, detail="ffmpeg not installed")
    try:
        s = start.replace("Z", "+00:00")
        datetime.fromisoformat(s)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid start datetime")
    url = CAMERAS[camera]
    outfile = EXPORT_DIR / f"export_{camera}_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}_{duration_sec}s.mp4"
    cmd = f'ffmpeg -y -rtsp_transport tcp -i "{url}" -t {duration_sec} -c copy "{outfile}"'
    subprocess.run(shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if not outfile.exists():
        raise HTTPException(status_code=500, detail="Export failed. Some devices do not support RTSP backseek; use NVR playback API for historical export.")
    return {"file": f"/api/camera/download/{outfile.name}"}

@router.get("/download/{filename}")
def download_file(filename: str):
    fp = EXPORT_DIR / filename
    if not fp.exists():
        raise HTTPException(status_code=404, detail="Not found")
    media = "video/mp4" if fp.suffix == ".mp4" else "application/octet-stream"
    return FileResponse(path=str(fp), media_type=media, filename=fp.name)

from fastapi import HTTPException, Query
from pydantic import BaseModel
from pathlib import Path
import os, json

from app.reolink_core import poll_ai_state, log_event

DATA_FILE = Path(os.getenv("CAMERA_DB", "/data/cameras_ai.json"))

def load_cameras():
    if DATA_FILE.exists():
        try:
            cams = json.loads(DATA_FILE.read_text())
            if isinstance(cams, dict):
                return cams
        except Exception:
            pass
    return {}

class ScanRequest(BaseModel):
    host: str
    user: str
    password: str
    channel: int = 0
    log: bool = True

@router.get("/{camera_id}/ai")
def get_ai_state(camera_id: str, channel: int = Query(0)):
    cams = load_cameras()
    cam = cams.get(camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found")

    host = cam["host"]
    user = cam.get("user", "admin")
    password = os.getenv("REOLINK_PASS", cam.get("password", ""))
    ch = channel if channel is not None else cam.get("channel", 0)

    try:
        res = poll_ai_state(host, user, password, ch)
        log_event(host, ch, res)
        return {"camera": camera_id, "host": host, "channel": ch, **res}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI poll failed: {e}")

@router.post("/scan")
def scan_arbitrary(req: ScanRequest):
    try:
        res = poll_ai_state(req.host, req.user, req.password, req.channel)
        if req.log:
            log_event(req.host, req.channel, res)
        return {"host": req.host, "channel": req.channel, **res}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI poll failed: {e}")

@router.get("/{camera_id}/humans")
def humans_scan(camera_id: str, channel: int = Query(0)):
    """
    Quick check: returns humans=0/1 based on AI 'people' alarm.
    Also logs the event like the /ai route.
    """
    cams = load_cameras()
    cam = cams.get(camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found")

    host = cam["host"]
    user = cam.get("user", "admin")
    password = os.getenv("REOLINK_PASS", cam.get("password", ""))
    ch = channel if channel is not None else cam.get("channel", 0)

    res = poll_ai_state(host, user, password, ch)
    log_event(host, ch, res)

    return {
        "camera": camera_id,
        "host": host,
        "channel": ch,
        "humans": 1 if res.get("people", 0) else 0,
        **res
    }

@router.get("/{camera_id}/humans_phrase")
def humans_phrase(camera_id: str, channel: int = Query(0)):
    """
    Returns a simple spoken-style phrase you can TTS:
    "Initiating scan of <camera_id>... Scan complete, no humans found."
    """
    cams = load_cameras()
    cam = cams.get(camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found")

    host = cam["host"]
    user = cam.get("user", "admin")
    password = os.getenv("REOLINK_PASS", cam.get("password", ""))
    ch = channel if channel is not None else cam.get("channel", 0)

    res = poll_ai_state(host, user, password, ch)
    log_event(host, ch, res)

    humans = 1 if res.get("people", 0) else 0
    camname = camera_id.replace("_", " ")
    if humans:
        phrase = f"Initiating scan of {camname}. Scan complete. Human detected."
    else:
        phrase = f"Initiating scan of {camname}. Scan complete, no humans found."

    return {
        "camera": camera_id,
        "channel": ch,
        "humans": humans,
        "phrase": phrase,
        **res
    }
