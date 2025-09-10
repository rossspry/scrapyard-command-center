#!/usr/bin/env bash
set -euo pipefail
API="${API_BASE:-http://127.0.0.1:8001}"
CH="${REOLINK_CHANNEL:-0}"
COOLDOWN="${COOLDOWN:-15}"   # seconds
LAST=0

speak() {
  msg="$1"
  # Try platform options; fallback to echo
  if command -v spd-say >/dev/null 2>&1; then spd-say "$msg" || true
  elif command -v say >/dev/null 2>&1; then say "$msg" || true
  elif command -v powershell.exe >/dev/null 2>&1; then powershell.exe -NoP -C "Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('$msg')" || true
  else echo "$msg"
  fi
}

while :; do
  now=$(date +%s)
  # get structured JSON
  j=$(curl -s "${API}/api/camera/signpost/humans?channel=${CH}")
  people=$(echo "$j" | jq -r '.people // 0' 2>/dev/null || echo 0)

  if [ "$people" -ge 1 ] && [ $((now - LAST)) -ge "$COOLDOWN" ]; then
    phrase=$(curl -s "${API}/api/camera/signpost/humans_phrase?channel=${CH}" | jq -r '.phrase // "Human detected."')
    echo "$(date -Iseconds) ALERT: $phrase"
    speak "$phrase"
    LAST="$now"
  else
    echo "$(date -Iseconds) idle: p=$people"
  fi

  sleep 2
done
