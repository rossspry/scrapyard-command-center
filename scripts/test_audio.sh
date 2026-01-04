#!/usr/bin/env bash
set -euo pipefail

APLAY_BIN=$(command -v aplay || true)

if [ -z "$APLAY_BIN" ]; then
  echo "aplay not found. Install 'alsa-utils' to run the local audio test."
  exit 1
fi

temp_file=$(mktemp /tmp/scc-audio-XXXX.wav)
trap 'rm -f "$temp_file"' EXIT

python - <<'PY'
import math
import struct
import sys
import wave

sample_rate = 44100
frequency = 440
seconds = 1
with wave.open(sys.argv[1], "w") as wav_file:
    wav_file.setnchannels(1)
    wav_file.setsampwidth(2)
    wav_file.setframerate(sample_rate)
    for i in range(sample_rate * seconds):
        value = int(32767 * math.sin(2 * math.pi * frequency * i / sample_rate))
        wav_file.writeframes(struct.pack("<h", value))
PY "$temp_file"

"$APLAY_BIN" -q "$temp_file"

echo "Played a 1s test tone locally using $APLAY_BIN."
