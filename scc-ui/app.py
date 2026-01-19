from flask import Flask, render_template, jsonify, request, send_from_directory
import subprocess
import os
import threading
import paho.mqtt.client as mqtt



app = Flask(__name__)

@app.route('/control')
def control_panel():
    return send_from_directory('static', 'control-panel.html')


# Global voice setting
GLITCH_VOICE = "en-AU-NatashaNeural"

def run_command(cmd):
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        return {"success": True, "output": result.stdout, "error": result.stderr}
    except Exception as e:
        return {"success": False, "error": str(e)}

def run_command_terminal(cmd):
    """Run command with longer timeout and combined output"""
    try:
        result = subprocess.run(
            cmd, 
            shell=True, 
            capture_output=True, 
            text=True, 
            timeout=60,
            cwd='/home/ross'
        )
        # Combine stdout and stderr
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += result.stderr
        return {"success": True, "output": output, "returncode": result.returncode}
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "Command timed out after 60 seconds"}
    except Exception as e:
        return {"success": False, "output": str(e)}

def play_tts_sync(text, voice):
    try:
        # Generate TTS
        cmd = f'edge-tts --voice "{voice}" --text "{text}" --write-media /tmp/glitch_announcement.mp3'
        subprocess.run(cmd, shell=True, check=True, timeout=30)
        
        # Play audio
        subprocess.run('mpg123 -q /tmp/glitch_announcement.mp3', shell=True, check=True, timeout=30)
        
        # Cleanup
        subprocess.run('rm -f /tmp/glitch_announcement.mp3', shell=True)
    except Exception as e:
        print(f"TTS Error: {e}")

# MQTT callback for person detection
def on_message(client, userdata, msg):
    try:
        import json
        payload = json.loads(msg.payload.decode())
        
        # Check if it's a new person event on gate cameras
        if payload.get('type') == 'new' and payload.get('after', {}).get('label') == 'person':
            camera = payload.get('after', {}).get('camera', '')
            
            if camera in ['front_gate', 'signpost']:
                # Play announcement in background thread
                threading.Thread(target=play_tts_sync, args=("Someone is at the gate", GLITCH_VOICE)).start()
    except Exception as e:
        print(f"MQTT Error: {e}")

# Start MQTT client in background
def start_mqtt_listener():
    client = mqtt.Client()
    client.on_message = on_message
    
    try:
        client.connect("127.0.0.1", 1883, 60)
        client.subscribe("frigate/events")
        client.loop_forever()
    except Exception as e:
        print(f"MQTT Connection Error: {e}")

# Start MQTT in background thread
mqtt_thread = threading.Thread(target=start_mqtt_listener, daemon=True)
mqtt_thread.start()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/terminal', methods=['POST'])
def terminal_execute():
    cmd = request.json.get('command', '')
    if not cmd:
        return jsonify({"success": False, "output": "No command provided"})
    
    result = run_command_terminal(cmd)
    return jsonify(result)

@app.route('/api/service/<action>/<service>')
def service_control(action, service):
    if service == 'frigate':
        if action == 'start':
            return jsonify(run_command('docker start frigate'))
        elif action == 'stop':
            return jsonify(run_command('docker stop frigate'))
        elif action == 'restart':
            return jsonify(run_command('docker restart frigate'))
        elif action == 'status':
            return jsonify(run_command('docker ps | grep frigate'))
    
    elif service == 'homeassistant':
        if action == 'start':
            return jsonify(run_command('docker start homeassistant'))
        elif action == 'stop':
            return jsonify(run_command('docker stop homeassistant'))
        elif action == 'restart':
            return jsonify(run_command('docker restart homeassistant'))
        elif action == 'status':
            return jsonify(run_command('docker ps | grep homeassistant'))
    
    elif service == 'whisper':
        if action == 'start':
            return jsonify(run_command('docker start whisper'))
        elif action == 'stop':
            return jsonify(run_command('docker stop whisper'))
        elif action == 'restart':
            return jsonify(run_command('docker restart whisper'))
        elif action == 'status':
            return jsonify(run_command('docker ps | grep whisper'))
    
    return jsonify({"success": False, "error": "Unknown service or action"})

@app.route('/api/system/<action>')
def system_control(action):
    if action == 'shutdown':
        return jsonify(run_command('sudo shutdown -h now'))
    elif action == 'reboot':
        return jsonify(run_command('sudo reboot'))
    elif action == 'backup':
        return jsonify(run_command('bash /srv/scc-ui/backup.sh'))
    elif action == 'git-pull':
        return jsonify(run_command('cd /home/ross/scrapyard-command-center && git pull'))
    elif action == 'git-push':
        return jsonify(run_command('cd /home/ross/scrapyard-command-center && git add -A && git commit -m "Auto commit" && git push'))
    elif action == 'tailscale-status':
        return jsonify(run_command('tailscale status'))
    return jsonify({"success": False, "error": "Unknown action"})

@app.route('/api/announce', methods=['POST'])
def announce():
    text = request.json.get('text', '')
    voice = request.json.get('voice', GLITCH_VOICE)
    
    # Play announcement in background thread
    threading.Thread(target=play_tts_sync, args=(text, voice)).start()
    
    return jsonify({"success": True, "output": "Announcement playing"})

@app.route('/api/stats')
def get_stats():
    cpu = run_command("top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1")
    mem = run_command("free -m | awk 'NR==2{printf \"%.2f\", $3*100/$2 }'")
    disk = run_command("df -h / | awk 'NR==2{print $5}' | cut -d'%' -f1")
    
    return jsonify({
        "cpu": cpu.get("output", "N/A").strip(),
        "memory": mem.get("output", "N/A").strip(),
        "disk": disk.get("output", "N/A").strip()
    })


@app.route('/api/glitch/status')
def glitch_status():
    result = subprocess.run(['systemctl', 'is-active', 'glitch-voice'], capture_output=True, text=True)
    return jsonify({'status': result.stdout.strip(), 'active': result.stdout.strip() == 'active'})

@app.route('/api/glitch/start', methods=['POST'])
def glitch_start():
    subprocess.run(['sudo', 'systemctl', 'start', 'glitch-voice'])
    return jsonify({'success': True})

@app.route('/api/glitch/stop', methods=['POST'])
def glitch_stop():
    subprocess.run(['sudo', 'systemctl', 'stop', 'glitch-voice'])
    return jsonify({'success': True})

@app.route('/api/homeassistant/porch-lights', methods=['POST'])
def toggle_porch_lights():
    data = request.json
    state = data.get('state', 'off')  # 'on' or 'off'
    
    # Toggle both porch lights in Home Assistant
    ha_url = "http://localhost:8123/api/services/light/turn_" + state
    headers = {"Authorization": "Bearer YOUR_HA_TOKEN"}  # TODO: Add HA token
    
    lights = ["light.scrapyard_porch_light", "light.small_porch_light"]
    
    for light in lights:
        try:
            requests.post(ha_url, headers=headers, json={"entity_id": light})
        except Exception as e:
            print(f"HA error: {e}")
    
    return jsonify({'success': True, 'state': state})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
