cat > scripts/vlc_play.py <<'EOF'
import os, sys, json, subprocess, shutil
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

# Args: camera_name HH:MM(:SS optional)
if len(sys.argv) < 3:
    print("usage: vlc_play.py <camera_name> <HH:MM[:SS]>"); sys.exit(2)

cam_name = sys.argv[1]
t_str    = sys.argv[2]

# Convert HH:MM[:SS] -> seconds
parts = [int(x) for x in t_str.split(":")]
if len(parts) == 2: h,m = 0,parts[0]; s = parts[1]
else:               h,m,s = parts
start_seconds = h*3600 + m*60 + s

cfg_path = Path("alarm/data/cameras_ai.json")
camdb = json.loads(cfg_path.read_text())
cam = camdb[cam_name]

host = cam["host"]; ch = int(cam.get("channel",0))
user = cam.get("user") or os.getenv("REOLINK_USER","admin")
pw   = cam.get("password") or os.getenv("REOLINK_PASS","")

# NOTE: Adjust RTSP path if needed for your model/stream profile.
# Common Reolink paths: h264Preview_01_main or h264Preview_01_sub
# Channel offset +1 is typical for Reolink "01", "02", etc.
stream_idx = ch + 1
rtsp = f"rtsp://{user}:{pw}@{host}:554/h264Preview_{stream_idx:02d}_main"

# Find VLC (Linux or Windows path via WSL)
vlc = shutil.which("vlc") or "/mnt/c/Program Files/VideoLAN/VLC/vlc.exe"

# Launch VLC at requested timestamp
cmd = [vlc, "--start-time", str(start_seconds), rtsp]
subprocess.Popen(cmd)
print("Launched:", " ".join(cmd))
EOF
