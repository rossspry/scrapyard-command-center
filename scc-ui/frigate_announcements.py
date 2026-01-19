#!/usr/bin/env python3
import paho.mqtt.client as mqtt
import os
import subprocess
import time
import json
from datetime import datetime

MQTT_HOST = "localhost"
MQTT_PORT = 1883
GLITCH_VOICE = "en-AU-NatashaNeural"

# Cameras to monitor for greetings
GREETING_CAMERAS = ["facetag"]

# Track recent announcements to avoid spam
last_announcement = {}
COOLDOWN_SECONDS = 300  # 5 minutes between greetings

def speak_greeting(camera):
    """Use Glitch voice to announce (pre-generated audio)"""
    audio_files = {
        "facetag": "/srv/scc-ui/greeting_ross.mp3"
    }
    
    audio_file = audio_files.get(camera)
    
    if audio_file and os.path.exists(audio_file):
        try:
            # Play pre-generated greeting
            subprocess.run(f'mpg123 -q {audio_file} 2>/dev/null', shell=True, check=True, timeout=30)
            print(f"[{datetime.now()}] Played greeting for {camera}")
        except Exception as e:
            print(f"Playback Error: {e}")
    else:
        print(f"No audio file found: {audio_file}")

def on_connect(client, userdata, flags, rc):
    print(f"Connected to MQTT broker with result code {rc}")
    # Subscribe to Frigate events
    client.subscribe("frigate/events")
    print("Subscribed to frigate/events")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        
        # Debug: show all events
        event_type = payload.get('type')
        label = payload.get('after', {}).get('label')
        camera = payload.get('after', {}).get('camera', '')
        print(f"[DEBUG] Event: type={event_type}, label={label}, camera={camera}")
        
        # Check if it's a new person event
        if payload.get('type') == 'new' and payload.get('after', {}).get('label') == 'person':
            camera = payload.get('after', {}).get('camera', '')
            
            if camera in GREETING_CAMERAS:
                # Check cooldown
                now = time.time()
                if camera not in last_announcement or (now - last_announcement[camera]) > COOLDOWN_SECONDS:
                    print(f"[{datetime.now()}] Person detected on {camera}, announcing...")
                    speak_greeting(camera)
                    last_announcement[camera] = now
                else:
                    print(f"[{datetime.now()}] Person on {camera} (cooldown active)")
                    
    except Exception as e:
        print(f"Message processing error: {e}")

def main():
    print("🎤 Frigate Announcement Service Starting...")
    print(f"Monitoring cameras: {', '.join(GREETING_CAMERAS)}")
    
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_forever()

if __name__ == "__main__":
    main()
