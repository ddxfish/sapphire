"""Piper TTS provider — fast local neural TTS for CPU / weak hardware.

Loaded via exec() by the plugin loader (not imported as a module), so:
  - imports are absolute (`from core.tts.providers.base import ...`)
  - the heavy `piper` import is LAZY (inside methods) — the class still registers
    if piper-tts isn't installed yet; is_available() reports real readiness.

Output is OGG/Opus (small, 5G-friendly). low-tier voices are 16 kHz = Opus-native;
medium/high are 22.05 kHz and get resampled to 24 kHz before Opus encode. Threads
are pinned by swapping voice.session (onnxruntime has no OpenMP, PiperVoice.load has
no thread arg — the session swap is the only working knob; see tmp/piper-benchmark/).
"""
import io
import logging
import threading
from pathlib import Path
from typing import Iterator, List, Optional

import numpy as np
import soundfile as sf

from core.tts.providers.base import BaseTTSProvider
import core.tts.providers.base as _base

logger = logging.getLogger(__name__)

# Anchor to the CORE package, not this exec'd plugin file, so symlinked plugins
# still resolve the real sapphire root. .absolute() not .resolve() — never follow
# symlinks when walking up (tmp memory: symlinked_plugins_resolve_trap).
SAPPHIRE_ROOT = Path(_base.__file__).absolute().parents[3]
VOICES_DIR = SAPPHIRE_ROOT / "user" / "piper-voices"

OPUS_RATES = {8000, 12000, 16000, 24000, 48000}
DEFAULT_VOICE = "en_US-hfc_female-medium"


def _settings() -> dict:
    try:
        from core.plugin_loader import plugin_loader
        return plugin_loader.get_plugin_settings("piper") or {}
    except Exception:
        return {}


def _is_piper_voice(name: str) -> bool:
    """A Piper voice name looks like `en_US-lessac-medium`; a Kokoro voice
    (`af_heart`) does not. Lets us ignore the chat's Kokoro voice and use ours."""
    return bool(name) and name.count("-") >= 2 and name.rsplit("-", 1)[-1] in ("low", "medium", "high")


class PiperTTSProvider(BaseTTSProvider):
    """Generates audio with Piper in-process (no subprocess). One ORT session per
    voice, cached; synthesis serialized by a lock (espeak/session safety)."""

    audio_content_type = "audio/ogg"  # OGG/Opus
    SPEED_MIN = 0.5
    SPEED_MAX = 2.0
    supports_streaming = True
    # Native pitch via varispeed resample pre-encode (kokoro precedent).
    # Without this the stream pump dropped pitch entirely — Piper streams
    # spoke at 1.0 while the same voice was shifted on every other lane.
    supports_pitch = True

    def __init__(self):
        self._voices = {}            # name -> PiperVoice (cached)
        self._lock = threading.Lock()        # serializes synthesis
        self._dl_locks = {}          # name -> Lock (one download at a time per voice)
        self._dl_guard = threading.Lock()    # guards _dl_locks dict
        logger.info("Piper TTS provider initialized")

    # --- settings ---
    @property
    def _voice_name(self) -> str:
        return (_settings().get("voice") or "").strip() or DEFAULT_VOICE

    @property
    def _threads(self) -> int:
        try:
            return int(_settings().get("threads", 0) or 0)
        except Exception:
            return 0

    def _resolve_voice(self, voice: str) -> str:
        """Use the chat-passed voice only if it's actually a Piper voice; else our setting."""
        return voice if _is_piper_voice(voice) else self._voice_name

    # --- voice loading: lazy download + lazy import + thread pin (NOT locked here;
    #     callers hold self._lock so the cache write is safe and we never re-acquire) ---
    def _model_path(self, name: str) -> Path:
        return VOICES_DIR / f"{name}.onnx"

    def _voice_dl_lock(self, name: str) -> threading.Lock:
        with self._dl_guard:
            lk = self._dl_locks.get(name)
            if lk is None:
                lk = threading.Lock()
                self._dl_locks[name] = lk
            return lk

    def _download_voice(self, name: str, tries: int = 4):
        """piper.download_voices.download_voice has NO retry AND writes straight to
        the final path (non-atomic). We download into a temp dir on the SAME
        filesystem, then os.replace into place — CONFIG FIRST, MODEL LAST — so the
        model file (the .exists() gate everyone checks) only appears once BOTH parts
        are complete. An interrupted/killed/network-dropped download leaves only temp
        junk, never a half-written voice. Atomic rename also makes concurrent
        downloads across plugin reloads safe (last complete writer wins), so the
        instance-scoped dl lock no longer has to be the only guard."""
        import time, tempfile, shutil, os
        from piper.download_voices import download_voice
        VOICES_DIR.mkdir(parents=True, exist_ok=True)
        model_dst = self._model_path(name)
        cfg_dst = Path(str(model_dst) + ".json")
        with self._voice_dl_lock(name):
            if model_dst.exists() and cfg_dst.exists():
                return  # already complete (both parts present)
            last = None
            for i in range(tries):
                tmp = Path(tempfile.mkdtemp(dir=str(VOICES_DIR), prefix=f".dl-{name}-"))
                try:
                    download_voice(name, tmp)
                    model_tmp = tmp / f"{name}.onnx"
                    cfg_tmp = tmp / f"{name}.onnx.json"
                    if not model_tmp.exists():
                        raise RuntimeError("download produced no model file")
                    if cfg_tmp.exists():
                        os.replace(str(cfg_tmp), str(cfg_dst))    # config first
                    os.replace(str(model_tmp), str(model_dst))    # model last = the gate
                    return
                except Exception as e:
                    last = e
                    logger.warning(f"[piper] download '{name}' attempt {i + 1}/{tries} failed: {e!r}")
                    time.sleep(2 * (i + 1))
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(f"piper voice download failed for '{name}': {last}")

    def _get_voice(self, name: str):
        v = self._voices.get(name)
        if v is not None:
            return v
        from piper import PiperVoice
        mp = self._model_path(name)
        if not mp.exists():
            logger.info(f"[piper] voice '{name}' not present — downloading to {VOICES_DIR}")
            self._download_voice(name)
        try:
            v = PiperVoice.load(str(mp))
        except Exception as e:
            # Self-heal: a truncated/corrupt model (e.g. left by an old non-atomic
            # download) can pass .exists() but fail to load. Delete it + its config
            # and re-download once, then retry.
            logger.warning(f"[piper] voice '{name}' failed to load ({e!r}) — re-downloading")
            for p in (mp, Path(str(mp) + ".json")):
                try:
                    p.unlink()
                except OSError:
                    pass
            self._download_voice(name)
            v = PiperVoice.load(str(mp))
        threads = self._threads
        if threads > 0:
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.intra_op_num_threads = threads
            so.inter_op_num_threads = 1
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            v.session = ort.InferenceSession(str(mp), sess_options=so, providers=["CPUExecutionProvider"])
            logger.info(f"[piper] '{name}' pinned to {threads} intra-op threads")
        self._voices[name] = v
        return v

    # --- synthesis helpers ---
    def _syn_config(self, speed: float):
        try:
            from piper import SynthesisConfig
        except ImportError:
            from piper.config import SynthesisConfig
        s = max(self.SPEED_MIN, min(self.SPEED_MAX, speed or 1.0))
        # length_scale is INVERSE of speed (smaller = faster)
        return SynthesisConfig(length_scale=1.0 / s)

    def _resample(self, audio: np.ndarray, sr: int, tgt: int):
        n = int(len(audio) * tgt / sr)
        x = np.linspace(0, len(audio), n, endpoint=False)
        return np.interp(x, np.arange(len(audio)), audio).astype("float32"), tgt

    def _pitch_shift(self, audio: np.ndarray, sr: int, pitch) -> np.ndarray:
        """Varispeed shift, same semantics as TTSClient._apply_pitch_shift:
        pitch<1 = deeper and slightly longer. Stretch to len/pitch samples at
        the same rate — expressed through _resample by faking the source rate."""
        if not pitch or pitch == 1.0 or audio is None or len(audio) == 0:
            return audio
        shifted, _ = self._resample(audio, int(sr * float(pitch)), sr)
        return shifted

    def _encode_opus(self, audio: np.ndarray, sr: int) -> bytes:
        if audio is None or len(audio) == 0:
            return b""
        if sr not in OPUS_RATES:  # 22050 -> 24000 for Opus
            audio, sr = self._resample(audio, sr, 24000)
        buf = io.BytesIO()
        sf.write(buf, audio, sr, format="OGG", subtype="OPUS")
        return buf.getvalue()

    # --- public API ---
    def generate(self, text: str, voice: str, speed: float, **kwargs) -> Optional[bytes]:
        if not text or not text.strip():
            return None
        name = self._resolve_voice(voice)
        try:
            with self._lock:
                v = self._get_voice(name)
                sr = v.config.sample_rate
                cfg = self._syn_config(speed)
                chunks = [c.audio_float_array for c in v.synthesize(text.replace("*", ""), syn_config=cfg)]
            audio = np.concatenate(chunks) if chunks else np.zeros(1, dtype="float32")
            audio = self._pitch_shift(audio, sr, kwargs.get("pitch"))
            return self._encode_opus(audio, sr)
        except Exception as e:
            logger.error(f"[piper] generate failed (voice={name}): {e!r}")
            return None

    def generate_stream(self, text: str, voice: str, speed: float, **kwargs) -> Iterator[bytes]:
        """Yield one OGG/Opus blob per sentence (synthesize() streams per-sentence).
        Falls back to one-shot generate() only if NOTHING was yielded yet."""
        if not text or not text.strip():
            return
        name = self._resolve_voice(voice)
        yielded = 0
        try:
            with self._lock:
                v = self._get_voice(name)
                sr = v.config.sample_rate
                cfg = self._syn_config(speed)
                for chunk in v.synthesize(text.replace("*", ""), syn_config=cfg):
                    # Constant per-chunk ratio — concatenated shifted chunks
                    # equal the shifted concatenation, so no boundary artifacts.
                    blob = self._encode_opus(
                        self._pitch_shift(chunk.audio_float_array, sr, kwargs.get("pitch")), sr)
                    if blob:
                        yielded += 1
                        yield blob
        except Exception as e:
            logger.error(f"[piper] stream failed after {yielded} chunk(s) (voice={name}): {e!r}")
            if yielded == 0:
                audio = self.generate(text, voice, speed, **kwargs)
                if audio:
                    yield audio

    def is_available(self) -> bool:
        try:
            import piper  # noqa: F401
            return True
        except Exception:
            return False

    # --- pre-download hooks: fetch the selected voice off the hot path + toast ---
    def _ensure_voice_async(self, name: str):
        """Background-fetch `name` if it's a Piper voice we don't have yet. No-op
        for Kokoro/other voices and for voices already on disk (so it's safe to
        call on every voice change / chat load)."""
        if not name or not _is_piper_voice(name) or self._model_path(name).exists():
            return
        threading.Thread(target=self._warm_download, args=(name,), daemon=True).start()

    def on_voice_selected(self, voice: str):
        """Called by TTSClient.set_voice() whenever the active voice changes — the
        real chokepoint (chat voice dropdown, chat load). This is the primary
        pre-download trigger; pre-fetches so the first utterance isn't stalled."""
        self._ensure_voice_async(self._resolve_voice(voice))

    def on_settings_saved(self, plugin_name: str = None, settings: dict = None):
        """Called by core when a plugin's settings are saved (Settings → Plugins →
        Piper default voice). Secondary trigger — the chat dropdown path goes
        through on_voice_selected instead."""
        if plugin_name not in (None, "piper"):
            return
        name = ((settings or {}).get("voice") or "").strip() or self._voice_name
        self._ensure_voice_async(name)

    def _warm_download(self, name: str):
        from core.event_bus import publish, Events
        label = name.replace("en_US-", "").replace("-", " ")
        try:
            publish(Events.PLUGIN_NOTICE, {"plugin": "piper",
                    "message": f"Downloading voice: {label}…", "severity": "info"})
            self._download_voice(name)
            publish(Events.PLUGIN_NOTICE, {"plugin": "piper",
                    "message": f"Voice ready: {label}", "severity": "success"})
        except Exception as e:
            logger.error(f"[piper] warm download failed for '{name}': {e!r}")
            publish(Events.PLUGIN_NOTICE, {"plugin": "piper",
                    "message": f"Voice download failed: {label}", "severity": "error"})

    def list_voices(self) -> List[dict]:
        """Curated voices (the manifest dropdown). Marks which are downloaded."""
        curated = [
            ("en_US-hfc_female-medium", "HFC Female (medium)", "American Female"),
            ("en_US-kristin-medium", "Kristin (medium)", "American Female"),
            ("en_US-lessac-medium", "Lessac (medium)", "American Female"),
            ("en_US-ljspeech-medium", "LJSpeech (medium)", "American Female"),
            ("en_US-amy-medium", "Amy (medium)", "American Female"),
            ("en_US-amy-low", "Amy (low)", "American Female"),
            ("en_US-kathleen-low", "Kathleen (low)", "American Female"),
            ("en_US-lessac-low", "Lessac (low)", "American Female"),
        ]
        return [{"voice_id": vid, "name": nm, "category": cat,
                 "downloaded": self._model_path(vid).exists()} for vid, nm, cat in curated]
