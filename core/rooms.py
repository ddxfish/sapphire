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
# round cap per run, the Bots switch, shuffle (Krem's votes, 2026-10-11), and
# blind: every seat is cued before any of them speaks, so nobody is swayed by
# whoever went first (Krem 2026-10-13).
#
# Every seat is her own Sapphire with a chat of her own (Krem 2026-10-12). A
# local seat's is `seat-<id>` here (mode `seat`, shown as "room · name"): made
# with the seat, cleared and deleted with the room, swept at boot when its room
# or seat is gone. Her turn is the cadence lane on THAT chat (run_turn with a
# caller-built brain: the seat's settings, her prompt plus the room preamble,
# her fenced tools), quiet - the room paints it here under her name. The cue
# is the room's lines since her last line (projection), pictures and all
# (keep_images: each seat keeps her own copy, an image has ONE owner chat
# and the room's copy goes with a clear - Krem 2026-10-13); her chat keeps the
# cue, her tool calls and results and her thinking, so what she read on her
# first turn she still has on her tenth. The ROOM is the record and her chat
# follows it: her turn's rows ride on her room row (`turn`), and a room edited
# under her - a line deleted, an import - rebuilds her chat from the room
# before her next turn (_sync; Krem deleted a question the bots still
# remembered, 2026-10-13). The room gets one row - her line,
# her thinking in front of it as her own chat keeps it (the live bubble
# showed it and the swap lost it, Krem 2026-10-13) - with a footnote naming
# the tools she used (inputs and results too: they ran here). The cue strips
# the thinking. Seats never see each other's tools, and never the room's
# rows as rows: a seat's view of the room is her own chat. The runner is a
# client of the inbox: each seat turn is an Item(run) on the room chat, so a
# line typed mid-round lands in order, between seats, and a seat turn never
# races a line (Krem 2026-10-11: the queue was built so we never reject).
# Record: tmp/chat-room-plan.md.
#
# A seat may also be ANOTHER SAPPHIRE (`kind: sapphire`): a device (the
# Another Sapphire driver) invited through her MCP door, who decided over
# there and named the chat on her side to cue her in. Her turn is one `ask`
# with the lines since her last; her box runs it with her own prompt, model
# and tools, so her seat here is a name, a face, a device and her chat, no
# brain; her line's footnote names her tools only when her box shares them
# (results stay there). Cleared or closed, her box sets her room chat aside
# whole and starts a fresh one (room_reset). A private room keeps its words on
# this machine: no inviting, and a guest already seated is skipped with a
# line (the door's own ruling).
import logging
import socket
import queue
import re
import threading
import time
import uuid

from core.event_bus import publish, Events

logger = logging.getLogger(__name__)

MODE = 'room'
SOURCE = 'room'                 # the inbox source a seat turn carries - the one `run` item a room admits
USER = 'user'                   # the person at the keyboard
NOTICE = 'room'                 # the room's own lines (a seat skipped)
PASS = '(pass)'                 # a seat's whole answer when it has nothing to add
SEAT_TURN_S = 300.0             # past this a seat is cut off and skipped with a line (review 2026-10-06; 180 until
                                # 2026-10-12: Fireworks took 126 s to return one tool call. Now the guest's ask wait.)
MIND = ('memory', 'knowledge', 'people', 'goals')   # categories whose WRITERS are off unless the seat's box is ticked
OFF = ('meta_danger', 'agents')                     # never a seat's: the tools that change HER, and agents
OFF_NAMES = ('ask_user',)                           # a card has no surface in a room
SEAT_FIELDS = ('name', 'context', 'memory_write', 'enabled', 'color', 'tts')   # tts: her lines aloud (the browser's queue)
GUEST = 'sapphire'              # seat kind: another Sapphire, reached through her door
DOOR = 'door'                   # seat kind: a persona answered AT this box's MCP door by whoever holds its key (Claude Code's seat)
DOOR_WAIT = 120.0               # seconds her turn waits for her line once she is at the door (the stdio bridge gives one call 150 s)
DOOR_PRESENT = 90.0             # seconds since her last call at the door she still counts as here; away = skipped at once
WAIT_MAX = 120                  # the longest one room_wait parks
PASS_MARK = '<<PASS>>'          # anywhere in a line: she passes (Krem 2026-10-13, after the phone's <<HANG UP>>)
SEAT = 'seat'                   # the mode of a local seat's own chat: her tools, her memory, her rows
TOOL_NOTE_CAP = 8000            # characters of one tool result on a room line's footnote
CUE_CAP = 7000                  # characters of a guest's cue (her door takes 8000 with the header)
CUE_IMAGES = 6                  # the newest pictures a cue carries (the rest are still on the rows)
# The rules, with their defaults. human_every: the seats go this many rounds
# since the PERSON's last line, then wait for them (0 = never wait; a door's
# line - MCP, an agent, another room - never stands in for the person, so
# this is also the chain-reaction brake). max_rounds: a run stops after this
# many rounds (0 = no cap). pause_s: between seat turns. bots_enabled: off =
# lines land, nobody answers. shuffle: a fresh speaking order every round.
# blind: every seat's cue is read before any of them speaks, each from the
# room as it stood before the round (no seat's answer this round is in
# another's cue). The turns still run one after another: two streams at
# once on the same provider left the second one starved (Fireworks held
# the second connection silent for 20-120 s while the first ran, socket
# evidence 2026-10-13), and one at a time keeps the live bubble.
# mention_max: `@Name` in a line cues that seat ahead of any round, whoever
# wrote it; a seat's answer naming another chains; this many mentioned turns
# since the person's last line is the brake (0 = no cap). Krem 2026-10-13:
# 3 while we get used to it, 50 for a coding session.
RULES = {'human_every': 1, 'pause_s': 0.0, 'max_rounds': 0, 'bots_enabled': True, 'shuffle': False,
         'blind': False, 'mention_max': 3}
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
    out = {k: seat.get(k) for k in ('id', 'name', 'persona', 'context', 'memory_write', 'enabled', 'color', 'avatar', 'tts')}
    out['settings'] = dict(seat.get('settings') or {})
    if seat.get('kind') == GUEST:
        out.update(kind=GUEST, device=seat.get('device'), label=seat.get('label'), chat=seat.get('chat'))
    elif seat.get('kind') == DOOR:
        out['kind'] = DOOR                           # her brain is her key's holder's: no chat here
    else:
        out['chat'] = _seat_chat_name(seat)          # her own chat here (Chat Manager > Chat Rooms)
    return out


def _is_seat(x):
    return isinstance(x, dict) and x.get('id') and (x.get('persona') or x.get('kind') == GUEST)


def _local(seat):
    """A seat whose brain runs here, with a chat of her own: not a guest, not at the door."""
    return seat.get('kind') not in (GUEST, DOOR)


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
    back on with an unanswered line, the seats answer it. Every edit of the
    record (here, the seats, an import) reads and writes under _lock: two
    PUTs in flight used to write the whole record over each other."""
    patch = patch if isinstance(patch, dict) else {}
    with _lock:
        _, room = _room(chat)
        was = room['purpose']
        if 'purpose' in patch:
            room['purpose'] = str(patch['purpose'] or '')[:2000]
        for k in RULES:
            if k in patch:
                room[k] = patch[k]
        room.update(_rules(room))
        _write(chat, room)
    if room['purpose'] != was:
        _brief_guests(chat, room)
    if 'bots_enabled' in patch:
        if not room['bots_enabled']:
            stop(chat)
        else:
            rows = rows_of(chat)
            last = rows[-1].get('speaker') if rows else None
            if last is not None and last != NOTICE and last not in {x.get('id') for x in room['seats']}:
                _kick(chat, _rt(chat), room, human=False)
    return room


def add_seat(chat, persona, name=None, door=False):
    """Seat a persona. `door`: she is answered at this box's MCP door by whoever
    holds an API key that speaks as her (room_wait / room_post, core/mcp_server.py)
    - no brain here, no chat of her own; her voice and face are the persona's."""
    from core.personas import persona_manager
    p = persona_manager.get(str(persona or ''))
    if not p:
        raise RoomError(f"There is no persona named '{persona}'.")
    seed = dict(p.get('settings') or {})
    shown = str(persona)[:1].upper() + str(persona)[1:]            # 'sapphire' sits as 'Sapphire'
    with _lock:
        s, room = _room(chat)
        seat = {'id': uuid.uuid4().hex[:8], 'name': _unique_name(room, str(name or '').strip() or shown),
                'persona': str(persona), 'context': '', 'memory_write': False, 'enabled': True, 'tts': True,
                'color': str(seed.get('trim_color') or ''), 'avatar': p.get('avatar'), 'settings': seed}
        if door:
            seat['kind'] = DOOR
        else:
            seat_chat(chat, s, seat)                 # her own chat first: no seat without one
        room['seats'].append(seat)
        try:
            _write(chat, room)
        except RoomError:
            if not door:
                _delete_chat(_seat_chat_name(seat))  # ...and no chat without a seat
            raise
    return _public(seat)


# --- another Sapphire's seat ---------------------------------------------------------

def _host():
    import config
    return str(getattr(config, 'INSTANCE_NAME', '') or '').strip() or socket.gethostname()


def _door(device_id):
    """(module, brief, config, secrets) of the Another Sapphire device, or
    RoomError. Whatever the health keeper believes about her: the invitation
    goes to her door itself, and the driver says in words what it found there
    (off, unreachable, a refused token, no invitations taken)."""
    from core.devices import engine
    try:
        return engine.door(device_id, 'chat', 'invite')
    except engine.DeviceError as e:
        raise RoomError(str(e))


def invite_sapphire(chat, device_id, name=None, instructions=''):
    """Invite another Sapphire to sit here. She decides on her box (minutes);
    joined, her seat is added. {'joined', 'said', 'seat'?}."""
    s, room = _room(chat)
    if s.get('private_chat'):
        raise RoomError('This room is private: its words stay on this machine, so another Sapphire cannot sit here.')
    mod, brief, cfg, secrets = _door(str(device_id or ''))
    taken = next((x for x in room['seats'] if x.get('kind') == GUEST and x.get('device') == brief['id']), None)
    if taken:
        raise RoomError(f"{brief.get('label') or brief['id']} is already seated here as '{taken.get('name')}'. "
                        "Remove that seat to invite her again.")
    names = _names(room)
    shown = str(name or '').strip()[:80] or str(brief.get('label') or 'Sapphire')
    card = {'room': chat, 'host': _host(), 'purpose': room['purpose'],
            'people': [names[USER]] + [names[x['id']] for x in room['seats'] if x.get('enabled', True)],
            'instructions': str(instructions or '')[:4000], 'private': False, 'name': shown, 'rules': _rules_text(room)}
    try:
        out = mod.invite(brief, cfg, secrets, card)
    except Exception as e:                           # the driver's Problem, in words
        raise RoomError(str(e) or type(e).__name__)
    said = str(out.get('said') or '').strip()[:2000]
    if not out.get('joined') or not out.get('chat'):
        return {'joined': False, 'said': said}
    with _lock:
        _, room = _room(chat)                        # read again: the invitation took a while
        seat = {'id': uuid.uuid4().hex[:8], 'kind': GUEST, 'device': brief['id'], 'label': brief.get('label'),
                'chat': str(out['chat'])[:200], 'name': _unique_name(room, str(out.get('name') or shown)[:80]),
                'persona': '', 'context': card['instructions'], 'memory_write': False, 'enabled': True, 'tts': True,
                'color': '', 'avatar': None, 'settings': {}}
        room['seats'].append(seat)
        _write(chat, room)
    return {'joined': True, 'said': said, 'seat': _public(seat)}


def update_seat(chat, seat_id, patch):
    patch = patch if isinstance(patch, dict) else {}
    with _lock:
        s, room = _room(chat)
        seat = _find(room, seat_id)
        was = seat.get('context')
        for k in SEAT_FIELDS:
            if k in patch:
                seat[k] = bool(patch[k]) if k in ('memory_write', 'enabled', 'tts') else str(patch[k] or '')[:4000]
        if isinstance(patch.get('settings'), dict) and seat.get('kind') != GUEST:      # a guest's brain is hers
            from core.chat.history import normalize_llm_pin
            cur = dict(seat.get('settings') or {})
            seat['settings'] = {**cur, **normalize_llm_pin(cur, dict(patch['settings']))}
        _write(chat, room)
        if _local(seat) and _sm().chat_exists(_seat_chat_name(seat)):
            _sm().set_named_chat_settings(_seat_chat_name(seat), _seat_chat_settings(chat, s, seat))   # her name, her settings
    if seat.get('kind') == GUEST and seat.get('context') != was:
        _brief_guests(chat, room, [seat])
    return _public(seat)


def remove_seat(chat, seat_id):
    with _lock:
        _, room = _room(chat)
        seat = _find(room, seat_id)
        room['seats'].remove(seat)
        _write(chat, room)
    if seat.get('kind') == DOOR:
        _door_gone(chat, seat_id)                    # a wait parked for her wakes to find no seat
    elif _local(seat):
        rt = _rooms.get(chat)
        s = rt.streams.get(seat_id) if rt is not None else None
        if s is not None:                            # she is speaking: her turn is cut, then her chat goes
            s.cancel_flag = True
            _wait_idle(_seat_chat_name(seat))
        _delete_chat(_seat_chat_name(seat))          # her chat goes with her seat, now


# --- a seat's own chat ----------------------------------------------------------------

def _wait_idle(name, timeout=15.0):
    """Until `name` has no stream on it (the store refuses to delete a chat
    mid-stream, and a seat removed while speaking left hers behind)."""
    sm = _sm()
    try:
        if sm.is_streaming(name):
            sm.chat_idle_event(name).wait(timeout=timeout)
    except AttributeError:
        pass

def _seat_chat_name(seat):
    return f"seat-{seat['id']}"


def _seat_chat_settings(chat, s_room, seat):
    """A local seat's own chat: her persona's settings (a typed turn there is
    her too), tagged to the room and the seat, shown as "room · name", as
    private as the room."""
    st = dict(seat.get('settings') or {})
    st.update({'mode': SEAT, 'room': chat, 'seat': seat['id'], 'persona': seat.get('persona') or '',
               'private_display_name': f"{chat} · {seat.get('name') or seat['id']}",
               'private_chat': bool(s_room.get('private_chat'))})
    return st


def seat_chat(chat, s_room, seat):
    """The name of a local seat's own chat, made if missing (an imported seat,
    a chat deleted by hand). None for a guest (hers is on her box) and for a
    seat at the door (her key's holder keeps his own)."""
    if not _local(seat):
        return None
    sm = _sm()
    name = _seat_chat_name(seat)
    if not sm.chat_exists(name):
        if not sm.create_chat(name, settings=_seat_chat_settings(chat, s_room, seat)):
            raise RoomError(f"{seat.get('name')}'s own chat could not be made.")
    return name


def _seat_chats(chat):
    """[(name, settings)] of every seat chat tagged to `chat`, from the store's
    listing: a room being deleted has no record left to ask."""
    out = []
    for c in _sm().list_chat_files() or []:
        s = c.get('settings') if isinstance(c, dict) else None
        if isinstance(s, dict) and s.get('mode') == SEAT and s.get('room') == chat and c.get('name'):
            out.append((c['name'], s))
    return out


def _delete_chat(name):
    """A seat's own chat, gone: the chat route's one true delete (the inbox,
    agents, the active pointer), never a bare row delete."""
    try:
        from core.routes.chat import _delete_one_chat
        ok, why = _delete_one_chat(_system(), name, origin='room')
    except Exception as e:
        ok, why = False, f"{type(e).__name__}: {e}"
    if not ok:
        logger.warning(f"[ROOM] the seat chat '{name}' was not deleted: {why}")


def _reset_guests(chat, room, closed):
    """The room was cleared (or closed): each guest's box sets her room chat
    aside WHOLE under a dated, archived name and, unless closed, starts a
    fresh one for the next cue (her door's room_reset). Nothing of hers is
    wiped, no turn of hers runs, the host can read but never tamper (Krem
    2026-10-12). Not waited for; a door that fails is a line in the log."""
    for seat in [x for x in room['seats'] if x.get('kind') == GUEST and x.get('chat')]:
        def go(seat=seat):
            try:
                mod, brief, cfg, secrets = _door(seat.get('device'))
                said = mod.reset(brief, cfg, secrets, {'room': chat, 'host': _host(), 'closed': bool(closed)})
                logger.info(f"[ROOM] '{chat}': {seat.get('name')}'s box: {said}")
            except Exception as e:
                logger.warning(f"[ROOM] '{chat}': {seat.get('name')}'s room chat was not reset over there ({e})")
        threading.Thread(target=go, daemon=True, name=f"room-reset-{seat['id']}").start()


def _brief_guests(chat, room, seats=None):
    """The room changed - its purpose, or what the host asks of a guest: each
    guest's box (or the given seats') swaps the room block in her room chat
    for the new words (her door's room_update; J15/J16 closed 2026-10-13).
    Not waited for; a door that fails is a notice here and a line in the log."""
    names = _names(room)
    people = [names[USER]] + [names[x['id']] for x in room['seats'] if x.get('enabled', True)]
    for seat in [x for x in (room['seats'] if seats is None else seats) if x.get('kind') == GUEST and x.get('chat')]:
        card = {'room': chat, 'host': _host(), 'purpose': room['purpose'], 'people': people,
                'instructions': str(seat.get('context') or '')[:4000], 'rules': _rules_text(room)}

        def go(seat=seat, card=card):
            try:
                mod, brief, cfg, secrets = _door(seat.get('device'))
                said = mod.update(brief, cfg, secrets, card)
                logger.info(f"[ROOM] '{chat}': {seat.get('name')}'s box: {said}")
            except Exception as e:
                logger.warning(f"[ROOM] '{chat}': {seat.get('name')}'s box was not told the room changed ({e})")
                _notice(chat, f"{seat.get('name')}'s box could not be told the room changed: {_cause(e)}")
        threading.Thread(target=go, daemon=True, name=f"room-brief-{seat['id']}").start()


def chat_deleting(chat):
    """The delete route, BEFORE the row goes: a room's seat chats go with it
    and each guest's box sets her room chat aside. Any other chat: nothing."""
    try:
        _, room = _room(chat)
    except RoomError:
        return
    _settle(chat)
    for name, _s in _seat_chats(chat):
        _delete_chat(name)
    _reset_guests(chat, room, closed=True)


def sweep():
    """Seat chats whose room or seat is gone are deleted: a room deleted while
    the app was down, an import that replaced the seats. At boot, and after an
    import. Returns the names that went."""
    gone, seats_of = [], {}
    for c in _sm().list_chat_files() or []:
        s = c.get('settings') if isinstance(c, dict) else None
        if not isinstance(s, dict) or s.get('mode') != SEAT or not c.get('name'):
            continue
        room = str(s.get('room') or '')
        if room not in seats_of:
            try:
                seats_of[room] = {x['id'] for x in _room(room)[1]['seats'] if _local(x)} if room else set()
            except RoomError:
                seats_of[room] = set()
        if s.get('seat') not in seats_of[room]:
            gone.append(c['name'])
    for name in gone:
        _delete_chat(name)
    if gone:
        logger.info(f"[ROOM] swept {len(gone)} seat chat(s) whose room or seat is gone: {', '.join(gone)}")
    return gone


def _settle(chat):
    """Stop the run and wait for the seat in flight to end, so a wipe or a
    transcript swap never lands inside a turn."""
    stop(chat)
    rt = _rooms.get(chat)
    t = rt.thread if rt is not None else None
    if t is not None and t.is_alive() and t is not threading.current_thread():
        t.join(timeout=5.0)


def _reset(chat):
    rt = _rt(chat)
    rt.round = rt.since_human = rt.rounds_run = rt.skip = 0
    _publish_state(rt)


def clear(chat):
    """Wipe the transcript. The seats, the rules and the purpose stay. A local
    seat's own chat is wiped with it; a guest's box sets hers aside whole and
    starts a fresh one (room_reset through her door)."""
    _, room = _room(chat)
    _settle(chat)
    if not _sm().clear_chat(chat):
        raise RoomError(f"The room '{chat}' could not be cleared (a seat is still speaking, or it is sealed).")
    for name, _s in _seat_chats(chat):
        if not _sm().clear_chat(name):
            logger.warning(f"[ROOM] '{chat}': the seat chat '{name}' was not cleared (a turn in flight, or sealed)")
    publish(Events.CHAT_CLEARED, {'chat_name': chat})
    _reset(chat)
    _reset_guests(chat, room, closed=False)
    return state(chat)


def import_room(chat, data):
    """A room export back in: `messages` replace the transcript; `room` (the
    chat export's settings.room - seats, rules, purpose) replaces the room
    record when given, so a room travels whole."""
    data = data if isinstance(data, dict) else {}
    _room(chat)
    msgs = data.get('messages')
    if isinstance(msgs, list):                      # the transcript first: it can refuse (a seat still streaming)
        _settle(chat)
        ok, err = _sm().replace_messages(chat, [m for m in msgs if isinstance(m, dict)])
        if not ok:
            raise RoomError(f"The transcript could not be imported: {err}")
        _reset(chat)
    rec = data.get('room')
    if isinstance(rec, dict):
        seats = [dict(x) for x in (rec.get('seats') or []) if _is_seat(x)]
        with _lock:
            _room(chat)
            _write(chat, {'purpose': str(rec.get('purpose') or '')[:2000], 'seats': seats, **_rules(rec)})
        sweep()                                      # the seats that went took their chats; new ones get theirs at their turn
    return describe(chat)


# --- the projection ---------------------------------------------------------------

def _names(room):
    import config
    names = {USER: str(getattr(config, 'DEFAULT_USERNAME', '') or 'User'), NOTICE: 'Room'}
    for s in room['seats']:
        names[s['id']] = s.get('name') or s.get('persona') or s['id']
    return names


_AT_ALL = re.compile(r'(?<!\w)@(?:all|everyone)(?!\w)', re.I)


def _mentions(room, text):
    """The seats a line names as `@Name`, ids in order of mention: enabled seats
    only, case-insensitive, the longest name first (so `@Sapphire 2` is not also
    `@Sapphire`). `@all` / `@everyone` names nobody in particular: the usual round."""
    text = str(text or '')
    if '@' not in text or _AT_ALL.search(text):
        return []
    found = []
    for seat in sorted((x for x in room['seats'] if x.get('enabled', True)), key=lambda x: -len(x.get('name') or '')):
        name = seat.get('name') or ''
        m = re.search(r'(?<!\w)@' + re.escape(name) + r'(?!\w)', text, re.I) if name else None
        if m:
            found.append((m.start(), seat['id']))
            text = text[:m.start()] + ' ' * (m.end() - m.start()) + text[m.end():]
    return [sid for _, sid in sorted(found)]


def _rules_text(room):
    """The rules in words, in every seat's cue and preamble and on a guest's
    card: a seat that cannot see them proposes them as new ideas (Sapphire-
    purple, opening night 2026-10-13: a fuel limit, quiet presence)."""
    person = _names(room)[USER]
    he = room['human_every']
    turns = (f"the bots take a round after each of {person}'s lines" if he == 1
             else f"the bots keep going without waiting for {person}" if he == 0
             else f"the bots take {he} rounds, then wait for {person}")
    cap = f"a run stops after {room['max_rounds']} rounds" if room['max_rounds'] else 'no round cap'
    order = ('blind rounds (every cue is read before anyone speaks)' if room['blind']
             else 'shuffled order' if room['shuffle'] else 'round robin')
    pause = f"; {room['pause_s']:g} s between bots" if room['pause_s'] else ''
    mm = room['mention_max']
    chain = (f"a chain of mentions stops after {mm} mentioned turn{'s' if mm != 1 else ''} since {person}'s last line"
             if mm else 'chains of mentions have no cap')
    return (f"House rules: {turns}; {cap}; {order}{pause}; name a seat as @Name and it answers next, whoever wrote "
            f"the line, and {chain}; nothing to add = (pass) or {PASS_MARK}.")


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
    return sp or names[NOTICE]          # a row with no speaker (a compress summary) is the room's


def rows_of(chat):
    """The room's rows as stored: user and assistant lines only."""
    msgs, _state, _skips = _sm()._read_store_messages(chat)
    return [m for m in (msgs or []) if isinstance(m, dict) and m.get('role') in ('user', 'assistant')]


def projection(rows, seat, names):
    """(history for the engine, the cue). The seat's own lines are assistant
    rows; everything between two of them is ONE user row of labelled lines.
    The cue is every line she has not been shown: the lines since her last
    line, plus any that landed between her last cue and that line - `seen` on
    her row is the room's row count when she was cued, and in a blind round
    the others' answers land between (cued one at a time, the two are the
    same). Own-ness keys on the seat id, never the persona name (one persona
    sitting twice, review 2026-10-06). Returns (her view, the cue, the image
    parts of the cue's lines - the newest CUE_IMAGES)."""
    from core import think
    out, buf, kept, last = [], [], [], None

    def flush():
        if buf:
            out.append({'role': 'user', 'content': '\n\n'.join(buf)})
            buf.clear()

    for i, r in enumerate(rows):
        if not isinstance(r, dict) or r.get('role') not in ('user', 'assistant'):
            continue                                   # a tool row or thinking never reaches a seat
        text = think.strip(_text(r.get('content')))     # her thinking rides on her row, never in a cue
        if not text and _image_parts(r.get('content')):
            text = '(an image)'                          # a picture alone is still a line
        if not text:
            continue
        kept.append((i, r, text))
        if r.get('speaker') == seat['id']:
            flush()
            out.append({'role': 'assistant', 'content': text})
            last = i
        else:
            buf.append(f"{_label(r, names)}: {text}")
    if last is None:
        start = 0
    else:
        seen = rows[last].get('seen')                  # stale (a row deleted before it): the lines since her line
        start = seen if isinstance(seen, int) and 0 <= seen <= last else last + 1
    fresh = [(r, t) for i, r, t in kept if i >= start and r.get('speaker') != seat['id']]
    new = [f"{_label(r, names)}: {t}" for r, t in fresh]
    pics = [p for r, _ in fresh for p in _image_parts(r.get('content'))][-CUE_IMAGES:]
    if new:
        cue = '\n\n'.join(new)
    else:
        cue = '(Nobody has spoken since your last line.)' if out else '(Nobody has spoken yet.)'
    return out, f"{cue}\n\n(Your turn, {names.get(seat['id'], seat.get('name', ''))}.)", pics


def _preamble(chat, room, seat, names, first=False):
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
                 f'If you have nothing to add this turn, answer exactly {PASS}, or put {PASS_MARK} in your line.')
    lines.append(_rules_text(room))
    if first:
        lines.append('This is your first turn here: read your self-sheet (read_self) before your line, so you '
                     'speak as yourself. What you read stays in this chat of yours.')
    return '\n'.join(lines)


def seat_brain(chat, s_room, room, seat):
    """The stream brain for one local seat's turn, on HER OWN chat's session
    (seat_chat): the engine saves there - the cue, her tool calls and results,
    her thinking - so what she read on her first turn she still has on her
    tenth. The brain fences the turn: the seat's settings (an unlisted scope
    is OFF, never her own - the task lane's force-None rule), her persona's
    prompt plus the room preamble, and her toolset minus the tools that
    change her, agents, the card, and - unless this seat's box is ticked -
    every writer of the mind categories (§29 metadata, no name lists).
    Returns (brain, cue): the cue is the room's lines since her last line."""
    from core.chat.function_manager import scope_setting_keys
    from core import prompts
    llm = _system().llm_chat
    fm = llm.function_manager
    own = seat_chat(chat, s_room, seat)
    sess = llm.session_manager.make_stream_session(own)
    if sess is None:
        raise RoomError(f"{seat.get('name')}'s own chat '{own}' cannot be read right now (missing, sealed, or unreadable rows).")
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
    hist = sess.get('history')
    rows = getattr(hist, 'messages', hist) or []
    first = not any(isinstance(m, dict) and m.get('role') == 'assistant' for m in rows)
    has_self = any((t.get('function') or {}).get('name') == 'read_self' for t in (tools or []) if isinstance(t, dict))
    sess.update({'settings': st, 'tools': tools, 'room': chat, 'seat': seat['id'],
                 'system_prompt': f"{base}\n\n{_preamble(chat, room, seat, names, first=first and has_self)}"})
    _, cue, parts = projection(rows_of(chat), seat, names)
    return sess, cue, [p for p in map(_pixels, parts) if p]


def _rows(chat):
    msgs, _state, _skips = _sm()._read_store_messages(chat)
    return [m for m in (msgs or []) if isinstance(m, dict)]


def _tools_of(chat, before):
    """The tools a local seat used this turn, lifted from HER chat's rows past
    `before` (the row count before the turn): [{id, name, args, result}] in
    the order they ran. Inputs come off the tool_calls row, results off the
    tool rows; a call without a result row (Stop mid-cycle) is listed without one."""
    import json
    calls, out = {}, []
    for m in _rows(chat)[before:]:
        if m.get('role') == 'assistant':
            for tc in m.get('tool_calls') or []:
                fn = tc.get('function') if isinstance(tc, dict) and isinstance(tc.get('function'), dict) else None
                if not fn or not tc.get('id') or not fn.get('name'):
                    continue
                args = fn.get('arguments')
                if isinstance(args, str):
                    try:
                        args = json.loads(args) if args.strip() else {}
                    except ValueError:
                        args = {'arguments': args}
                calls[tc['id']] = {'id': tc['id'], 'name': fn['name'], 'args': args if isinstance(args, dict) else {}}
                out.append(calls[tc['id']])
        elif m.get('role') == 'tool':
            t = calls.get(m.get('tool_call_id'))
            if t is not None:
                t['result'] = str(m.get('content') or '')[:TOOL_NOTE_CAP]
    return out


def _image_parts(content):
    """A row's image parts ({type: image, media_type, handle | data}), in order."""
    return [b for b in content if isinstance(b, dict) and b.get('type') == 'image'] if isinstance(content, list) else []


def _keep_images(chat, images):
    """The line's pictures into the room chat's own image store (one owner per
    image, core/images.py), as the row's parts. A picture the store refuses
    rides inline on the row, as a chat's does."""
    import base64
    from core import images as store
    parts = []
    for img in images or []:
        if not isinstance(img, dict) or not isinstance(img.get('data'), str) or not img['data']:
            continue
        mt = str(img.get('media_type') or 'image/jpeg')
        try:
            handle = store.stash(base64.b64decode(img['data']), mt, visible=True, chat_name=chat)
            parts.append({'type': 'image', 'media_type': mt, 'handle': handle})
        except Exception as e:
            logger.warning(f"[ROOM] '{chat}': a picture was not stored ({e}); it rides on the row inline")
            parts.append({'type': 'image', 'media_type': mt, 'data': img['data']})
    return parts


def _pixels(part):
    """An image part as a turn takes it: {data (base64), media_type}; None when
    the store no longer has it (the room was cleared under a running cue)."""
    import base64
    from core import images as store
    if part.get('data'):
        return {'data': part['data'], 'media_type': part.get('media_type') or 'image/jpeg'}
    try:
        got = store.resolve(str(part.get('handle') or ''))
        return {'data': base64.b64encode(got.data).decode('ascii'), 'media_type': got.media_type or 'image/jpeg'}
    except Exception as e:
        logger.warning(f"[ROOM] a picture could not be read for a cue: {e}")
        return None


def _key(r):
    """A room row's identity for the seats' view: its own key, else its words."""
    import hashlib
    k = r.get('k')
    return str(k) if k else hashlib.blake2b(f"{r.get('speaker')}|{r.get('content')}".encode('utf-8'), digest_size=6).hexdigest()


def _view(rows, seat, names):
    """Her chat as the room would have handed it to her: the others' lines
    bundled as her cues - each bundle what her cue held then (`seen`), with
    the turn suffix, pictures as parts pointing at the room's - and her own
    turns as the rows they saved (`turn` on her room row; a row from before
    turns were kept is her line alone). The lines since her last line are
    left out: they are her next cue, and the turn writes it."""
    from core import think
    me = names.get(seat['id'], seat.get('name', ''))
    out, pending = [], []                              # pending: (index, 'Name: text', image parts) not handed to her yet
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or r.get('role') not in ('user', 'assistant'):
            continue
        content = r.get('content')
        text, pics = think.strip(_text(content)), _image_parts(content)
        if not text and pics:
            text = '(an image)'
        if not text:
            continue
        if r.get('speaker') != seat['id']:
            pending.append((i, f"{_label(r, names)}: {text}", pics))
            continue
        seen = r.get('seen')
        cut = seen if isinstance(seen, int) and 0 <= seen <= i else i
        handed, pending = [p for p in pending if p[0] < cut], [p for p in pending if p[0] >= cut]
        if handed:
            words = '\n\n'.join(p[1] for p in handed)
        else:
            words = '(Nobody has spoken since your last line.)' if out else '(Nobody has spoken yet.)'
        words += f"\n\n(Your turn, {me}.)"
        parts = [x for p in handed for x in p[2]]
        out.append({'role': 'user', 'content': ([{'type': 'text', 'text': words}] + parts) if parts else words})
        turn = r.get('turn')
        if isinstance(turn, list) and turn:
            out.extend(dict(m) for m in turn if isinstance(m, dict))
        else:
            out.append({'role': 'assistant', 'content': content})
    return out


def _sync(chat, s_room, room, seat, rows):
    """Her chat follows the room (Krem 2026-10-13: he deleted his question
    and the answers, and the bots still remembered it - it sat in their own
    chats). The room is the record; her chat is her view of it plus what her
    turns saved. Appends never move it; any other change to the rows before
    her last sync - a line deleted, an edit, an import, a clear - rebuilds her
    chat from the room before her turn (_view). Her own copies of earlier
    pictures are orphaned by a rebuild and pruned later; the rebuilt cues
    point at the room's. Returns her chat's name."""
    own = seat_chat(chat, s_room, seat)
    keys = [_key(r) for r in rows]
    st = _sm().read_chat_settings(own) or {}
    synced = st.get('room_sync') if isinstance(st.get('room_sync'), list) else []
    if keys[:len(synced)] != synced:
        view = _view(rows, seat, _names(room))
        ok, err = _sm().replace_messages(own, view)
        if not ok:
            raise RoomError(f"{seat.get('name')}'s chat could not follow the room's edit: {err}")
        logger.info(f"[ROOM] '{chat}': the room changed under {seat.get('name')}: her chat follows it ({len(view)} rows)")
    if keys != synced:
        _sm().set_named_chat_settings(own, {'room_sync': keys}, touch_updated=False)
    return own


def _thinking_of(chat, before):
    """Her thinking this turn, lifted from HER chat's rows past `before`: the
    serialized blocks (core/think.py), one per assistant row that carried one,
    in order - so her row in the room shows what the live bubble showed."""
    from core import think
    found = [think.thinking(_text(m.get('content'))) for m in _rows(chat)[before:] if m.get('role') == 'assistant']
    return '\n\n'.join(think.wrap(t) for t in found if t)


# --- lines ----------------------------------------------------------------------------

def append_line(chat, role, text, speaker, name, persona=None, kind=None, tools=None, seen=None, images=None, turn=None):
    """One row on the room, by name, with a key of its own (`k`: the seats'
    chats follow the room by it); `tools` is the line's footnote (the tools
    the seat used that turn); `seen` is the room's row count when she was
    cued (projection); `images` are the line's pictures, stored under the
    room chat and kept as parts beside the words; `turn` is what her turn
    saved on her own chat past the cue (her view is rebuilt from it). False
    when the store refused."""
    parts = _keep_images(chat, images)
    content = (([{'type': 'text', 'text': text}] if text else []) + parts) if parts else text
    row = {'role': role, 'content': content, 'speaker': speaker, 'name': name, 'k': uuid.uuid4().hex[:8]}
    if turn:
        row['turn'] = [dict(m) for m in turn if isinstance(m, dict)]
    if persona:
        row['persona'] = persona
    if kind:
        row['kind'] = kind
    if tools:
        row['tools'] = tools
    if seen is not None:
        row['seen'] = int(seen)
    ok = _sm().append_messages_to_chat(chat, [row])
    if not ok:
        logger.error(f"[ROOM] a line was not written to '{chat}' (sealed, degraded, or still busy)")
    return bool(ok)


def _notice(chat, text):
    append_line(chat, 'user', text, NOTICE, 'Room')


def _cause(e):
    """An exception as a notice can carry it: its words, or its name when it has
    none. "(RuntimeError)" told nobody why a seat could not take a turn (opening
    night, 2026-10-13): the cause - no model loaded in LM Studio - sat in the journal."""
    return (str(e) or type(e).__name__)[:400]


def take_line(chat, items):
    """The inbox hands a room chat's TEXT items here: each one is a line from
    its author, never her turn. The person's line resets the human_every
    count and wakes the seats; a door's line wakes them only while the rules
    allow (see _kick). Returns a note for any waiter (an MCP ask)."""
    _, room = _room(chat)
    names = _names(room)
    human, heard, named = False, None, []
    for it in items:
        who = str(getattr(it, 'who', '') or '').strip()
        src = str(getattr(it, 'source', '') or '')
        text = str(it.text or '')
        named += [sid for sid in _mentions(room, text) if sid not in named]
        if src.split(':', 1)[0] in PERSON_SOURCES or who == USER:
            speaker, name, human = USER, names[USER], True
            heard = src if src.startswith('device:') else None   # the bots' lines are read where the person spoke from
        elif _door_for(room, who) is not None:        # her key's holder, out of turn (tell): her line all the same
            _door_line(chat, _door_for(room, who), text, getattr(it, 'images', None))
            continue
        else:
            speaker, name = f"{src.split(':', 1)[0] or 'door'}:{who or src}", who or src
        append_line(chat, 'user', text, speaker, name, images=getattr(it, 'images', None))
    rt = _rt(chat)
    if human:
        rt.heard = heard
        rt.since_human = 0
        rt.mention_turns = 0
        rt.said += 1
    if named:
        _mention(chat, rt, room, named)
    else:
        _kick(chat, rt, room, human)
    if not room['bots_enabled']:
        return f"Posted to the room '{chat}'. Its bots are switched off; nobody answers until they are on."
    if named:
        return f"Posted to the room '{chat}'. {', '.join(names[sid] for sid in named)} will answer."
    return f"Posted to the room '{chat}'. The seats answer on their turns."


def _mention(chat, rt, room, ids):
    """A line naming seats: they go, whoever wrote it (a door's mention fires
    too; mention_max is the brake), ahead of any round."""
    for sid in ids:
        if sid not in rt.mentions:
            rt.mentions.append(sid)
    if not room['bots_enabled']:
        return
    try:
        if rt.running:
            if rt.waiting:
                rt.user_turn.set()
        else:
            start(chat)
    except RoomError as e:
        logger.info(f"[ROOM] '{chat}': a mention landed but the seats did not run: {e}")


def _door_line(chat, seat, text, images=None):
    """A door seat's line out of turn (`tell`): a bot's row, painted and read
    like one of her turns (START, CHUNK, END for the browser's bubble and
    voices; the satellite the person spoke from), never a person's row with
    her name on it (Krem 2026-10-13: his own avatar on a bot's bubble, and
    the line was mute). No cue, so no `seen`: projection cues from the row."""
    rt = _rt(chat)
    mid = uuid.uuid4().hex
    try:
        active = _sm().get_active_chat_name()
    except Exception:
        active = None
    tag = {'chat': chat, 'foreign': bool(chat != active), 'source': SOURCE,
           'speaker': seat['id'], 'name': seat.get('name'), 'persona': seat.get('persona'), 'kind': DOOR}
    publish(Events.VOICE_TURN_START, {'message_id': mid, **tag})
    append_line(chat, 'assistant', text, seat['id'], seat.get('name'), persona=seat.get('persona'), kind=DOOR,
                images=images)
    _read_aloud(rt, seat, text)
    publish(Events.VOICE_TURN_CHUNK, {'message_id': mid, 'text': text, 'chat': chat, 'foreign': tag['foreign']})
    publish(Events.VOICE_TURN_END, {'message_id': mid, 'text': text, 'speak': None, 'dropped': not text,
                                    'why': '', 'heard': rt.heard, 'tools': [], **tag})


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


def say(chat, text, source='web', who=USER, images=None):
    """The person's line (or a door's), through the room's inbox so it lands
    between seat turns, never inside one. `images`: [{data (base64),
    media_type}] as the composer sends them; a picture alone is a line.
    Returns the inbox ticket."""
    from core.chat import inbox
    _room(chat)
    text = str(text or '').strip()
    images = [i for i in (images or []) if isinstance(i, dict) and isinstance(i.get('data'), str) and i['data']]
    if not text and not images:
        raise RoomError('Say something.')
    return inbox.put(chat, inbox.Item(text=text, source=source, lane='now', who=who, images=images or None)).ticket


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
        self.said = 0             # the person's lines landed this runtime ...
        self.read = 0             # ... and how many of them the latest cue had read: said > read = one nobody has answered
        self.rounds_run = 0       # rounds this run (the max_rounds count)
        self.mentions = []        # seat ids named `@Name` and not yet answered, in order (the mention lane)
        self.mention_turns = 0    # mentioned turns since the person's last line (the mention_max count)
        self.streams = {}        # seat id -> her stream in flight (Stop, delete and remove_seat cut by seat)
        self.guests = {}         # guest seat id -> the worker thread of her last cue (one in flight per seat)
        self.heard = None        # 'device:<id>' when the person's last line came from a satellite: the bots' lines are read there
        self.voice = queue.Queue()   # (device id, text, voice, speed, pitch): lines waiting to be read on that satellite
        self.reader = None       # the thread reading them, one line at a time, in order
        self.doors = {}          # door seat id -> _Door: the cue parked for her key's holder, her line back


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
                'since_human': 0, 'rounds_run': 0, 'heard': None, 'doors': {}}
    return {'chat': chat, 'running': rt.running, 'round': rt.round, 'turn': rt.turn,
            'waiting': rt.waiting, 'skip': rt.skip, 'since_human': rt.since_human, 'rounds_run': rt.rounds_run,
            'heard': rt.heard, 'doors': {sid: d.here() for sid, d in list(rt.doors.items())}}


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
    rt.mentions.clear()
    _cut_all(rt)
    rt.user_turn.set()
    return state(chat)


def _cut_all(rt):
    for s in list(rt.streams.values()):
        s.cancel_flag = True
    for d in list(rt.doors.values()):                # a cue parked at the door is withdrawn; a turn waiting on her ends
        d.drop()
    while not rt.voice.empty():                      # the lines waiting to be read on the satellite go unread
        try:
            rt.voice.get_nowait()
        except queue.Empty:
            break
    if rt.heard:
        try:
            from core.devices import voice
            voice.halt(rt.heard[len('device:'):])
        except Exception as e:
            logger.debug(f"[ROOM] '{rt.chat}': halt on {rt.heard}: {e}")


def _read_aloud(rt, seat, text):
    """Her line read on the satellite the person last spoke from (rt.heard),
    in the seat's own voice, lines in order, the round never waiting: one
    reader thread per room, voice.say one line at a time. The browser's queue
    stays quiet meanwhile (her END carries `heard`). Krem 2026-10-13: 'so I
    can talk to you and Sapph in that chat from my satellite in my room'."""
    dev = rt.heard
    if not dev or not text or seat.get('tts', True) is False:
        return
    st = seat.get('settings') or {}
    rt.voice.put((dev[len('device:'):], text, st.get('voice'), st.get('speed'), st.get('pitch')))
    with _lock:
        if rt.reader is None or not rt.reader.is_alive():
            rt.reader = threading.Thread(target=_reader, args=(rt,), daemon=True, name=f"room-voice-{rt.chat[:20]}")
            rt.reader.start()


def _reader(rt):
    from core.devices import voice
    while True:
        try:
            dev, text, v, sp, pi = rt.voice.get(timeout=30)        # idle half a minute: the thread goes, the next line starts one
        except queue.Empty:
            return
        try:
            said, ok = voice.say(dev, text, chat_settings=settings_of(rt.chat), voice=v, speed=sp, pitch=pi)
            if not ok:
                logger.warning(f"[ROOM] '{rt.chat}': a line was not read on {dev}: {said}")
        except Exception as e:
            logger.warning(f"[ROOM] '{rt.chat}': reading a line on {dev} failed: {e}")


def _person_turn(rt):
    """The floor is the person's: wait for their line, unless one already
    landed that no cue has read (typed during a blind round, or as the round
    ended): that line IS their turn, so the next round answers it now. Checked
    on both sides of `waiting` going up, so a line landing at any moment
    either wakes the wait (_kick) or is seen here, never lost between."""
    if rt.said > rt.read:
        return
    rt.waiting = True
    if rt.said > rt.read:
        rt.waiting = False
        rt.user_turn.clear()                          # _kick may have woken a wait that never began
        return
    _publish_state(rt)
    rt.user_turn.wait()
    rt.user_turn.clear()


def _loop(rt):
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
            if rt.mentions:
                # the mention lane overrides the round: only the seats named speak, then
                # the floor is the person's (whatever human_every says)
                _mention_run(chat, rt, room)
                if rt.stop_flag.is_set():
                    break
                _person_turn(rt)
                fresh = True
                continue
            if room['shuffle']:
                random.shuffle(seats)
            rt.round += 1
            rt.rounds_run += 1
            spoke = False
            if room['blind'] and len(seats) > 1:
                # blind: one inbox item for the round, every cue read before anyone speaks
                if not fresh and room['pause_s'] and rt.stop_flag.wait(room['pause_s']):
                    break
                fresh = False
                spoke = _item(rt, lambda: _blind_round(chat, seats, rt, room['pause_s']), 'The round')
            else:
                for seat in seats:
                    if rt.stop_flag.is_set():
                        break
                    if not fresh and room['pause_s'] and rt.stop_flag.wait(room['pause_s']):
                        break
                    fresh = False
                    rt.turn, rt.waiting = seat['id'], False
                    _publish_state(rt)
                    if _item(rt, lambda s=seat: _turn(chat, s, rt), seat.get('name')):
                        spoke = True
                    rt.turn = None
            if rt.stop_flag.is_set():
                break
            if rt.mentions:
                continue                               # a line this round named a seat: the lane, now, not after the person
            if not spoke:
                # every seat passed, failed or was skipped: nothing to answer, so the
                # run ends instead of asking the same question forever (human every 0
                # used to spin here, one full-context call per seat, all night)
                _notice(chat, 'Nobody spoke this round, so the room stopped. Say something to start it again.')
                break
            rt.since_human += 1
            if room['max_rounds'] and rt.rounds_run >= room['max_rounds']:
                _notice(chat, f"Stopped after {rt.rounds_run} round{'s' if rt.rounds_run != 1 else ''}.")
                break
            if rt.skip > 0:
                rt.skip -= 1
                continue
            if room['human_every'] and rt.since_human >= room['human_every']:
                _person_turn(rt)
                fresh = True
    finally:
        rt.running, rt.turn, rt.waiting = False, None, False
        rt.streams.clear()
        _publish_state(rt)
        try:
            sweep()                   # a seat removed mid-turn whose chat could not go then: now it can
        except Exception as e:
            logger.warning(f"[ROOM] sweep after the run failed: {e}")


def _item(rt, fn, who):
    """One inbox item of the run - a seat's turn, or a whole blind round -
    on the room chat, so a line typed meanwhile lands between items. True when
    someone spoke. The inbox refusing ends the run; anything else is a notice."""
    from core.chat import inbox
    chat = rt.chat
    try:
        return bool(inbox.turn(chat, fn, source=SOURCE, lane='now'))
    except inbox.InboxRefused as e:
        logger.warning(f"[ROOM] '{chat}': the inbox refused a seat turn: {e}")
        rt.stop_flag.set()
    except Exception as e:
        logger.warning(f"[ROOM] '{chat}': {who}'s turn failed: {type(e).__name__}: {e}")
        _notice(chat, f"{who} could not take a turn: {_cause(e)}")
    return False


def _turn(chat, seat, rt, prep=None):
    """One seat's turn (_seat_turn) and, after it, the seats her line names:
    on the mention lane (the chain), never herself. Returns her line."""
    if prep is None:
        rt.read = rt.said                 # her cue is read now (a line lands only between inbox items)
    text = _seat_turn(chat, seat, rt) if prep is None else _seat_turn(chat, seat, rt, prep)
    if text:
        _, room = _room(chat)
        for sid in _mentions(room, text):
            if sid != seat['id'] and sid not in rt.mentions:
                rt.mentions.append(sid)
    return text


def _mention_run(chat, rt, room):
    """The mention lane: the seats named go, in order, one inbox item each; a
    line of theirs naming another chains (_turn), up to mention_max turns since
    the person's last line (0 = no cap). True when someone spoke."""
    spoke = False
    while rt.mentions and not rt.stop_flag.is_set():
        cap = room['mention_max']
        if cap and rt.mention_turns >= cap:
            rt.mentions.clear()
            _notice(chat, f"Stopped after {cap} mentioned turn{'s' if cap != 1 else ''}. Say something to go on.")
            break
        sid = rt.mentions.pop(0)
        seat = next((x for x in room['seats'] if x['id'] == sid and x.get('enabled', True)), None)
        if seat is None:
            continue
        rt.mention_turns += 1
        rt.turn, rt.waiting = sid, False
        _publish_state(rt)
        if _item(rt, lambda x=seat: _turn(chat, x, rt), seat.get('name')):
            spoke = True
        rt.turn = None
    return spoke


def _prep(chat, seat):
    """What a seat's turn starts from, read NOW: {brain (None for a guest),
    cue, images (the cue's pictures, a local seat's only), seen (the room's
    row count: where her cue left off)}. A blind
    round takes every seat's before any of them speaks, so no answer given
    this round is in another seat's cue."""
    from core import cadence
    s_room = settings_of(chat)
    if s_room is None:
        raise cadence.Unreachable(f"'{chat}' is not a reachable room (missing, sealed, or no longer a room)")
    _, room = _room(chat)
    rows = rows_of(chat)
    seen = len(rows)
    if seat.get('kind') == GUEST:
        return {'brain': None, 'cue': _guest_cue(chat, room, seat), 'images': [], 'seen': seen}
    if seat.get('kind') == DOOR:
        cue, parts = _door_cue(chat, room, seat)      # her key's holder sees the pictures: they ride the cue
        return {'brain': None, 'cue': cue, 'images': [p for p in map(_pixels, parts) if p], 'seen': seen}
    _sync(chat, s_room, room, seat, rows)             # the room edited under her: her chat follows it first
    brain, cue, images = seat_brain(chat, s_room, room, seat)
    return {'brain': brain, 'cue': cue, 'images': images, 'seen': seen}


def _blind_round(chat, seats, rt, pause_s=0.0):
    """Every seat answers from the room as it stood before the round (Krem
    2026-10-13: whoever went first swayed the second). The cues are all read
    first, then the turns run one after another as ever; the next round is
    where they read each other (`seen` on her row). One seat's trouble is her
    notice, not the round's. True if anyone spoke."""
    rt.read = rt.said                     # every cue of the round is read now
    preps = []
    for seat in seats:
        try:
            preps.append((seat, _prep(chat, seat)))
        except Exception as e:
            logger.warning(f"[ROOM] '{chat}': {seat.get('name')}'s turn failed: {type(e).__name__}: {e}")
            _notice(chat, f"{seat.get('name')} could not take a turn: {_cause(e)}")
    spoke = False
    for i, (seat, prep) in enumerate(preps):
        if rt.stop_flag.is_set() or (i and pause_s and rt.stop_flag.wait(pause_s)):
            break
        rt.turn, rt.waiting = seat['id'], False
        _publish_state(rt)
        try:
            if _turn(chat, seat, rt, prep):
                spoke = True
        except Exception as e:
            logger.warning(f"[ROOM] '{chat}': {seat.get('name')}'s turn failed: {type(e).__name__}: {e}")
            _notice(chat, f"{seat.get('name')} could not take a turn: {_cause(e)}")
        rt.turn = None
    return spoke


def _seat_turn(chat, seat, rt, prep=None):
    """One seat's turn: the cadence lane on HER OWN chat with the room's brain
    (seat_brain), quiet - the room paints it here under her name from the
    engine's events. Raises ChatBusy from begin_stream (the inbox puts the
    item back and tries again). `prep`: what she starts from, read earlier by
    a blind round (_prep); None = read now. Returns the seat's line ('' when
    she passed, was cut off, stopped, or said nothing: a line in the log, never
    in the room)."""
    from core import cadence
    if rt.stop_flag.is_set():
        return ''
    if seat.get('kind') == GUEST:
        return _guest_turn(chat, seat, rt, prep=prep)
    if seat.get('kind') == DOOR:
        return _door_turn(chat, seat, rt, prep=prep)
    prep = prep or _prep(chat, seat)
    brain, cue = prep['brain'], prep['cue']
    own = brain['chat']
    mid = uuid.uuid4().hex
    try:
        active = _sm().get_active_chat_name()
    except Exception:
        active = None
    tag = {'chat': chat, 'foreign': bool(chat != active), 'source': SOURCE,
           'speaker': seat['id'], 'name': seat.get('name'), 'persona': seat.get('persona')}
    cut, report, timer, started = [], {}, [], []

    def hold(stream):
        # the stream exists: Stop can cut it, the seat clock runs, and the turn is
        # on the wire (a ChatBusy from begin_stream never gets this far: no bubble)
        rt.streams[seat['id']] = stream

        def _cut():
            cut.append(True)
            stream.cancel_flag = True
        t = threading.Timer(SEAT_TURN_S, _cut)
        t.daemon = True
        t.start()
        timer.append(t)
        started.append(True)
        publish(Events.VOICE_TURN_START, {'message_id': mid, **tag})

    def seen(ev):
        et = ev.get('type') if isinstance(ev, dict) else None
        if et == 'content':
            publish(Events.VOICE_TURN_CHUNK, {'message_id': mid, 'text': ev.get('text', ''),
                                              'chat': chat, 'foreign': tag['foreign']})
        elif et == 'tool_start':
            publish(Events.VOICE_TURN_TOOL, {'message_id': mid, 'chat': chat, 'foreign': tag['foreign'],
                                             'phase': 'start', 'id': ev.get('id'), 'name': ev.get('name'),
                                             'args': ev.get('args')})
        elif et == 'tool_end':
            publish(Events.VOICE_TURN_TOOL, {'message_id': mid, 'chat': chat, 'foreign': tag['foreign'],
                                             'phase': 'end', 'id': ev.get('id'), 'name': ev.get('name'),
                                             'result': ev.get('result'), 'error': bool(ev.get('error'))})
        elif et == 'turn_report':
            report.update(ev)

    before = len(_rows(own))
    text, failed = '', None
    try:
        text = str(cadence.run_turn(own, cue, images=prep.get('images') or None, keep_images=True,
                                    source=SOURCE, on_event=seen, keep_prompt=True,
                                    brain=brain, on_stream=hold, quiet=True) or '').strip()
    except Exception as e:
        failed = e
    finally:
        for t in timer:
            t.cancel()
        rt.streams.pop(seat['id'], None)
    passed = _passed(text)
    # why there is no line: her own word (pass), the seat clock, or what the lane saw
    # (stopped, the model's token ceiling, tools and no words, nothing at all); '' when she spoke
    why = ('said: ' + repr(text) if passed else 'ran out of time' if cut else '' if text
           else str(report.get('why') or (f"failed: {_cause(failed)}" if failed is not None else 'no answer')))
    if not why:
        # her row is in BEFORE her END goes out: the browser swaps the live bubble for the
        # saved row 500 ms after END, which used to be a race it mostly won
        thought = _thinking_of(own, before)
        append_line(chat, 'assistant', f"{thought}\n\n{text}" if thought else text, seat['id'], seat.get('name'),
                    persona=seat.get('persona'), tools=_tools_of(own, before), seen=prep['seen'],
                    turn=_rows(own)[before + 1:])              # past the cue: what her turn saved, for her view
        _read_aloud(rt, seat, text)
    if started:
        publish(Events.VOICE_TURN_END, {'message_id': mid, 'text': '' if passed else text, 'speak': None,
                                        'dropped': passed or not text, 'why': why, 'heard': rt.heard,
                                        'tools': list(report.get('tools') or []), **tag})
    if failed is not None:
        raise failed
    if cut:
        _notice(chat, f"{seat.get('name')} ran out of time and was skipped.")
        return ''
    if why:
        # a silent turn is nothing in the room (Krem's rule) but a line in the log: three
        # silent turns in a row looked like lost lines once (field report 2026-10-12)
        logger.info(f"[ROOM] '{chat}': {seat.get('name')} passed ({why})")
        return ''
    return text


def _door_cue(chat, room, seat):
    """(the cue, the image parts on its lines) for a seat cued in words."""
    _, cue, parts = projection(rows_of(chat), seat, _names(room))
    if len(cue) > CUE_CAP:
        cue = '(earlier lines left out)\n\n' + cue[-CUE_CAP:]
    return cue, parts


def _guest_cue(chat, room, seat):
    return _door_cue(chat, room, seat)[0]            # her door takes words; a picture is "(an image)" to her


def _guest_turn(chat, seat, rt, prep=None):
    """Another Sapphire's turn: the lines since her last, through her door
    into the room's chat on her side; her answer is her line. Her box runs
    the turn - nothing of hers is set here. Stop abandons the wait and her
    late answer is dropped. `prep`: read earlier by a blind round (_prep);
    None = read now. Never raises: a miss is a line in the room."""
    from core.cadence import visible_text
    s_room, room = _room(chat)
    name = seat.get('name')
    if s_room.get('private_chat'):
        _notice(chat, f"{name} was skipped: this room is private, and her box is across the network.")
        return ''
    try:
        mod, brief, cfg, secrets = _door(seat.get('device'))
    except RoomError as e:
        _notice(chat, f"{name} was skipped: {e}")
        return ''
    last = rt.guests.get(seat['id'])
    if last is not None and last.is_alive():
        _notice(chat, f"{name} is still answering the last cue, so this round skipped her.")
        return ''
    prep = prep or _prep(chat, seat)
    cue = prep['cue']
    mid = uuid.uuid4().hex
    try:
        active = _sm().get_active_chat_name()
    except Exception:
        active = None
    tag = {'chat': chat, 'foreign': bool(chat != active), 'source': SOURCE,
           'speaker': seat['id'], 'name': name, 'persona': '', 'kind': GUEST}
    box = {}

    def go():
        try:
            box['text'], box['tools'] = mod.cue(brief, cfg, secrets, seat.get('chat'), cue,
                                                f"the chat room '{chat}' on {_host()}")
        except Exception as e:
            box['error'] = str(e) or type(e).__name__

    publish(Events.VOICE_TURN_START, {'message_id': mid, **tag})
    t = threading.Thread(target=go, daemon=True, name=f"room-guest-{seat['id']}")
    rt.guests[seat['id']] = t
    t.start()
    while t.is_alive() and not rt.stop_flag.wait(0.2):
        pass
    raw = str(box.get('text') or '')
    text = '' if t.is_alive() or 'error' in box else visible_text(raw).strip()[:8000]
    low = text.strip('() .').lower()
    if _passed(text) or low.startswith('she said nothing') or low.startswith('nothing came back'):
        text = ''                       # her pass, or her door's words for an empty answer (and why)
    if not text and 'error' not in box:
        logger.info(f"[ROOM] '{chat}': {name} passed ("
                    f"{'abandoned at Stop' if t.is_alive() else 'her door said: ' + repr(raw[:200])})")
    shared = [str(t) for t in (box.get('tools') or []) if t]           # names only, when her box shares them
    why = ('' if text else 'abandoned at Stop' if t.is_alive()
           else f"did not answer: {box['error']}" if 'error' in box else f"her door said: {raw[:120]!r}")
    if text:
        append_line(rt.chat, 'assistant', text, seat['id'], name, kind=GUEST,      # rt.chat: the room may have been renamed meanwhile
                    tools=[{'name': n} for n in shared], seen=prep['seen'])        # her row is in before her END (see _seat_turn)
        _read_aloud(rt, seat, text)
        publish(Events.VOICE_TURN_CHUNK, {'message_id': mid, 'text': text, 'chat': chat, 'foreign': tag['foreign']})
    publish(Events.VOICE_TURN_END, {'message_id': mid, 'text': text, 'speak': None, 'dropped': not text, 'heard': rt.heard,
                                    'why': why, 'tools': shared, **tag})
    if 'error' in box:
        _notice(chat, f"{name} did not answer: {box['error']}")
        return ''
    return text


# --- a seat at the door ---------------------------------------------------------------
#
# A door seat is a persona whose brain is whoever holds an API key that speaks
# as her at this box's MCP door (Claude Code, for one: core/mcp_server.py SEAT).
# Nothing here can call her, so she pulls: room_wait parks at the door until her
# turn comes and hands her the cue (the lines since her last, the pictures on
# them, who is here, what the host asks of her); room_post is her line back;
# room_read the room so far. Away - no call at the door for DOOR_PRESENT s and
# nobody parked - is skipped at once with a line in the log and never in the
# room; here, the room waits DOOR_WAIT s for her line. Krem 2026-10-13: "lets
# wire you in first"; the Matrix door for people and other bots stands on the
# same frame next.

class _Door:
    """A door seat's side of the door: the cue parked for her key's holder, her
    line back, and whether anyone is there."""

    def __init__(self):
        self.ready = threading.Event()        # a cue is parked (or the door was dropped: wake and look)
        self.answered = threading.Event()     # her line is in, or the turn was dropped
        self.cue = None                       # {'mid', 'cue', 'images'} while her turn is open
        self.taken = False                    # the cue was handed out (a second wait is told to post)
        self.answer = None
        self.seen = None                      # her last call at the door (time.monotonic)
        self.parked = 0                       # room_wait calls parked now

    def here(self):
        return self.parked > 0 or (self.seen is not None and time.monotonic() - self.seen < DOOR_PRESENT)

    def park(self, mid, cue, images):
        with _lock:
            self.cue, self.taken, self.answer = {'mid': mid, 'cue': cue, 'images': images}, False, None
            self.answered.clear()
            self.ready.set()

    def take_back(self):
        """(answered, her line): the turn closes and the cue is withdrawn, so a
        late line finds no turn open."""
        with _lock:
            out = self.answered.is_set(), self.answer
            self.cue, self.taken, self.answer = None, False, None
            self.ready.clear()
            self.answered.clear()
            return out

    def drop(self):
        """Stop, or her seat gone: the cue is withdrawn, a turn waiting on her
        ends, a wait parked for her wakes to find nothing."""
        with _lock:
            self.cue, self.taken, self.answer = None, False, None
            self.ready.set()
            self.answered.set()


def _passed(text):
    """Her whole answer is (pass), or PASS_MARK is anywhere in it."""
    t = str(text or '').strip()
    return bool(t) and (t.strip('() .').lower() == 'pass' or PASS_MARK in t.upper())


def _door_of(rt, seat_id):
    with _lock:
        d = rt.doors.get(seat_id)
        if d is None:
            d = rt.doors[seat_id] = _Door()
        return d


def _door_for(room, persona):
    """The room's door seat for this persona (by name, case apart), or None."""
    p = str(persona or '').strip().lower()
    if not p:
        return None
    return next((x for x in room['seats'] if x.get('kind') == DOOR and str(x.get('persona') or '').lower() == p), None)


def _door_seat(persona, chat):
    """(the room record, her seat) for the persona's seat at the door of `chat`;
    RoomError in words for no persona, no such room, or no such seat."""
    if not str(persona or '').strip():
        raise RoomError('This needs an API token that speaks as a persona (Settings > System > API Keys): '
                        "her seat at a room's door is found by that name.")
    _, room = _room(str(chat or ''))
    seat = _door_for(room, persona)
    if seat is None:
        raise RoomError(f"The persona '{persona}' has no seat at the door of '{chat}'. The host adds one in the "
                        "room's roster (Add bot > At the door).")
    return room, seat


def _about(chat, room, seat):
    """What her key's holder reads with every cue: the room, who she is here,
    its purpose, who else is here, what the host asks of her."""
    names = _names(room)
    return {'room': chat, 'you': names[seat['id']], 'purpose': room['purpose'],
            'people': [names[USER]] + [names[x['id']] for x in room['seats']
                                       if x.get('enabled', True) and x['id'] != seat['id']],
            'instructions': str(seat.get('context') or ''), 'rules': _rules_text(room)}


def _door_gone(chat, seat_id):
    rt = _rooms.get(chat)
    d = rt.doors.pop(seat_id, None) if rt is not None else None
    if d is not None:
        d.drop()


def door_rooms(persona):
    """The rooms where this persona has a seat at the door: [{room, you, purpose, running}]."""
    out = []
    for r in list_rooms():
        try:
            _, room = _room(r['chat'])
        except RoomError:
            continue
        seat = _door_for(room, persona)
        if seat is not None:
            out.append({'room': r['chat'], 'you': seat.get('name'), 'purpose': room['purpose'],
                        'running': bool(r['state'].get('running'))})
    return out


def door_read(persona, chat, limit=40):
    """The room so far, for her key's holder: {about, lines: [{k, who, text,
    pictures?, you?}], your_turn}. Words only (thinking and tool footnotes never
    leave). A call at the door counts as being here."""
    from core import think
    room, seat = _door_seat(persona, chat)
    rt = _rt(chat)
    d = _door_of(rt, seat['id'])
    d.seen = time.monotonic()
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 40
    names, lines = _names(room), []
    for r in rows_of(chat)[-max(1, min(n, 200)):]:
        text = think.strip(_text(r.get('content')))
        pics = len(_image_parts(r.get('content')))
        if text or pics:
            lines.append({'k': r.get('k'), 'who': _label(r, names), 'text': text,
                          **({'pictures': pics} if pics else {}), **({'you': True} if r.get('speaker') == seat['id'] else {})})
    return {'about': _about(chat, room, seat), 'lines': lines, 'your_turn': bool(d.cue) and not d.taken}


def door_wait(persona, chat, seconds=None):
    """Park at the door until her turn comes, up to WAIT_MAX s: {turn, about,
    cue?, images?, seconds?, note}. The cue is handed out once; a second wait
    while her turn is open is told to post. Parked = here (the roster shows it)."""
    room, seat = _door_seat(persona, chat)
    rt = _rt(chat)
    d = _door_of(rt, seat['id'])
    try:
        secs = int(seconds)
    except (TypeError, ValueError):
        secs = WAIT_MAX
    secs = max(1, min(secs, WAIT_MAX))
    with _lock:
        d.parked += 1
        d.seen = time.monotonic()
    _publish_state(rt)
    try:
        d.ready.wait(secs)
    finally:
        with _lock:
            d.parked -= 1
            d.seen = time.monotonic()
    with _lock:
        cue = d.cue
        if cue is None:
            d.ready.clear()                              # a drop woke us: nothing to take
            out = {'turn': False, 'note': 'Nobody cued you in that time. Wait again, or room_read the room so far.'}
        else:
            # every waiter gets the cue while the turn is open, not only the first: a wait
            # left parked by a client that died (a watcher killed mid-wait) woke first, took
            # the cue, and the live one was told "already handed" and knew nothing (Krem
            # 2026-10-13: "Claude did not answer in time and was skipped"). room_post is
            # one line per turn either way; the note says when it was handed out before.
            again = d.taken
            d.taken = True
            d.ready.clear()
            out = {'turn': True, 'cue': cue['cue'], 'images': list(cue['images'] or []), 'seconds': int(DOOR_WAIT),
                   'note': ('Your turn is still open (this cue was handed out before): ' if again else '')
                           + f"room_post your line within {int(DOOR_WAIT)} s; an empty post or {PASS_MARK} in it passes."}
    _publish_state(rt)
    try:
        room, seat = _door_seat(persona, chat)           # read again: the purpose, the people, or her seat may have changed
    except RoomError as e:
        return {'turn': False, 'note': str(e)}           # her seat went while she waited
    return {**out, 'about': _about(chat, room, seat)}


def door_post(persona, chat, text):
    """Her line for the turn open at the door. Empty, or with PASS_MARK in it,
    she passes. RoomError when no turn of hers is open: a line out of turn goes
    through `tell` with the room as the chat, and lands as her all the same."""
    room, seat = _door_seat(persona, chat)
    rt = _rt(chat)
    d = _door_of(rt, seat['id'])
    with _lock:
        d.seen = time.monotonic()
        if d.cue is None:
            raise RoomError(f"It is not {seat.get('name')}'s turn in '{chat}' right now: wait for the cue with "
                            "room_wait, or say it out of turn with tell.")
        words = d.answer = str(text or '')[:8000]
        d.answered.set()
    return 'Passed.' if not words.strip() or _passed(words) else f"Posted as {seat.get('name')} in '{chat}'."


def _door_turn(chat, seat, rt, prep=None):
    """A door seat's turn: the cue is parked for her key's holder and the room
    waits for her line (DOOR_WAIT s). Away is skipped at once - a line in the
    log, never in the room (Krem's rule for silent turns); out of time is a
    notice, like a local seat cut off. Stop withdraws the cue and drops a late
    line. `prep`: read earlier by a blind round (_prep); None = read now."""
    from core.cadence import visible_text
    name = seat.get('name')
    d = _door_of(rt, seat['id'])
    if not d.here():
        logger.info(f"[ROOM] '{chat}': {name} passed (away: nobody at the door)")
        return ''
    prep = prep or _prep(chat, seat)
    mid = uuid.uuid4().hex
    try:
        active = _sm().get_active_chat_name()
    except Exception:
        active = None
    tag = {'chat': chat, 'foreign': bool(chat != active), 'source': SOURCE,
           'speaker': seat['id'], 'name': name, 'persona': seat.get('persona'), 'kind': DOOR}
    d.park(mid, prep['cue'], prep['images'])
    publish(Events.VOICE_TURN_START, {'message_id': mid, **tag})
    end = time.monotonic() + DOOR_WAIT
    while not d.answered.wait(0.2):
        if rt.stop_flag.is_set() or time.monotonic() > end:
            break
    answered, raw = d.take_back()
    stopped = rt.stop_flag.is_set()
    text = '' if stopped or not answered else visible_text(str(raw or '')).strip()[:8000]
    why = ('abandoned at Stop' if stopped else 'ran out of time' if not answered
           else 'said: ' + repr(text[:80]) if _passed(text) else '' if text else 'said nothing')
    if not why:
        append_line(rt.chat, 'assistant', text, seat['id'], name, persona=seat.get('persona'), kind=DOOR,
                    seen=prep['seen'])                   # her row is in before her END (see _seat_turn)
        _read_aloud(rt, seat, text)
        publish(Events.VOICE_TURN_CHUNK, {'message_id': mid, 'text': text, 'chat': chat, 'foreign': tag['foreign']})
    publish(Events.VOICE_TURN_END, {'message_id': mid, 'text': '' if why else text, 'speak': None, 'dropped': bool(why),
                                    'why': why, 'heard': rt.heard, 'tools': [], **tag})
    if why == 'ran out of time':
        _notice(chat, f"{name} did not answer in time and was skipped.")
        return ''
    if why:
        logger.info(f"[ROOM] '{chat}': {name} passed ({why})")
        return ''
    return text


# --- the chat's life ------------------------------------------------------------------

def chat_deleted(chat):
    with _lock:
        rt = _rooms.pop(chat, None)
    if rt is not None:
        rt.stop_flag.set()
        _cut_all(rt)
        rt.user_turn.set()


def chat_renamed(old, new):
    with _lock:
        rt = _rooms.pop(old, None)
        if rt is not None and new:
            rt.chat = new
            _rooms[new] = rt
    try:
        _, room = _room(new)
    except RoomError:
        return
    names = {s['id']: s.get('name') or s['id'] for s in room['seats']}
    for name, s in _seat_chats(old):                 # her chat is tagged to the room by name: retag, reshow
        _sm().set_named_chat_settings(name, {'room': new, 'private_display_name': f"{new} · {names.get(s.get('seat'), s.get('seat'))}"})
