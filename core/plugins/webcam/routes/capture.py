# Webcam capture route handlers
# Receives images from the browser during capture_webcam tool execution.
#
# Shares state with tools/webcam.py via sys.modules (see WEBCAM_STATE_KEY).
# The tool blocks on a threading.Event; these handlers check/deliver the image.

import base64
import logging
import sys
import threading
import types

logger = logging.getLogger(__name__)

WEBCAM_STATE_KEY = '_sapphire_webcam_state'
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10MB


def _get_shared_state():
    """Get or create shared state accessible by both tool and route handler."""
    if WEBCAM_STATE_KEY not in sys.modules:
        mod = types.ModuleType(WEBCAM_STATE_KEY)
        mod.lock = threading.Lock()
        mod.pending = {"event": None, "nonce": None, "image": None}
        sys.modules[WEBCAM_STATE_KEY] = mod
    return sys.modules[WEBCAM_STATE_KEY]


def get_pending(**_):
    """GET /api/plugin/webcam/pending — check if a capture is pending."""
    state = _get_shared_state()
    with state.lock:
        event = state.pending["event"]
        if event and not event.is_set():
            return {"pending": True, "nonce": state.pending["nonce"]}
    return {"pending": False}


def handle_capture(body, **_):
    """POST /api/plugin/webcam/capture — deliver a captured image or error."""
    if not body:
        return {"error": "Empty request body"}

    nonce = body.get("nonce")

    # Browser can report errors (insecure context, permission denied, etc.)
    browser_error = body.get("error")
    if nonce and browser_error:
        state = _get_shared_state()
        with state.lock:
            event = state.pending["event"]
            if event and not event.is_set() and nonce == state.pending["nonce"]:
                state.pending["image"] = {"error": browser_error}
                event.set()
        return {"status": "ok", "accepted": "error"}

    data = body.get("data")
    media_type = body.get("media_type", "image/jpeg")

    if not nonce or not data:
        return {"error": "Missing nonce or data"}

    state = _get_shared_state()
    with state.lock:
        event = state.pending["event"]
        if not event or event.is_set():
            return {"error": "No pending capture request"}
        if nonce != state.pending["nonce"]:
            logger.warning("[WEBCAM] Nonce mismatch — rejecting capture")
            return {"error": "Invalid nonce"}

        # Validate image size
        try:
            raw = base64.b64decode(data)
            if len(raw) > MAX_IMAGE_BYTES:
                return {"error": f"Image too large ({len(raw)} bytes, max {MAX_IMAGE_BYTES})"}
        except Exception:
            return {"error": "Invalid base64 data"}

        # Validate media type
        if media_type not in ("image/jpeg", "image/png", "image/webp"):
            return {"error": f"Unsupported media type: {media_type}"}

        state.pending["image"] = {"data": data, "media_type": media_type}
        event.set()

    return {"status": "ok"}
