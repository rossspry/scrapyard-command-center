from fastapi import FastAPI, HTTPException
import os, requests

IP   = os.getenv("REOLINK_IP","192.168.1.31")
USER = os.getenv("REOLINK_USER","admin")
PASS = os.getenv("REOLINK_ADMIN_PASS","")
app = FastAPI()

def login():
    if not PASS: raise HTTPException(500,"REOLINK_ADMIN_PASS not set")
    r = requests.post(f"http://{IP}/cgi-bin/api.cgi?cmd=Login",
                      json=[{"cmd":"Login","param":{"User":{"userName":USER,"password":PASS}}}],
                      timeout=5).json()[0]
    if r.get("code")!=0: raise HTTPException(502,"login failed")
    return r["value"]["Token"]["name"]
@app.get("/api/camera/signpost/humans")
def humans(channel:int=0):
    t = login()
    r = requests.post(f"http://{IP}/cgi-bin/api.cgi?token={t}",
                      json=[{"cmd":"GetAiState","action":0,"param":{"channel":channel}}], timeout=5).json()[0]
    v = r.get("value",{})
    return {
        "people":  v.get("people",{}).get("alarm_state",0),
        "vehicle": v.get("vehicle",{}).get("alarm_state",0),
        "motion":  v.get("dog_cat",{}).get("alarm_state",0),
        "channel": v.get("channel",channel)
    }

@app.get("/api/camera/signpost/snapshot")
def snapshot(channel:int=0):
    t = login()
    # GET variant (your firmware’s happy path)
    url = f"http://{IP}/cgi-bin/api.cgi?cmd=Snap&channel={channel}&token={t}"
    r = requests.get(url, timeout=10)
    if r.headers.get("Content-Type","").startswith("image/"):
        # Return as a simple OK with size so the caller knows it worked
        return {"ok": True, "bytes": len(r.content), "channel": channel}
    # If not an image, bubble up a simple diagnostic
    return {"ok": False, "content_type": r.headers.get("Content-Type",""), "status": r.status_code}

@app.get("/api/camera/signpost/snapshot_save")
def snapshot_save(channel:int=0):
    t = login()
    url = f"http://{IP}/cgi-bin/api.cgi?cmd=Snap&channel={channel}&token={t}"
    r = requests.get(url, timeout=10)
    if not r.headers.get("Content-Type","").startswith("image/"):
        return {"ok": False, "content_type": r.headers.get("Content-Type",""), "status": r.status_code}
    import time, pathlib
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = pathlib.Path("./data/exports")/f"{IP.replace('.','-')}-ch{channel}-{ts}.jpg"
    out.write_bytes(r.content)
    return {"ok": True, "path": str(out), "bytes": len(r.content), "channel": channel}

@app.get("/api/camera/signpost/humans_phrase")
def humans_phrase(channel:int=0):
    data = humans(channel)
    if data["people"]:
        return {"phrase": f"Human detected on channel {channel}."}
    elif data["vehicle"]:
        return {"phrase": f"Vehicle detected on channel {channel}."}
    elif data["motion"]:
        return {"phrase": f"Motion detected on channel {channel}."}
    else:
        return {"phrase": f"Initiating scan of channel {channel}. Scan complete, nothing found."}
