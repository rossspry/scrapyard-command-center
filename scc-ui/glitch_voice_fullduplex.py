import os
import time
import subprocess
import requests
import threading
import queue
from ddgs import DDGS
from datetime import datetime
import webrtcvad
import collections
import wave
import json

# Configuration
SAMPLE_RATE = 16000
CAMERA_RTSP = "rtsp://scc:scc12345@192.168.1.42:554/h264Preview_01_main"
FRAME_DURATION = 30  # ms
VAD_AGGRESSIVENESS = 2  # 0-3, higher = more aggressive
GLITCH_VOICE = "en-AU-NatashaNeural"
OLLAMA_URL = "http://192.168.1.3:11434/api/generate"
OLLAMA_MODEL = "dolphin-llama3:8b"
TRANSCRIPTION_LOG = "/var/log/glitch_transcription.log"
FOLLOW_UP_WINDOW = 4

# Thread-safe queues
audio_queue = queue.Queue()
transcription_queue = queue.Queue()
is_speaking = threading.Event()

vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)

# Load context file
def load_context():
    try:
        with open('/srv/scc-ui/glitch_context.json', 'r') as f:
            return json.load(f)
    except:
        return {}

CONTEXT = load_context()


def log_transcription(text):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(TRANSCRIPTION_LOG, 'a') as f:
            f.write(f"[{timestamp}] {text}\n")
    except:
        pass

def continuous_audio_capture():
    """Continuously capture audio from camera RTSP stream"""
    print("🎥 Starting continuous audio capture from Facetag camera...")
    
    while True:
        try:
            cmd = [
                'ffmpeg', '-re',
                '-rtsp_transport', 'tcp',
                '-i', CAMERA_RTSP,
                '-vn',
                '-acodec', 'pcm_s16le',
                '-ar', str(SAMPLE_RATE),
                '-ac', '1',
                '-f', 's16le',
                'pipe:1'
            ]
            
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            
            frame_size = int(SAMPLE_RATE * FRAME_DURATION / 1000) * 2
            
            while True:
                audio_data = process.stdout.read(frame_size)
                if not audio_data:
                    break
                
                if not is_speaking.is_set():
                    audio_queue.put(audio_data)
            
            process.wait()
            
        except Exception as e:
            print(f"Audio capture error: {e}")
            time.sleep(2)

def voice_activity_detector():
    """Detect speech using WebRTC VAD and trigger transcription"""
    print("🎧 Voice activity detector started...")
    
    ring_buffer = collections.deque(maxlen=50)
    triggered = False
    voiced_frames = []
    
    while True:
        try:
            audio_data = audio_queue.get(timeout=0.1)
            
            is_speech = vad.is_speech(audio_data, SAMPLE_RATE)
            
            if not triggered:
                ring_buffer.append((audio_data, is_speech))
                num_voiced = len([f for f, speech in ring_buffer if speech])
                
                if num_voiced > 0.8 * ring_buffer.maxlen:
                    triggered = True
                    print("🎤 Speech detected, recording...", flush=True)
                    voiced_frames = [f for f, s in ring_buffer]
                    ring_buffer.clear()
            else:
                voiced_frames.append(audio_data)
                ring_buffer.append((audio_data, is_speech))
                num_unvoiced = len([f for f, speech in ring_buffer if not speech])
                
                if num_unvoiced > 0.9 * ring_buffer.maxlen:
                    triggered = False
                    print("✅ Speech ended, transcribing...", flush=True)
                    
                    audio_file = f"/tmp/glitch_{int(time.time())}.wav"
                    wf = wave.open(audio_file, 'wb')
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(SAMPLE_RATE)
                    wf.writeframes(b''.join(voiced_frames))
                    wf.close()
                    
                    transcription_queue.put(audio_file)
                    voiced_frames = []
                    ring_buffer.clear()
                    
        except queue.Empty:
            continue
        except Exception as e:
            print(f"VAD error: {e}")
            time.sleep(0.1)

def transcribe_with_whisper(audio_file):
    try:
        result = subprocess.run(
            ['whisper', audio_file, '--model', 'tiny', '--language', 'en', 
             '--output_format', 'txt', '--output_dir', '/tmp'],
            capture_output=True,
            text=True,
            timeout=15
        )
        
        txt_file = '/tmp/' + os.path.basename(audio_file).replace('.wav', '.txt')
        if os.path.exists(txt_file):
            with open(txt_file, 'r') as f:
                text = f.read().strip()
            os.remove(txt_file)
            return text
        return None
    except Exception as e:
        print(f"Whisper error: {e}")
        return None
    finally:
        if os.path.exists(audio_file):
            os.remove(audio_file)

def get_system_info(query):
    query_lower = query.lower()
    
    if 'what time' in query_lower or 'what is the time' in query_lower:
        return datetime.now().strftime("The current time is %I:%M %p")
    
    if 'what date' in query_lower or "what's the date" in query_lower:
        return datetime.now().strftime("Today is %A, %B %d, %Y")
    
    if 'what day' in query_lower and 'news' not in query_lower:
        return datetime.now().strftime("Today is %A")
    
    return None

def needs_web_search(text):
    text_lower = text.lower()
    # Don't web search for weather - we have dedicated API
    if any(w in text_lower for w in ['weather', 'temperature', 'forecast']):
        return False
    keywords = ['today', 'now', 'current', 'latest', 'recent', 'super bowl', 'nfl', 
                'nba', 'playoff', 'score', 'game', 'news', 'stock', 'price',
                'who is', 'what is', 'when did', 'who won']
    return any(kw in text_lower for kw in keywords)

def web_search(query):
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
            if results:
                return "\n".join([f"- {r['title']}: {r['body']}" for r in results])
    except Exception as e:
        print(f"Search error: {e}")
    return None


def get_weather(location_info):
    """Get weather using zip code"""
    try:
        zip_code = location_info.get('zip', '27054')
        city = location_info.get('city', 'Woodleaf')
        state = location_info.get('state', 'NC')
        
        # Use wttr.in service (no API key needed)
        import urllib.request
        url = f"https://wttr.in/{zip_code}?format=%C+%t+%h+%w&u"  # &u = imperial units
        response = urllib.request.urlopen(url, timeout=5)
        weather_data = response.read().decode('utf-8').strip()
        
        return f"Weather in {city}, {state}: {weather_data}"
    except Exception as e:
        print(f"Weather API error: {e}")
        return None

def query_ollama(prompt, context=None):
    try:
        # Build system prompt with context
        location = CONTEXT.get('location', {})
        user = CONTEXT.get('user', {})
        reminders = CONTEXT.get('reminders', [])
        
        system_prompt = f"""You are Glitch, AI assistant for {user.get('name', 'the user')}.
Location: {location.get('city')}, {location.get('state')}

CRITICAL RULES (follow EVERY time):
{chr(10).join(['- ' + r for r in reminders])}

Be helpful but BRIEF."""
        
        if context:
            full_prompt = f"{system_prompt}\n\nContext:\n{context}\n\nQuestion: {prompt}\n\nAnswer in 1-2 sentences max:"
        else:
            full_prompt = f"{system_prompt}\n\nUser: {prompt}\n\nGlitch (respond in 1-2 sentences):"
        
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": full_prompt,
            "stream": False,
            "options": {"temperature": 0.7, "num_predict": 40}
        }
        
        response = requests.post(OLLAMA_URL, json=payload, timeout=15)
        if response.status_code == 200:
            return response.json().get('response', 'Sorry, I could not respond.')
        return "Sorry, I encountered an error."
    except Exception as e:
        print(f"Ollama error: {e}")
        return "Sorry, I'm having trouble."

def speak_tts(text):
    try:
        is_speaking.set()
        text = text.replace('"', '\\"')
        subprocess.run(
            f'edge-tts --voice "{GLITCH_VOICE}" --text "{text}" --write-media /tmp/glitch_response.mp3',
            shell=True, check=True, timeout=15, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL
        )
        subprocess.run('mpg123 -q /tmp/glitch_response.mp3 2>/dev/null', shell=True, check=True, timeout=60)
        subprocess.run('rm -f /tmp/glitch_response.mp3', shell=True)
    except Exception as e:
        print(f"TTS Error: {e}")
    finally:
        is_speaking.clear()

def main_processor():
    """Process transcriptions and respond"""
    print("🧠 Main processor started...")
    
    conversation_mode = False
    conversation_timeout = 0
    
    while True:
        try:
            audio_file = transcription_queue.get(timeout=0.5)
            
            text = transcribe_with_whisper(audio_file)
            if not text or text.strip() in ['.', '']:
                continue
            
            print(f"📝 Heard: {text}", flush=True)
            log_transcription(text)
            
            text_lower = text.lower()
            has_wake_word = 'hey glitch' in text_lower or 'glitch' in text_lower
            
            in_conversation = conversation_mode and time.time() < conversation_timeout
            
            if not has_wake_word and not in_conversation:
                print("⏭️  (ignoring)\n", flush=True)
                continue
            
            if has_wake_word:
                for wake in ['hey glitch', 'glitch']:
                    if wake in text_lower:
                        idx = text_lower.index(wake)
                        text = text[idx + len(wake):].strip()
                        break
            
            if not text:
                continue
            
            print(f"💭 Processing: {text}", flush=True)
            
            response = get_system_info(text)
            
            # Check if it's a weather query
            if not response and any(w in text.lower() for w in ['weather', 'temperature', 'forecast']):
                weather_data = get_weather(CONTEXT.get('location', {}))
                if weather_data:
                    response = query_ollama(text, weather_data)
            
            if not response:
                context = None
                if needs_web_search(text):
                    print("🔍 Searching...", flush=True)
                    context = web_search(text)
                
                print("🧠 Thinking...", flush=True)
                response = query_ollama(text, context)
            
            print(f"💬 Glitch: {response}\n", flush=True)
            log_transcription(f"RESPONSE: {response}")
            
            speak_tts(response)
            
            conversation_mode = True
            conversation_timeout = time.time() + FOLLOW_UP_WINDOW
            
        except queue.Empty:
            if conversation_mode and time.time() >= conversation_timeout:
                conversation_mode = False
                print("💤 Conversation ended\n", flush=True)
            continue
        except Exception as e:
            print(f"Processor error: {e}")
            time.sleep(0.1)

def main():
    print("\n🤖 Glitch Full-Duplex Voice Assistant")
    print(f"🎥 Using Facetag camera (192.168.1.42)")
    print(f"📝 Logging to: {TRANSCRIPTION_LOG}\n")
    
    capture_thread = threading.Thread(target=continuous_audio_capture, daemon=True)
    vad_thread = threading.Thread(target=voice_activity_detector, daemon=True)
    processor_thread = threading.Thread(target=main_processor, daemon=True)
    
    capture_thread.start()
    vad_thread.start()
    processor_thread.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")

if __name__ == "__main__":
    main()
