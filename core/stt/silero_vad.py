"""Silero VAD wrapper — drop-in replacement for amplitude-based VAD.

Lazy-downloads the ONNX model to user/models/silero_vad.onnx on first use,
then runs inference via onnxruntime (no torch/torchaudio dep). Designed to
replace _is_silent() in core/stt/recorder.py.

Silero VAD wants 16kHz mono float32 audio in fixed chunk sizes:
  - 512 samples (32ms) for 16kHz
  - 256 samples (16ms) for 8kHz
The model is stateful — each call updates a hidden state from the previous
call. Reset state at the start of each recording session.

Why not the silero-vad pip package: it pulls torchaudio 2.11 which conflicts
with our pinned torch 2.10. Using the ONNX directly is also lighter — single
~2.3MB file, runs on the onnxruntime we already ship.
"""
import logging
import os
import shutil
import socket
import threading
import requests
from pathlib import Path
from typing import Optional

import numpy as np

# Network timeouts for model download. Without these, a stuck socket (corp
# proxy, captive portal, GitHub stall) blocks the warmup thread forever,
# leaving the UI badge stuck on "pending" with no recovery path. 2026-05-20.
_DOWNLOAD_CONNECT_TIMEOUT_S = 15
_DOWNLOAD_READ_TIMEOUT_S = 60
# Hard ceiling on the whole warmup (download + ONNX load). If we blow this,
# mark warmup as failed so the UI badge transitions out of "pending" and the
# recorder gives up on silero for this process. 2026-05-20.
_WARMUP_HARD_TIMEOUT_S = 120

logger = logging.getLogger(__name__)

# Stable upstream — snakers4/silero-vad master branch
# Commit-pinned (negspace fold-in, 2026-08-31): master is a mutable ref —
# a moved file would have been swallowed silently (sha was never checked).
# Pin = the model file at its last change (2025-11-06), verified against
# the locally cached copy. Upstream update -> bump BOTH url commit + sha.
SILERO_VAD_URL = "https://github.com/snakers4/silero-vad/raw/bfdc0193023f121ea5b3cc7b176dbed570a68a59/src/silero_vad/data/silero_vad.onnx"
SILERO_VAD_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"

PROJECT_ROOT = Path(__file__).parent.parent.parent
MODEL_CACHE_PATH = PROJECT_ROOT / "user" / "models" / "silero_vad.onnx"

# Silero v5 ONNX takes (audio_chunk + leading context, state, sr) and returns
# (speech_prob, new_state). Critical detail learned the hard way 2026-05-16:
# the model expects the LAST 64 samples (at 16kHz) of the previous chunk
# prepended to the new chunk, for waveform continuity. Without that context
# every chunk is scored as a cold-start fragment and real speech reads as ~0.
_SILERO_STATE_SHAPE = (2, 1, 128)
_SILERO_CONTEXT_SAMPLES = {16000: 64, 8000: 32}


# ── Warmup state (system capability, separate from user intent) ──────────────
# Background warmup verifies silero is operational on this machine without
# blocking startup. Result is read by /api/stt/vad-status and by the recorder's
# decision to attempt silero. User INTENT (STT_VAD_BACKEND setting) stays
# untouched — this is system CAPABILITY only. Network failures don't overwrite
# user preferences. 2026-05-16.
_WARMUP_STATE = {
    "state": "pending",   # pending / ready / failed
    "reason": "",
    "started": False,
}
_warmup_lock = threading.Lock()


def get_warmup_status() -> dict:
    """Read-only snapshot of warmup state. Used by recorder + API endpoint."""
    with _warmup_lock:
        return dict(_WARMUP_STATE)


def is_available() -> bool:
    """True if silero is verified working on this machine. False if still
    downloading/loading OR if it failed. Recorder uses this to decide whether
    to attempt silero at all for a given recording."""
    return get_warmup_status()["state"] == "ready"


def warmup_async():
    """Kick off background download+load test. Idempotent — only starts once
    per process. Safe to call multiple times. Sets warmup state on completion.

    A watchdog thread enforces a hard timeout so the UI badge can't get stuck
    on 'pending' forever — if anything below our explicit socket timeouts
    blocks (kernel-level network stall, ONNX init hang on a broken GPU
    runtime), the watchdog flips state to 'failed' after _WARMUP_HARD_TIMEOUT_S.
    The work thread keeps running in the background but state is now
    definitive so the recorder + UI can move on. 2026-05-20."""
    with _warmup_lock:
        if _WARMUP_STATE["started"]:
            return
        _WARMUP_STATE["started"] = True

    done_evt = threading.Event()

    def _warmup():
        try:
            logger.info("[SILERO-WARMUP] Starting background warmup (download + load)")
            _ensure_model_downloaded()
            # Construct session — this is the load-test. Any ONNX issue will throw.
            SileroVAD._get_shared_session()
            with _warmup_lock:
                # Don't clobber if watchdog already declared failure
                if _WARMUP_STATE["state"] == "pending":
                    _WARMUP_STATE["state"] = "ready"
                    _WARMUP_STATE["reason"] = ""
            logger.info("[SILERO-WARMUP] Silero ready")
        except Exception as e:
            with _warmup_lock:
                if _WARMUP_STATE["state"] == "pending":
                    _WARMUP_STATE["state"] = "failed"
                    _WARMUP_STATE["reason"] = str(e)
            logger.warning(f"[SILERO-WARMUP] Silero unavailable: {e}")
        finally:
            done_evt.set()

    def _watchdog():
        if done_evt.wait(timeout=_WARMUP_HARD_TIMEOUT_S):
            return  # warmup completed within deadline
        with _warmup_lock:
            if _WARMUP_STATE["state"] == "pending":
                _WARMUP_STATE["state"] = "failed"
                _WARMUP_STATE["reason"] = f"warmup exceeded {_WARMUP_HARD_TIMEOUT_S}s — network or ONNX init stuck"
                logger.warning(f"[SILERO-WARMUP] Watchdog tripped: {_WARMUP_STATE['reason']}")

    threading.Thread(target=_warmup, daemon=True, name="silero-warmup").start()
    threading.Thread(target=_watchdog, daemon=True, name="silero-warmup-watchdog").start()


class SileroVAD:
    """Per-recording-session VAD instance. Holds the model state across chunks."""

    _shared_session: Optional["onnxruntime.InferenceSession"] = None
    _shared_lock = threading.Lock()

    def __init__(self, sample_rate: int = 16000):
        if sample_rate not in (8000, 16000):
            raise ValueError(f"silero-vad supports 8kHz or 16kHz, got {sample_rate}")
        self.sample_rate = sample_rate
        self.chunk_samples = 512 if sample_rate == 16000 else 256
        self.context_samples = _SILERO_CONTEXT_SAMPLES[sample_rate]
        self.state = np.zeros(_SILERO_STATE_SHAPE, dtype=np.float32)
        # Leading-context buffer prepended to each new chunk. Starts as zeros;
        # after each score_chunk we save the last context_samples of the chunk.
        self.context = np.zeros(self.context_samples, dtype=np.int16)
        self.sr_tensor = np.array(sample_rate, dtype=np.int64)
        self.session = self._get_shared_session()

    @classmethod
    def _get_shared_session(cls):
        """One ONNX session per process. Model is small (~2.3MB) but inference
        objects aren't free — share across all recording sessions."""
        if cls._shared_session is not None:
            return cls._shared_session
        with cls._shared_lock:
            if cls._shared_session is not None:
                return cls._shared_session
            model_path = _ensure_model_downloaded()
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            cls._shared_session = ort.InferenceSession(
                str(model_path),
                sess_options=opts,
                providers=["CPUExecutionProvider"],
            )
            logger.info(f"[SILERO] Loaded model from {model_path}")
            return cls._shared_session

    def reset(self):
        """Clear hidden state and context — call at the start of each recording."""
        self.state.fill(0.0)
        self.context = np.zeros(self.context_samples, dtype=np.int16)

    def score_chunk(self, audio_int16: np.ndarray) -> float:
        """Score one chunk of audio for speech probability (0.0..1.0).

        audio_int16: 1-D np.int16 array of exactly self.chunk_samples samples.
        Internally prepends the previous chunk's trailing context (64 samples
        at 16kHz) for waveform continuity, then advances state and context.
        """
        if audio_int16.dtype != np.int16:
            audio_int16 = audio_int16.astype(np.int16)
        if len(audio_int16) != self.chunk_samples:
            if len(audio_int16) < self.chunk_samples:
                audio_int16 = np.pad(audio_int16, (0, self.chunk_samples - len(audio_int16)))
            else:
                audio_int16 = audio_int16[:self.chunk_samples]

        # Concatenate stored context with new chunk
        with_context = np.concatenate([self.context, audio_int16])  # 576 samples @ 16k

        audio_f32 = (with_context.astype(np.float32) / 32768.0).reshape(1, -1)
        outputs = self.session.run(
            None,
            {
                "input": audio_f32,
                "state": self.state,
                "sr": self.sr_tensor,
            },
        )
        speech_prob = float(outputs[0].squeeze())
        self.state = outputs[1]
        # Save the last context_samples of THIS chunk for the next call
        self.context = audio_int16[-self.context_samples:].copy()
        return speech_prob


def run_voice_test(duration_s: float = 5.0) -> dict:
    """Open the mic, record N seconds with NO end-of-speech cutoff, score every
    chunk with silero. Returns summary stats and a threshold suggestion.

    Used by the "Test my voice" button to let users tune STT_VAD_SPEECH_THRESHOLD
    against their actual voice. Does NOT use the recorder's capture loop —
    direct mic→silero→score path so there's no silent-timeout interference."""
    from core.audio.backend import sd
    from core.audio import get_device_manager, convert_to_mono, resample_audio

    if not is_available():
        st = get_warmup_status()
        return {
            "ok": False,
            "error": f"Silero not available: state={st['state']} reason={st['reason']}",
        }

    # Pick mic device — same as recorder does
    dm = get_device_manager()
    device_config = dm.find_input_device(target_rate=None,
                                          preferred_blocksize=1024)
    rate = device_config.sample_rate
    channels = device_config.channels
    blocksize = device_config.blocksize
    needs_stereo = device_config.needs_stereo_downmix

    vad = SileroVAD(sample_rate=16000)
    vad.reset()
    silero_buffer = np.zeros(0, dtype=np.int16)
    scores = []
    amps = []
    chunks_collected = []

    try:
        with sd.InputStream(device=device_config.device_index,
                            samplerate=rate, channels=channels,
                            dtype=np.int16, blocksize=blocksize) as stream:
            import time as _time
            t_start = _time.time()
            while _time.time() - t_start < duration_s:
                data, _ = stream.read(blocksize)
                if needs_stereo:
                    audio_data = convert_to_mono(data)
                else:
                    audio_data = data.flatten().astype(np.int16)

                amps.append(int(np.max(np.abs(audio_data))) if len(audio_data) else 0)

                if rate != 16000:
                    chunk_16k = resample_audio(audio_data, rate, 16000)
                else:
                    chunk_16k = audio_data
                if chunk_16k.dtype != np.int16:
                    chunk_16k = chunk_16k.astype(np.int16)

                silero_buffer = np.concatenate([silero_buffer, chunk_16k])
                while len(silero_buffer) >= 512:
                    window = silero_buffer[:512]
                    silero_buffer = silero_buffer[512:]
                    scores.append(vad.score_chunk(window))
    except Exception as e:
        logger.exception("voice test failed")
        return {"ok": False, "error": f"mic capture failed: {e}"}

    if not scores:
        return {"ok": False, "error": "no audio captured (mic permission? blocked device?)"}

    max_prob = max(scores)
    mean_prob = float(np.mean(scores))
    peak_amp = max(amps) if amps else 0

    # Suggestion heuristic: comfortable if max - threshold > 0.2,
    # marginal if 0.05 < diff < 0.2, too-high if diff < 0.05.
    import config as _cfg
    current_threshold = float(getattr(_cfg, 'STT_VAD_SPEECH_THRESHOLD', 0.5))
    margin = max_prob - current_threshold
    if margin > 0.2:
        verdict = "comfortable"
        suggestion = f"Threshold {current_threshold:.2f} is comfortable for your voice (peaked at {max_prob:.2f}, {margin:.2f} margin above threshold)."
    elif margin > 0.05:
        verdict = "marginal"
        suggested = max(0.2, max_prob - 0.25)
        suggestion = f"Threshold {current_threshold:.2f} is marginal — peaked at {max_prob:.2f}. Consider dropping to {suggested:.2f} for safety."
    else:
        verdict = "too_high"
        suggested = max(0.2, max_prob - 0.20)
        suggestion = f"Threshold {current_threshold:.2f} is TOO HIGH — your voice peaks at {max_prob:.2f}. Drop to {suggested:.2f} or lower."

    return {
        "ok": True,
        "max_prob": round(max_prob, 4),
        "mean_prob": round(mean_prob, 4),
        "peak_amp": peak_amp,
        "num_chunks_scored": len(scores),
        "duration_s": duration_s,
        "current_threshold": current_threshold,
        "verdict": verdict,
        "suggestion": suggestion,
    }


def _ensure_model_downloaded() -> Path:
    """Download silero_vad.onnx to user/models/ on first use. Returns path."""
    if MODEL_CACHE_PATH.exists() and MODEL_CACHE_PATH.stat().st_size > 1_000_000:
        return MODEL_CACHE_PATH

    MODEL_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"[SILERO] Downloading {SILERO_VAD_URL} -> {MODEL_CACHE_PATH}")
    # Atomic download — write to .tmp then rename so a Ctrl-C mid-download
    # doesn't leave a half-file that the next run silently uses.
    # Explicit timeouts on the socket so a stuck CDN / corp proxy / captive
    # portal can't hang the warmup thread forever. urlretrieve doesn't take
    # a timeout kwarg directly; use urlopen + copyfileobj instead. 2026-05-20.
    tmp_path = MODEL_CACHE_PATH.with_suffix(".onnx.tmp")
    try:
        # get_session, not urllib: urllib can't ride the SOCKS env (no SOCKS
        # support — under a proxy this download just died), and proxy users
        # want model pulls routed too. SOCKS-for-all conversion, 2026-08-31.
        from core.socks_proxy import get_session
        resp = get_session().get(SILERO_VAD_URL, timeout=_DOWNLOAD_READ_TIMEOUT_S, stream=True)
        resp.raise_for_status()
        with open(tmp_path, 'wb') as f:
            for chunk in resp.iter_content(65536):
                f.write(chunk)
        if tmp_path.stat().st_size < 1_000_000:
            raise RuntimeError(f"Downloaded file too small ({tmp_path.stat().st_size} bytes) — likely a redirect/error page")
        if SILERO_VAD_SHA256:
            import hashlib
            got = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
            if got != SILERO_VAD_SHA256:
                tmp_path.unlink(missing_ok=True)
                raise RuntimeError(f"silero_vad.onnx sha256 mismatch (got {got[:16]}…) — refusing unverified model")
        os.replace(tmp_path, MODEL_CACHE_PATH)
        logger.info(f"[SILERO] Model cached at {MODEL_CACHE_PATH} ({MODEL_CACHE_PATH.stat().st_size:,} bytes)")
        return MODEL_CACHE_PATH
    except (socket.timeout, TimeoutError, requests.exceptions.Timeout) as e:
        # Clean up partial download
        if tmp_path.exists():
            try: tmp_path.unlink()
            except Exception: pass
        raise RuntimeError(f"silero-vad model download timed out after {_DOWNLOAD_READ_TIMEOUT_S}s — check network") from e
    except Exception as e:
        # Clean up partial download
        if tmp_path.exists():
            try: tmp_path.unlink()
            except Exception: pass
        raise RuntimeError(f"silero-vad model download failed: {e}") from e
