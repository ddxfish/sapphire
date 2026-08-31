# Webcam capture tool — lets the AI request a snapshot from the user's camera.
#
# Flow:
# 1. AI calls capture_webcam
# 2. Tool blocks, waiting for browser to deliver an image
# 3. Browser JS detects tool_start event, captures webcam, POSTs image
# 4. Tool unblocks, returns image via standard tool image infrastructure
#
# Shared state: both this tool and routes/capture.py need access to the same
# pending capture slot. Since plugin_loader uses exec() for each file, they
# get isolated namespaces. We use sys.modules to store a shared state module
# that both sides can access.

import logging
import secrets
import sys
import threading
import types

logger = logging.getLogger(__name__)

# Shared state key — used by both tool and route handler
WEBCAM_STATE_KEY = '_sapphire_webcam_state'

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "capture_webcam",
            "description": "Capture a photo from the user's webcam/camera. The browser will prompt for camera access if needed. Returns the image for visual analysis. Use this when the user asks you to look at something, see them, or when visual context would help.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }
]


def _get_shared_state():
    """Get or create shared state accessible by both tool and route handler."""
    if WEBCAM_STATE_KEY not in sys.modules:
        mod = types.ModuleType(WEBCAM_STATE_KEY)
        mod.lock = threading.Lock()
        mod.pending = {"event": None, "nonce": None, "image": None}
        sys.modules[WEBCAM_STATE_KEY] = mod
    return sys.modules[WEBCAM_STATE_KEY]


def execute(function_name, arguments, config):
    if function_name == "capture_webcam":
        return _capture()
    return f"Unknown function: {function_name}", False


def _capture():
    """Request a webcam capture from the browser and wait for delivery."""
    state = _get_shared_state()
    nonce = secrets.token_urlsafe(16)
    event = threading.Event()

    with state.lock:
        state.pending["event"] = event
        state.pending["nonce"] = nonce
        state.pending["image"] = None

    logger.info("[WEBCAM] Waiting for browser capture (timeout=15s)")
    event.wait(timeout=15)

    with state.lock:
        image = state.pending["image"]
        state.pending["event"] = None
        state.pending["nonce"] = None
        state.pending["image"] = None

    if not image:
        return "Webcam capture timed out — the browser didn't respond. The user may need to grant camera permission or have the web UI open.", False

    # Browser reported an error (insecure context, permission denied, etc.)
    if "error" in image:
        return image["error"], False

    return {
        "text": "Webcam snapshot captured successfully",
        "images": [image]
    }, True
