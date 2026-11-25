"""Kokoro's files: the model, its config and the voices, resolved from the
Hugging Face cache by the PARENT before the worker spawns.

The worker (tts_server.py) downloads what it lacks, but it is a subprocess
with no line to the notification center, so a first boot on a slow line
looked like "Kokoro never comes up" (user report 2026-10-20). The parent
fetches first, under one progress row, then spawns; the worker finds
everything cached. The worker keeps its own download as the fallback (a
standalone run, a file renamed upstream), so a miss here costs the progress
row, never the voice. Voices picked later download inside the worker, half
a megabyte each, with no row.
"""
import logging

from core.hf_words import why

logger = logging.getLogger(__name__)

REPO_ID = 'hexgrad/Kokoro-82M'
CONFIG_FILE = 'config.json'
MODEL_FILE = 'kokoro-v1_0.pth'      # KModel.MODEL_NAMES[REPO_ID]: the worker reads the authority, this is the pre-fetch
DEFAULT_VOICE = 'af_heart'


def voice_file(name):
    return f'voices/{name}.pt'


def missing(voices=(DEFAULT_VOICE,)):
    """The repo files not in the local cache, in download order."""
    from huggingface_hub import try_to_load_from_cache
    out = []
    for f in [CONFIG_FILE, MODEL_FILE] + [voice_file(v) for v in voices]:
        try:
            cached = try_to_load_from_cache(REPO_ID, f)
        except Exception:
            cached = None
        if not isinstance(cached, str):
            out.append(f)
    return out


last_error = None       # why the last fetch() failed, in words; None when it worked


def fetch(files):
    """Download `files` through the download door (core/hf_fetch.py), under one
    progress row in the notification center. True when they all landed; False
    (logged, last_error and the row carry the cause in words) when they did
    not. The caller then leaves the worker OFF: started anyway it tried the
    same download itself with no row, and its monitor restarted it to try
    again for as long as Sapphire ran."""
    global last_error
    last_error = None
    if not files:
        return True
    from core import hf_fetch
    from core.notify import progress
    try:
        with progress("Downloading the Kokoro voice model", source='core',
                      go={'view': 'settings', 'tab': 'tts'}, key='kokoro-model',
                      done_title='Kokoro voice model ready',
                      failed_title='Kokoro voice model download failed', words=why,
                      retry='switch:TTS_PROVIDER=kokoro') as p:
            hf_fetch.fetch(REPO_ID, files=files, handle=p)
        return True
    except Exception as e:
        last_error = why(e)
        logger.warning(f"[TTS] Kokoro files not fetched ({type(e).__name__}: {last_error}); the worker stays off")
        return False
