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
import traceback
import psutil

# Her words never reach a log. Ours goes to stderr; stdout belongs to the
# libraries, and misaki prints a word it can't read (en.py 'TODO:NUM'), so
# stdout goes nowhere. kokoro's own loguru logger is off (kokoro/__init__).
sys.stdout = open(os.devnull, 'w')
os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')  # pure phone-home UA
from huggingface_hub import hf_hub_download, try_to_load_from_cache
from kokoro import KModel, KPipeline

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
    HOST = '127.0.0.1'
    PORT = 5012


# --- Cross-platform temp directory ---
def get_temp_dir():
    """Get optimal temp directory. Prefers /dev/shm (Linux RAM disk) for speed."""
    if sys.platform == 'linux':
        shm = '/dev/shm'
        if os.path.exists(shm) and os.access(shm, os.W_OK):
            return shm
    return tempfile.gettempdir()


def _private_error(what, e):
    """Log a failure by its type and frames only: a synthesizer's message can
    quote the text it failed on, and this log may hold a stranger's words too."""
    frames = ''.join(traceback.format_tb(e.__traceback__))
    logger.error(f"{what}: {type(e).__name__} (message withheld: it may quote the text)\n{frames}")


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
#   1. cuda requested + cuda available + model.to('cuda') works → cuda
#   2. cuda requested but cuda not available → cpu (with warning)
#   3. cuda init throws (driver mismatch, OOM, etc) → cpu (with warning)
#   4. cpu requested → cpu (always works)
# (_init_kpipeline builds the model from cache-resolved files, then moves it)
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
            _why = ("this torch build has no CUDA (the default Windows torch from PyPI)"
                    if not _torch_check.version.cuda else
                    f"torch has CUDA {_torch_check.version.cuda} but cannot open the GPU "
                    "(no card, driver too old, or a build that does not know this card)")
            logger.warning(f"KOKORO_DEVICE='cuda' but torch.cuda.is_available()=False: {_why} - using cpu. "
                           "See docs/TROUBLESHOOTING.md 'Voice runs on the CPU'")
            _kokoro_device = 'cpu'
    except Exception as e:
        logger.warning(f"CUDA availability check failed ({e}) — using cpu")
        _kokoro_device = 'cpu'

logger.info(f"Loading Kokoro model on device='{_kokoro_device}'...")


REPO_ID = 'hexgrad/Kokoro-82M'


def _hf_file(filename):
    """Local path of a Kokoro repo file: the cache when it's there (zero
    network, quiet egress / negspace E4), else one explicit download.

    Replaces arming the hub's process-wide offline flag at import and
    "lifting" it later: hf_hub bakes that flag into its session, and the
    lift wrapped kokoro's
    generator *creation*, which never raises (the voice fetch runs on the
    first next()), so every voice not already cached failed forever
    (user report 2026-10-15)."""
    path = try_to_load_from_cache(REPO_ID, filename)
    if isinstance(path, str):
        return path
    logger.info(f"[TTS] {filename} not in the local cache, downloading from Hugging Face")
    return hf_hub_download(REPO_ID, filename)


_packs = {}   # voice name -> CPU tensor (kokoro's own cache is keyed by the string it is given)


def _voice_pack(voice):
    """Voice name (or comma blend) to a loaded voice tensor, resolving each
    file from the cache (one download if missing) and loading it here.
    kokoro splits a voice STRING on commas, so handing it cache paths would
    break every voice on a path with a comma in it; a tensor passes through
    untouched. Loading here also means a corrupt file fails before any
    headers go out."""
    names = [n.strip() for n in str(voice).split(',') if n.strip()] or [DEFAULT_VOICE]
    packs = []
    for name in names:
        pack = _packs.get(name)
        if pack is None:
            path = name if name.endswith('.pt') else _hf_file(f'voices/{name}.pt')
            pack = _packs[name] = _torch.load(path, map_location='cpu', weights_only=True)
        packs.append(pack)
    return packs[0] if len(packs) == 1 else _torch.mean(_torch.stack(packs), dim=0)


def _unavailable_status(e):
    """A definite 'no such file' from the hub is the caller's mistake: 404,
    which the provider does not retry. Anything else (hub unreachable,
    corrupt file) is 503, which it does."""
    code = getattr(getattr(e, 'response', None), 'status_code', None)
    return 404 if code == 404 else 503


def _init_kpipeline():
    """Model from cache-resolved files, moved to the device: cuda, falling
    back to cpu when CUDA fails at runtime (driver mismatch, OOM, context
    corruption) even though torch.cuda.is_available() said yes."""
    global _kokoro_device
    kmodel = KModel(repo_id=REPO_ID, config=_hf_file('config.json'),
                    model=_hf_file(KModel.MODEL_NAMES[REPO_ID]))
    try:
        kmodel = kmodel.to(_kokoro_device).eval()
    except Exception as e:
        if _kokoro_device != 'cuda':
            logger.error(f"Kokoro init failed on cpu: {e}")
            raise
        logger.warning(f"Kokoro CUDA init failed ({e}), falling back to cpu")
        _kokoro_device = 'cpu'
        kmodel = kmodel.to('cpu').eval()
    return KPipeline(lang_code='a', repo_id=REPO_ID, model=kmodel)



def _claim_port():
    """A port already held (an older Kokoro, a stranger) is a one-line exit
    now, not a 10 s model load first. The real bind below repeats this
    exclusively on Windows (2026-10-16)."""
    s = socket.socket(socket.AF_INET6 if ':' in str(HOST) else socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == 'win32':
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((HOST, PORT))
    except OSError as e:
        logger.error(f"Port {PORT} on {HOST} is not free ({e}): another Kokoro or program holds it. Not loading the model.")
        sys.exit(75)
    finally:
        s.close()


if __name__ == '__main__':
    _claim_port()          # only as the server: an importer (tests) must never be exited
pipeline = _init_kpipeline()
logger.info(f"Model loaded successfully on device='{_kokoro_device}'. Using temp dir: {TEMP_DIR}")
# Warm the default voice: a box that can't reach Hugging Face says so here,
# in kokoro.log at boot, instead of as a 500 on the first sentence.
try:
    _voice_pack(DEFAULT_VOICE)
except Exception as _e:
    logger.warning(f"Default voice '{DEFAULT_VOICE}' is not cached and could not be downloaded "
                   f"({_e}); uncached voices will fail until Hugging Face is reachable")
os.makedirs(TEMP_DIR, exist_ok=True)


def clean_text(text):
    """Cleans text by removing think blocks, stripping HTML, and filtering characters."""
    # Stage 1: Remove thinking blocks
    from core import think
    text = think.strip(text)

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

        # Status only: this door may face the network, and a request counter
        # there tells a stranger when she talks. The rest goes to the log.
        logger.debug(f"health: device={_kokoro_device} requests={request_count} memory={mem_gb:.2f}/{MAX_MEMORY_GB} GB")
        _json_response(self, {'status': 'ok'})

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

        # Resolve (download once if missing) the voice outside the lock and
        # answer failures as JSON: an exception escaping a handler makes
        # ThreadingMixIn close the socket with no response, which the main
        # app can only report as "Remote end closed connection".
        try:
            voice_pack = _voice_pack(voice)
        except Exception as e:
            logger.error(f"Voice '{voice}' unavailable: {e}")
            _json_response(self, {'error': f"Voice '{voice}' unavailable: {e}"}, _unavailable_status(e))
            return

        generation_start = time.time()
        try:
            with pipeline_lock:
                generator = pipeline(text_to_speak, voice=voice_pack, speed=speed)
                # Copy each segment to decouple from PyTorch tensor memory
                # Without copy, GC of generator tensors can free memory numpy still references → SIGSEGV
                audio_segments = [np.copy(seg) for _, _, seg in generator]
                del generator
        except Exception as e:
            _private_error(f"Synthesis failed (voice={voice})", e)
            # The caller sent the text, so the reply may say what broke (a CUDA
            # error is the Test button's whole use); only the log holds it back.
            _json_response(self, {'error': f'Synthesis failed: {e}'}, 500)
            return

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

        # Resolve the voice BEFORE committing headers: a missing voice or an
        # unreachable Hugging Face is still a clean JSON answer (404 / 503) here.
        try:
            voice_pack = _voice_pack(voice)
        except Exception as e:
            logger.error(f"Voice '{voice}' unavailable: {e}")
            _json_response(self, {'error': f"Voice '{voice}' unavailable: {e}"}, _unavailable_status(e))
            return

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
                generator = pipeline(text_to_speak, voice=voice_pack, speed=speed)
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
            _private_error(f"Stream error after {segments_emitted} segments", e)
            # Best-effort terminating chunk so client doesn't hang
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except Exception:
                pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTPServer that handles each request in a new thread."""
    daemon_threads = True
    # Windows: the stdlib default SO_REUSEADDR lets a second Kokoro co-bind the
    # port silently (two servers, double VRAM, one answers). Exclusive there.
    allow_reuse_address = sys.platform != 'win32'

    def server_bind(self):
        if sys.platform == 'win32':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _open_shared_door():
    """Other computers reach this Kokoro here: TLS, a key per client
    (core/worker_door.py). The plain door above stays as it was."""
    from core import worker_door
    folder = os.path.join(project_root, 'user', 'worker_door', 'kokoro')
    port = int(getattr(config, 'TTS_SHARE_PORT', 5013) or 5013)

    class Shared(TTSHandler):
        def _let_in(self):
            if worker_door.admit(self):
                return True
            _json_response(self, {'error': 'unauthorized'}, 401)
            return False

        def do_GET(self):
            if self._let_in():
                super().do_GET()

        def do_POST(self):
            if self._let_in():
                super().do_POST()

    try:
        srv = worker_door.serve(ThreadedHTTPServer, Shared, '0.0.0.0', port, folder)
    except Exception as e:
        logger.error(f"Shared door not opened on port {port}: {e}")
        return
    threading.Thread(target=srv.serve_forever, daemon=True, name='kokoro-door').start()
    logger.info(f"Shared with other computers on port {port} (TLS, {len(worker_door.clients(folder))} clients)")


def main():
    """Main server function."""
    logger.info(f"Starting Kokoro TTS server on {HOST}:{PORT}")
    logger.info(f"Memory limit: {MAX_MEMORY_GB}GB, Request limit: {MAX_REQUESTS}")
    logger.info(f"Temp directory: {TEMP_DIR}")

    server = ThreadedHTTPServer((HOST, PORT), TTSHandler)
    try:
        import config as _cfg
        if getattr(_cfg, 'TTS_SHARE_ENABLED', False):
            _open_shared_door()
    except ImportError:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("TTS server shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
