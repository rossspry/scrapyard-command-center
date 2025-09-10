cat > scripts/wake_listener.py <<'EOF'
import os, re, queue, threading, time, subprocess
import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
from openwakeword.model import Model

load_dotenv()
WAKE = os.getenv("WAKE_WORD", "hey scrapyard")  # say this to trigger
SAMPLE_RATE = 16000
BLOCK_DUR   = 0.5    # seconds per inference block
CHANNELS    = 1

# Serialized job queue (one-at-a-time)
jobs = queue.Queue()

def tts_say(text: str):
    subprocess.Popen(["python3", "scripts/tts.py", text])

def handle_command(cmd: str):
    """Very simple parser for: play camera X at HH:MM[:SS]"""
    m = re.search(r"play camera (\d+)\s+at\s+(\d{1,2}:\d{2}(?::\d{2})?)", cmd, re.I)
    if m:
        cam_num = int(m.group(1))
        ts      = m.group(2)
        # Map camera number -> camera name in your JSON
        # e.g., 1 -> "signpost"; adjust mapping as you add more.
        name = {1:"signpost"}.get(cam_num)
        if not name:
            tts_say(f"I don't know camera {cam_num} yet.")
            return
        subprocess.Popen(["python3","scripts/vlc_play.py", name, ts])
        tts_say(f"Playing camera {cam_num} at {ts}.")
    else:
        tts_say("I heard you, but I didn't understand the command.")

def worker():
    while True:
        fn, arg = jobs.get()
        try:
            fn(arg)
        finally:
            jobs.task_done()

threading.Thread(target=worker, daemon=True).start()

# Wake word model (bundled free models, low-latency)
oww = Model()

buf = np.zeros(int(SAMPLE_RATE*BLOCK_DUR), dtype=np.float32)
triggered = False
last_trigger = 0

def on_audio(indata, frames, time_info, status):
    global triggered, last_trigger, buf
    if status: return
    mono = indata[:,0] if indata.ndim > 1 else indata
    # resample if needed
    if sd.default.samplerate != SAMPLE_RATE:
        # naive resample
        ratio = SAMPLE_RATE/float(sd.default.samplerate)
        idx = (np.arange(int(len(mono)*ratio)) / ratio).astype(np.int32)
        mono = mono[np.clip(idx,0,len(mono)-1)]
    # accumulate then run detector per block
    buf = np.concatenate([buf, mono]).astype(np.float32)
    need = int(SAMPLE_RATE*BLOCK_DUR)
    while len(buf) >= need:
        block, buf = buf[:need], buf[need:]
        scores = oww.predict(block)
        # Trigger if any model crosses threshold
        if any(v > 0.5 for v in scores.values()):
            now = time.time()
            if not triggered or (now - last_trigger) > 2.0:
                triggered = True
                last_trigger = now
                jobs.put((lambda _: tts_say("Yes?"), None))

# Simple stdin loop for post-wake voice “commands” typed as text for now.
# Later we can add speech-to-text; for now we keep it reliable.
def stdin_loop():
    while True:
        try:
            line = input().strip()
        except EOFError:
            break
        if not line: 
            continue
        jobs.put((handle_command, line))

if __name__ == "__main__":
    # Start mic stream
    sd.default.samplerate = 16000
    sd.default.channels = CHANNELS
    with sd.InputStream(channels=CHANNELS, callback=on_audio):
        tts_say("Wake word listener armed.")
        stdin_loop()
EOF
