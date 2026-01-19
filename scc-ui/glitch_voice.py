import os
import wave
import time
import pyaudio
import subprocess
import json
import requests
from duckduckgo_search import DDGS

# Configuration
WAKE_WORD_PHRASE = "hey glitch"
SAMPLE_RATE = 16000
CHUNK_SIZE = 1024
SILENCE_DURATION = 2.0
GLITCH_VOICE = "en-AU-NatashaNeural"
OLLAMA_URL = "http://192.168.1.3:11434/api/generate"
OLLAMA_MODEL = "dolphin-llama3:8b"

audio = pyaudio.PyAudio()

def play_beep(freq=1000, duration=0.1):
    """Play audio feedback beep"""
    subprocess.run(f"speaker-test -t sine -f {freq} -l 1 & sleep {duration} && killall speaker-test 2>/dev/null", shell=True, stderr=subprocess.DEVNULL)

def speak_tts(text):
    """Speak text using Edge TTS"""
    try:
        text = text.replace('"', '\\"')
        cmd = f'edge-tts --voice "{GLITCH_VOICE}" --text "{text}" --write-media /tmp/glitch_response.mp3'
        subprocess.run(cmd, shell=True, check=True, timeout=30, stderr=subprocess.DEVNULL)
        subprocess.run('mpg123 -q /tmp/glitch_response.mp3 2>/dev/null', shell=True, check=True, timeout=60)
        subprocess.run('rm -f /tmp/glitch_response.mp3', shell=True)
    except Exception as e:
        print(f"TTS Error: {e}")

def transcribe_with_whisper(audio_file):
    """Transcribe using local Whisper"""
    try:
        result = subprocess.run(
            ['whisper', audio_file, '--model', 'base', '--language', 'en', '--output_format', 'txt'],
            capture_output=True,
            text=True,
            timeout=30
        )
        txt_file = audio_file.replace('.wav', '.txt')
        if os.path.exists(txt_file):
            with open(txt_file, 'r') as f:
                text = f.read().strip()
            os.remove(txt_file)
            return text
        return None
    except Exception as e:
        print(f"Whisper error: {e}")
        return None

def web_search(query):
    """Search the web using DuckDuckGo"""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
            if results:
                context = "\n".join([f"- {r['title']}: {r['body']}" for r in results])
                return context
    except Exception as e:
        print(f"Search error: {e}")
    return None

def needs_web_search(text):
    """Check if query needs current web information"""
    web_keywords = [
        'super bowl', 'nfl', 'playoff', 'score', 'game', 'match', 
        'weather', 'temperature', 'forecast', 'news', 'today', 'current',
        'latest', 'recent', 'now', 'stock', 'price', 'who won', 'results'
    ]
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in web_keywords)

def query_ollama(prompt, context=None):
    """Query Ollama with optional web context"""
    try:
        if context:
            full_prompt = f"Using this current information:\n{context}\n\nUser question: {prompt}\n\nProvide a concise answer."
        else:
            full_prompt = prompt
        
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": full_prompt,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_predict": 150
            }
        }
        
        response = requests.post(OLLAMA_URL, json=payload, timeout=30)
        if response.status_code == 200:
            return response.json().get('response', 'Sorry, I could not generate a response.')
        else:
            return "Sorry, I encountered an error."
    except Exception as e:
        print(f"Ollama error: {e}")
        return "Sorry, I'm having trouble."

def record_audio(duration=5):
    """Record audio"""
    stream = audio.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=SAMPLE_RATE,
        input=True,
        input_device_index=3,
        frames_per_buffer=CHUNK_SIZE
    )
    
    frames = []
    for _ in range(0, int(SAMPLE_RATE / CHUNK_SIZE * duration)):
        data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
        frames.append(data)
    
    stream.stop_stream()
    stream.close()
    
    audio_file = "/tmp/glitch_command.wav"
    with wave.open(audio_file, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(audio.get_sample_size(pyaudio.paInt16))
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b''.join(frames))
    
    return audio_file

def listen_for_wake_word():
    """Simple wake word detection"""
    print("👂 Listening for 'Hey Glitch'...")
    audio_file = record_audio(duration=3)
    text = transcribe_with_whisper(audio_file)
    
    if text and WAKE_WORD_PHRASE in text.lower():
        return True
    return False

def main_loop():
    """Main voice assistant loop"""
    print("🤖 Glitch Voice Assistant Started")
    print(f"Model: {OLLAMA_MODEL}")
    
    while True:
        try:
            if listen_for_wake_word():
                print("✨ Wake word detected!")
                play_beep(1000, 0.1)
                speak_tts("Yes?")
                
                print("🎤 Recording command...")
                audio_file = record_audio(duration=5)
                user_text = transcribe_with_whisper(audio_file)
                
                if user_text:
                    print(f"User: {user_text}")
                    
                    if needs_web_search(user_text):
                        print("🔍 Searching...")
                        speak_tts("Let me search for that")
                        web_context = web_search(user_text)
                        response = query_ollama(user_text, context=web_context)
                    else:
                        response = query_ollama(user_text)
                    
                    print(f"Glitch: {response}")
                    speak_tts(response)
                else:
                    speak_tts("Sorry, I didn't catch that.")
                
                play_beep(500, 0.1)
                print("💤 Going back to sleep...")
            
            time.sleep(0.5)
            
        except KeyboardInterrupt:
            print("\n👋 Shutting down...")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main_loop()
