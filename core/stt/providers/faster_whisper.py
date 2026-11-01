"""Local faster-whisper STT provider: the client of core/stt/whisper_worker.py."""
import importlib.util
import json
import os
import platform
import queue
import subprocess
import sys
import time
import tempfile
import logging
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import soundfile as sf
import numpy as np

import config
from core.stt.providers.base import BaseSTTProvider, stt_failed

logger = logging.getLogger(__name__)

ROOT = Path(__file__).absolute().parent.parent.parent.parent
WORKER = ROOT / 'core' / 'stt' / 'whisper_worker.py'
LOG = ROOT / 'user' / 'logs' / 'whisper.log'
LOAD_WAIT = 300             # seconds for the model to load, downloads excluded
TRANSCRIBE_WAIT = 600       # past this the worker is stuck, whatever the audio: 25 MB is about 13 minutes
_FLAGS = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}


class _Dead(Exception):
    """The worker crashed or stopped answering."""


def _download_with_progress(repo_id, size):
    """faster_whisper hardcodes a silent tqdm in its download_model. Fetch the
    same files ourselves through huggingface_hub with a progress row in the
    notification center (core/notify.py) and hand the worker the directory,
    which skips its download. None = let faster_whisper fetch it."""
    try:
        from huggingface_hub import snapshot_download
        from core.notify import progress, tqdm_for
        if not repo_id:
            return None
        with progress(f"Downloading the Whisper model ({size})", source='core',
                      go={'view': 'settings', 'tab': 'stt'}, key='whisper-model',
                      done_title=f"Whisper model ready ({size})") as p:
            rep = tqdm_for(p)
            return snapshot_download(repo_id, allow_patterns=["config.json", "preprocessor_config.json",
                                                              "model.bin", "tokenizer.json", "vocabulary.*"],
                                     **({'tqdm_class': rep} if rep else {}))
    except Exception as e:
        logger.warning(f"[STT] progress download failed ({e}); letting faster_whisper fetch it")
        return None


def _safe_unlink(path, retries=3, delay=0.2):
    """Windows-safe file deletion with retries."""
    for attempt in range(retries):
        try:
            if os.path.exists(path):
                os.unlink(path)
            return True
        except PermissionError:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                logger.warning(f"Could not delete temp file after {retries} attempts: {path}")
                return False
        except Exception as e:
            logger.warning(f"Error deleting {path}: {e}")
            return False
    return True


def _library_dir():
    """Where the on-demand CUDA 12 cuBLAS landed (core/extras.py 'whisper-gpu'), or None."""
    if sys.platform != 'linux':
        return None
    for root in sys.path:
        lib = Path(root or '.') / 'nvidia' / 'cublas' / 'lib'
        if (lib / 'libcublas.so.12').exists():
            return str(lib)
    return None


def _worker_env():
    env = dict(os.environ, PYTHONUNBUFFERED='1')
    lib = _library_dir()
    if lib:         # ahead of any older system copy, and in this one process only
        env['LD_LIBRARY_PATH'] = os.pathsep.join(filter(None, [lib, env.get('LD_LIBRARY_PATH')]))
    return env


def _open_log():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    keep = LOG.exists() and LOG.stat().st_size < 1024 * 1024
    return open(LOG, 'a' if keep else 'w', encoding='utf-8')


class FasterWhisperProvider(BaseSTTProvider):
    """Local faster-whisper STT. Same doors as ever; the engine runs in a child."""

    def __init__(self, model_size=None, language=None):
        self._model_size = model_size or config.STT_MODEL_SIZE
        logger.info(f"Loading faster-whisper model: {self._model_size}")
        self._lock = threading.Lock()
        self._proc = None
        self._inbox = None
        self._crashes = 0
        self._cpu_only = False
        self._told = self._closed = False
        self.device = self.compute = self.why = self.kind = None
        if importlib.util.find_spec('faster_whisper') is None:
            raise RuntimeError("Faster Whisper not installed: No module named 'faster_whisper'")
        try:
            self._start()
        except _Dead as e:      # a crash on the card: the CPU may still load
            logger.warning(f"[STT] Whisper worker died loading on the card ({e}), trying the CPU")
            self._cpu_only, self.why, self.kind = True, f"the worker crashed loading on the card ({e})", 'other'
            try:
                self._start()
            except _Dead as e2:
                self._kill()
                raise RuntimeError(f"Failed to initialize STT model: the worker died ({e2}), see user/logs/whisper.log")
        self._tell()

    # --- the worker ---------------------------------------------------------

    def _settings(self):
        return {'size': self._model_size, 'device': 'cpu' if self._cpu_only else getattr(config, 'FASTER_WHISPER_DEVICE', 'cuda'),
                'compute_type': getattr(config, 'FASTER_WHISPER_COMPUTE_TYPE', 'int8'),
                'num_workers': getattr(config, 'FASTER_WHISPER_NUM_WORKERS', 2),
                'cuda_device': getattr(config, 'FASTER_WHISPER_CUDA_DEVICE', 0)}

    def _start(self):
        """A fresh worker, returning once its model answered. RuntimeError = it
        said why it can't load; _Dead = it crashed or hung."""
        self._kill()
        why, kind = self.why, self.kind
        with _open_log() as log:
            self._proc = subprocess.Popen([sys.executable, str(WORKER), json.dumps(self._settings())],
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, cwd=str(ROOT),
                                          env=_worker_env(), text=True, encoding='utf-8', bufsize=1, **_FLAGS)
        self._inbox = queue.Queue()
        threading.Thread(target=self._pump, args=(self._proc, self._inbox), name='whisper-pump', daemon=True).start()
        while True:
            msg = self._next(LOAD_WAIT)
            if 'need' in msg:
                self._send({'path': _download_with_progress(msg['need'], msg.get('size'))})
                continue
            if not msg.get('ready'):
                self._kill()
                raise RuntimeError(f"Failed to initialize STT model: {msg.get('error')}")
            self._note(msg)
            if self._cpu_only:      # the worker was told CPU: keep the reason it was
                self.why, self.kind = why, kind
            logger.info(f"[STT] Whisper worker ready on {self.device} ({self.compute})")
            return

    @staticmethod
    def _pump(proc, inbox):
        for line in proc.stdout:
            try:
                inbox.put(json.loads(line))
            except ValueError:
                logger.warning("[STT] Whisper worker wrote something that is not a message")
        inbox.put(None)

    def _send(self, msg):
        try:
            self._proc.stdin.write(json.dumps(msg) + '\n')
            self._proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise _Dead(f"its pipe closed: {e}")

    def _next(self, wait):
        try:
            msg = self._inbox.get(timeout=wait)
        except queue.Empty:
            self._kill()
            raise _Dead(f"no answer in {wait} s")
        if msg is None:
            raise _Dead(f"exit code {self._proc.wait()}")
        return msg

    def _kill(self):
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception as e:
            logger.warning(f"[STT] Whisper worker did not stop: {e}")
        for pipe in (proc.stdin, proc.stdout):
            try:
                pipe.close()
            except Exception:
                pass

    def _note(self, msg):
        was = self.device
        self.device, self.compute, self.why, self.kind = msg.get('device'), msg.get('compute'), msg.get('why'), msg.get('kind')
        if was == 'cuda' and self.device == 'cpu':
            self._tell()

    def _ask(self, path, params):
        """Segments from the worker, called under self._lock. A crash restarts it
        and tries once more; a second crash on the card moves to the CPU until the
        next start. A worker stuck past the wait is killed, and that one attempt is
        lost: another full wait would hold every voice lane behind this lock."""
        if self._closed:    # a caller that held this client through a switch must not bring a worker back
            raise stt_failed("the Whisper engine was switched off")
        for attempt in (1, 2):
            try:
                if self._proc is None or self._proc.poll() is not None:
                    self._start()
                self._send({'path': path, 'params': params})
                msg = self._next(TRANSCRIBE_WAIT)
                self._note(msg)
                if not msg.get('ok'):
                    raise stt_failed(msg.get('error') or 'the engine gave no reason')
                self._crashes = 0
                return [SimpleNamespace(**s) for s in msg['segments']]
            except _Dead as e:
                logger.error(f"[STT] Whisper worker died: {e}")
                self._crashes += 1
                self._kill()
                if self.device == 'cuda' and self._crashes >= 2:
                    self._cpu_only, self.device = True, 'cpu'
                    self.why, self.kind = f"the worker keeps crashing on the card ({e})", 'other'
                    self._tell()
                if attempt == 2 or 'no answer' in str(e):
                    raise stt_failed(f"the Whisper worker stopped ({e}), see user/logs/whisper.log")
            except (RuntimeError, OSError) as e:    # it could not start again
                raise stt_failed(str(e))

    def reload(self):
        """Back onto the card if it can: after the GPU library arrives."""
        with self._lock:
            self._cpu_only, self._crashes, self.why, self.kind = False, 0, None, None
            self._start()
            self._tell()

    def close(self):
        """Waits for a transcription in flight, then ends the worker."""
        with self._lock:
            self._closed = True
            self._kill()

    # --- what the person is told --------------------------------------------

    def gpu_offer(self):
        """Whether a missing or too-old cuBLAS is what keeps Whisper off the card
        and the on-demand set would fix it. Installed already: nothing to offer."""
        if self.device != 'cpu' or self.kind != 'library':
            return False
        if sys.platform != 'linux' or platform.machine() != 'x86_64':
            return False
        try:
            from core import extras
            return not extras.installed('whisper-gpu') and not extras._managed()
        except Exception:
            return False

    def report(self):
        return {'device': self.device, 'why': self.why, 'offer': self.gpu_offer()}

    def _tell(self):
        """A row when Whisper wanted the card and is on the CPU. A torch without
        CUDA is left to core/gpu_status.py, whose row has its own button."""
        try:
            from core import notify
            where = {'go': {'view': 'settings', 'tab': 'stt'}, 'key': 'stt-cpu', 'source': 'stt'}
            if self.device == 'cuda' and self._told:
                self._told = False
                notify.notify('Whisper is back on the GPU', 'info', '', **where)
            elif self.device != 'cuda' and self.why and self.kind != 'torch':
                self._told = True
                hint = (" Settings > STT has the button: Install Whisper GPU support." if self.gpu_offer()
                        else " Set Whisper's device to CPU to stop this reminder.")
                notify.notify('Whisper is running on the CPU', 'warning', f"Hearing still works, just slower. {self.why}.{hint}", **where)
            else:       # a row from before a restart would still say CPU
                notify.forget('stt-cpu')
        except Exception as e:
            logger.debug(f"[STT] row not posted: {e}")

    # --- the provider's doors -----------------------------------------------

    def transcribe_segments(self, audio_path: str, **params) -> list:
        """faster-whisper's own segments (text, no_speech_prob, avg_logprob, …).
        Thread-safe. `params` override the defaults (a caller may pass
        vad_filter=False for audio its own gate already endpointed)."""
        transcription_params = {
            'language': config.STT_LANGUAGE,
            'beam_size': getattr(config, 'FASTER_WHISPER_BEAM_SIZE', 3),
            **params,
        }
        with self._lock:
            return self._ask(audio_path, transcription_params)

    def _transcribe_impl(self, audio_path: str) -> Optional[str]:
        """Transcribe an audio file. Thread-safe. Unreadable audio is None like
        silence; an engine failure raises STTFailed.

        Hallucination filter is applied by `BaseSTTProvider.transcribe_file`
        — return raw text here.
        """
        temp_path = None
        try:
            try:
                audio_data, sample_rate = sf.read(audio_path)
                if len(audio_data) == 0:
                    return None
                if len(audio_data.shape) > 1:
                    audio_data = audio_data.mean(axis=1)

                rms = np.sqrt(np.mean(audio_data ** 2))
                duration = len(audio_data) / sample_rate if sample_rate > 0 else 0
                max_val = np.max(np.abs(audio_data))

                # Skip near-silent audio — likely wrong mic selected in browser
                if rms < 0.001:
                    logger.warning(f"[STT] Audio too quiet ({duration:.1f}s, RMS={rms:.6f}) — check mic selection")
                    return ""

                if max_val > 0:
                    audio_data = audio_data / max_val

                fd, temp_path = tempfile.mkstemp(suffix=".wav", prefix="stt_processed_")
                os.close(fd)
                sf.write(temp_path, audio_data, sample_rate)
            except Exception as e:
                logger.error(f"[STT] Could not read the audio: {e}")
                return None

            transcription_params = {
                'language': config.STT_LANGUAGE,
                'beam_size': getattr(config, 'FASTER_WHISPER_BEAM_SIZE', 3),
                'vad_filter': getattr(config, 'FASTER_WHISPER_VAD_FILTER', True),
                'vad_parameters': getattr(config, 'FASTER_WHISPER_VAD_PARAMETERS', None)
            }
            with self._lock:
                segments = self._ask(temp_path, transcription_params)
            # Filter out segments where Whisper thinks there's no speech
            return " ".join([
                segment.text for segment in segments
                if segment.no_speech_prob < 0.7
            ]).strip()
        finally:
            if temp_path:
                _safe_unlink(temp_path)

    def is_available(self) -> bool:
        return self.device is not None
