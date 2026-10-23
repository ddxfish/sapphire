"""Setup wizard state and its featured plugins (tmp/setup-wizard-plan.md, 2026-10-22).

The wizard is the `#setup` view (interfaces/web/static/views/setup.js): it hosts
the real Settings tab modules, so there is nothing provider-shaped here. This
module owns three things only: where the person is (`SETUP_STEP`), whether they
finished (`SETUP_COMPLETE`), when they consented (`SETUP_CONSENT`, stamped HERE,
never by the client), and the plugins the wizard offers to turn on.

The old wizard's `SETUP_WIZARD_STEP` (an int) is migrated by the settings
manager on load and never reused.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from core.auth import require_login
from core.settings_manager import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Every step the wizard can show, in order. The server list is the resume
# vocabulary: a stored step outside it (a step that left, a typo) starts over.
ALL_STEPS = ('consent', 'you', 'brain', 'voice', 'audio', 'gate', 'plugins', 'look', 'next')

# Package sets the voice step can install under their own row (core/extras.py).
PACKAGE_SETS = ('tts', 'stt', 'wakeword')

# The plugins the wizard offers, in this order: (name, group, one line). A
# group is a radio pair (enable one, not both). A plugin the loader did not
# scan (managed_hide, or not shipped) is dropped. Curated here, one place.
FEATURED = (
    ('mindpalace', 'mind', 'Layered memory: self, events, people and knowledge behind one tool surface.'),
    ('memory', 'mind', 'The classic long-term memory, knowledge base, goals and people.'),
    ('discord', None, 'A Discord bot on her conversation engine: chat, images, voice channels.'),
    ('telegram', None, 'Talk to her from Telegram, as a bot or from your own account.'),
    ('piper', None, 'A fast local voice for CPUs and weak hardware.'),
    ('remembrance', None, 'Encrypted offsite backups to the Remembrance vault.'),
)


def step_ids():
    """The steps that apply to this install: no audio devices in Docker, and
    managed mode has no local voice or audio at all (the router speaks)."""
    ids = list(ALL_STEPS)
    if settings.is_managed():
        ids = [s for s in ids if s not in ('voice', 'audio')]
    elif settings.is_docker():
        ids.remove('audio')
    return ids


def _packages():
    try:
        from core import extras
        return {name: bool(extras.installed(name)) for name in PACKAGE_SETS}
    except Exception as e:
        logger.debug(f"[setup] package sets not read: {e}")
        return {name: True for name in PACKAGE_SETS}


def state():
    ids = step_ids()
    step = settings.get('SETUP_STEP', '') or ''
    return {
        'complete': bool(settings.get('SETUP_COMPLETE', False)),
        'step': step if step in ids else '',
        'consent': settings.get('SETUP_CONSENT', None) or None,
        'steps': ids,
        'managed': settings.is_managed(),
        'docker': settings.is_docker(),
        'packages': _packages(),
    }


def apply(data):
    """Validate and store a state change: `complete` (bool), `step` (a known id
    or '' to clear), `consent` (true: the server stamps the time). Unknown keys
    are ignored; a bad value is a 400 and nothing is stored."""
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Expected an object")
    writes = {}
    if 'complete' in data:
        if not isinstance(data['complete'], bool):
            raise HTTPException(status_code=400, detail="complete must be true or false")
        writes['SETUP_COMPLETE'] = data['complete']
    if 'step' in data:
        step = data['step']
        if not isinstance(step, str) or (step and step not in step_ids()):
            raise HTTPException(status_code=400, detail=f"Unknown step: {step!r}")
        writes['SETUP_STEP'] = step
    if 'consent' in data:
        if data['consent'] is not True:
            raise HTTPException(status_code=400, detail="consent is given, never taken back here")
        writes['SETUP_CONSENT'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    for key, value in writes.items():
        settings.set(key, value, persist=True)
    return state()


@router.get("/api/setup/state")
async def get_state(request: Request, _=Depends(require_login)):
    """Where setup stands: complete, the step to resume at, consent time, the
    steps that apply here, managed/docker, and which voice package sets are in."""
    return state()


@router.put("/api/setup/state")
async def put_state(request: Request, _=Depends(require_login)):
    """`{complete?: bool, step?: id|'', consent?: true}` -> the new state."""
    return apply(await request.json())


def _has_settings(info):
    manifest = info.get('manifest') or {}
    ui = manifest.get('settingsUI', 'auto')
    if ui is None or ui == 'none':
        return False
    if ui in ('plugin', 'manifest', 'core'):
        return True
    path = info.get('path') or ''
    if path and (Path(path) / 'web' / 'index.js').exists():
        return True
    return bool((manifest.get('capabilities') or {}).get('settings'))


def featured():
    try:
        from core.plugin_loader import plugin_loader
        from core.routes.plugins import _plugin_display_title, _safe_emoji_icon
    except Exception as e:
        logger.debug(f"[setup] featured plugins not listed: {e}")
        return []
    out = []
    for name, group, blurb in FEATURED:
        info = plugin_loader.get_plugin_info(name)
        if not info:
            continue
        manifest = info.get('manifest') or {}
        out.append({
            'name': name,
            'group': group,
            'title': _plugin_display_title(manifest, name),
            'blurb': blurb,
            'description': manifest.get('description') or '',
            'icon': _safe_emoji_icon(manifest.get('icon')),
            'enabled': bool(info.get('enabled')),
            'loaded': bool(info.get('loaded')),
            'missing_deps': list(info.get('missing_deps') or []),
            'has_settings': _has_settings(info),
        })
    return out


@router.get("/api/setup/featured")
async def get_featured(request: Request, _=Depends(require_login)):
    """The plugins the wizard offers: title, one line, icon, on/off, missing
    packages, whether a settings tab exists, and the radio group if any."""
    return {'featured': featured()}
