from flask import Flask, request, send_file, g
import time
import os
import sys
import uuid
import soundfile as sf
import logging
import numpy as np
import re
import threading
import tempfile
import psutil
from kokoro import KPipeline

# --- Path setup for config import ---
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(script_dir))
sys.path.insert(0, project_root)

# --- Set up file-based logging ---
log_dir = os.path.join(script_dir, '..', '..', 'user', 'logs')
os.makedirs(log_dir, exist_ok=True)
log_file_path = os.path.join(log_dir, 'kokoro.log')

logging.basicConfig(
    filename=log_file_path,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Import config after logging setup ---
try:
    import config
    HOST = config.TTS_SERVER_HOST
    PORT = config.TTS_SERVER_PORT
    logger.info(f"Loaded TTS server config: {HOST}:{PORT}")
except Exception as e:
    logger.warning(f"Could not load config, using defaults: {e}")
    HOST = '0.0.0.0'
    PORT = 5012


# --- Cross-platform temp directory ---
def get_temp_dir():
    """Get optimal temp directory. Prefers /dev/shm (Linux RAM disk) for speed."""
    shm = '/dev/shm'
    if os.path.exists(shm) and os.access(shm, os.W_OK):
        return shm
    return tempfile.gettempdir()


# --- Constants ---
TEMP_DIR = get_temp_dir()
DEFAULT_VOICE = 'af_heart'
DEFAULT_SPEED = 1.0
AUDIO_SAMPLE_RATE = 24000

# --- Memory Management ---
MAX_MEMORY_GB = 3.0
MAX_REQUESTS = 500
request_count = 0

def check_memory():
    """Return True if memory exceeds limit."""
    try:
        process = psutil.Process(os.getpid())
        mem_gb = process.memory_info().rss / (1024**3)
        return mem_gb > MAX_MEMORY_GB
    except Exception as e:
        logger.error(f"Memory check failed: {e}")
        return False

def schedule_restart(reason: str):
    """Schedule graceful restart after current request completes."""
    logger.warning(f"Scheduling restart: {reason}")
    def _exit():
        time.sleep(1)
        logger.info("Exiting for restart...")
        os._exit(0)
    threading.Thread(target=_exit, daemon=True).start()

# --- App & Model Setup ---
app = Flask(__name__)
logger.info("Loading Kokoro model...")
pipeline = KPipeline(lang_code='a')
logger.info(f"Model loaded successfully! Using temp dir: {TEMP_DIR}")
os.makedirs(TEMP_DIR, exist_ok=True)


def clean_text(text):
    """Cleans text by removing think blocks, stripping HTML, and filtering characters."""
    # Stage 1: Remove thinking blocks
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<seed:think>.*?</seed:think>', '', text, flags=re.DOTALL)
    
    # Stage 2: Strip all HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    
    # Stage 3: Replace problematic punctuation
    text = re.sub(r'[—–―]|--', '. ', text)  # Em/en dashes and -- → period for TTS pause
    text = re.sub(r'…+', '.', text)
    text = re.sub(r'\.{3,}', '.', text)
    text = re.sub(r'[\u201C\u201D\u201E\u201A]', '"', text)  # Smart double quotes
    text = re.sub(r'[\u2018\u2019\u201B]', "'", text)  # Smart single quotes/apostrophes
    text = re.sub(r'[•·‧∙]', ' ', text)
    text = re.sub(r'[⁄∕]', '/', text)
    text = re.sub(r'[‹›«»]', '"', text)
    text = re.sub(r'\s+', ' ', text)
    
    # Stage 4: Character whitelist
    cleaned_text = re.sub(r"[^a-zA-Z0-9 .,?!'\"\-():;']", '', text)
    
    return cleaned_text.strip()


@app.route('/tts', methods=['POST'])
def generate_tts():
    """Generate text-to-speech from the provided text."""
    global request_count
    request_count += 1
    
    # Check memory/requests every 10 requests
    if request_count % 10 == 0:
        mem_exceeded = check_memory()
        req_exceeded = request_count >= MAX_REQUESTS
        
        if mem_exceeded or req_exceeded:
            reason = f"Memory: {mem_exceeded}, Requests: {request_count}/{MAX_REQUESTS}"
            schedule_restart(reason)
    
    if 'text' not in request.form:
        return {'error': 'No text provided'}, 400

    text = request.form['text']
    text_to_speak = clean_text(text)
    if not text_to_speak.strip():
        return {'error': 'Text is empty after filtering'}, 400

    voice = request.form.get('voice', DEFAULT_VOICE)
    try:
        speed = float(request.form.get('speed', DEFAULT_SPEED))
    except ValueError:
        speed = DEFAULT_SPEED

    generation_start = time.time()
    generator = pipeline(text_to_speak, voice=voice, speed=speed)
    audio_segments = [audio_segment for _, _, audio_segment in generator]
    
    if not audio_segments:
        logger.error(f"Failed to generate audio for text.")
        return {'error': 'Failed to generate audio'}, 500

    audio = np.concatenate(audio_segments) if len(audio_segments) > 1 else audio_segments[0]
    generation_time = time.time() - generation_start
    logger.info(f"Audio generation completed in {generation_time:.2f}s (request #{request_count})")

    file_uuid = uuid.uuid4().hex
    timestamp = int(time.time())
    file_path = os.path.join(TEMP_DIR, f'audio_{timestamp}_{file_uuid}.ogg')

    try:
        sf.write(file_path, audio, AUDIO_SAMPLE_RATE, format='OGG', subtype='VORBIS')
        g.file_to_delete = file_path
        
        response = send_file(
            file_path,
            mimetype='audio/ogg',
            as_attachment=True,
            download_name='tts_output.ogg'
        )
        return response
    except Exception as e:
        logger.error(f"Error processing audio file: {e}")
        return {'error': f'Server error: {str(e)}'}, 500


@app.after_request
def delete_file(response):
    """Delete the temporary file after sending the response."""
    if hasattr(g, 'file_to_delete'):
        file_path = g.file_to_delete
        if os.path.exists(file_path):
            try:
                time.sleep(0.1)
                os.remove(file_path)
            except Exception as e:
                logger.error(f"Error deleting file {file_path}: {e}")
    return response


@app.route('/health', methods=['GET'])
def health_check():
    """Health check with memory stats."""
    try:
        process = psutil.Process(os.getpid())
        mem_gb = process.memory_info().rss / (1024**3)
    except Exception:
        mem_gb = -1
    
    return {
        'status': 'ok',
        'model': 'loaded',
        'requests': request_count,
        'memory_gb': round(mem_gb, 2),
        'memory_limit_gb': MAX_MEMORY_GB,
        'temp_dir': TEMP_DIR
    }


def main():
    """Main server function."""
    logger.info(f"Starting Kokoro TTS server on {HOST}:{PORT}")
    logger.info(f"Memory limit: {MAX_MEMORY_GB}GB, Request limit: {MAX_REQUESTS}")
    logger.info(f"Temp directory: {TEMP_DIR}")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()