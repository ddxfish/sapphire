"""A Hugging Face Hub failure in words a person can act on.

The hub wraps the real cause of a failed download in LocalEntryNotFoundError,
whose words are "check your connection ... make sure your Internet connection
is on". When the cause is the hub's own offline mode (HF_HUB_OFFLINE or
TRANSFORMERS_OFFLINE in the environment, read once at import), the connection
is fine and the fix is the variable; a Windows user found that only in a
Python traceback (user report 2026-10-21). why() is for wherever a hub error
reaches a person: a notification row (notify.progress words=), kokoro.log,
the TTS Test button. Sapphire never sets or clears these variables itself.
"""
import os

OFFLINE_VARS = ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE')
_TRUE = ('1', 'ON', 'YES', 'TRUE')          # what the hub counts as on (huggingface_hub.constants._is_true)


def offline_vars():
    """The offline variables set in this environment, as the hub reads them."""
    return [k for k in OFFLINE_VARS if os.environ.get(k, '').upper() in _TRUE]


def why(e):
    """str(e), except an offline-mode failure (anywhere in the cause chain)
    names the variable and the fix."""
    try:
        from huggingface_hub.errors import OfflineModeIsEnabled
    except Exception:                       # no hub here: nothing of its own to translate
        return str(e) or type(e).__name__
    cause, hops = e, 0
    while cause is not None and hops < 8:
        if isinstance(cause, OfflineModeIsEnabled):
            names = ' and '.join(offline_vars()) or 'HF_HUB_OFFLINE'
            return (f"Hugging Face offline mode is on in this computer's environment ({names}), "
                    "so a model or voice not downloaded yet cannot be fetched. Clear that variable "
                    "and restart Sapphire; see Troubleshooting, 'offline mode is on'.")
        cause, hops = cause.__cause__, hops + 1
    return str(e) or type(e).__name__
