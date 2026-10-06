# core/audio/playback.py - a sound out of this machine's speakers
#
# For callers that are not her voice: a chime, a persona speaking through the
# MCP door. tts_client plays her own speech itself, with its stop and its
# events, and nothing here touches that. One sound at a time: a second caller
# waits its turn.
import io
import logging
import threading

import numpy as np

logger = logging.getLogger(__name__)

CHUNK = 0.1                    # seconds per write, so a stop lands fast
_turn = threading.Lock()       # one sound at a time
_stop = threading.Event()      # stop(): the sound playing now ends at its next chunk


def stop():
    """End the sound that is playing now. A sound still waiting its turn plays."""
    _stop.set()


def _fit(samples, rate, to_rate):
    if rate == to_rate:
        return samples
    n = int(len(samples) * to_rate / rate)
    if n <= 0:
        return samples[:0]
    return np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples).astype(np.float32)


def play(audio, wait=30):
    """Play `audio` on the output device and return when it has ended.
    `audio` is the bytes of a wav, ogg or flac, or a path to one. `wait` is how
    long to stand in line behind another sound. Returns (ok, reason): the
    reason is '' for a sound played to its end."""
    import soundfile as sf
    from core.audio import get_device_manager
    from core.audio.backend import sd
    try:
        source = io.BytesIO(audio) if isinstance(audio, (bytes, bytearray)) else str(audio)
        samples, rate = sf.read(source, dtype='float32')
    except Exception as e:
        return False, f"That sound could not be read: {e}"
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    if not len(samples):
        return False, "That sound is empty."
    device, device_rate, name = get_device_manager().find_output_device()
    if device is None:
        return False, "This machine has no sound output."
    if not _turn.acquire(timeout=wait):
        return False, f"The speakers stayed busy for {wait}s."
    try:
        _stop.clear()
        refused = None
        for use in dict.fromkeys((rate, device_rate)):   # the sound's own rate first, the device's when it refuses that
            if not use:
                continue
            data = _fit(samples, rate, use)
            step = max(1, int(use * CHUNK))
            started = False
            try:
                with sd.OutputStream(samplerate=use, device=device, channels=1, dtype='float32') as out:
                    for i in range(0, len(data), step):
                        if _stop.is_set():
                            return True, "Stopped."
                        out.write(data[i:i + step].reshape(-1, 1))
                        started = True
                return True, ''
            except sd.PortAudioError as e:
                refused = e
                if started:                                # it broke mid-sound: say so, never play it twice
                    break
        logger.warning(f"[AUDIO] output '{name}' refused a sound: {refused}")
        return False, f"The output device '{name}' refused the sound: {refused}"
    finally:
        _turn.release()
