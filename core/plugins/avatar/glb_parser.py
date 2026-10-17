"""GLB/GLTF animation track extraction — pure Python, no deps."""

import json
import struct
from pathlib import Path


def extract_tracks(glb_path):
    """Extract animation track info from a GLB file.

    Returns list of dicts: [{"name": str, "duration": float, "channels": int}]
    """
    path = Path(glb_path)
    if not path.exists():
        return []

    try:
        with open(path, 'rb') as f:
            magic, version, length = struct.unpack('<III', f.read(12))
            if magic != 0x46546C67:  # 'glTF'
                return []

            chunk_len, chunk_type = struct.unpack('<II', f.read(8))
            gltf = json.loads(f.read(chunk_len).decode('utf-8'))
    except Exception:
        return []

    tracks = []
    for i, anim in enumerate(gltf.get('animations', [])):
        name = anim.get('name', f'track_{i}')
        channels = len(anim.get('channels', []))

        # Duration from sampler input accessor max values
        max_time = 0
        for s in anim.get('samplers', []):
            acc_idx = s.get('input')
            if acc_idx is not None and acc_idx < len(gltf.get('accessors', [])):
                acc = gltf['accessors'][acc_idx]
                if 'max' in acc:
                    max_time = max(max_time, acc['max'][0])

        tracks.append({
            'name': name,
            'duration': round(max_time, 2),
            'channels': channels,
        })

    return tracks


def get_model_info(glb_path):
    """Get basic model info: meshes, materials, skeleton, tracks."""
    path = Path(glb_path)
    if not path.exists():
        return None

    try:
        with open(path, 'rb') as f:
            magic, version, length = struct.unpack('<III', f.read(12))
            if magic != 0x46546C67:
                return None

            chunk_len, chunk_type = struct.unpack('<II', f.read(8))
            gltf = json.loads(f.read(chunk_len).decode('utf-8'))
    except Exception:
        return None

    skins = gltf.get('skins', [])
    return {
        'meshes': len(gltf.get('meshes', [])),
        'materials': len(gltf.get('materials', [])),
        'textures': len(gltf.get('textures', [])),
        'joints': len(skins[0].get('joints', [])) if skins else 0,
        'tracks': extract_tracks(glb_path),
    }


# Common animation name patterns for auto-mapping. Keys must match a state
# name that some transition in sidebar.js actually targets, OR be 'wave'
# which feeds the greeting_track default. Adding new keys without a runtime
# consumer just populates dead entries in the generated track_map.
AUTO_MAP = {
    'idle':        ['idle', 'stand', 'standing', 'rest', 'default', 'breathe', 'breathing'],
    'processing':  ['thinking', 'think', 'ponder', 'concentrate', 'focus', 'plotting'],
    'typing':      ['typing', 'type', 'keyboard', 'defaultanim', 'compose', 'writing', 'texting'],
    'listening':   ['listening', 'listen', 'hear', 'attentive', 'look', 'lookaround', 'listening_nod'],
    'speaking':    ['speaking', 'speak', 'talk', 'talking', 'say', 'attention'],
    'toolcall':    ['action', 'use', 'grab', 'reach', 'attention2', 'interact', 'typing'],
    'happy':       ['happy', 'joy', 'celebrate', 'cheer', 'smile', 'excited', 'victory', 'clapping', 'laughing'],
    'wakeword':    ['alert', 'surprise', 'startle', 'notice', 'greeting', 'wave'],
    'wave':        ['wave', 'greet', 'greeting', 'hello', 'hi', 'bye', 'farewell', 'blow_kiss'],
    'user_typing': ['curious', 'notice', 'perk', 'attentive', 'idle_curious', 'lookaround'],
    'reading':     ['read', 'reading', 'look_down', 'study', 'typing_phone'],
}


def auto_map_tracks(track_names):
    """Attempt to map avatar states to track names by common patterns.

    Returns dict of {state: track_name} for matches found.
    """
    lower_map = {t.lower(): t for t in track_names}
    result = {}

    for state, patterns in AUTO_MAP.items():
        for pattern in patterns:
            if pattern in lower_map:
                result[state] = lower_map[pattern]
                break

    return result


def build_default_config(track_names):
    """Build a default config for a newly uploaded model."""
    mapped = auto_map_tracks(track_names)

    # Default track map — fill unmapped states with idle or first track.
    # Keep this list aligned with AVATAR_STATES in web/index.js.
    fallback = mapped.get('idle', track_names[0] if track_names else 'idle')
    track_map = {}
    for state in ['idle', 'processing', 'typing', 'listening', 'speaking', 'toolcall', 'happy', 'wakeword', 'agent', 'cron', 'user_typing', 'reading']:
        track_map[state] = mapped.get(state, fallback)

    # Behavior pool — every track included. Each entry has roles:
    #   base:    list of time-bucket names this track can be a resting pose for
    #   variety: eligible as an occasional overlay (fires every N minutes)
    #   weight:  relative frequency for variety random selection
    # Smart defaults: sleep tracks → night base; idle/stand/breathing → day
    # base; everything is variety-eligible so the pool starts full (the
    # settings UI's check-all/uncheck-all lets the user prune).
    night_patterns = ('sleep', 'lie', 'lying')
    day_patterns = ('idle', 'stand', 'standing', 'rest', 'breathe', 'breathing')
    idle_pool = []
    for name in track_names:
        lower = name.lower()
        base = []
        if any(p in lower for p in night_patterns):
            base.append('night')
        elif any(p in lower for p in day_patterns):
            base.append('day')
        idle_pool.append({'track': name, 'weight': 10, 'variety': True, 'base': base})

    # Guarantee each shipped bucket has at least one base track — fall back
    # to the auto-mapped idle track so she always has a resting pose.
    idle_track = mapped.get('idle', fallback)
    for bucket in ('day', 'night'):
        if not any(bucket in e['base'] for e in idle_pool):
            for e in idle_pool:
                if e['track'] == idle_track:
                    e['base'].append(bucket)
                    break

    greeting = mapped.get('wave', None)

    return {
        'track_map': track_map,
        'idle_pool': idle_pool,
        # N-bucket ready; ships with day/night. start = hour (24h) the bucket
        # begins; the latest start <= current hour wins (wrapping midnight).
        # quiet = no variety overlays fire (she rests undisturbed). Night
        # defaults quiet so she sleeps in peace.
        'time_buckets': [
            {'name': 'day', 'start': 7, 'quiet': False},
            {'name': 'night', 'start': 21, 'quiet': True},
        ],
        'variety_interval_min': 2,
        'greeting_track': greeting,
        'camera': {'x': 0, 'y': 1.3, 'z': 4.4},
        'target': {'x': 0, 'y': 1.1, 'z': 0},
    }
