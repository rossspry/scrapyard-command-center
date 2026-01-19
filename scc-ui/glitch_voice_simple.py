import os
import time
import subprocess
import requests
import audioop
import wave
import pyaudio
from ddgs import DDGS
from datetime import datetime

# Configuration
SAMPLE_RATE = 16000
CHUNK_SIZE = 1024
SILENCE_DURATION = 1.5  # Seconds of silence before stopping recording
SILENCE_THRESHOLD = 500  # Volume threshold (adjust if needed)
MAX_RECORDING_TIME = 10  # Maximum recording length in seconds
GLITCH_VOICE = "en-AU-NatashaNeural"
OLLAMA_URL = "http://192.168.1.3:11434/api/generate"
OLLAMA_MODEL = "dolphin-llama3:8b"
TRANSCRIPTION_LOG = "/var/log/glitch_transcription.log"
FOLLOW_UP_WINDOW = 4  # Seconds to wait for follow-up

audio = pyaudio.PyAudio()

def log_transcription(text):
    """Log transcribed text with timestamp"""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(TRANSCRIPTION_LOG, 'a') as f:
            f.write(f"[{timestamp}] {text}\n")
    except Exception as e:
        print(f"Logging error: {e}")

def is_silent(data):
    """Check if audio chunk is silent"""
    return audioop.rms(data, 2) < SILENCE_THRESHOLD

def record_audio():
    """Record audio when volume detected, stop after silence"""
    stream = audio.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK_SIZE
    )
    
    print("🎧 Listening for voice...", flush=True)
    
    frames = []
    silent_chunks = 0
    max_silent_chunks = int(SILENCE_DURATION * SAMPLE_RATE / CHUNK_SIZE)
    recording = False
    total_chunks = 0
    max_chunks = int(MAX_RECORDING_TIME * SAMPLE_RATE / CHUNK_SIZE)
    
    try:
        while total_chunks < max_chunks:
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            
            if not is_silent(data):
                if not recording:
                    print("🎤 Recording...", flush=True)
                    recording = True
                frames.append(data)
                silent_chunks = 0
                total_chunks += 1
            elif recording:
                frames.append(data)
                silent_chunks += 1
                total_chunks += 1
                
                if silent_chunks > max_silent_chunks:
                    print("✅ Recording complete", flush=True)
                    break
    
    except Exception as e:
        print(f"Recording error: {e}")
    finally:
        stream.stop_stream()
        stream.close()
    
    if not frames:
        return None
    
    # Save recording
    audio_file = f"/tmp/glitch_{int(time.time())}.wav"
    wf = wave.open(audio_file, 'wb')
    wf.setnchannels(1)
    wf.setsampwidth(audio.get_sample_size(pyaudio.paInt16))
    wf.setframerate(SAMPLE_RATE)
    wf.writeframes(b''.join(frames))
    wf.close()
    
    return audio_file

def transcribe_with_whisper(audio_file):
    """Transcribe using Whisper"""
    try:
        print(f"🔄 Transcribing {audio_file}...", flush=True)
        result = subprocess.run(
            ['whisper', audio_file, '--model', 'base', '--language', 'en', 
             '--output_format', 'txt', '--output_dir', '/tmp'],
            capture_output=False,  # Show output
            text=True,
            timeout=30
        )
        print(f"Whisper exit code: {result.returncode}", flush=True)
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
    print(f"📝 Logging to: {TRANSCRIPTION_LOG}")
    print(f"🔊 Silence threshold: {SILENCE_THRESHOLD}\n")
    
    conversation_mode = False
    conversation_timeout = 0
    
    while True:
        try:
            # Check if in follow-up window
            if conversation_mode and time.time() < conversation_timeout:
                print(f"💬 Listening for follow-up ({int(conversation_timeout - time.time())}s)...", flush=True)
            else:
                conversation_mode = False
            
            # Record audio
            audio_file = record_audio()
            if not audio_file:
                time.sleep(0.5)
                continue
            
            # Transcribe
            text = transcribe_with_whisper(audio_file)
            if not text:
                continue
            
            print(f"📝 Heard: {text}", flush=True)
            log_transcription(text)
            
            # Check for wake word (or if in conversation mode)
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
