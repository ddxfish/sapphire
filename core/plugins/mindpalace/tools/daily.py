# core/plugins/mindpalace/tools/daily.py - the Daily layer (2026-11-20).
#
# Every finished day's summary from /compact-daily is kept here, in the chat's
# own memory scope, so a day that rolls off the chat window is not gone. Plan,
# votes and the scout hunt: tmp/daily-compact-plan.md.
#
# Where days come from: core publishes CHAT_COMPRESSED after a compact has
# written. We listen, read THAT chat's day pairs (rows carrying
# compressed_day) and record the ones not held yet. Every start of the palace
# also walks all chats once: a compact with nothing new publishes nothing, so
# the event alone would never repair a day that failed to record.
#
# A day = N rows in `chunks` (layer 'daily'), one per piece of at most 512
# chars, sharing `source` = day:<date>:<compressed_at>, ordered by
# chunk_index, `created` = that day, `label` = the ISO date. meta.sep holds
# the exact text that stood before each piece, so the pieces give the summary
# back to the character. The chat's name is in meta.chat only: the vault
# scrub and the rename listener keep that one place true.
#
# Rules: private chats, rooms, games and a memory scope of none write nothing.
# A day is recorded once, in one scope. She cannot save, edit or delete here
# (palace_tools refuses). The librarian never reads this layer. A day deleted
# in the app stays deleted (daily_gone). Days link to the people they name
# with edges of their own kind ('retells'): a walk can leave a day for its
# people, and never counts a day against a person or lands on a day's pieces.

import json
import logging
import re
import sys
import threading
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

LAYER = 'daily'
EDGE_KIND = 'retells'
MIXED_DAYS = 3        # days shown under an all-layer search
# 1,500 was the first guess; a real day (2026-11-20) showed a match plus the
# piece after it is ~1,000 chars, and two matches in one day did not fit.
MIXED_CHARS = 2400    # ...sharing this much text
LAYER_CHARS = 6000    # search_memory(layer='daily'): shared by the days found
READ_CHARS = 12000    # whole days asked for by date; over it, the index instead
INDEX_CHARS = 160
# The palace's 50,000-row cap guards against a runaway writer. This layer
# grows by a few rows a night by design: at the old cap three chats lose a
# day every night after a few years (longevity scout, 2026-11-20).
ROW_CAP = 500_000

_lock = threading.Lock()    # one recorder at a time
_stopping = False           # the palace is unloading: no new writes


def _pt():
    from plugins.mindpalace.tools import palace_tools
    return palace_tools


def enabled() -> bool:
    """daily_enabled (default ON). Unreadable settings = on: the rows are her
    memory, and a skipped night is only repaired while the pair is in chat."""
    try:
        from core.plugin_loader import plugin_loader
        return plugin_loader.get_plugin_settings('mindpalace').get('daily_enabled', True) is not False
    except Exception:
        return True


# ─── Splitting a summary into pieces ─────────────────────────────────────────

_LINE = re.compile(r'(\s*\n\s*)')
_SENTENCE = re.compile(r'(?<=[.!?…"\')\]])(\s+)(?=[A-Z0-9"\'(\[])')


def split(text: str, cap: int = 512) -> list:
    """[(piece, sep)] in order; sep is the exact text that stood before the
    piece ('' for the first), so join() gives text.strip() back to the
    character. Pieces never cross a line: a paragraph is one topic, and a
    piece that holds two embeds as neither. A sentence over the cap is cut at
    its last whitespace, or mid-word (sep '') when it has none."""
    out = []
    parts = _LINE.split((text or '').strip())       # line, separator, line, ...
    for k in range(0, len(parts), 2):
        line = parts[k]
        if not line:
            continue
        cur, cur_sep = '', (parts[k - 1] if k else '')
        units = _SENTENCE.split(line)               # sentence, whitespace, sentence, ...
        for j in range(0, len(units), 2):
            sent, lead = units[j], (units[j - 1] if j else '')
            if cur and len(cur) + len(lead) + len(sent) <= cap:
                cur += lead + sent
            else:
                if cur:
                    out.append((cur, cur_sep))
                    cur_sep = lead
                cur = sent
            while len(cur) > cap:
                at = max((m.start() for m in re.finditer(r'\s+', cur[:cap + 1]) if m.start() > 0),
                         default=0)
                if at:
                    gap = re.match(r'\s+', cur[at:]).end()
                    out.append((cur[:at], cur_sep))
                    cur_sep, cur = cur[at:at + gap], cur[at + gap:]
                else:
                    out.append((cur[:cap], cur_sep))
                    cur_sep, cur = '', cur[cap:]
        if cur:
            out.append((cur, cur_sep))
    return out


def join(pieces) -> str:
    """Undo split(): [(text, sep)] back to one text."""
    return ''.join(sep + piece for piece, sep in pieces)


def _sep(meta, index) -> str:
    """The text before a stored piece. Rows written before `sep` existed
    carry br (0 = space, 1 = line break, 2 = blank line)."""
    if index == 0:
        return ''
    if isinstance(meta.get('sep'), str):
        return meta['sep']
    br = meta.get('br', 0)
    return (' ', '\n', '\n\n')[br if br in (0, 1, 2) else 0]


# ─── Day pairs in a chat ─────────────────────────────────────────────────────

def day_pairs(msgs) -> list:
    """The day summaries in a chat's rows: [{day, at, text, persona, messages}].
    A pair is the user row and the assistant row after it, both carrying the
    same compressed_day (core/chat/compress.py, _summary_pair)."""
    out = []
    for u, a in zip(msgs or [], (msgs or [])[1:]):
        if not (isinstance(u, dict) and isinstance(a, dict)):
            continue
        day = u.get('compressed_day')
        if not day or u.get('role') != 'user' or a.get('role') != 'assistant' \
                or a.get('compressed_day') != day or not isinstance(a.get('content'), str):
            continue
        try:
            date.fromisoformat(day)
        except (TypeError, ValueError):
            continue
        text = a['content'].strip()
        if not text:
            continue
        count = re.search(r'\((\d+) messages?\)', str(u.get('content') or ''))
        out.append({'day': day, 'at': str(u.get('compressed_at') or u.get('timestamp') or ''),
                    'text': text, 'persona': str(a.get('persona') or ''),
                    'messages': int(count[1]) if count else None})
    return out


def source_of(pair) -> str:
    return f"day:{pair['day']}:{pair['at']}"


def _user_tz():
    from zoneinfo import ZoneInfo
    import config
    try:
        return ZoneInfo(getattr(config, 'USER_TIMEZONE', 'UTC') or 'UTC')
    except Exception:
        return ZoneInfo('UTC')


def _created(day: str) -> str:
    """Noon of that day in the person's zone, in the palace's one timestamp
    format: recall shows the right day whatever the zone."""
    d = date.fromisoformat(day)
    return datetime(d.year, d.month, d.day, 12, tzinfo=_user_tz()).astimezone(
        timezone.utc).isoformat(timespec='seconds')


# ─── Where a chat's days go ──────────────────────────────────────────────────

_OFF = (False, 0, '', 'false', '0', 'no', 'off', None)


def chat_scope(sm, chat):
    """(scope, settings, why). scope None = record nothing, and why says it.
    Everything unreadable fails closed. Nothing here builds a provider or
    reads a message: it is the check that runs before a chat is opened, and
    again at the moment its days are written."""
    try:
        s = sm.read_chat_settings(chat)
    except Exception as e:
        return None, None, f"settings unreadable ({e})"
    if not isinstance(s, dict):
        return None, None, 'no readable settings'
    if s.get('private_chat') not in _OFF:
        return None, None, 'private chat'
    if s.get('mode') in ('room', 'game'):
        return None, None, f"a {s.get('mode')} chat"
    try:
        from core.chat.function_manager import SCOPE_REGISTRY
        reg = SCOPE_REGISTRY.get('memory')
    except Exception:
        reg = None
    if not reg or not reg.get('setting'):
        return None, None, 'no memory scope registered'
    scope = s.get(reg['setting'], '')
    if scope == '':
        scope = reg.get('default')
    if not isinstance(scope, str) or not scope or scope in ('none', 'global'):
        return None, None, 'memory scope none'
    return scope, s, ''


def chat_writer(chat, settings):
    """(provider key, model) that summarized: the chat's own model, by the
    resolver compress itself used (`auto` becomes the provider that answers,
    a chat on its provider's default gets that model's name). None when it
    cannot be said: a day whose writer is unknown is not recorded, because it
    could be a private model's day handed to every reader."""
    key, model = settings.get('llm_primary') or 'auto', settings.get('llm_model') or ''
    if key == 'none':
        return None
    try:
        from core.chat.llm_providers.resolve import resolve
        sel = resolve(key, model, private=False, health='skip', conversation=chat)
        return (sel.key, sel.effective_model or None) if sel.key else None
    except Exception as e:
        logger.warning(f"[DAILY] a chat's model could not be resolved; its days are not recorded: {e}")
        return None


# ─── Recording ───────────────────────────────────────────────────────────────

def _tables(cursor):
    cursor.execute('CREATE TABLE IF NOT EXISTS daily_gone (scope TEXT NOT NULL, source TEXT NOT NULL, '
                   'gone TEXT NOT NULL, PRIMARY KEY (scope, source))')
    try:
        # One row per piece of a day, whoever writes it (a second recorder, an
        # import): the lock is a courtesy, this is the rule. It is also the
        # index every read of a day uses.
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_chunks_daily_piece "
                       "ON chunks(scope, source, chunk_index) WHERE layer = 'daily'")
    except Exception as e:
        logger.warning(f"[DAILY] the day index could not be made (duplicate pieces already stored?): {e}")


def record_chat(chat, sm=None, pt=None) -> dict:
    """Record the day summaries of one chat that are not held yet.
    Returns {'days': [iso, ...], 'scope': str|None, 'skipped': why}."""
    if _stopping or not enabled():
        return {'days': [], 'scope': None, 'skipped': 'switched off'}
    sm, pt = sm or _session_manager(), pt or _pt()
    scope, settings, why = chat_scope(sm, chat)     # settings first: a private chat is never read
    if scope is None:
        return {'days': [], 'scope': None, 'skipped': why}
    exported = sm.export_chat(chat)
    pairs = day_pairs((exported or {}).get('messages'))
    if not pairs:
        return {'days': [], 'scope': scope, 'skipped': ''}
    with _lock:
        if _stopping:
            return {'days': [], 'scope': None, 'skipped': 'switched off'}
        days, why = _record(pt, sm, scope, chat, settings, pairs)
    return {'days': days, 'scope': None if why and not days else scope, 'skipped': why}


def _record(pt, sm, scope, chat, settings, pairs):
    """(days recorded, why not). One transaction: all of a run or none."""
    if not pt._ensure_db():
        raise RuntimeError('mind database unavailable')
    from plugins.mindpalace.tools import metadata as md
    new, private, full, held = [], False, False, 0
    with pt._get_connection() as conn:
        cur = conn.cursor()
        _tables(cur)
        # A day is recorded once, in ONE scope: a chat that changes persona
        # keeps its pairs for days, and the next compact would copy the old
        # persona's days into the new one's memory (scout hunt 2026-11-20).
        have = {r[0] for r in cur.execute(
            "SELECT DISTINCT source FROM chunks WHERE layer = 'daily' AND source IS NOT NULL")}
        have |= {r[0] for r in cur.execute('SELECT source FROM daily_gone')}
        todo = [p for p in pairs if source_of(p) not in have]
        if not todo:
            return [], ''
        writer = chat_writer(chat, settings)        # only now: most chats have nothing new
        if writer is None:
            return [], "the chat's model could not be resolved"
        held = cur.execute("SELECT COUNT(*) FROM chunks WHERE scope = ? AND layer = 'daily'",
                           (scope,)).fetchone()[0]
        try:
            # A cloud model's day does not link to a private-model person.
            with pt.as_reader(pt._provider_private(writer[0])):
                arows = md.entity_aliases(cur, scope)
            amap, aliases = md.alias_map(arows), [a for _, _, a in arows]
        except Exception as e:
            logger.warning(f"[DAILY] entity names unavailable, days recorded unlinked: {e}")
            amap, aliases = {}, []
        now = pt._now()
        for p in todo:
            src = source_of(p)
            if src in have:
                continue                    # the same pair twice in one chat
            pieces = split(p['text'], pt.MAX_CHUNK_LENGTH)
            if held + len(pieces) > ROW_CAP:
                full = True                 # the days that fit are kept
                break
            have.add(src)
            held += len(pieces)
            ids, was_private = _write_day(cur, pt, scope, chat, p, src, pieces, writer, now, md)
            _link_day(cur, md, ids, [c for c, _ in pieces], amap, aliases, now)
            private = private or was_private
            new.append(p['day'])
        if new:
            # The chat may have gone private, or the palace begun unloading,
            # while this ran: the check that let us read it is made again at
            # the moment of writing. Not committed = not written.
            if _stopping or chat_scope(sm, chat)[0] != scope:
                return [], 'the chat changed while its days were being recorded'
            days = sorted(set(new))
            pt._ledger(scope, 'system', 'recorded', layer=LAYER, cursor=cur, private=private,
                       summary=(f"{len(new)} day{'s' if len(new) != 1 else ''} kept in daily memory: "
                                + (days[0] if len(days) == 1 else f"{days[0]} to {days[-1]}")))
            conn.commit()
    if new:
        pt.reset_backfill_latch()           # the pieces have no vectors yet
        pt._publish_mind(LAYER, scope, 'save')
        try:
            pt._backfill_embeddings(background=True)
        except Exception as e:
            logger.debug(f"[DAILY] embedding sweep not started: {e}")
        logger.info(f"[DAILY] recorded {len(new)} day(s) in scope '{scope}'")
    _tell_cap(held, full)
    return new, ('the daily layer of this scope is full' if full and not new else '')


def _tell_cap(held, full):
    try:
        if not full and held < ROW_CAP * 0.8:
            return
        from core import notify
        if full:
            notify.notify('Daily memory is full', level='warning', source='mindpalace', key='daily-full',
                          body=f"A memory scope holds {held:,} pieces of day summaries, the most it keeps. "
                               'New days are NOT being recorded. Delete old days in Mind > Daily to make room.',
                          go={'view': 'layer-daily'})
        else:
            notify.notify('Daily memory is four fifths full', level='warning', source='mindpalace',
                          key='daily-cap-80', once=True,
                          body=f"A memory scope holds {held:,} of {ROW_CAP:,} pieces of day summaries. "
                               'When it is full, new days stop being recorded.', go={'view': 'layer-daily'})
    except Exception as e:
        logger.debug(f"[DAILY] cap notice skipped: {e}")


def _write_day(cur, pt, scope, chat, pair, src, pieces, writer, now, md):
    """One day's rows: (their ids in order, are they private-model rows)."""
    created, ids, private = _created(pair['day']), [], False
    for i, (content, sep) in enumerate(pieces):
        try:
            # anchor: "tomorrow" in a summary counts from ITS day. No noun
            # harvest (link_text=''): nothing here feeds the link pass.
            meta = md.derivable_meta(content, anchor=created, link_text='')
        except Exception as e:
            logger.warning(f"[DAILY] metadata skipped for a piece: {e}")
            meta = {'md_v': md.MD_VERSION}
        meta.pop('when', None)              # a retelling is never a plan
        meta.pop('when_src', None)
        meta.update({'added_by': 'ai', 'day': pair['day'], 'chat': chat, 'part': i,
                     'parts': len(pieces), 'compressed_at': pair['at']})
        if i and sep != ' ':
            meta['sep'] = sep
        if pair['persona']:
            meta['persona'] = pair['persona']
        if pair['messages'] is not None:
            meta['messages'] = pair['messages']
        cur.execute(
            'INSERT OR IGNORE INTO chunks (layer, scope, content, label, favorite, meta, created, '
            "updated, chunk_index, source) VALUES ('daily', ?, ?, ?, 0, ?, ?, ?, ?, ?)",
            (scope, content, pair['day'], json.dumps(meta, ensure_ascii=False), created, now, i, src))
        if not cur.rowcount:
            raise RuntimeError('a piece of this day is already stored')     # the index spoke: all of a run or none
        cid = cur.lastrowid
        private = bool(pt._stamp_writer(cur, cid, writer=writer)) or private
        ids.append(cid)
    return ids, private


def _link_day(cur, md, ids, contents, amap, aliases, now):
    """One edge per person per day, from the first piece that names them, of
    the day's own kind: a walk can leave a day for its people, and a day is
    never counted against a person or landed on (spider.py)."""
    linked = set()
    for cid, content in zip(ids, contents):
        try:
            for hit in md.match_entities(content, aliases):
                ent = amap.get(hit.lower())
                if ent and ent[0] not in linked:
                    linked.add(ent[0])
                    cur.execute(
                        "INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                        "VALUES ('chunk', ?, 'entity', ?, ?, ?, ?)",
                        (cid, ent[0], EDGE_KIND, md.MENTION_EDGE_WEIGHT, now))
        except Exception as e:
            logger.warning(f"[DAILY] linking skipped for a piece: {e}")


def import_days(pt, cur, scope, chunks, src_scope, now) -> dict:
    """A days export file into `scope`, whole days only (routes/transfer.py).
    A day already held in this scope, or deleted here, is skipped whole; rows
    without a day identity are skipped. Loose-row import would leave holes
    (its dedupe is content+created, and a day's pieces share `created`),
    double a day, or bring a deleted one back. The caller commits."""
    from plugins.mindpalace.tools import metadata as md
    _tables(cur)
    have = {r[0] for r in cur.execute(
        "SELECT DISTINCT source FROM chunks WHERE layer = 'daily' AND scope = ? AND source IS NOT NULL",
        (scope,))}
    have |= {r[0] for r in cur.execute('SELECT source FROM daily_gone WHERE scope = ?', (scope,))}
    days, skipped = {}, 0
    for c in chunks:
        src = c.get('source') if isinstance(c, dict) else None
        if not isinstance(src, str) or not src.startswith('day:') \
                or not isinstance(c.get('content'), str) or not c['content'].strip():
            skipped += 1
            continue
        days.setdefault(src, []).append(c)
    try:
        arows = md.entity_aliases(cur, scope)
        amap, aliases = md.alias_map(arows), [a for _, _, a in arows]
    except Exception:
        amap, aliases = {}, []
    held = cur.execute("SELECT COUNT(*) FROM chunks WHERE scope = ? AND layer = 'daily'",
                       (scope,)).fetchone()[0]
    imported = n_days = 0
    for src, rows in days.items():
        rows.sort(key=lambda c: c.get('chunk_index') if isinstance(c.get('chunk_index'), int) else 0)
        if src in have or held + len(rows) > ROW_CAP:
            skipped += len(rows)
            continue
        ids = []
        for i, c in enumerate(rows):
            meta = dict(c['meta']) if isinstance(c.get('meta'), dict) else {}
            for k in ('librarian_at', 'temporal_at', 'link_at', 'dedup_at', 'derived_from',
                      'promoted_to', 'import_key', 'library_migrated'):
                meta.pop(k, None)
            meta['import_src'] = src_scope
            meta['part'], meta['parts'] = i, len(rows)
            created = c.get('created') or _created(src[4:14])
            cur.execute(
                'INSERT INTO chunks (layer, scope, content, label, favorite, meta, created, updated, '
                "chunk_index, source) VALUES ('daily', ?, ?, ?, 0, ?, ?, ?, ?, ?)",
                (scope, c['content'], c.get('label') or src[4:14],
                 json.dumps(meta, ensure_ascii=False), created, c.get('updated') or created, i, src))
            cid = cur.lastrowid
            pt._stamp_writer(cur, cid, imported=True)
            pt._apply_writer(cur, cid, c.get('writer'))
            ids.append(cid)
        _link_day(cur, md, ids, [c['content'] for c in rows], amap, aliases, now)
        held += len(rows)
        imported += len(rows)
        n_days += 1
    return {'imported': imported, 'skipped': skipped, 'days': n_days}


# ─── The listeners ───────────────────────────────────────────────────────────

def _session_manager():
    from core.api_fastapi import get_system
    return get_system().llm_chat.session_manager


def sweep(chats=None, sm=None) -> dict:
    """Record every listed chat (None = all of them). One chat failing never
    stops the rest; the failure is logged and told once."""
    sm = sm or _session_manager()
    if chats is None:
        chats = [c.get('name') for c in sm.list_chat_files() if isinstance(c, dict) and c.get('name')]
    days, failed = 0, 0
    for chat in chats:
        if _stopping:
            break
        try:
            days += len(record_chat(chat, sm)['days'])
        except Exception:
            failed += 1
            logger.exception('[DAILY] a chat\'s days could not be recorded')   # no chat name: it may be private
    if failed:
        try:
            from core import notify
            notify.notify("A day's summary was not saved to memory", level='warning', source='mindpalace',
                          key='daily-record', body='The chat was compacted; its days were not recorded. '
                          'It is tried again when Sapphire next starts and at that chat\'s next daily '
                          'compact, while the chat still holds the day. See the log.')
        except Exception:
            pass
    return {'days': days, 'failed': failed}


def _run(chat):
    try:
        sweep([chat])
    except Exception:
        logger.exception('[DAILY] recording after a compact failed')


def _boot():
    """Once per start, after the app is up: every chat's day pairs that are
    not held yet. The repair for a night that failed or ran with the palace
    off (a compact with nothing new publishes no event to retry on)."""
    import time
    sm = None
    for _ in range(60):
        if _stopping:
            return
        try:
            sm = _session_manager()
            break
        except Exception:
            time.sleep(5)
    if sm is None:
        return
    time.sleep(20)                          # let the boot settle; nothing here is urgent
    if _stopping:
        return
    try:
        out = sweep(None, sm)
        if out['days']:
            logger.info(f"[DAILY] start-up pass recorded {out['days']} day(s)")
    except Exception:
        logger.exception('[DAILY] start-up pass failed')


def _on_compressed(event):
    """On the publisher's thread: decide and hand off, nothing more."""
    try:
        data = (event or {}).get('data') or {}
        if (data.get('report') or {}).get('mode') != 'daily' or not data.get('chat_name'):
            return
        threading.Thread(target=_run, args=(data['chat_name'],), daemon=True, name='palace-daily').start()
    except Exception as e:
        logger.warning(f"[DAILY] compact event ignored: {e}")


def _renamed(old, new):
    """A chat's memories follow its name. meta.chat is the one place a chat is
    named, the vault scrub matches the CURRENT name, and days show the name to
    her: a rename left the old one behind for good (scout hunt 2026-11-20)."""
    try:
        pt = _pt()
        with pt._get_connection() as conn:
            for table in ('chunks', 'entities'):
                conn.execute(f"UPDATE {table} SET meta = json_set(meta, '$.chat', ?) "
                             f"WHERE json_extract(meta, '$.chat') = ?", (new, old))
            conn.commit()
    except Exception as e:
        logger.warning(f"[DAILY] a renamed chat's memories keep its old name: {e}")


def _on_renamed(event):
    try:
        data = (event or {}).get('data') or {}
        old, new = data.get('old'), data.get('new')
        if isinstance(old, str) and isinstance(new, str) and old and new and old != new:
            threading.Thread(target=_renamed, args=(old, new), daemon=True, name='palace-renamed').start()
    except Exception as e:
        logger.warning(f"[DAILY] rename event ignored: {e}")


def start(boot=True):
    global _stopping
    from core.event_bus import Events, get_event_bus
    bus = get_event_bus()
    _stopping = False
    bus.off([Events.CHAT_COMPRESSED], _on_compressed)       # a reused module: never two listeners
    bus.off(['chat_renamed'], _on_renamed)
    bus.on([Events.CHAT_COMPRESSED], _on_compressed)
    bus.on(['chat_renamed'], _on_renamed)
    if boot and 'pytest' not in sys.modules:
        threading.Thread(target=_boot, daemon=True, name='palace-daily-boot').start()


def stop():
    """Unload: no new writes, and wait for the one in flight. A recorder that
    outlived the palace re-imported it and wrote while it was off."""
    global _stopping
    _stopping = True
    try:
        from core.event_bus import Events, get_event_bus
        bus = get_event_bus()
        bus.off([Events.CHAT_COMPRESSED], _on_compressed)
        bus.off(['chat_renamed'], _on_renamed)
    except Exception as e:
        logger.debug(f"[DAILY] listener not removed: {e}")
    if _lock.acquire(timeout=10):
        _lock.release()


# ─── Dates she can ask by ────────────────────────────────────────────────────

_ISO = r'\d{4}-\d{2}-\d{2}'
_DAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
_MONTHS = ('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september',
           'october', 'november', 'december')
SPAN_FORMS = ("a date (2026-11-02 or Nov 2), a range (2026-11-02..2026-11-09), a month (2026-11), "
              "a weekday (Monday = the last one), or today, yesterday, 3 days ago, this week, "
              "last week, this month, last month, last N days")


def _today() -> date:
    return datetime.now(_user_tz()).date()


def _month_day(t, today):
    """'nov 2', 'november 2nd 2026', '2 nov': a date, the last such one when
    no year is said. None when t is not that and nothing else."""
    m = re.fullmatch(r'([a-z]{3,9})\.? (\d{1,2})(?:st|nd|rd|th)?(?:,? (\d{4}))?', t)
    if m:
        name, day, year = m.groups()
    else:
        m = re.fullmatch(r'(\d{1,2})(?:st|nd|rd|th)? (?:of )?([a-z]{3,9})\.?(?:,? (\d{4}))?', t)
        if not m:
            return None
        day, name, year = m.groups()
    months = [i for i, full in enumerate(_MONTHS, 1) if full.startswith(name)]
    if len(months) != 1:
        return None
    d = date(int(year) if year else today.year, months[0], int(day))
    if not year and d > today:
        d = d.replace(year=d.year - 1)      # a bare "Nov 2" asked in memory is the last one
    return d


def parse_span(text, today=None):
    """A `day` argument to ((first, last), None) or (None, error). Both ends
    are dates and both are included. Only whole, known forms are read: a
    guess that lands on the wrong day returns "nothing that day" and she has
    no error to correct from (the first version read "Monday" as a date a
    year back)."""
    t = ' '.join(str(text or '').strip().lower().split())
    if not t:
        return None, None
    today = today or _today()

    def one(d):
        return (d, d), None
    try:
        m = re.fullmatch(rf'({_ISO})\s*(?:\.\.+|to|through|until|-)\s*({_ISO})', t)
        if m:
            a, b = date.fromisoformat(m[1]), date.fromisoformat(m[2])
            return (min(a, b), max(a, b)), None
        if re.fullmatch(_ISO, t):
            return one(date.fromisoformat(t))
        if re.fullmatch(r'\d{4}-\d{2}', t):
            a = date.fromisoformat(t + '-01')
            return (a, (a.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)), None
        if t == 'today':
            return one(today)
        if t == 'yesterday':
            return one(today - timedelta(days=1))
        if t in ('day before yesterday', 'the day before yesterday'):
            return one(today - timedelta(days=2))
        m = re.fullmatch(r'(\d{1,3}) days? ago', t)
        if m:
            return one(today - timedelta(days=int(m[1])))
        m = re.fullmatch(r'(?:the )?(?:last|past) (\d{1,3}) days?', t)
        if m and int(m[1]) > 0:
            return (today - timedelta(days=int(m[1])), today), None
        monday = today - timedelta(days=today.weekday())
        if t == 'this week':
            return (monday, today), None
        if t == 'last week':
            return (monday - timedelta(days=7), monday - timedelta(days=1)), None
        if t == 'this month':
            return (today.replace(day=1), today), None
        if t == 'last month':
            last = today.replace(day=1) - timedelta(days=1)
            return (last.replace(day=1), last), None
        m = re.fullmatch(r'(?:last |on |this past )?([a-z]{3,9})', t)
        if m:
            which = [i for i, full in enumerate(_DAYS) if full.startswith(m[1])]
            if len(which) == 1:             # the most recent one, never today, never ahead
                return one(today - timedelta(days=(today.weekday() - which[0]) % 7 or 7))
        d = _month_day(t, today)
        if d:
            return one(d)
    except ValueError:
        return None, f"'{text}' is not a real date. day takes {SPAN_FORMS}."
    return None, f"Could not read day '{text}'. day takes {SPAN_FORMS}."


def span_bounds(span):
    """(first, last) dates to what a read filters with: [from, before) on
    `created` (midnight to midnight in the person's zone, for every layer but
    this one) and the two ISO days (this layer filters on `label`, the day a
    summary is OF, so a change of time zone never moves a day)."""
    tz = _user_tz()

    def at(d):
        return datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(timezone.utc).isoformat(timespec='seconds')
    return at(span[0]), at(span[1] + timedelta(days=1)), span[0].isoformat(), span[1].isoformat()


# ─── Reading ─────────────────────────────────────────────────────────────────

def day_label(day: str) -> str:
    """'Mon Nov 2 2026'. By hand: no %-d (Windows), ASCII (cp1252)."""
    try:
        d = date.fromisoformat(str(day)[:10])
        return f"{d:%a} {d:%b} {d.day} {d.year}"
    except (TypeError, ValueError):
        return str(day)


def _meta(raw) -> dict:
    try:
        m = json.loads(raw) if raw else {}
        return m if isinstance(m, dict) else {}
    except (TypeError, ValueError):
        return {}


def _pieces(cur, pt, scope, private_key, sources, span=None) -> dict:
    """{source: [(id, content, chunk_index, meta), ...] in order} of the rows
    this reader is given. Every read of the layer comes through here or
    through _read_filters itself, so the gates are the palace's own."""
    where, params = pt._read_filters(scope, [], private_key, LAYER, span=span)
    out = {}
    sources = list(sources)
    for at in range(0, len(sources), 400):
        part = sources[at:at + 400]
        for cid, content, idx, meta, src in cur.execute(
                f'SELECT c.id, c.content, c.chunk_index, c.meta, c.source FROM chunks c '
                f"WHERE {where} AND c.layer = 'daily' AND c.source IN ({','.join('?' * len(part))}) "
                f'ORDER BY c.chunk_index, c.id', params + part):
            out.setdefault(src, []).append((cid, content, idx if idx is not None else 0, _meta(meta)))
    return out


def _head(src, rows) -> str:
    m = rows[0][3]
    bits = []
    if m.get('messages'):
        bits.append(f"{m['messages']} messages")
    if m.get('chat') and m['chat'] != '__private__':
        bits.append(f"chat '{m['chat']}'")
    return f"[daily] {day_label(m.get('day') or src[4:14])}" + (f" ({', '.join(bits)})" if bits else '')


def _whole(rows) -> bool:
    """Is this every piece of the day? A piece deleted, or kept from this
    reader, leaves fewer rows than the day was written with."""
    parts = rows[0][3].get('parts')
    return not isinstance(parts, int) or len(rows) >= parts


def _stitch(rows, picked) -> str:
    """The picked pieces in order; a gap between two says so."""
    text, prev = '', None
    for i in sorted(picked):
        piece = rows[i][1]
        if prev is None:
            text = ('[...] ' if rows[i][2] > 0 else '') + piece
        elif i == prev + 1 and rows[i][2] == rows[prev][2] + 1:
            text += _sep(rows[i][3], rows[i][2]) + piece
        else:
            text += ' [...] ' + piece
        prev = i
    last = rows[prev] if prev is not None else None
    parts = last[3].get('parts') if last else None
    cut = prev is not None and (prev < len(rows) - 1 or (isinstance(parts, int) and last[2] < parts - 1))
    return text + (' [...]' if cut else '')


def shape(cur, pt, hit_ids, scope, private_key, days, chars, span=None):
    """Search hits (row ids, best first) to (lines, seed ids). The ONE sizing
    rule: `chars` of text is shared by the days that matched, each day's
    share in proportion to how many of its pieces matched, and a day spends
    its share on each matched piece and the piece AFTER it (in a retelling
    the outcome follows the setup: "you asked ... I said ..." then "you
    decided ..."), then outward from the best match. One day matched reads
    long; many days read short. No score thresholds."""
    if not hit_ids:
        return [], []
    order = {}
    for at in range(0, len(hit_ids), 400):
        part = hit_ids[at:at + 400]
        for cid, src in cur.execute(
                f"SELECT id, source FROM chunks WHERE layer = 'daily' AND id IN ({','.join('?' * len(part))})",
                part):
            order[cid] = src
    groups = {}
    for cid in hit_ids:
        src = order.get(cid)
        if src and (src in groups or len(groups) < days):
            groups.setdefault(src, []).append(cid)
    if not groups:
        return [], []
    full = _pieces(cur, pt, scope, private_key, groups, span=None)
    matched = sum(len(h) for h in groups.values())
    lines, shown = [], []
    for src, hits in groups.items():
        rows = full.get(src)
        if not rows:
            continue
        at = {r[0]: i for i, r in enumerate(rows)}
        hit_at = [at[c] for c in hits if c in at]
        if not hit_at:
            continue
        share = max(1, chars * len(hits) // matched)
        best = hit_at[0]
        first = []                          # each match, then the piece after it
        for i in hit_at:
            first += [j for j in (i, i + 1) if j < len(rows) and j not in first]
        near = sorted((i for i in range(len(rows)) if i not in first),
                      key=lambda i: (abs(i - best), i < best))
        picked, used = {best}, len(rows[best][1])
        for i in first[1:] + near:
            if used + len(rows[i][1]) > share:
                if i in first:
                    continue                # this one does not fit: a later match still may
                break                       # neighbours come nearest first: none after fits better
            picked.add(i)
            used += len(rows[i][1])
        # Her words, not the store's: she never sees "pieces" (her AIX note, 2026-11-20).
        where = 'whole day' if len(picked) == len(rows) and _whole(rows) else 'excerpt'
        lines.append(f"{_head(src, rows)} [{where}] {_stitch(rows, picked)}")
        shown.append(src)
    seeds = []
    if shown:
        ids = [r[0] for s in shown for r in full[s]]
        for at_ in range(0, len(ids), 400):
            part = ids[at_:at_ + 400]
            seeds += [r[0] for r in cur.execute(
                "SELECT DISTINCT src_id FROM edges WHERE src_type = 'chunk' AND dst_type = 'entity' "
                f"AND src_id IN ({','.join('?' * len(part))})", part)]
    return lines, seeds


DOOR = "A whole day in order: get_recent_memories(layer='daily', day='YYYY-MM-DD')."


def _hits(pt, query, scope, private_key, labels, span, fetch, loose):
    """The palace's cascade over this layer: exact words, loose words, meaning;
    the first that finds anything answers. loose=False skips the middle step:
    under an all-layer search a word like "we" or a prefix of one would match
    nearly every day, and the days block would ride on every search she makes."""
    import sqlite3
    with pt._get_connection() as conn:
        cur = conn.cursor()
        tries = [pt._sanitize_fts_query(query)]
        if loose:
            tries.append(pt._sanitize_fts_query(query, use_or=True, use_prefix=True))
        try:
            for q in dict.fromkeys(x for x in tries if x):
                rows = pt._fts_search(cur, q, scope, labels, fetch, private_key=private_key,
                                      layer=LAYER, span=span)
                if rows:
                    return [r[0] for r in rows]
        except sqlite3.OperationalError as e:
            logger.warning(f"[DAILY] FTS query failed: {e}")
    return [r[0] for r in pt._vector_search(query, scope, labels, fetch, private_key=private_key,
                                            layer=LAYER, span=span)]


def has_days(scope, pt=None) -> bool:
    """Does this scope (or the global overlay) hold any day at all? The cheap
    question asked before any search of the layer."""
    pt = pt or _pt()
    with pt._get_connection() as conn:
        scope_sql, scope_params = pt._scope_condition(scope)
        return bool(conn.execute(f"SELECT 1 FROM chunks WHERE layer = 'daily' AND {scope_sql} LIMIT 1",
                                 scope_params).fetchone())


def search(query, scope, private_key=None, labels=(), span=None, days=MIXED_DAYS, chars=MIXED_CHARS,
           pt=None, loose=False):
    """(lines, seed ids) for a query. Failures give ([], []): the days block
    must never break a search that already answered. pt: the palace_tools
    instance that is asking (the spider's rule: never a second copy's state)."""
    try:
        pt = pt or _pt()
        if not has_days(scope, pt):         # no days kept here: no query, no embedding call
            return [], []
        ids = _hits(pt, query, scope, private_key, list(labels or []), span, max(30, days * 8), loose)
        if not ids:
            return [], []
        with pt._get_connection() as conn:
            return shape(conn.cursor(), pt, ids, scope, private_key, days, chars)
    except Exception as e:
        logger.warning(f"[DAILY] day search skipped: {e}")
        return [], []


def search_text(query, scope, limit=10, private_key=None, labels=(), span=None, depth=0, pt=None) -> str:
    """search_memory(layer='daily')."""
    pt = pt or _pt()
    lines, seeds = search(query, scope, private_key, labels, span, days=limit, chars=LAYER_CHARS,
                          pt=pt, loose=True)
    if not lines:
        return f"No days found for '{query}'" + (' in that range.' if span else '.')
    out = f"Found {len(lines)} day{'s' if len(lines) != 1 else ''}:\n" + "\n".join(lines) + "\n" + DOOR
    if depth and seeds:
        block = pt._spider_block(query, scope, private_key, seeds, depth)
        if block:
            out += "\n\n" + block
    return out


def _sources(cur, pt, scope, private_key, span, count):
    """[(source, pieces)] newest day first."""
    where, params = pt._read_filters(scope, [], private_key, LAYER, span=span)
    return cur.execute(
        f"SELECT c.source, COUNT(*) FROM chunks c WHERE {where} AND c.layer = 'daily' "
        f'AND c.source IS NOT NULL GROUP BY c.source ORDER BY MIN(c.label) DESC, c.source DESC LIMIT ?',
        params + [count]).fetchall()


def recent_text(scope, count=10, private_key=None, span=None, pt=None) -> str:
    """get_recent_memories(layer='daily'). With `day`: those days whole and in
    order while they fit READ_CHARS. Otherwise (or when they do not fit) the
    index: one line per day, the door to each."""
    pt = pt or _pt()
    with pt._get_connection() as conn:
        cur = conn.cursor()
        found = _sources(cur, pt, scope, private_key, span, 400 if span else count)
        if not found:
            return 'No days recorded' + (' in that range.' if span else ' yet.')
        full = _pieces(cur, pt, scope, private_key, [s for s, _ in found])
    found = [(s, n) for s, n in found if full.get(s)]
    if span and sum(len(r[1]) for s, _ in found for r in full[s]) <= READ_CHARS:
        blocks = [f"{_head(s, full[s])}\n{_stitch(full[s], range(len(full[s])))}" for s, _ in reversed(found)]
        return "\n\n".join(blocks)
    lines = []
    for s, _ in found[:count]:
        rows = full[s]
        first = ' '.join(rows[0][1].split())
        if len(first) > INDEX_CHARS:
            first = (first[:INDEX_CHARS].rsplit(' ', 1)[0] or first[:INDEX_CHARS]) + '...'
        one = len(rows) == 1 and _whole(rows) and not first.endswith('...')
        lines.append(f"{_head(s, rows)} [{'whole day' if one else 'beginning'}] {first}")
    more = len(found) - len(lines)
    head = (f"{len(found)} days in that range are too long to show whole. " if span else '') \
        + f"{'Newest ' if not span else ''}{len(lines)} day{'s' if len(lines) != 1 else ''}"
    return (f"{head}:\n" + "\n".join(lines)
            + (f"\n({more} more: narrow the day.)" if more > 0 else '') + "\n" + DOOR)


def span_hint(scope, span, private_key=None, pt=None) -> str:
    """One line for a read by `day` that found no memories while days of that
    span are kept: the door, instead of "nothing"."""
    try:
        pt = pt or _pt()
        with pt._get_connection() as conn:
            if _sources(conn.cursor(), pt, scope, private_key, span, 1):
                return " Your days hold that date: get_recent_memories(layer='daily', day=...) reads it."
    except Exception as e:
        logger.debug(f"[DAILY] span hint skipped: {e}")
    return ''


# ─── The app's side: whole days ──────────────────────────────────────────────

def reset(cursor, scope):
    """Forget which days were deleted by hand in this scope (a scope deleted,
    wiped, or its Daily tab cleared: a reset, so days still in a chat are
    recorded again). The caller commits."""
    _tables(cursor)
    cursor.execute('DELETE FROM daily_gone WHERE scope = ?', (scope,))


def forget(cursor, scope, sources) -> int:
    """Delete days and remember that they were deleted, so the next compact of
    their chat does not bring them back. The caller commits. Returns rows
    deleted."""
    _tables(cursor)
    now = _pt()._now()
    gone = 0
    for src in sources:
        ids = [r[0] for r in cursor.execute(
            "SELECT id FROM chunks WHERE layer = 'daily' AND scope = ? AND source = ?", (scope, src))]
        if not ids:
            continue
        marks = ','.join('?' * len(ids))
        cursor.execute(f"DELETE FROM edges WHERE src_type = 'chunk' AND src_id IN ({marks})", ids)
        cursor.execute(f'DELETE FROM chunks WHERE id IN ({marks})', ids)
        cursor.execute('INSERT OR REPLACE INTO daily_gone (scope, source, gone) VALUES (?, ?, ?)',
                       (scope, src, now))
        gone += len(ids)
    return gone
