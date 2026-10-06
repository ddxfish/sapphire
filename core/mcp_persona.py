# core/mcp_persona.py - what a key that speaks as a persona may do at the MCP door
#
# An API token can name a persona (Settings > System > API Keys). A client
# holding that token is that persona here: `speak` uses the persona's voice,
# pitch and speed, the memory tools reach the persona's memory scope and no
# other, `ding` plays a chime on this machine, and `listen` (behind its own
# switch, MCP_SERVER_MIC) takes one spoken answer from this machine's
# microphone. None of it is in her own toolkit; it exists for the client
# outside. core/mcp_server.py offers these beside ask and tell when the
# caller's token carries a persona.
#
# The client never names a voice or a scope. Both come from the persona, so a
# key can reach nothing its persona was not given.
import logging
from pathlib import Path

import config

logger = logging.getLogger(__name__)

SPEAK_MAX = 2000               # characters of one speak
HER_TURN = 20                  # seconds a sound waits for her own voice to end
DING = Path(__file__).parent / 'audio' / 'ding.wav'
LISTEN = Path(__file__).parent / 'audio' / 'listen.wav'      # a chime of its own: this one means the microphone
WARNING = "I'm about to listen on the mic."                  # said aloud before every listen
_warned = {}                   # WARNING as audio for the voice last used, so a listen does not wait on the engine
HERS = ('default', 'global', 'none')     # memory scopes no key may hold: her own, the overlay, and off
_NOT_A_CHAT = {'private_chat': False}    # a client's own words: no chat's privacy rides on them

_SOUND = [
    {
        'name': 'speak',
        'description': "Say something aloud on the Sapphire machine's speakers, in your persona's voice. "
                       "Returns when it has been said. Write plain sentences; markdown is stripped.",
        'inputSchema': {
            'type': 'object',
            'properties': {'text': {'type': 'string', 'description': 'What to say.'}},
            'required': ['text'],
        },
    },
    {
        'name': 'ding',
        'description': "Play a short chime on the Sapphire machine's speakers, to call the user back to the screen.",
        'inputSchema': {'type': 'object', 'properties': {}},
    },
]

_LISTEN = {
    'name': 'listen',
    'description': "Listen on the Sapphire machine's microphone for the user's spoken answer. It first says "
                   "aloud that it is about to listen, then a chime opens the microphone; it closes when they "
                   "stop talking, or after `seconds`. Returns the words heard. It hears anything in the room, "
                   "so a video playing counts as speech. Call it right after a `speak` that asked something.",
    'inputSchema': {
        'type': 'object',
        'properties': {'seconds': {'type': 'integer', 'default': 20,
                                   'description': 'The longest to wait, 3 to 60.'}},
    },
}

_MEMORY = [
    {
        'name': 'memory_save',
        'description': "Save a memory to your own persistent memory. 512 characters at most: longer is "
                       "trimmed and the receipt says what was cut. A label makes it easy to filter later.",
        'inputSchema': {
            'type': 'object',
            'properties': {
                'content': {'type': 'string', 'description': 'The memory.'},
                'label': {'type': 'string', 'description': 'A tag, for example "session:20261006-1400".'},
            },
            'required': ['content'],
        },
    },
    {
        'name': 'memory_search',
        'description': "Search your own persistent memory by meaning and by words.",
        'inputSchema': {
            'type': 'object',
            'properties': {
                'query': {'type': 'string'},
                'label': {'type': 'string', 'description': 'Only memories with this label.'},
                'limit': {'type': 'integer', 'default': 10},
            },
            'required': ['query'],
        },
    },
    {
        'name': 'memory_recent',
        'description': "Your most recent memories, newest first. Call it at the start of a session.",
        'inputSchema': {
            'type': 'object',
            'properties': {
                'count': {'type': 'integer', 'default': 10},
                'label': {'type': 'string', 'description': 'Only memories with this label.'},
            },
        },
    },
    {
        'name': 'memory_delete',
        'description': "Delete one of your own memories by its id, the number shown in brackets.",
        'inputSchema': {
            'type': 'object',
            'properties': {'memory_id': {'type': 'integer'}},
            'required': ['memory_id'],
        },
    },
]

NAMES = frozenset(t['name'] for t in _SOUND + [_LISTEN] + _MEMORY)    # held for this door: a ticked tool never takes one


def mic():
    """The user's own switch: may a persona key open this machine's microphone."""
    return bool(getattr(config, 'MCP_SERVER_MIC', False))


# --- who the key is -------------------------------------------------------------

def settings(persona):
    """The persona's saved settings, or None when there is no such persona."""
    if not persona:
        return None
    try:
        from core.personas import persona_manager
        found = persona_manager.get(str(persona))
    except Exception as e:
        logger.warning(f"[MCP] the persona '{persona}' could not be read: {e}")
        return None
    held = found.get('settings') if isinstance(found, dict) else None
    return held if isinstance(held, dict) else None


def memory_scope(persona):
    """The one memory scope this persona's key may touch, or None. Never
    'default' (that one is hers) and never a guess: a persona that is missing,
    has no scope of its own, or cannot be read gets no memory here."""
    held = settings(persona)
    scope = held.get('memory_scope') if held else None
    scope = scope.strip() if isinstance(scope, str) else ''
    return scope if scope and scope.lower() not in HERS else None


def offered(persona):
    """The tools a key with this persona gets, in MCP's own shape. None for a
    key without a persona, and no memory tools for a persona without a memory
    scope of its own."""
    if settings(persona) is None:
        return []
    tools = _SOUND + ([_LISTEN] if mic() else []) + (_MEMORY if memory_scope(persona) else [])
    return [dict(t) for t in tools]


def about(persona):
    """One line for the client at connect: who it is here and what that gives."""
    if settings(persona) is None:
        return ''
    line = (f" You are here as the persona '{persona}': `speak` says your words aloud in its voice "
            f"and `ding` plays a chime, both on this machine's speakers.")
    if mic():
        line += " `listen` takes the user's spoken answer from its microphone."
    scope = memory_scope(persona)
    if scope:
        return line + f" The memory_ tools keep your own memory (scope '{scope}')."
    return line + (" This persona has no memory scope of its own, so no memory tools are offered: "
                   "set one on the persona in the Personas view.")


# --- sound ---------------------------------------------------------------------

def _number(value, fallback):
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _play(system, audio, done):
    """A sound on this machine: after her own voice has ended, and with the
    wake word held off so the sound cannot wake her."""
    from core.audio import playback
    tts = getattr(system, 'tts', None)
    if tts is not None and hasattr(tts, 'wait'):
        tts.wait(timeout=HER_TURN)
    hold, release = getattr(system, 'web_active_inc', None), getattr(system, 'web_active_dec', None)
    if hold:
        hold()
    try:
        ok, why = playback.play(audio)
    finally:
        if release:
            release()
    return (why or done), ok


def _voice(system, persona, text):
    """`text` as audio in the persona's own voice: (bytes, the voice's name),
    or (None, why not)."""
    tts = getattr(system, 'tts', None)
    if tts is None or not getattr(config, 'TTS_ENABLED', False):
        return None, "The voice is off: no TTS provider is set on this Sapphire."
    from core.tts.utils import validate_voice, default_voice
    held = settings(persona) or {}
    # All three are passed, always: one left out would fall back to HER live value.
    voice = validate_voice(str(held.get('voice') or '').strip()) or default_voice()
    audio, kind = tts.render(text, chat_settings=_NOT_A_CHAT, voice=voice,
                             speed=_number(held.get('speed'), 1.0), pitch=_number(held.get('pitch'), 1.0))
    return (audio, voice) if audio else (None, str(kind))


def _speak(system, persona, args):
    text = str(args.get('text') or '').strip()
    if not text:
        return "There is nothing to say.", False
    if len(text) > SPEAK_MAX:
        return f"That is too long to say at once: {len(text)} characters, {SPEAK_MAX} at most.", False
    audio, note = _voice(system, persona, text)
    if not audio:
        return note, False
    said, ok = _play(system, audio, 'Said.')
    logger.info(f"[MCP] '{persona}' spoke {len(text)} chars as {note}: {said}")
    return said, ok


def _ding(system):
    if not DING.is_file():
        return "The chime file is missing from this install.", False
    return _play(system, DING, 'Ding.')


def _cue(system, persona):
    """What the user hears before the microphone opens: the warning in the
    persona's voice, then the listen chime. Never the ding: the ear has to
    know this sound means the microphone. With the voice off, the chime alone."""
    from core.audio import playback
    held = settings(persona) or {}
    key = (getattr(config, 'TTS_PROVIDER', ''), held.get('voice'), held.get('speed'), held.get('pitch'))
    if key not in _warned:
        audio, _ = _voice(system, persona, WARNING)
        if audio:
            _warned.clear()
            _warned[key] = audio
    if key in _warned:
        playback.play(_warned[key])
    playback.play(LISTEN)


def _listen(system, persona, args):
    if not mic():
        return ("The microphone is not open to persona keys on this Sapphire. The user turns it on "
                "under Settings > MCP Server."), False
    from core.stt import listen
    return listen.once(system, args.get('seconds', 20), cue=lambda: _cue(system, persona))


# --- memory --------------------------------------------------------------------

def _whole(value, fallback, most):
    try:
        return max(1, min(most, int(value)))
    except (TypeError, ValueError):
        return fallback


def _memory(persona, name, args):
    scope = memory_scope(persona)
    if not scope:
        return (f"The persona '{persona}' has no memory scope of its own, so this key has no memory. "
                "Set its memory scope in the Personas view."), False
    from core.chat.function_manager import SCOPE_REGISTRY
    if 'memory' not in SCOPE_REGISTRY:
        return "Memory is off on this Sapphire: the memory plugin is not loaded.", False
    try:
        from plugins.memory.tools import memory_tools as mt
    except ImportError as e:
        return f"The memory plugin could not be reached: {e}", False
    label = args.get('label') or None
    # The scope goes in by name on every call. It is never ambient and never the client's to give.
    if name == 'memory_save':
        return mt._save_memory(str(args.get('content') or ''), label=label, scope=scope)
    if name == 'memory_search':
        return mt._search_memory(str(args.get('query') or ''), limit=_whole(args.get('limit'), 10, 50),
                                 label=label, scope=scope)
    if name == 'memory_recent':
        return mt._get_recent_memories(count=_whole(args.get('count'), 10, 50), label=label, scope=scope)
    try:
        ident = int(args.get('memory_id'))
    except (TypeError, ValueError):
        return "memory_delete needs the memory's id, the number shown in brackets.", False
    return mt._delete_memory(ident, scope=scope)


# --- a call ---------------------------------------------------------------------

def call(system, persona, name, args):
    """Run one persona tool. (text, ok)."""
    if settings(persona) is None:
        return (f"'{name}' needs an API token that speaks as a persona. "
                "Give the token one under Settings > System > API Keys."), False
    if name == 'speak':
        return _speak(system, persona, args)
    if name == 'ding':
        return _ding(system)
    if name == 'listen':
        return _listen(system, persona, args)
    return _memory(persona, name, args)
