import os
import time
import subprocess
import requests
import wave
from ddgs import DDGS
from datetime import datetime

# Configuration
SAMPLE_RATE = 16000
CAMERA_RTSP = "rtsp://scc:scc12345@192.168.1.42:554/h264Preview_01_main"
SILENCE_DURATION = 1.5
MAX_RECORDING_TIME = 10
GLITCH_VOICE = "en-AU-NatashaNeural"
OLLAMA_URL = "http://192.168.1.3:11434/api/generate"
OLLAMA_MODEL = "dolphin-llama3:8b"
TRANSCRIPTION_LOG = "/var/log/glitch_transcription.log"
FOLLOW_UP_WINDOW = 4

def log_transcription(text):
    """Log transcribed text with timestamp"""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(TRANSCRIPTION_LOG, 'a') as f:
            f.write(f"[{timestamp}] {text}\n")
    except Exception as e:
        print(f"Logging error: {e}")

def record_audio_from_camera(duration=5):
    """Extract audio from camera RTSP stream"""
    audio_file = f"/tmp/glitch_{int(time.time())}.wav"
    
    try:
        print(f"🎤 Recording from Facetag camera for {duration}s...", flush=True)
        
        # Use ffmpeg to extract audio from RTSP stream
        cmd = [
            'ffmpeg', '-y',
            '-rtsp_transport', 'tcp',
            '-i', CAMERA_RTSP,
            '-t', str(duration),
            '-vn',  # No video
            '-acodec', 'pcm_s16le',
            '-ar', str(SAMPLE_RATE),
            '-ac', '1',  # Mono
            audio_file
        ]
        
        result = subprocess.run(cmd, capture_output=True, timeout=duration+5)
        
        if os.path.exists(audio_file) and os.path.getsize(audio_file) > 1000:
            print("✅ Recording complete", flush=True)
            return audio_file
        else:
            print("❌ Recording failed or empty", flush=True)
            return None
            
    except Exception as e:
        print(f"Recording error: {e}")
        return None

def transcribe_with_whisper(audio_file):
    """Transcribe using Whisper"""
    try:
        print(f"🔄 Transcribing...", flush=True)
        result = subprocess.run(
            ['whisper', audio_file, '--model', 'base', '--language', 'en', 
             '--output_format', 'txt', '--output_dir', '/tmp'],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        txt_file = '/tmp/' + os.path.basename(audio_file).replace('.wav', '.txt')
        if os.path.exists(txt_file):
            with open(txt_file, 'r') as f:
                text = f.read().strip()
            os.remove(txt_file)
            os.remove(audio_file)
            return text
        return None
    except Exception as e:
        print(f"Whisper error: {e}")
        if os.path.exists(audio_file):
            os.remove(audio_file)
        return None

def get_system_info(query):
    """Get system information"""
    query_lower = query.lower()
    
    if ('what time' in query_lower or 'what is the time' in query_lower):
        return datetime.now().strftime("The current time is %I:%M %p")
    
    if ('what date' in query_lower or "what's the date" in query_lower or "today's date" in query_lower):
        return datetime.now().strftime("Today is %A, %B %d, %Y")
    
    if ('what day' in query_lower and 'news' not in query_lower and 'price' not in query_lower):
        return datetime.now().strftime("Today is %A")
    
    return None

def needs_web_search(text):
    """Check if query needs web search"""
    text_lower = text.lower()
    
    time_indicators = ['today', 'now', 'current', 'latest', 'recent', 'this week', 'this year']
    sports_keywords = ['super bowl', 'nfl', 'nba', 'mlb', 'playoff', 'score', 'game', 'who won']
    news_keywords = ['news', 'breaking', 'update', 'what happened']
    live_data = ['weather', 'temperature', 'stock', 'price', 'forecast']
    people_queries = ['who is', 'what is', 'when did', 'where is', 'how many']
    
    return any(kw in text_lower for kw in 
              time_indicators + sports_keywords + news_keywords + live_data + people_queries)

def web_search(query):
    """Search web for current info"""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
            if results:
                return "\n".join([f"- {r['title']}: {r['body']}" for r in results])
    except Exception as e:
        print(f"Search error: {e}")
    return None

def query_ollama(prompt, context=None):
    """Query Ollama"""
    try:
        system_prompt = """You are Glitch, a helpful AI assistant. Be concise and direct in your responses."""
        
        if context:
            full_prompt = f"{system_prompt}\n\nContext:\n{context}\n\nQuestion: {prompt}\n\nAnswer concisely:"
        else:
            full_prompt = f"{system_prompt}\n\nUser: {prompt}\n\nGlitch:"
        
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": full_prompt,
            "stream": False,
            "options": {"temperature": 0.7, "num_predict": 150}
        }
        
        response = requests.post(OLLAMA_URL, json=payload, timeout=30)
        if response.status_code == 200:
            return response.json().get('response', 'Sorry, I could not respond.')
        return "Sorry, I encountered an error."
    except Exception as e:
        print(f"Ollama error: {e}")
        return "Sorry, I'm having trouble."

def speak_tts(text):
    """Speak using Edge TTS"""
    try:
        text = text.replace('"', '\\"')
        subprocess.run(
            f'edge-tts --voice "{GLITCH_VOICE}" --text "{text}" --write-media /tmp/glitch_response.mp3',
            shell=True, check=True, timeout=30, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL
        )
        subprocess.run('mpg123 -q /tmp/glitch_response.mp3 2>/dev/null', shell=True, check=True, timeout=60)
        subprocess.run('rm -f /tmp/glitch_response.mp3', shell=True)
    except Exception as e:
        print(f"TTS Error: {e}")

def main():
    """Main loop"""
    print("\n🤖 Glitch Voice Assistant Started")
    print(f"🎥 Using Facetag camera audio (192.168.1.42)")
    print(f"📝 Logging to: {TRANSCRIPTION_LOG}\n")
    
    conversation_mode = False
    conversation_timeout = 0
    
    while True:
        try:
            # Determine recording duration
            if conversation_mode and time.time() < conversation_timeout:
                print(f"💬 Listening for follow-up ({int(conversation_timeout - time.time())}s)...", flush=True)
                duration = 4
            else:
                conversation_mode = False
                print("🎧 Listening for wake word...", flush=True)
                duration = 5
            
            # Record from camera
            audio_file = record_audio_from_camera(duration)
            if not audio_file:
                time.sleep(1)
                continue
            
            # Transcribe
            text = transcribe_with_whisper(audio_file)
            if not text or text.strip() == '.':
                continue
            
            print(f"📝 Heard: {text}", flush=True)
            log_transcription(text)
            
            # Check for wake word
            text_lower = text.lower()
            has_wake_word = 'hey glitch' in text_lower or 'glitch' in text_lower
            
            if not has_wake_word and not conversation_mode:
                print("⏭️  (not wake word, ignoring)\n", flush=True)
                continue
            
            # Remove wake word from command
            if has_wake_word:
                for wake in ['hey glitch', 'glitch']:
                    if wake in text_lower:
                        idx = text_lower.index(wake)
                        text = text[idx + len(wake):].strip()
                        break
            
            if not text:
                continue
            
            print(f"💭 Processing: {text}", flush=True)
            
            # Check system info first
            response = get_system_info(text)
            
            if not response:
                # Check web search
                context = None
                if needs_web_search(text):
                    print("🔍 Searching web...", flush=True)
                    context = web_search(text)
                
                # Query Ollama
                print("🧠 Thinking...", flush=True)
                response = query_ollama(text, context)
            
            print(f"💬 Glitch: {response}\n", flush=True)
            log_transcription(f"RESPONSE: {response}")
            
            # Speak response
            speak_tts(response)
            
            # Enter conversation mode
            conversation_mode = True
            conversation_timeout = time.time() + FOLLOW_UP_WINDOW
            
        except KeyboardInterrupt:
            print("\n👋 Shutting down...")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()
EOF
