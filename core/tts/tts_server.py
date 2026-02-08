from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import faulthandler
import json
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

# Dump Python traceback on SIGSEGV/SIGFPE/SIGABRT to stderr
faulthandler.enable()

# --- Path setup for config import ---
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(script_dir))
sys.path.insert(0, project_root)

# --- Set up logging to stderr (captured by ProcessManager into kokoro.log) ---
logging.basicConfig(
    stream=sys.stderr,
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
MAX_CONTENT_LENGTH = 1024 * 1024  # 1MB max request body
request_count = 0
request_count_lock = threading.Lock()
pipeline_lock = threading.Lock()

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

# --- Model Setup ---
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


def _json_response(handler, data, status=200):
    """Send a JSON response."""
    body = json.dumps(data).encode()
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Content-Length', str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _file_response(handler, file_path, mimetype='audio/ogg'):
    """Send a file as the response, then delete it."""
    try:
        with open(file_path, 'rb') as f:
            data = f.read()
        handler.send_response(200)
        handler.send_header('Content-Type', mimetype)
        handler.send_header('Content-Length', str(len(data)))
        handler.send_header('Content-Disposition', 'attachment; filename="tts_output.ogg"')
        handler.end_headers()
        handler.wfile.write(data)
    finally:
        try:
            os.remove(file_path)
        except Exception as e:
            logger.error(f"Error deleting temp file {file_path}: {e}")


class TTSHandler(BaseHTTPRequestHandler):
    """Handle TTS requests — POST /tts (JSON) and GET /health."""

    def log_message(self, format, *args):
        """Suppress default stderr logging — we use file-based logging."""
        pass

    def do_GET(self):
        if self.path == '/health':
            self._handle_health()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == '/tts':
            self._handle_tts()
        else:
            self.send_error(404)

    def _handle_health(self):
        try:
            process = psutil.Process(os.getpid())
            mem_gb = process.memory_info().rss / (1024**3)
        except Exception:
            mem_gb = -1

        _json_response(self, {
            'status': 'ok',
            'model': 'loaded',
            'requests': request_count,
            'memory_gb': round(mem_gb, 2),
            'memory_limit_gb': MAX_MEMORY_GB,
            'temp_dir': TEMP_DIR
        })

    def _handle_tts(self):
        global request_count
        with request_count_lock:
            request_count += 1
            current_count = request_count

        # Check memory/requests every 10 requests
        if current_count % 10 == 0:
            mem_exceeded = check_memory()
            req_exceeded = current_count >= MAX_REQUESTS

            if mem_exceeded or req_exceeded:
                reason = f"Memory: {mem_exceeded}, Requests: {current_count}/{MAX_REQUESTS}"
                schedule_restart(reason)

        # Read and parse JSON body
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > MAX_CONTENT_LENGTH:
                _json_response(self, {'error': 'Request body too large'}, 413)
                return
            body = self.rfile.read(content_length)
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            _json_response(self, {'error': 'Invalid JSON body'}, 400)
            return

        if 'text' not in data:
            _json_response(self, {'error': 'No text provided'}, 400)
            return

        text_to_speak = clean_text(data['text'])
        if not text_to_speak.strip():
            _json_response(self, {'error': 'Text is empty after filtering'}, 400)
            return

        voice = data.get('voice', DEFAULT_VOICE)
        try:
            speed = float(data.get('speed', DEFAULT_SPEED))
        except (ValueError, TypeError):
            speed = DEFAULT_SPEED

        generation_start = time.time()
        with pipeline_lock:
            generator = pipeline(text_to_speak, voice=voice, speed=speed)
            # Copy each segment to decouple from PyTorch tensor memory
            # Without copy, GC of generator tensors can free memory numpy still references → SIGSEGV
            audio_segments = [np.copy(seg) for _, _, seg in generator]
            del generator

        if not audio_segments:
            logger.error("Failed to generate audio for text.")
            _json_response(self, {'error': 'Failed to generate audio'}, 500)
            return

        audio = np.concatenate(audio_segments) if len(audio_segments) > 1 else audio_segments[0]
        del audio_segments  # Free segment list
        generation_time = time.time() - generation_start
        logger.info(f"Audio generated in {generation_time:.2f}s — shape={audio.shape} dtype={audio.dtype} (req #{request_count})")
        sys.stderr.flush()

        file_uuid = uuid.uuid4().hex
        timestamp = int(time.time())
        file_path = os.path.join(TEMP_DIR, f'audio_{timestamp}_{file_uuid}.ogg')

        try:
            logger.info(f"Encoding OGG/Opus to {file_path}...")
            sys.stderr.flush()
            sf.write(file_path, audio, AUDIO_SAMPLE_RATE, format='OGG', subtype='OPUS')
            logger.info(f"OGG/Opus written OK ({os.path.getsize(file_path)} bytes)")
            sys.stderr.flush()
            del audio  # Free numpy array before sending
            _file_response(self, file_path)
            logger.info("Response sent OK")
            sys.stderr.flush()
        except Exception as e:
            logger.error(f"Error processing audio file: {e}")
            _json_response(self, {'error': f'Server error: {str(e)}'}, 500)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTPServer that handles each request in a new thread."""
    daemon_threads = True


def main():
    """Main server function."""
    logger.info(f"Starting Kokoro TTS server on {HOST}:{PORT}")
    logger.info(f"Memory limit: {MAX_MEMORY_GB}GB, Request limit: {MAX_REQUESTS}")
    logger.info(f"Temp directory: {TEMP_DIR}")

    server = ThreadedHTTPServer((HOST, PORT), TTSHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("TTS server shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
