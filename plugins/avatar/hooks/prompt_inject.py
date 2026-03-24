"""Inject avatar animation instructions into the system prompt."""

import json
import struct
from pathlib import Path

USER_AVATAR_DIR = Path(__file__).parent.parent.parent.parent / "user" / "avatar"


def _get_track_names():
    """Extract animation track names from the active GLB model."""
    # For now, hardcoded to sapphire.glb — will read from plugin state config later
    glb_path = USER_AVATAR_DIR / "sapphire.glb"
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


def run(event):
    tracks = _get_track_names()
    if not tracks:
        return

    track_list = ', '.join(tracks)
    event.context_parts.append(
        f"\n[Avatar]\n"
        f"You have a 3D animated avatar visible to the user. "
        f"You can trigger animations by including <<avatar: trackname>> in your responses. "
        f"Available tracks: {track_list}. "
        f"Use these naturally to express yourself — wave when greeting, "
        f"show happy when celebrating, use attention when something catches your interest. "
        f"The tags are visible in chat as part of your expression. Don't overuse them."
    )
