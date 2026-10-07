# MIDI tools: she plays a synth, loops a beat, listens to the keys, shapes the sound.
# Any synth found by its ALSA port name; what a synth calls its voices and how its
# effects switch comes from a profile (synths.py) — the FM-1 ships as the first.
# Regular chat tools. Listening never blocks her turn: the take is recorded on a
# thread and handed back through core.cadence.fire_once as a new turn on the chat
# that asked, the same server-side door the wake word and the phone use.
import logging
import sys
import threading
import time
from pathlib import Path

# .absolute(), never .resolve(): symlinked plugin dirs must not escape.
_PLUGIN_ROOT = str(Path(__file__).absolute().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

import midi_core as midi
import midi_songs as songs
import softsynth as synth
import synths

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🎹'
AVAILABLE_FUNCTIONS = ['midi_play', 'midi_listen', 'midi_sound', 'midi_stop', 'song_save']

FIRST_WAIT = 60      # seconds she waits for the first key
QUIET_END = 5        # seconds of quiet that end a take
MAX_LISTEN = 300

_lock = threading.Lock()
_listen = None       # {'thread', 'cancel', 'chat'}: one take at a time

_NOTATION = ("Notation: tokens separated by spaces. A token is note[:beats], "
             "[chord notes][:beats], or R[:beats] for a rest. Length defaults to 1 beat. "
             "A note is a letter, an optional # or b, and an octave; C4 is middle C. "
             "Example: C4 E4 G4 C5:2 [C4 E4 G4]:4 R:1 is three one-beat notes, a two-beat C5, "
             "a C major chord held four beats, then a one-beat rest")

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "midi_play",
            "description": ("Play notes on the synth, out loud in the room. Returns at once and "
                            "the music plays in the background, so you can talk while it sounds. "
                            + _NOTATION + ". The synth has one sound at a time, shared with whoever "
                            "is playing its keys."),
            "parameters": {
                "type": "object",
                "properties": {
                    "notes": {"type": "string", "description": "The music, in the notation above."},
                    "bpm": {"type": "number", "description": "Tempo in beats per minute, 20-400. Default 120."},
                    "voice": {"type": "integer", "description": "Optional voice number 1-128 to switch to first. See midi_sound for names."},
                    "velocity": {"type": "integer", "description": "How hard the keys are struck: 1 is a whisper, 127 is full force. Default 90."},
                    "loop_minutes": {"type": "number", "description": "Repeat the phrase as a backing loop for this many minutes (max 15) so someone can play over it. A loop and a phrase run side by side: start a loop, then play phrases over it. midi_stop ends it."},
                    "then_listen": {"type": "number", "description": "Your turn, then theirs, in one call: seconds to listen once your phrase ends. It is midi_listen started for you at the right moment, and what they play arrives as a new message the same way. Leave it out when you only want to play. Use midi_listen by itself when you only want to hear them."}
                },
                "required": ["notes"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "midi_listen",
            "description": ("Listen to someone play the synth's keys, or another MIDI keyboard linked to this "
                            "machine. Returns at once and does not hold up "
                            f"your reply. It waits up to {FIRST_WAIT}s for the first key, then records up to "
                            f"`seconds`, ending early after {QUIET_END}s of quiet. What was played then arrives "
                            "in this chat as a new message, written in the same notation midi_play uses, with "
                            "how hard the keys were struck. You hear notes and rhythm, not the sound itself. "
                            "After calling this, finish your reply and invite them to play."),
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {"type": "number", "description": f"Longest take to record, counted from the first key. Default 30, max {MAX_LISTEN}."},
                    "bpm": {"type": "number", "description": "Tempo grid, 20-400, used to write down what was played. Defaults to the tempo you last played at."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "midi_sound",
            "description": ("Change how the synth sounds: pick a voice, switch an effect, or both. Works while "
                            "someone is playing, so you can shape the sound under their hands. Voice names are the "
                            "synth's own when it is one I know (the FM-1), else General MIDI. Effects exist only on "
                            "synths I know: the FM-1 has filter (type 0-2, cutoff 0-107, q 0-10), reverb (type 0-2, "
                            "decay, mix), delay (decay, rate, mix), distortion (gain, tone, level), chorus (freq, "
                            "depth, mix), phaser (freq, depth, mix); levels run 0-100 unless noted."),
            "parameters": {
                "type": "object",
                "properties": {
                    "voice": {"type": "string", "description": "A voice number 1-128, part of a name like 'piano' or 'bass', or 'list' to see all 128 names. Names are terse synth names such as E.PIANO 1, so a name that misses returns the full list."},
                    "effect": {"type": "string", "description": "The effect to switch, by name (reverb, delay, ...). voice=\"list\" also names the effects this synth has."},
                    "on": {"type": "boolean", "description": "Effect on or off. Default true."},
                    "levels": {"type": "object", "description": "Levels for the effect, e.g. {\"mix\": 60, \"decay\": 40}."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "midi_stop",
            "description": "Stop everything of yours on the synth: your phrase, your loop, and any listening in progress. Silences every note.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "song_save",
            "description": ("Save a song you wrote as files the user can play and download: an MP3 and a MIDI "
                            "file. Needs no synth. The MP3 is rendered with a General MIDI instrument, so it "
                            "may not sound like the synth in the room. Same notation as midi_play. " + _NOTATION + ". "
                            "The user gets a player and download buttons for it automatically, under "
                            "the tool result. You have nothing to paste."),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The song's name."},
                    "notes": {"type": "string", "description": "The music, in the notation above. Up to 10 minutes."},
                    "bpm": {"type": "number", "description": "Tempo in beats per minute, 20-400. Default 120."},
                    "instrument": {"type": "string", "description": "Instrument for the MP3: " + ", ".join(songs.INSTRUMENTS) + ". Or a General MIDI number 1-128. Default piano."},
                    "velocity": {"type": "integer", "description": "How hard the keys are struck: 1 is a whisper, 127 is full force. Default 90."}
                },
                "required": ["title", "notes"]
            }
        }
    },
]


def _names(settings):
    """The synth port names to look for: the user's, else every profiled synth's."""
    raw = str((settings or {}).get('synth_names') or '')
    names = tuple(n.strip() for n in raw.split(',') if n.strip())
    return names or synths.port_names()


def _synths(a, settings, start=True):
    """The synths a call may play on, first found wins. A hardware synth comes
    first. The computer's own synth stands in when none is linked. A call from the
    keyboard device names the computer (on='computer') and gets only that.
    start=False never switches anything on: stopping must not start a synth."""
    if str((a or {}).get('on') or '').lower() == 'computer':
        if start:
            try:
                synth.start()                   # playing on it switches it on
            except synth.SynthError as e:
                raise midi.MidiError(str(e))
        return (synth.NAME,) if synth.running() else ()
    return _names(settings) + ((synth.NAME,) if synth.running() else ())


def _shown(name):
    """A synth's name as she reads it. The computer's synth has a port name
    that says nothing about where the sound comes from."""
    return "the computer's synth" if name == synth.NAME else name


def _keyboards(settings):
    """Names of the keyboards she hears, besides the synth. The user may name
    them. Left empty it is every MIDI source that is here, held to the
    keyboard devices' filter when there is one."""
    raw = str((settings or {}).get('keyboard_names') or '')
    names = tuple(n.strip() for n in raw.split(',') if n.strip())
    if names:
        return names
    try:
        ids = synth.counting_ids() if synth.wanted else None
        return tuple(s['name'] for s in synth.sources()
                     if (ids is None or s['id'] in ids) and s['name'] not in _names(settings))
    except Exception as e:
        logger.warning(f"[midi] could not look for keyboards: {e}")
        return ()


def _listen_ports(settings):
    """-> ('128:0,20:0', 'FM-1_BLE and AKM320'): one synth port (first found)
    plus every other keyboard that is connected."""
    found = midi.find_ports(_names(settings))[:1] + midi.find_ports(_keyboards(settings))
    seen, ports = set(), []
    for port, name in found:
        if port not in seen:
            seen.add(port)
            ports.append((port, name))
    if not ports:
        raise midi.MidiError("Nothing to listen to: no synth and no MIDI keyboard is linked to "
                           "the machine I run on.")
    return ','.join(p for p, _ in ports), ' and '.join(n for _, n in ports)


def _private():
    """True in a private chat, and True when it can't be told (fail closed)."""
    try:
        from core.chat.function_manager import scope_private
        return bool(scope_private.get())
    except Exception as e:
        logger.warning(f"[midi] could not read the private flag: {e}")
        return True


def _speak(settings):
    return 'speakers' if (settings or {}).get('reply_voice', 'speakers') == 'speakers' else None


def _calling_chat():
    """The chat THIS turn runs in. Never a guess: no chat = no listen, since a
    take delivered to the wrong chat is worse than no take."""
    try:
        from core.chat.function_manager import tool_context
        chat = (tool_context.get() or {}).get('chat')
        if chat:
            return chat
    except Exception as e:
        logger.warning(f"[midi] tool_context unreadable: {e}")
    try:
        from core.api_fastapi import get_system
        return get_system().llm_chat.session_manager._effective_chat_name() or None
    except Exception as e:
        logger.warning(f"[midi] could not resolve the calling chat: {e}")
        return None


def _deliver(chat, text, speak):
    """Hand the take to her through the chat's inbox (core/chat/inbox.py): it
    runs now if she is free, waits its turn if not. Before this a chat busy for
    three minutes dropped the take."""
    from core.chat import inbox
    try:
        inbox.tell(chat, text, source='midi', coalesce=False, speak=speak)   # a take is one event: its own reply
        logger.info(f"[midi] take handed to the inbox of '{chat}'")
        return True
    except Exception as e:
        logger.warning(f"[midi] delivering the take to '{chat}' failed: {e}")
        return False


def _keep_take(played, bpm, private):
    """Save what was played -> the lines to add to the delivered text ('' when
    there is nothing to keep, the chat is private, or saving fails)."""
    if not played or private:
        return ''
    try:
        m = songs.save_take(songs.song_dir(), 'Keys take, ' + time.strftime('%b %d %H:%M'), played, bpm)
        row = songs.files_marker(m)
        if not row:
            return ''
        return ("\n(The take is saved. The user has a player and download buttons for it on "
                "this message.)\n" + row)
    except Exception as e:
        logger.warning(f"[midi] the take could not be saved: {e}")
        return ''


def _listen_run(chat, port, seconds, bpm, speak, cancel, after, private=True):
    try:
        while after is not None and after.poll() is None and not cancel.is_set():
            time.sleep(0.1)                     # trading phrases: her phrase finishes first
        if cancel.is_set():
            logger.info("[midi] listen cancelled before it began")
            return
        logger.info(f"[midi] listening on {port} for '{chat}' (up to {seconds:g}s)")
        notes = midi.capture(port, max_seconds=seconds, first_wait=FIRST_WAIT,
                            quiet_end=QUIET_END, cancel=cancel)
        if cancel.is_set():
            logger.info(f"[midi] listen cancelled; {len(notes)} notes discarded")
            return
        logger.info(f"[midi] heard {len(notes)} notes")
        _deliver(chat, midi.heard_text(notes, bpm, FIRST_WAIT) + _keep_take(notes, bpm, private), speak)
    except Exception as e:
        logger.error(f"[midi] listen failed: {e}", exc_info=True)
        _deliver(chat, f"[Keys: listening failed ({e}). This is the instrument reporting, not typed text.]", None)


def _start_listen(chat, port, seconds, bpm, speak, after=None):
    global _listen
    private = _private()            # read here: the chat's flags don't follow a new thread
    with _lock:
        if _listen and _listen['thread'].is_alive():
            return False
        cancel = threading.Event()
        t = threading.Thread(target=_listen_run, daemon=True, name='midi-listen',
                             args=(chat, port, seconds, bpm, speak, cancel, after, private))
        _listen = {'thread': t, 'cancel': cancel, 'chat': chat}
        t.start()
    return True


def _listen_args(a, key):
    seconds = max(2.0, min(float(MAX_LISTEN), float(a.get(key) or 30)))
    bpm = float(a.get('bpm') or midi.state['bpm'])
    if not 20 <= bpm <= 400:
        raise midi.MidiError('bpm has to be between 20 and 400.')
    return seconds, bpm


_NO_CHAT = "I can't tell which chat this call came from, so there is nowhere to send what I hear. Nothing was started."
_BUSY = "Already listening. That take arrives as a new message when it ends; midi_stop cancels it."
_ARRIVES = "What is played arrives as a new message in this chat."


def _play(a, settings):
    notes = str(a.get('notes') or '').strip()
    if not notes:
        raise midi.MidiError('notes is required. Example: C4 E4 G4 [C4 E4 G4]:2')
    then = float(a.get('then_listen') or 0)
    chat = _calling_chat() if then > 0 else None
    if then > 0 and not chat:
        raise midi.MidiError(_NO_CHAT)
    port, name = midi.find_port(_synths(a, settings))
    r = midi.play(port, notes, bpm=a.get('bpm') or 120, voice=a.get('voice'),
                 velocity=a.get('velocity') or 90, loop_minutes=a.get('loop_minutes') or 0)
    name = _shown(name)
    if r['slot'] == 'loop':
        msg = (f"Looping on {name}: {r['notes']} notes over {r['beats']:g} beats at {r['bpm']:g} bpm, "
               f"{r['low']} to {r['high']}, {r['reps']} times, {r['seconds'] / 60:.1f} minutes. "
               "midi_stop ends it.")
    else:
        msg = (f"Playing on {name}: {r['notes']} notes over {r['beats']:g} beats at {r['bpm']:g} bpm, "
               f"{r['low']} to {r['high']}, {r['seconds']:.1f}s. It sounds in the background.")
    if then > 0:
        seconds, bpm = _listen_args({'seconds': then, 'bpm': a.get('bpm')}, 'seconds')
        after = None if r['slot'] == 'loop' else r['proc']
        ears, _ = _listen_ports(settings)
        if _start_listen(chat, ears, seconds, bpm, _speak(settings), after=after):
            when = 'now' if after is None else 'when the phrase ends'
            msg += f" Listening starts {when}, up to {seconds:g}s. {_ARRIVES}"
        else:
            msg += ' ' + _BUSY
    return msg


def _listen_tool(a, settings):
    chat = _calling_chat()
    if not chat:
        raise midi.MidiError(_NO_CHAT)
    seconds, bpm = _listen_args(a, 'seconds')
    port, name = _listen_ports(settings)
    if not _start_listen(chat, port, seconds, bpm, _speak(settings)):
        return _BUSY
    return (f"Listening on {name}. Waiting up to {FIRST_WAIT}s for the first key, then recording up to "
            f"{seconds:g}s, ending {QUIET_END}s after the keys go quiet. {_ARRIVES} "
            "Finish your reply now and invite them to play.")


def _sound(a, settings):
    want = a.get('voice')
    effect = a.get('effect')
    listing = want is not None and str(want).strip().lower() == 'list'
    if want in (None, '') and not effect:
        raise midi.MidiError('Give a voice, an effect, or voice="list".')
    port, name = midi.find_port(_synths(a, settings))
    if name == synth.NAME:                      # General MIDI names, no effects
        if listing:
            return "The computer's synth, General MIDI instruments: " + ', '.join(songs.INSTRUMENTS) + \
                '. Any number 1-128 works too. It has no effects.'
        if effect:
            raise midi.MidiError("The computer's synth has no effects.")
        n, iname = songs.pick_instrument(want)
        try:
            synth.instrument(n)
        except synth.SynthError as e:
            raise midi.MidiError(str(e))
        return f'{_shown(name).capitalize()}: instrument {n} {iname}.'
    prof = synths.for_port(name)
    names = synths.voice_names(prof, songs.INSTRUMENTS)
    effects = (prof or {}).get('effects') or {}
    if listing:
        head = f"{prof['name']} voices" if prof else f"{name} is not a synth I know, so these are General MIDI names"
        tail = ('\nEffects: ' + ', '.join(effects)) if effects else '\nNo effects I know how to switch on it.'
        return f"{head}:\n" + '\n'.join(f'{i + 1} {n}' for i, n in enumerate(names)) + tail
    out = []
    if want not in (None, ''):
        n, vname, others = midi.pick_voice(names, want)
        midi.program(port, n)
        out.append(f'Voice {n} {vname}.')
        if others:
            out.append('Also matching: ' + ', '.join(str(o) for o in others[:12]) + '.')
    if effect:
        if not effects:
            raise midi.MidiError(f"{name} has no effects I know how to switch"
                                 + (" (it is not a synth I have a profile for)." if not prof else "."))
        on = a.get('on')
        on = True if on is None else bool(on)
        levels = a.get('levels') if isinstance(a.get('levels'), dict) else {}
        done = midi.fx(port, effects, int(prof.get('fx_channel') or 0), effect, on, levels)
        out.append(f"{effect} {'on' if on else 'off'}"
                   + (' ' + ', '.join(f'{k} {v}' for k, v in done.items()) if done else '') + '.')
    return f'{name}: ' + ' '.join(out)


def _song_save(a, settings):
    if _private():
        raise midi.MidiError("This is a private chat, and saved songs are kept as plain files outside "
                           "the vault. Nothing was saved.")
    m = songs.save(songs.song_dir(), a.get('title'), str(a.get('notes') or '').strip(),
                   bpm=a.get('bpm') or 120, instrument=a.get('instrument'),
                   velocity=a.get('velocity') or 90)
    msg = (f"Saved {m['title']}: {m['note_count']} notes over {m['beats']:g} beats at {m['bpm']:g} bpm, "
           f"{m['low']} to {m['high']}, {m['seconds']:g}s, {m['instrument']}.")
    if not m['audio']:
        msg += f" MIDI only, no MP3: {m['audio_problem']}."
    row = songs.files_marker(m)
    if not row:
        return (msg + " Put these links in your reply exactly as written:\n"
                + "\n".join(songs.links(m)))
    return (msg + " The user now has a player and download buttons for it, right under this "
            "result. There is nothing to paste: just tell them about the song.\n" + row)


def _stop(a, settings):
    cancelled = False
    with _lock:
        if _listen and _listen['thread'].is_alive():
            _listen['cancel'].set()
            cancelled = True
    try:
        ports = [p for p, _ in midi.find_ports(_synths(a, settings, start=False))]
    except midi.MidiError:
        ports = []
    midi.stop_all(ports[0] if ports else None)
    try:
        synth.hush()                            # a held pedal outlives every note-off
    except synth.SynthError as e:
        logger.warning(f"[midi] the computer's synth was not hushed: {e}")
    for extra in ports[1:]:                     # a hardware synth and the computer's can both be on
        try:
            midi.send(extra, ' '.join(f'8{midi.NOTE_CH:X} {n:02X} 00' for n in range(128)))
        except midi.MidiError as e:
            logger.warning(f"[midi] note-offs to {extra} failed: {e}")
    msg = 'Stopped. Your phrase and loop are off'
    msg += ' and every note is silenced' if ports else ' (no synth is connected, so no note-offs went out)'
    return msg + ('. Listening was cancelled.' if cancelled else '.')


def execute(function_name, arguments, config, plugin_settings=None):
    a = arguments if isinstance(arguments, dict) else {}
    handlers = {'midi_play': _play, 'midi_listen': _listen_tool, 'midi_sound': _sound,
                'song_save': _song_save}
    try:
        if function_name == 'midi_stop':
            return _stop(a, plugin_settings), True
        if function_name in handlers:
            return handlers[function_name](a, plugin_settings), True
        return f'Unknown function: {function_name}', False
    except midi.MidiError as e:
        return str(e), False
    except (TypeError, ValueError) as e:
        return f'Bad value: {e}', False
    except Exception as e:
        logger.error(f"[midi] {function_name} failed: {e}", exc_info=True)
        return f'MIDI error: {e}', False
