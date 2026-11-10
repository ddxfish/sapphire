"""Inject avatar animation instructions into the system prompt."""

import json
import struct
from pathlib import Path

from core.paths import PROJECT_ROOT

USER_AVATAR_DIR = PROJECT_ROOT / "user" / "avatar"


def _get_active_model():
    """Get active model filename from plugin state."""
    try:
        from core.plugin_loader import plugin_loader
        state = plugin_loader.get_plugin_state("avatar")
        return state.get("active_model", "sapphire.glb")
    except Exception:
        return "sapphire.glb"


def _get_track_names(glb_path):
    """Extract animation track names from a GLB file."""
    if not glb_path.exists():
        return []
    try:
        with open(glb_path, 'rb') as f:
            _magic, _version, _length = struct.unpack('<III', f.read(12))
            chunk_len, _chunk_type = struct.unpack('<II', f.read(8))
            gltf = json.loads(f.read(chunk_len).decode('utf-8'))
            return [a.get('name', f'track_{i}') for i, a in enumerate(gltf.get('animations', []))]
    except Exception:
        return []


def state_stage():
    try:
        from core.plugin_loader import plugin_loader
        return plugin_loader.get_plugin_state("avatar").get("stage") or {}
    except Exception:
        return {}


def prompt_inject(event):
    # Check if prompt injection is enabled
    try:
        from core.plugin_loader import plugin_loader
        state = plugin_loader.get_plugin_state("avatar")
        # Enabled switch (accordion) and inject_prompt (settings page) both gate
        if not state.get("enabled", True) or not state.get("inject_prompt", True):
            return
    except Exception:
        pass

    model = _get_active_model()
    glb_path = USER_AVATAR_DIR / model
    tracks = _get_track_names(glb_path)
    if not tracks:
        return

    track_list = ', '.join(tracks)
    event.context_parts.append(
        f"\n[Avatar]\n"
        f"You have a 3D animated avatar visible to the user. "
        f"Trigger animations with <<avatar: trackname>> — the track plays once and "
        f"your avatar returns to its normal behavior. "
        f"For sustained moods, add `loop`: <<avatar: sleep loop>> holds the pose "
        f"until you call another <<avatar: ...>> tag. Use loop for ongoing states "
        f"(sleep, dance, meditate); leave it off for reactions (wave, nod, celebrate). "
        f"ONLY use track names from this list — do not invent new ones: {track_list}. "
        f"The tags are visible in chat as part of your expression. Don't overuse them."
    )
    stage = state_stage()
    if stage.get("on"):
        seats = [s for s in ("couch", "desk", "bed") if stage.get(s)] + (["chair"] if stage.get("chair") is not None else [])
        chair = (" You can sit" + (" (the bed is for lying down)" if "bed" in seats else "") + ": " + ", ".join(f"<<avatar: go_{s}>>" for s in seats) +
                 ". Seated you still talk and gesture from the waist up.") if seats else ""
        event.context_parts.append(
            "Right now your avatar stands in the chat window itself, in front of the messages "
            "(you go see-through where you cross one). "
            "You can walk: <<avatar: go_left>>, <<avatar: go_right>>, <<avatar: go_center>>, or "
            "<<avatar: go_0>> to <<avatar: go_100>> (percent across, 0 = far left). "
            "<<avatar: go_back>> walks deeper into the scene (you look further away), "
            "<<avatar: go_front>> comes back up close." + chair +
            " Move when it means something. The middle is over the text, so it is a visit: "
            "after a moment there you step back to an edge on your own."
        )
