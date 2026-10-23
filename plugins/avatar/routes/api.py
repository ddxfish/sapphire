"""Avatar plugin routes — model management and config."""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

AVATAR_DIR = Path(__file__).parent.parent.parent.parent / "user" / "avatar"
MAX_SIZE = 50 * 1024 * 1024  # 50MB hard limit
_WIN_RESERVED = ({'con', 'prn', 'aux', 'nul'}
                 | {f'com{i}' for i in range(1, 10)}
                 | {f'lpt{i}' for i in range(1, 10)})


def _get_state():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_state("avatar")


_seeded = False

def _get_config():
    """Load full avatar config from plugin state. Auto-seeds defaults once for unconfigured models."""
    global _seeded
    state = _get_state()
    active = state.get('active_model', 'sapphire.glb')
    models = state.get('models', {})

    # Auto-seed only once per process
    if not _seeded:
        from plugins.avatar.glb_parser import extract_tracks, build_default_config
        AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        changed = False
        for f in AVATAR_DIR.glob('*.glb'):
            if f.name not in models:
                tracks = extract_tracks(f)
                if tracks:
                    models[f.name] = build_default_config([t['name'] for t in tracks])
                    changed = True
        if changed:
            state.save('models', models)
        _seeded = True

    return {
        'active_model': active,
        'models': models,
        'enabled': state.get('enabled', True),
        'inject_prompt': state.get('inject_prompt', True),
        'strip_tags': state.get('strip_tags', False),
        'user_tags': state.get('user_tags', False),
    }


def _save_config(cfg):
    """Save avatar config to plugin state."""
    state = _get_state()
    state.save('active_model', cfg.get('active_model', 'sapphire.glb'))
    state.save('models', cfg.get('models', {}))


# GET /api/plugin/avatar/models
async def list_models(**kwargs):
    """List available avatar models (lightweight — no GLB parsing)."""
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    cfg = _get_config()
    models_cfg = cfg.get('models', {})
    models = []

    for f in sorted(AVATAR_DIR.glob('*.glb')):
        model_cfg = models_cfg.get(f.name, {})
        track_count = len(model_cfg.get('track_map', {})) or len(model_cfg.get('idle_pool', []))
        models.append({
            'filename': f.name,
            'size': f.stat().st_size,
            'active': f.name == cfg.get('active_model'),
            'configured': bool(model_cfg),
            'track_count': track_count,
        })

    return {'models': models, 'active_model': cfg.get('active_model')}


# POST /api/plugin/avatar/upload
async def upload_model(**kwargs):
    """Upload a new GLB model. Multipart form: file field."""
    from plugins.avatar.glb_parser import extract_tracks, build_default_config

    request = kwargs.get('request')
    if not request:
        return {'error': 'No request'}, 400

    # Read multipart form
    form = await request.form()
    file = form.get('file')
    if not file:
        return {'error': 'No file uploaded'}

    # Validate filename
    filename = file.filename
    if not filename.lower().endswith('.glb'):
        return {'error': 'Only .glb files are supported'}

    # Sanitize filename
    safe_name = ''.join(c for c in filename if c.isalnum() or c in '.-_').strip('.')
    if not safe_name:
        safe_name = 'avatar.glb'
    if not safe_name.lower().endswith('.glb'):
        safe_name += '.glb'
    # CON/NUL-class stems are device files on Windows
    if safe_name.split('.')[0].lower() in _WIN_RESERVED:
        safe_name = '_' + safe_name

    # Read file with size check
    content = await file.read()
    if len(content) > MAX_SIZE:
        return {'error': f'File too large ({len(content) // 1024 // 1024}MB). Max is 50MB.'}

    # Validate GLB magic bytes
    if len(content) < 12 or content[:4] != b'glTF':
        return {'error': 'Invalid GLB file'}

    # Save
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    dest = AVATAR_DIR / safe_name
    dest.write_bytes(content)

    # Extract tracks and build default config
    tracks = extract_tracks(dest)
    track_names = [t['name'] for t in tracks]
    default_cfg = build_default_config(track_names)

    # Save default config for this model if not already configured
    cfg = _get_config()
    if safe_name not in cfg.get('models', {}):
        if 'models' not in cfg:
            cfg['models'] = {}
        cfg['models'][safe_name] = default_cfg

    # If this is the first model, make it active
    if not cfg.get('active_model') or not (AVATAR_DIR / cfg['active_model']).exists():
        cfg['active_model'] = safe_name

    _save_config(cfg)

    return {
        'filename': safe_name,
        'tracks': tracks,
        'config': default_cfg,
    }


# DELETE /api/plugin/avatar/models/{filename}
async def delete_model(**kwargs):
    """Delete an avatar model."""
    filename = kwargs.get('filename', '')

    # Sanitize: charset check (not just slash check) — on Windows a ':' makes
    # 'C:evil' drive-relative and escapes AVATAR_DIR entirely.
    if (not filename or not all(c.isalnum() or c in '.-_' for c in filename)
            or filename.split('.')[0].lower() in _WIN_RESERVED):
        return {'error': 'Invalid filename'}

    path = AVATAR_DIR / filename
    if not path.exists():
        return {'error': 'Model not found'}

    path.unlink()

    # Remove config and switch active if needed
    cfg = _get_config()
    cfg.get('models', {}).pop(filename, None)
    if cfg.get('active_model') == filename:
        remaining = list(AVATAR_DIR.glob('*.glb'))
        cfg['active_model'] = remaining[0].name if remaining else ''

    _save_config(cfg)
    return {'deleted': filename, 'active_model': cfg.get('active_model')}


# GET /api/plugin/avatar/tracks/{filename}
async def get_tracks(**kwargs):
    """Extract tracks from a specific model."""
    from plugins.avatar.glb_parser import extract_tracks

    filename = kwargs.get('filename', '')
    if not filename or '/' in filename or '\\' in filename:
        return {'error': 'Invalid filename'}

    path = AVATAR_DIR / filename
    if not path.exists():
        return {'error': 'Model not found'}

    return {'filename': filename, 'tracks': extract_tracks(path)}


# GET /api/plugin/avatar/config
async def get_config(**kwargs):
    """Get full avatar config."""
    return _get_config()


# PUT /api/plugin/avatar/config
async def save_config(**kwargs):
    """Save avatar config (active model, track mappings, idle pool, etc.)."""
    body = kwargs.get('body', {})
    if not body:
        return {'error': 'No config provided'}

    cfg = _get_config()

    # Update active model
    if 'active_model' in body:
        model_file = body['active_model']
        if model_file and (AVATAR_DIR / model_file).exists():
            cfg['active_model'] = model_file

    # Update per-model config
    if 'models' in body:
        if 'models' not in cfg:
            cfg['models'] = {}
        for model_name, model_cfg in body['models'].items():
            cfg['models'][model_name] = model_cfg

    # Update global avatar settings
    if 'enabled' in body:
        # The accordion's Enabled switch (Krem 2026-08-23): off = no render
        # (frees the browser's GPU share) AND no prompt instructions.
        _get_state().save('enabled', bool(body['enabled']))
    if 'inject_prompt' in body:
        state = _get_state()
        state.save('inject_prompt', bool(body['inject_prompt']))
    if 'strip_tags' in body:
        state = _get_state()
        state.save('strip_tags', bool(body['strip_tags']))
    if 'user_tags' in body:
        state = _get_state()
        state.save('user_tags', bool(body['user_tags']))

    # Update active location
    if 'active_location' in body:
        cfg['active_location'] = body['active_location']

    # Shorthand: update just the active model's config
    _model_keys = ('track_map', 'idle_pool', 'base_state', 'time_buckets',
                   'variety_interval_min', 'greeting_track', 'camera', 'target')
    if any(k in body for k in _model_keys):
        active = cfg.get('active_model', '')
        if active:
            if active not in cfg.get('models', {}):
                cfg['models'][active] = {}
            for key in _model_keys:
                if key in body:
                    cfg['models'][active][key] = body[key]

    _save_config(cfg)
    return cfg
