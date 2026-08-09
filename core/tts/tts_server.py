from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import faulthandler
import json
import socket
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
    if sys.platform == 'linux':
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

# --- CPU threading tuning (must happen before model load) ---
# Inter-op threads: Kokoro's forward pass is sequential (ALBERT → predictor
# → decoder), so high inter-op parallelism just wastes threads on
# coordination. 2 is enough for any internal parallel ops.
# Intra-op threads: set via OMP_NUM_THREADS env var from sapphire.py's
# env_callback (auto-tunes to 1.5x physical cores). PyTorch reads it on
# import, so by the time we get here it's already applied.
import torch as _torch
try:
    _torch.set_num_interop_threads(2)
except RuntimeError:
    pass  # already set (e.g. re-import or second init)
logger.info(f"Torch threads: intra={_torch.get_num_threads()}, "
            f"inter={_torch.get_num_interop_threads()}")

# --- Model Setup ---
# Device selection: KOKORO_DEVICE setting ('cuda' or 'cpu'). Default cuda.
# CPU mode frees ~1-2GB VRAM for other models (Whisper, embeddings).
# CUDA mode is much faster but requires a CUDA-capable GPU + working driver.
#
# Fallback chain for safe public-release:
#   1. cuda requested + cuda available + KPipeline(device='cuda') works → cuda
#   2. cuda requested but cuda not available → cpu (with warning)
#   3. cuda init throws (driver mismatch, OOM, etc) → cpu (with warning)
#   4. cpu requested → cpu (always works)
#   5. Old KPipeline version without device kwarg → library auto-detect
try:
    import config as _cfg
    _kokoro_device = (getattr(_cfg, 'KOKORO_DEVICE', 'cuda') or 'cuda').strip().lower()
except Exception:
    _kokoro_device = 'cuda'
if _kokoro_device not in ('cuda', 'cpu'):
    logger.warning(f"KOKORO_DEVICE='{_kokoro_device}' unrecognized — using cuda")
    _kokoro_device = 'cuda'

# Pre-flight: if cuda requested but torch reports unavailable, demote to cpu
# BEFORE attempting init. Saves a wasted exception cycle and gives a cleaner
# log message that points users at the right cause.
if _kokoro_device == 'cuda':
    try:
        import torch as _torch_check
        if not _torch_check.cuda.is_available():
            logger.warning(
                "KOKORO_DEVICE='cuda' but torch.cuda.is_available()=False "
                "(no GPU, broken driver, or CUDA libs missing) — using cpu"
            )
            _kokoro_device = 'cpu'
    except Exception as e:
        logger.warning(f"CUDA availability check failed ({e}) — using cpu")
        _kokoro_device = 'cpu'

logger.info(f"Loading Kokoro model on device='{_kokoro_device}'...")
pipeline = None
try:
    pipeline = KPipeline(lang_code='a', device=_kokoro_device)
except TypeError:
    # Older kokoro versions don't accept device= kwarg — fall back to
    # auto-detect (respects CUDA_VISIBLE_DEVICES env if set by parent).
    logger.warning("KPipeline doesn't accept device kwarg — using library default")
    try:
        pipeline = KPipeline(lang_code='a')
    except Exception as e:
        logger.error(f"Kokoro init failed even in auto-detect mode: {e}")
        raise
except Exception as e:
    # CUDA init can fail at runtime even when torch.cuda.is_available() returns
    # True (driver/lib mismatch, OOM, device-context corruption). Fall back to
    # cpu so TTS still works.
    if _kokoro_device == 'cuda':
        logger.warning(f"Kokoro CUDA init failed ({e}) — falling back to cpu")
        try:
            pipeline = KPipeline(lang_code='a', device='cpu')
            _kokoro_device = 'cpu'
        except Exception as e2:
            logger.error(f"Kokoro init failed on both cuda and cpu: {e2}")
            raise
    else:
        # cpu init failed — nothing left to fall back to
        logger.error(f"Kokoro init failed on cpu: {e}")
        raise
logger.info(f"Model loaded successfully on device='{_kokoro_device}'. Using temp dir: {TEMP_DIR}")
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


def _apply_pitch(audio, pitch):
    """Resample-shift PCM before encoding. Same math as the legacy client-side
    shift (np.interp to length/pitch, played at the original rate) so the two
    paths sound identical. pitch=1.0 or bad input → unchanged."""
    try:
        pitch = float(pitch)
    except (TypeError, ValueError):
        return audio
    if pitch == 1.0 or pitch <= 0:
        return audio
    pitch = max(0.5, min(2.0, pitch))
    try:
        n = len(audio)
        indices = np.linspace(0, n - 1, int(n / pitch))
        return np.interp(indices, np.arange(n), audio).astype(audio.dtype)
    except Exception as e:
        logger.error(f"Pitch shift failed (pitch={pitch}): {e}")
        return audio


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
        elif self.path == '/tts/stream':
            self._handle_tts_stream()
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
            'device': _kokoro_device,
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

        voice = data.get('voice') or DEFAULT_VOICE
        try:
            speed = float(data.get('speed', DEFAULT_SPEED))
        except (ValueError, TypeError):
            speed = DEFAULT_SPEED
        pitch = data.get('pitch', 1.0)

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
        audio = _apply_pitch(audio, pitch)
        del audio_segments  # Free segment list
        generation_time = time.time() - generation_start
        logger.info(f"Audio generated in {generation_time:.2f}s — shape={audio.shape} dtype={audio.dtype} (req #{request_count})")
        sys.stderr.flush()

        file_uuid = uuid.uuid4().hex
        timestamp = int(time.time())
        file_path = os.path.join(TEMP_DIR, f'audio_{timestamp}_{file_uuid}.ogg')

        # _file_response has its own finally to delete the file on the success
        # path. But if sf.write raises, _file_response never runs and the temp
        # file may exist as a partial write. TEMP_DIR is /dev/shm on Linux
        # (tmpfs = RAM), so per-failure leaks bloat RAM, not disk. Belt-and-
        # suspenders cleanup here. Longevity scout 2026-05-07.
        sent = False
        try:
            logger.info(f"Encoding OGG/Opus to {file_path}...")
            sys.stderr.flush()
            sf.write(file_path, audio, AUDIO_SAMPLE_RATE, format='OGG', subtype='OPUS')
            logger.info(f"OGG/Opus written OK ({os.path.getsize(file_path)} bytes)")
            sys.stderr.flush()
            del audio  # Free numpy array before sending
            _file_response(self, file_path)
            sent = True
            logger.info("Response sent OK")
            sys.stderr.flush()
        except Exception as e:
            logger.error(f"Error processing audio file: {e}")
            _json_response(self, {'error': f'Server error: {str(e)}'}, 500)
        finally:
            if not sent and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as _e:
                    logger.warning(f"Could not clean partial temp file {file_path}: {_e}")


    def _handle_tts_stream(self):
        """Streaming TTS — yield each Kokoro segment as a standalone OGG/Opus
        chunk via HTTP Transfer-Encoding: chunked. Same request shape as /tts;
        response is a sequence of self-contained OGG blobs (each one decodable
        independently). Used by v2.7.0 streaming pipeline.

        Pipeline lock is held for the full generation — Kokoro can't be
        parallelized across requests safely. Streaming just yields bytes as
        soon as each segment is encoded, instead of accumulating all first.
        """
        import io
        global request_count
        with request_count_lock:
            request_count += 1
            current_count = request_count

        if current_count % 10 == 0:
            mem_exceeded = check_memory()
            req_exceeded = current_count >= MAX_REQUESTS
            if mem_exceeded or req_exceeded:
                schedule_restart(f"Memory: {mem_exceeded}, Requests: {current_count}/{MAX_REQUESTS}")

        # Parse + validate input (same shape as /tts)
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

        voice = data.get('voice') or DEFAULT_VOICE
        try:
            speed = float(data.get('speed', DEFAULT_SPEED))
        except (ValueError, TypeError):
            speed = DEFAULT_SPEED
        pitch = data.get('pitch', 1.0)

        # Headers committed — past this point we can't return JSON errors,
        # only chunks or a close.
        self.send_response(200)
        self.send_header('Content-Type', 'audio/ogg')
        self.send_header('Transfer-Encoding', 'chunked')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Accel-Buffering', 'no')   # disable nginx buffering if proxied
        self.end_headers()
        # Disable Nagle on this socket — without TCP_NODELAY, Windows winsock
        # (and to a lesser extent Linux loopback) batches small chunked-transfer
        # frames waiting for ACKs. That negates the streaming latency win: each
        # OGG segment sits in the send buffer until the next is queued.
        # 2026-05-18 herring-table #6.
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception as _e:
            logger.debug(f"TCP_NODELAY set failed (non-fatal): {_e!r}")

        segments_emitted = 0
        first_chunk_ms = None
        generation_start = time.time()

        # Three-stage pipeline: generate → encode → send.
        # Only inference (generate) holds pipeline_lock. Encoding (OGG/Opus)
        # and socket I/O run on their own threads outside the lock, so the
        # next sentence can start inference while this one's segments are
        # still being encoded and sent. Both libsndfile and PyTorch release
        # the GIL during C-level work, so the stages run concurrently on
        # separate cores. 2026-05-20.
        import threading as _t
        import queue as _q
        encode_queue: "_q.Queue" = _q.Queue()
        write_queue: "_q.Queue" = _q.Queue()
        writer_err = {'exc': None}

        def _encoder():
            """Encode raw numpy segments to OGG/Opus, feed to writer."""
            try:
                while True:
                    item = encode_queue.get()
                    if item is None:
                        write_queue.put(b"0\r\n\r\n")
                        write_queue.put(None)
                        return
                    audio = _apply_pitch(item, pitch)
                    buf = io.BytesIO()
                    sf.write(buf, audio, AUDIO_SAMPLE_RATE,
                             format='OGG', subtype='OPUS')
                    ogg_bytes = buf.getvalue()
                    del audio, buf
                    frame_header = f"{len(ogg_bytes):x}\r\n".encode('ascii')
                    write_queue.put(frame_header + ogg_bytes + b"\r\n")
            except Exception as e:
                writer_err['exc'] = writer_err['exc'] or e
                # Still terminate the writer so it doesn't hang
                try:
                    write_queue.put(b"0\r\n\r\n")
                    write_queue.put(None)
                except Exception:
                    pass

        def _writer():
            try:
                while True:
                    item = write_queue.get()
                    if item is None:
                        self.wfile.flush()
                        return
                    self.wfile.write(item)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError) as e:
                writer_err['exc'] = e
            except Exception as e:
                writer_err['exc'] = e

        encoder_thread = _t.Thread(target=_encoder, daemon=True, name="tts-stream-encoder")
        writer_thread = _t.Thread(target=_writer, daemon=True, name="tts-stream-writer")
        encoder_thread.start()
        writer_thread.start()

        try:
            with pipeline_lock:
                generator = pipeline(text_to_speak, voice=voice, speed=speed)
                for _, _, seg in generator:
                    # If downstream already failed (client gone), bail early —
                    # no point finishing inference for a dead socket.
                    if writer_err['exc'] is not None:
                        break
                    # Copy to decouple from tensor memory, hand off to encoder.
                    encode_queue.put(np.copy(seg))

                    if first_chunk_ms is None:
                        first_chunk_ms = int((time.time() - generation_start) * 1000)
                    segments_emitted += 1
                del generator
            # Lock released — next sentence can start inference while
            # encoder + writer finish this one's segments.

            # Signal encoder that inference is done
            encode_queue.put(None)
            # Bounded wait so a wedged client can't pin this handler forever.
            encoder_thread.join(timeout=60)
            writer_thread.join(timeout=60)
            if writer_err['exc'] is not None:
                raise writer_err['exc']
            total_ms = int((time.time() - generation_start) * 1000)
            logger.info(
                f"Stream complete: {segments_emitted} segments, "
                f"first_chunk={first_chunk_ms}ms total={total_ms}ms "
                f"(req #{current_count})"
            )
            sys.stderr.flush()
        except (BrokenPipeError, ConnectionResetError) as e:
            logger.info(
                f"Client disconnected mid-stream after {segments_emitted} segments: {e}"
            )
        except Exception as e:
            logger.error(
                f"Stream error after {segments_emitted} segments: {e}",
                exc_info=True,
            )
            # Best-effort terminating chunk so client doesn't hang
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except Exception:
                pass


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
