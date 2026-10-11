# core/rooms.py - a chat room: one chat, several speakers, round robin (2026-10-11)
#
# A room is a chat tagged `mode: room`. Its rows are final prose only, one row
# per line, each with a `speaker`: a seat id, 'user' (the person at the
# keyboard), or an outside door ('mcp:<who>'). No normal turn ever runs on a
# room chat: the inbox refuses a door's own turn (a `run` item) at put and
# hands text items here as LINES (take_line); cadence.run_turn refuses the
# mode. Seats live on the chat's settings (`room.seats`), each a persona seed
# plus local edits - its own prompt, model, toolset, scopes, custom context,
# and a per-seat switch for memory writes (off by default, Krem 2026-10-06).
# The room's RULES sit beside them (`room.human_every` ...): how many rounds
# the seats go before the floor is the person's, a pause between seats, a
# round cap per run, the Bots switch, shuffle (Krem's votes, 2026-10-11).
#
# A seat's turn rides THE stream engine with a caller-built brain
# (StreamingChat.brain): an ephemeral carrier - the seat's settings, its
# system prompt, its tools, and a PROJECTION of the room (its own lines as
# assistant, everyone else's as speaker-labelled user text; no tool rows, no
# thinking - a room keeps prose only). The engine persists nothing; the room
# appends one prose row after. The runner is a client of the inbox: each seat
# turn is an Item(run) on the room chat, so a line typed mid-round lands in
# order, between seats, and a seat turn never races a line (Krem 2026-10-11:
# the queue was built so we never reject). Record: tmp/chat-room-plan.md.
import logging
import threading
import uuid

from core.event_bus import publish, Events

logger = logging.getLogger(__name__)

MODE = 'room'
SOURCE = 'room'                 # the inbox source a seat turn carries - the one `run` item a room admits
USER = 'user'                   # the person at the keyboard
NOTICE = 'room'                 # the room's own lines (a seat skipped)
PASS = '(pass)'                 # a seat's whole answer when it has nothing to add
SEAT_TURN_S = 180.0             # past this a seat is cut off and skipped with a line (review, 2026-10-06)
MIND = ('memory', 'knowledge', 'people', 'goals')   # categories whose WRITERS are off unless the seat's box is ticked
OFF = ('meta_danger', 'agents')                     # never a seat's: the tools that change HER, and agents
OFF_NAMES = ('ask_user',)                           # a card has no surface in a room
SEAT_FIELDS = ('name', 'context', 'memory_write', 'enabled', 'color')
# The rules, with their defaults. human_every: the seats go this many rounds
# since the PERSON's last line, then wait for them (0 = never wait; a door's
# line - MCP, an agent, another room - never stands in for the person, so
# this is also the chain-reaction brake). max_rounds: a run stops after this
# many rounds (0 = no cap). pause_s: between seat turns. bots_enabled: off =
# lines land, nobody answers. shuffle: a fresh speaking order every round.
RULES = {'human_every': 1, 'pause_s': 0.0, 'max_rounds': 0, 'bots_enabled': True, 'shuffle': False}
PERSON_SOURCES = ('web', 'voice', 'conversation', 'device')    # inbox sources that are the person speaking

_lock = threading.RLock()
_rooms = {}                     # chat -> _Runtime


class RoomError(ValueError):
    """A reason fit to show."""


# --- the store: seats on the chat's settings -----------------------------------

def _system():
    from core.api_fastapi import get_system
    return get_system()


def _sm():
    return _system().llm_chat.session_manager


def settings_of(chat):
    """The room chat's settings, or None (missing, sealed, or not a room)."""
    try:
        s = _sm().read_chat_settings(chat)
    except Exception:
        return None
    if not isinstance(s, dict) or s.get('mode') != MODE:
        return None
    return s


def is_room(chat):
    return settings_of(chat) is not None


def _rules(r):
    """The rules as typed values; a bad or missing one is its default."""
    out = {}
    for k, d in RULES.items():
        v = r.get(k, d)
        try:
            if isinstance(d, bool):
                out[k] = bool(v)
            elif isinstance(d, float):
                out[k] = max(0.0, float(v))
            else:
                out[k] = max(0, int(v))
        except (TypeError, ValueError):
            out[k] = d
    return out


def _room(chat):
    """(chat settings, the room record) or RoomError."""
    s = settings_of(chat)
    if s is None:
        raise RoomError(f"There is no room named '{chat}'.")
    r = s.get('room') if isinstance(s.get('room'), dict) else {}
    return s, {'purpose': str(r.get('purpose') or ''),
               'seats': [x for x in (r.get('seats') or []) if isinstance(x, dict)], **_rules(r)}


def _write(chat, room):
    if not _sm().set_named_chat_settings(chat, {'room': room}):
        raise RoomError(f"The room '{chat}' could not be saved.")
    publish('room_changed', {'chat': chat}, ephemeral=True)


def _public(seat):
    out = {k: seat.get(k) for k in ('id', 'name', 'persona', 'context', 'memory_write', 'enabled', 'color', 'avatar')}
    out['settings'] = dict(seat.get('settings') or {})
    return out


def _find(room, seat_id):
    for s in room['seats']:
        if s.get('id') == seat_id:
            return s
    raise RoomError(f"There is no seat '{seat_id}' in this room.")


def _unique_name(room, name):
    taken = {s.get('name') for s in room['seats']}
    base, n, out = name, 1, name
    while out in taken:
        n += 1
        out = f"{base} {n}"
    return out


def create(name, purpose=''):
    from core.chat.history import sanitize_chat_name
    sm = _sm()
    safe = sanitize_chat_name(str(name or '').strip())
    if not safe:
        raise RoomError('A room needs a name.')
    if sm.chat_exists(safe):
        raise RoomError(f"There is already a chat named '{safe}'.")
    if not sm.create_chat(safe, settings={'mode': MODE, 'room': {'purpose': str(purpose or '')[:2000], 'seats': []}}):
        raise RoomError(f"The room '{safe}' could not be made.")
    return safe


def list_rooms():
    out = []
    for c in _sm().list_chat_files() or []:          # the store's listing (mode rides on each entry)
        if isinstance(c, dict) and c.get('mode') == MODE and c.get('name'):
            out.append({'chat': c['name'], 'state': state(c['name'])})
    return out


def describe(chat):
    s, room = _room(chat)
    names = _names(room)
    guests, seen = [], set()          # everyone in the rows who is not a seat, the person, or the room
    for r in rows_of(chat):
        sp = r.get('speaker')
        if not sp or sp in names or sp in seen:
            continue
        seen.add(sp)
        guests.append({'speaker': sp, 'name': _label(r, names)})
    return {'chat': chat, 'purpose': room['purpose'], 'seats': [_public(x) for x in room['seats']],
            'person': names[USER], 'guests': guests, **{k: room[k] for k in RULES},
            'private': bool(s.get('private_chat')), 'state': state(chat)}


def update_room(chat, patch):
    """The purpose and the rules. Bots switched off stops a run; switched
    back on with an unanswered line, the seats answer it."""
    patch = patch if isinstance(patch, dict) else {}
    _, room = _room(chat)
    if 'purpose' in patch:
        room['purpose'] = str(patch['purpose'] or '')[:2000]
    for k in RULES:
        if k in patch:
            room[k] = patch[k]
    room.update(_rules(room))
    _write(chat, room)
    if 'bots_enabled' in patch:
        if not room['bots_enabled']:
            stop(chat)
        else:
            rows = rows_of(chat)
            last = rows[-1].get('speaker') if rows else None
            if last is not None and last != NOTICE and last not in {x.get('id') for x in room['seats']}:
                _kick(chat, _rt(chat), room, human=False)
    return room


def add_seat(chat, persona, name=None):
    from core.personas import persona_manager
    p = persona_manager.get(str(persona or ''))
    if not p:
        raise RoomError(f"There is no persona named '{persona}'.")
    _, room = _room(chat)
    seed = dict(p.get('settings') or {})
    shown = str(persona)[:1].upper() + str(persona)[1:]            # 'sapphire' sits as 'Sapphire'
    seat = {'id': uuid.uuid4().hex[:8], 'name': _unique_name(room, str(name or '').strip() or shown),
            'persona': str(persona), 'context': '', 'memory_write': False, 'enabled': True,
            'color': str(seed.get('trim_color') or ''), 'avatar': p.get('avatar'), 'settings': seed}
    room['seats'].append(seat)
    _write(chat, room)
    return _public(seat)


def update_seat(chat, seat_id, patch):
    patch = patch if isinstance(patch, dict) else {}
    _, room = _room(chat)
    seat = _find(room, seat_id)
    for k in SEAT_FIELDS:
        if k in patch:
            seat[k] = bool(patch[k]) if k in ('memory_write', 'enabled') else str(patch[k] or '')[:4000]
    if isinstance(patch.get('settings'), dict):
        from core.chat.history import normalize_llm_pin
        cur = dict(seat.get('settings') or {})
        seat['settings'] = {**cur, **normalize_llm_pin(cur, dict(patch['settings']))}
    _write(chat, room)
    return _public(seat)


def remove_seat(chat, seat_id):
    _, room = _room(chat)
    room['seats'].remove(_find(room, seat_id))
    _write(chat, room)


# --- the projection ---------------------------------------------------------------

def _names(room):
    import config
    names = {USER: str(getattr(config, 'DEFAULT_USERNAME', '') or 'User'), NOTICE: 'Room'}
    for s in room['seats']:
        names[s['id']] = s.get('name') or s.get('persona') or s['id']
    return names


def _text(content):
    if isinstance(content, list):
        return ' '.join(b.get('text', '') for b in content
                        if isinstance(b, dict) and b.get('type') == 'text').strip()
    return str(content or '').strip()


def _label(row, names):
    sp = row.get('speaker')
    if sp in names:
        return names[sp]
    if row.get('name'):
        return str(row['name'])
    if isinstance(sp, str) and ':' in sp:
        return sp.split(':', 1)[1] or sp
    return sp or 'someone'


def rows_of(chat):
    """The room's rows as stored: user and assistant lines only."""
    msgs, _state, _skips = _sm()._read_store_messages(chat)
    return [m for m in (msgs or []) if isinstance(m, dict) and m.get('role') in ('user', 'assistant')]


def projection(rows, seat, names):
    """(history for the engine, the cue). The seat's own lines are assistant
    rows; everything between two of them is ONE user row of labelled lines;
    the lines since its last turn are the cue. Own-ness keys on the seat id,
    never the persona name (one persona sitting twice, review 2026-10-06)."""
    out, buf = [], []

    def flush():
        if buf:
            out.append({'role': 'user', 'content': '\n\n'.join(buf)})
            buf.clear()

    for r in rows:
        if not isinstance(r, dict) or r.get('role') not in ('user', 'assistant'):
            continue                                   # a tool row or thinking never reaches a seat
        text = _text(r.get('content'))
        if not text:
            continue
        if r.get('speaker') == seat['id']:
            flush()
            out.append({'role': 'assistant', 'content': text})
        else:
            buf.append(f"{_label(r, names)}: {text}")
    if buf:
        cue = '\n\n'.join(buf)
    else:
        cue = '(Nobody has spoken since your last line.)' if out else '(Nobody has spoken yet.)'
    return out, f"{cue}\n\n(Your turn, {names.get(seat['id'], seat.get('name', ''))}.)"


def _preamble(chat, room, seat, names):
    others = [s for s in room['seats'] if s.get('id') != seat['id'] and s.get('enabled', True)]
    who = ', '.join(f"{names[s['id']]} ({s['persona']})"
                    if s.get('persona') and str(s['persona']).lower() != str(names[s['id']]).lower()
                    else names[s['id']] for s in others)
    lines = [f'[Chat room "{chat}"]', 'Several speakers share this conversation and take turns.']
    if room['purpose']:
        lines.append(f"What this room is for: {room['purpose']}")
    lines.append(f"You are {names[seat['id']]}. Also here: {names[USER]}" + (f", {who}" if who else '') + '.')
    lines.append('Other speakers\' lines reach you as "Name: text". Speak only as yourself, in your own words; '
                 'never write lines for anyone else, and do not start your line with your own name. '
                 f'If you have nothing to add this turn, answer exactly {PASS}.')
    return '\n'.join(lines)


def seat_brain(chat, s_room, room, seat):
    """The ephemeral carrier for one seat turn, and its cue. `ephemeral` makes
    every engine save a no-op; `chat` keeps the room's name for the gate,
    Stop and tool context. Scopes: an unlisted key is OFF, never her own
    (the task lane's force-None rule). Tools: the seat's toolset minus the
    tools that change her, agents, the card, and - unless this seat's box is
    ticked - every writer of the mind categories (§29 metadata, no name lists)."""
    from core.chat.function_manager import scope_setting_keys
    from core.chat.history import ConversationHistory
    from core import prompts
    llm = _system().llm_chat
    fm = llm.function_manager
    st = dict(seat.get('settings') or {})
    for k in scope_setting_keys():
        if not st.get(k):
            st[k] = 'none'
    st['private_chat'] = bool(s_room.get('private_chat') or st.get('private_chat'))
    st['spice_enabled'] = False
    st['rag_context'] = 'off'
    st['ghost_context'] = ''
    st['surface'] = MODE
    st['persona'] = seat.get('persona') or ''
    st['custom_context'] = '\n\n'.join(t for t in (str(st.get('custom_context') or '').strip(),
                                                   str(seat.get('context') or '').strip()) if t)
    pd = prompts.get_prompt(st.get('prompt') or 'default')
    if not isinstance(pd, dict):
        pd = prompts.get_prompt('default')
    base = (pd.get('content', '') if isinstance(pd, dict) else '') or ''
    names = _names(room)
    drop = set(OFF_NAMES)
    for cat in OFF:
        drop |= set(fm.tools_in_category(cat))
    if not seat.get('memory_write'):
        for cat in MIND:
            drop |= set(fm.tools_in_category(cat, writes=True))
    tools = fm.resolve_tools(st.get('toolset') or 'none', st.get('extra_toolsets'), drop_tools=drop)
    hist = ConversationHistory(max_history=llm.session_manager.max_history)
    hist.messages, cue = projection(rows_of(chat), seat, names)
    return {'chat': chat, 'ephemeral': True, 'settings': st,
            'system_prompt': f"{base}\n\n{_preamble(chat, room, seat, names)}",
            'tools': tools, 'history': hist, 'room': chat, 'seat': seat['id']}, cue


# --- lines ----------------------------------------------------------------------------

def append_line(chat, role, text, speaker, name, persona=None):
    """One prose row on the room, by name. False when the store refused."""
    row = {'role': role, 'content': text, 'speaker': speaker, 'name': name}
    if persona:
        row['persona'] = persona
    ok = _sm().append_messages_to_chat(chat, [row])
    if not ok:
        logger.error(f"[ROOM] a line was not written to '{chat}' (sealed, degraded, or still busy)")
    return bool(ok)


def _notice(chat, text):
    append_line(chat, 'user', text, NOTICE, 'Room')


def take_line(chat, items):
    """The inbox hands a room chat's TEXT items here: each one is a line from
    its author, never her turn. The person's line resets the human_every
    count and wakes the seats; a door's line wakes them only while the rules
    allow (see _kick). Returns a note for any waiter (an MCP ask)."""
    _, room = _room(chat)
    names = _names(room)
    human = False
    for it in items:
        who = str(getattr(it, 'who', '') or '').strip()
        src = str(getattr(it, 'source', '') or '')
        if src.split(':', 1)[0] in PERSON_SOURCES or who == USER:
            speaker, name, human = USER, names[USER], True
        else:
            speaker, name = f"{src.split(':', 1)[0] or 'door'}:{who or src}", who or src
        append_line(chat, 'user', str(it.text or ''), speaker, name)
    _kick(chat, _rt(chat), room, human)
    if not room['bots_enabled']:
        return f"Posted to the room '{chat}'. Its bots are switched off; nobody answers until they are on."
    return f"Posted to the room '{chat}'. The seats answer on their turns."


def _kick(chat, rt, room, human):
    """A line landed (or Bots came on): run or wake the seats if the rules
    allow. The person's line always counts; a door's never stands in for
    them, so with human_every reached the room keeps waiting for the person."""
    if human:
        rt.since_human = 0
    if not room['bots_enabled']:
        return
    if not human and room['human_every'] and rt.since_human >= room['human_every']:
        return
    try:
        if rt.running:
            if rt.waiting:
                rt.user_turn.set()
        else:
            start(chat)
    except RoomError as e:
        logger.info(f"[ROOM] '{chat}': a line landed but the seats did not run: {e}")


def say(chat, text, source='web', who=USER):
    """The person's line (or a door's), through the room's inbox so it lands
    between seat turns, never inside one. Returns the inbox ticket."""
    from core.chat import inbox
    _room(chat)
    text = str(text or '').strip()
    if not text:
        raise RoomError('Say something.')
    return inbox.put(chat, inbox.Item(text=text, source=source, lane='now', who=who)).ticket


# --- the runner ---------------------------------------------------------------------

class _Runtime:
    def __init__(self, chat):
        self.chat = chat
        self.thread = None
        self.stop_flag = threading.Event()
        self.user_turn = threading.Event()
        self.running = False
        self.round = 0
        self.turn = None
        self.waiting = False
        self.skip = 0
        self.since_human = 0      # rounds since the person's last line (the human_every count)
        self.rounds_run = 0       # rounds this run (the max_rounds count)
        self.stream = None


def _rt(chat):
    with _lock:
        rt = _rooms.get(chat)
        if rt is None:
            rt = _rooms[chat] = _Runtime(chat)
        return rt


def state(chat):
    rt = _rooms.get(chat)
    if rt is None:
        return {'chat': chat, 'running': False, 'round': 0, 'turn': None, 'waiting': False, 'skip': 0,
                'since_human': 0, 'rounds_run': 0}
    return {'chat': chat, 'running': rt.running, 'round': rt.round, 'turn': rt.turn,
            'waiting': rt.waiting, 'skip': rt.skip, 'since_human': rt.since_human, 'rounds_run': rt.rounds_run}


def _publish_state(rt):
    publish('room_state', state(rt.chat), ephemeral=True)


def start(chat, rounds=None):
    """Go. A waiting room wakes for one more round; a stopped room runs.
    rounds=N: the seats talk N rounds before the human_every rule is asked
    again ("skip my turn for N rounds")."""
    _, room = _room(chat)
    if not room['bots_enabled']:
        raise RoomError('Bots are switched off in this room.')
    if not [x for x in room['seats'] if x.get('enabled', True)]:
        raise RoomError('This room has no seats switched on.')
    rt = _rt(chat)
    with _lock:
        if rounds is not None:
            rt.skip = max(0, int(rounds))
        if rt.running:
            if rt.waiting:
                rt.user_turn.set()
            return state(chat)
        rt.running = True
        rt.rounds_run = 0
        rt.stop_flag.clear()
        rt.user_turn.clear()
        rt.thread = threading.Thread(target=_loop, args=(rt,), daemon=True, name=f"room-{chat[:24]}")
        rt.thread.start()
    _publish_state(rt)
    return state(chat)


def stop(chat):
    """Stop: the round ends, the seat in flight is cancelled and its partial
    line dropped, the floor is the person's (the review's policy, 2026-10-06)."""
    rt = _rooms.get(chat)
    if rt is None:
        return state(chat)
    rt.stop_flag.set()
    rt.skip = 0
    s = rt.stream
    if s is not None:
        s.cancel_flag = True
    rt.user_turn.set()
    return state(chat)


def _loop(rt):
    from core.chat import inbox
    import random
    fresh = True                  # no seat has spoken since the run began or the person last spoke: no pause
    try:
        while not rt.stop_flag.is_set():
            chat = rt.chat
            try:
                _, room = _room(chat)          # the rules are read every round: a change lands on the next one
            except RoomError:
                break
            seats = [s for s in room['seats'] if s.get('enabled', True)]
            if not seats or not room['bots_enabled']:
                break
            if room['shuffle']:
                random.shuffle(seats)
            rt.round += 1
            rt.rounds_run += 1
            for seat in seats:
                if rt.stop_flag.is_set():
                    break
                if not fresh and room['pause_s'] and rt.stop_flag.wait(room['pause_s']):
                    break
                fresh = False
                rt.turn, rt.waiting = seat['id'], False
                _publish_state(rt)
                try:
                    inbox.turn(chat, lambda s=seat: _seat_turn(chat, s, rt), source=SOURCE, lane='now')
                except inbox.InboxRefused as e:
                    logger.warning(f"[ROOM] '{chat}': the inbox refused a seat turn: {e}")
                    rt.stop_flag.set()
                    break
                except Exception as e:
                    logger.warning(f"[ROOM] '{chat}': {seat.get('name')}'s turn failed: {type(e).__name__}: {e}")
                    _notice(chat, f"{seat.get('name')} could not take a turn ({type(e).__name__}).")
                rt.turn = None
            if rt.stop_flag.is_set():
                break
            rt.since_human += 1
            if room['max_rounds'] and rt.rounds_run >= room['max_rounds']:
                _notice(chat, f"Stopped after {rt.rounds_run} round{'s' if rt.rounds_run != 1 else ''}.")
                break
            if rt.skip > 0:
                rt.skip -= 1
                continue
            if room['human_every'] and rt.since_human >= room['human_every']:
                rt.waiting = True
                _publish_state(rt)
                rt.user_turn.wait()
                rt.user_turn.clear()
                fresh = True
    finally:
        rt.running, rt.turn, rt.waiting, rt.stream = False, None, False, None
        _publish_state(rt)


def _seat_turn(chat, seat, rt):
    """One seat's turn through THE engine. Raises ChatBusy from begin_stream
    (the inbox puts the item back and tries again). Returns the seat's line
    ('' when it passed, was cut off, stopped, or said nothing)."""
    from core.cadence import visible_text, Unreachable
    if rt.stop_flag.is_set():
        return ''
    system = _system()
    llm = system.llm_chat
    sm = llm.session_manager
    s_room = settings_of(chat)
    if s_room is None:
        raise Unreachable(f"'{chat}' is not a reachable room (missing, sealed, or no longer a room)")
    _, room = _room(chat)
    brain, cue = seat_brain(chat, s_room, room, seat)
    stream, sid, name = llm.begin_stream(chat, exclusive=True)
    stream.brain = brain
    stream.suppress_tts = True
    mid = uuid.uuid4().hex
    try:
        active = sm.get_active_chat_name()
    except Exception:
        active = None
    tag = {'chat': chat, 'foreign': bool(chat != active), 'source': SOURCE,
           'speaker': seat['id'], 'name': seat.get('name'), 'persona': seat.get('persona')}
    parts, blocks, cancelled, errored, cut = [], [], False, None, []

    def _cut():
        cut.append(True)
        stream.cancel_flag = True

    timer = threading.Timer(SEAT_TURN_S, _cut)
    timer.daemon = True
    timer.start()
    rt.stream = stream
    gen = None
    publish(Events.VOICE_TURN_START, {'message_id': mid, **tag})
    try:
        gen = stream.chat_stream(cue)
        for ev in gen:
            if not isinstance(ev, dict):
                continue
            et = ev.get('type')
            if et == 'content':
                parts.append(ev.get('text') or '')
                publish(Events.VOICE_TURN_CHUNK, {'message_id': mid, 'text': ev.get('text', ''),
                                                  'chat': chat, 'foreign': tag['foreign']})
            elif et == 'tool_start':
                if visible_text(''.join(parts)):
                    blocks.append(''.join(parts))
                parts = []
                publish(Events.VOICE_TURN_TOOL, {'message_id': mid, 'chat': chat, 'foreign': tag['foreign'],
                                                 'phase': 'start', 'id': ev.get('id'), 'name': ev.get('name'),
                                                 'args': ev.get('args')})
            elif et == 'tool_end':
                publish(Events.VOICE_TURN_TOOL, {'message_id': mid, 'chat': chat, 'foreign': tag['foreign'],
                                                 'phase': 'end', 'id': ev.get('id'), 'name': ev.get('name'),
                                                 'result': ev.get('result'), 'error': bool(ev.get('error'))})
            elif et == 'final':
                cancelled = bool(ev.get('cancelled'))
                if ev.get('error'):
                    errored = ev.get('text') or 'turn refused'
                    parts, blocks = [], []
                elif ev.get('text'):
                    parts = [ev['text']]
            elif et == 'error':
                errored = ev.get('text') or 'stream error'
    except Exception as e:
        errored = errored or f"{type(e).__name__}: {e}"
    finally:
        timer.cancel()
        rt.stream = None
        if gen is not None:
            try:
                gen.close()          # the engine's finally (end_streaming) runs HERE, before the row below
            except Exception as e:
                logger.warning(f"[ROOM] '{chat}': engine close failed: {e}")
        llm.end_stream(sid, name)
    rounds = [visible_text(b) for b in blocks + [''.join(parts)]]
    text = ('\n\n'.join(r for r in rounds if r)).strip() if not cancelled else ''
    publish(Events.VOICE_TURN_END, {'message_id': mid, 'text': text, 'speak': None, 'dropped': not text, **tag})
    if errored:
        raise RuntimeError(errored)
    if cut:
        _notice(chat, f"{seat.get('name')} ran out of time and was skipped.")
        return ''
    if cancelled or not text or text.strip('() .').lower() == 'pass':
        return ''
    append_line(chat, 'assistant', text, seat['id'], seat.get('name'), persona=seat.get('persona'))
    return text


# --- the chat's life ------------------------------------------------------------------

def chat_deleted(chat):
    with _lock:
        rt = _rooms.pop(chat, None)
    if rt is not None:
        rt.stop_flag.set()
        s = rt.stream
        if s is not None:
            s.cancel_flag = True
        rt.user_turn.set()


def chat_renamed(old, new):
    with _lock:
        rt = _rooms.pop(old, None)
        if rt is not None and new:
            rt.chat = new
            _rooms[new] = rt
