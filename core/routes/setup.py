"""Setup wizard state and its featured plugins (tmp/setup-wizard-plan.md, 2026-10-22).

The wizard is the `#setup` view (interfaces/web/static/views/setup.js): it hosts
the real Settings tab modules, so there is nothing provider-shaped here. This
module owns the wizard's state only: where the person is (`SETUP_STEP`), whether
they finished (`SETUP_COMPLETE`), when they consented (`SETUP_CONSENT`, stamped
HERE, never by the client), which kind of setup they picked (`SETUP_MODE`: easy
or advanced, which decides the steps), and the plugins the wizard offers to turn
on. What Easy picks for a computer is core/setup_easy.py.

The old wizard's `SETUP_WIZARD_STEP` (an int) is migrated by the settings
manager on load and never reused.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from core.auth import require_login, check_endpoint_rate
from core.settings_manager import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# The kinds of setup (tmp/easy-mode-plan.md, 2026-11-15) and the steps each
# shows, in order. Nothing picked yet: the welcome and the pick. Easy is one
# page of what Sapphire chose for this computer (core/setup_easy.py); Advanced
# is every step. A later kind (Business) is one more row here.
ADVANCED = ('you', 'brain', 'voice', 'audio', 'gate', 'plugins', 'look', 'next')
MODES = {'easy': ('easy',), 'advanced': ADVANCED}
HEAD = ('consent', 'mode')

# Every step the wizard can show. The server list is the resume vocabulary: a
# stored step outside the current kind's list (a step that left, a typo, the
# other kind's step) starts over.
ALL_STEPS = HEAD + ('easy',) + ADVANCED

# Package sets the voice step can install under their own row (core/extras.py).
PACKAGE_SETS = ('tts', 'stt', 'wakeword')

# The plugins the wizard offers, in this order: (name, group, one line). A
# group is a radio pair (enable one, not both). A plugin the loader did not
# scan (managed_hide, or not shipped) is dropped. Curated here, one place.
# The ungrouped ones ship ON (`default_enabled` in their manifests, 2026-10-24):
# a fresh install arrives here with them ticked and unticks what it does not
# want; an install that turned one off keeps it off (the loader's disabled
# list wins over the default). Voice providers are not here: the Voice step's
# cards wake their plugin (Piper, ElevenLabs...).
FEATURED = (
    ('mindpalace', 'mind', 'Layered memory: self, events, people and knowledge behind one tool surface.'),
    ('memory', 'mind', 'The old memory system, on its way out. Only if you have a memory database from before Mind Palace.'),
    ('discord', None, 'A Discord bot on her conversation engine: chat, images, voice channels.'),
    ('telegram', None, 'Talk to her from Telegram, as a bot or from your own account.'),
    ('email', None, 'She reads and sends mail for you, to a whitelist of people.'),
    ('homeassistant', None, 'Lights, switches, scenes and the thermostat through Home Assistant.'),
    ('coding-harness', None, 'File and shell tools fenced to one working folder, for coding with her.'),
    ('webcam', None, 'She can look through your camera when you ask.'),
    ('screenshot', None, 'She can grab this screen when you ask.'),
)

# On their way out: the step dims and tags them, for people with old data only.
LEGACY = frozenset({'memory'})


def mode():
    m = settings.get('SETUP_MODE', '') or ''
    return m if m in MODES else ''


def step_ids(kind=None):
    """The steps that apply to this install and this kind of setup: no audio
    devices in Docker, and managed mode has no local voice or audio at all
    (the router speaks)."""
    ids = list(HEAD + MODES.get(mode() if kind is None else kind, ()))
    if settings.is_managed():
        ids = [s for s in ids if s not in ('voice', 'audio')]
    elif settings.is_docker():
        ids = [s for s in ids if s != 'audio']
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
        'mode': mode(),
        'modes': list(MODES),
        'steps': ids,
        'managed': settings.is_managed(),
        'docker': settings.is_docker(),
        'packages': _packages(),
    }


def apply(data):
    """Validate and store a state change: `complete` (bool), `mode` (a kind of
    setup), `step` (an id of that kind, or '' to clear), `consent` (true: the
    server stamps the time). Unknown keys are ignored; a bad value is a 400 and
    nothing is stored."""
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Expected an object")
    writes = {}
    kind = None
    if 'mode' in data:
        kind = data['mode']
        if kind not in MODES:
            raise HTTPException(status_code=400, detail=f"Unknown kind of setup: {kind!r}")
        writes['SETUP_MODE'] = kind
    if 'complete' in data:
        if not isinstance(data['complete'], bool):
            raise HTTPException(status_code=400, detail="complete must be true or false")
        writes['SETUP_COMPLETE'] = data['complete']
    if 'step' in data:
        step = data['step']
        if not isinstance(step, str) or (step and step not in step_ids(kind)):
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
    """`{complete?: bool, mode?: kind, step?: id|'', consent?: true}` -> the new state."""
    data = await request.json()
    out = apply(data)
    if data.get('complete') is True:
        _check_brain_after_finish()
    return out


NO_LLM_KEY = 'setup-no-llm'


def _check_brain_after_finish():
    """Setup just finished: when no LLM would answer, say so once in the
    notification center, since the wizard's own words are gone with it. Off
    the request (it asks the providers). The row goes when an LLM answers:
    Auto finding one (core/chat/llm_providers/resolve.py), or
    core/launcher_llm.py adding the launcher's."""
    import threading

    def run():
        try:
            from core import notify, setup_easy
            b = setup_easy.brain()
            if b.get('state') in ('none', 'stopped'):
                # its own words: the Brain row's hint points at a button that left with the wizard
                how = ("Start it in the Sapphire Launcher, on the LLM tab." if b['state'] == 'stopped'
                       else "Install one in the Sapphire Launcher, on the LLM tab, or add one under Settings › LLM.")
                notify.notify("She has no LLM yet", 'warning', f"She cannot reply until one answers. {how}",
                              go={'view': 'settings', 'tab': 'llm'}, key=NO_LLM_KEY)
            else:
                notify.forget(NO_LLM_KEY)
        except Exception as e:
            logger.debug(f"[setup] LLM not checked after finish: {e}")
    threading.Thread(target=run, name='setup-brain', daemon=True).start()


@router.get("/api/setup/easy")
async def get_easy(request: Request, _=Depends(require_login)):
    """What Easy setup would pick for this computer: the rows (voice, ears,
    wake word), each with its one line of why and the settings Finish saves."""
    from core import setup_easy
    return await asyncio.to_thread(setup_easy.plan)


@router.get("/api/setup/easy/brain")
async def get_easy_brain(request: Request, light: bool = False, _=Depends(require_login)):
    """The Easy page's Brain row: which LLM would answer now. Adds the Sapphire
    Launcher's LLM if it came up since boot. `light` = the page's poll."""
    from core import setup_easy
    return await asyncio.to_thread(setup_easy.brain, light)


def _system():
    from core.api_fastapi import get_system      # at call time: the app imports this module
    return get_system()


@router.get("/api/setup/easy/status")
async def get_easy_status(request: Request, _=Depends(require_login), system=Depends(_system)):
    """The light on each Easy row: `{voice|ears|wake: {state, why}}`, state
    `ok`, `loading`, `failed` or `off`, for what is live now."""
    from core import setup_easy
    return await asyncio.to_thread(setup_easy.status, system)


@router.post("/api/setup/easy/hello")
async def post_easy_hello(request: Request, _=Depends(require_login)):
    """The Brain row's test: the LLM Auto would use says hello. `{ok, text, name, why}`."""
    check_endpoint_rate(request, 'easy_hello', max_calls=10, window=60)
    from core import setup_easy
    return await asyncio.to_thread(setup_easy.hello)


@router.post("/api/setup/easy/wake")
async def post_easy_wake(request: Request, _=Depends(require_login), system=Depends(_system)):
    """The Wake row's test: waits up to 8 s for the wake phrase and says only
    whether it was heard. `{ok, heard, why, busy}`. Nothing is recorded."""
    check_endpoint_rate(request, 'easy_sound', max_calls=30, window=60)
    from core import setup_easy
    return await asyncio.to_thread(setup_easy.hear_wake, system)


def _at_the_page(request):
    """A person at the setup page (a browser session), not an API token."""
    return bool(request.session.get('logged_in'))


@router.post("/api/setup/easy/say")
async def post_easy_say(request: Request, _=Depends(require_login), system=Depends(_system)):
    """The Easy page's speaker test, on this computer's speakers: her voice
    when she has one, else a test tone and why. `{ok, kind, device, why, error}`."""
    check_endpoint_rate(request, 'easy_sound', max_calls=30, window=60)
    from core import setup_easy
    return await asyncio.to_thread(setup_easy.say, system)


@router.post("/api/setup/easy/listen")
async def post_easy_listen(request: Request, _=Depends(require_login), system=Depends(_system)):
    """The Easy page's microphone test, on this computer's microphone: a
    stream of JSON lines (`mic`, `level`..., `thinking`, then one with `done`:
    the words she heard, or why there are none). This computer says so first
    (one sentence in her voice, three beeps) and says "Done" after. For a person at the page (a browser session): an API token
    gets a 403, since this route hands back what the room said."""
    if not _at_the_page(request):
        raise HTTPException(status_code=403, detail="The microphone test runs from the setup page only.")
    check_endpoint_rate(request, 'easy_sound', max_calls=30, window=60)
    logger.info("[setup] microphone test started from the setup page")
    from core import setup_easy
    return StreamingResponse((json.dumps(ev) + '\n' for ev in setup_easy.listen(system)),
                             media_type='application/x-ndjson')


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
            'legacy': name in LEGACY,
        })
    return out


@router.get("/api/setup/featured")
async def get_featured(request: Request, _=Depends(require_login)):
    """The plugins the wizard offers: title, one line, icon, on/off, missing
    packages, whether a settings tab exists, and the radio group if any."""
    return {'featured': featured()}
