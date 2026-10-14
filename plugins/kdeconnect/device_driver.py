# plugins/kdeconnect/device_driver.py - a phone as a device, through KDE Connect.
#
# The phone runs the KDE Connect app; Sapphire pretends to be a desktop
# (plugins/kdeconnect/link.py speaks the protocol). What the phone can do for
# her: share a picture, a document, a text or a link INTO her chat; show her
# the text messages on it and send texts as its owner (locked until the user
# allows it); a notification from her on the phone; battery; ring it to find
# it; names from its contacts. Record: tmp/kdeconnect-plan.md.
#
# One Link for all phones. It starts when the first device of this kind is
# tended or the Add Device picker looks, and stops with the watcher. A phone
# is matched to a device row by its KDE Connect id (the `phone` pick).
import base64
import io
import logging
import os
import re
import socket
import threading
import time
from datetime import datetime
from pathlib import Path

from core import net
from core.plugin_loader import PLUGIN_STATE_DIR
from plugins.kdeconnect import link as L
from plugins.kdeconnect import protocol as P

logger = logging.getLogger(__name__)

DRIVER = 'kdeconnect'
PLUGIN = 'kdeconnect'
IDENTITY_DIR = PLUGIN_STATE_DIR / 'kdeconnect_identity'      # key.pem, cert.pem: who this Sapphire is to phones
INBOX = PLUGIN_STATE_DIR / 'kdeconnect_inbox'                # files shared from phones, by phone id
LOOK = 2.5               # seconds the picker waits for phones on a fresh start
GATHER = 2.0             # seconds texts that arrive together are gathered into one turn
DOC_CHARS = 20000        # characters of a shared document she reads inline
READ_DEFAULT = 20        # texts of one conversation shown
CONTACTS_FRESH = 600     # seconds the contacts cache holds
CONV_FRESH = 600         # seconds the conversations cache answers from
CONV_FIRST = 20          # seconds for the phone's first conversation (it reads its whole store first)
CONV_QUIET = 5           # seconds of nothing = the phone has sent them all (one packet per conversation, slowly)
TEXT_EXT = {'.txt', '.md', '.csv', '.json', '.log', '.srt', '.vtt', '.xml', '.html', '.htm', '.py', '.yaml', '.yml', '.ini'}
_NUMBER = re.compile(r'^\+?[\d\s().-]{3,24}$')

# what we take from a phone / what we send it (the packet types, as the app names them)
INCOMING = ('kdeconnect.battery', 'kdeconnect.share.request', 'kdeconnect.sms.messages',
            'kdeconnect.sms.attachment_file', 'kdeconnect.ping',
            'kdeconnect.contacts.response_uids_timestamps', 'kdeconnect.contacts.response_vcards')
OUTGOING = ('kdeconnect.ping', 'kdeconnect.findmyphone.request', 'kdeconnect.share.request',
            'kdeconnect.sms.request', 'kdeconnect.sms.request_conversations',
            'kdeconnect.sms.request_conversation',
            'kdeconnect.contacts.request_all_uids_timestamps', 'kdeconnect.contacts.request_vcards_by_uid')

# Sapphire capability -> the packet the phone must take (in) or send (out) to have it
NEEDS = {'battery': ('out', 'kdeconnect.battery'), 'share': ('in', 'kdeconnect.share.request'),
         'sms': ('in', 'kdeconnect.sms.request'), 'notify': ('in', 'kdeconnect.ping'),
         'contacts': ('in', 'kdeconnect.contacts.request_all_uids_timestamps')}
LIST_DEFAULT = 15        # conversations shown
LIST_MOST = 40
LIST_WORDS = 400         # characters of a conversation's last text shown in the list ([...] after)

_lock = threading.RLock()
_link = None             # the one Link, while it runs
_changed = None          # presence's "look again", while a watcher runs
_pending_sms = {}        # peer id -> {'timer', 'messages', 'row'}
_pair_notes = {}         # peer id -> the last word on a pairing (for status)
_last_look = 0.0         # monotonic: when the pick list last said our name
_seen_ids = {}           # peer id -> set of message _ids already handled
_threads = {}            # peer id -> {'by': {thread id: newest message}, 'ts', 'lock'}: kept across re-links


# --- the link ---------------------------------------------------------------------

def _state():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_state(PLUGIN)


def _trusted():
    """{peer id: {'cert', 'name', 'kind', 'addr'}} as kept on disk."""
    held = _state().get('trusted')
    return dict(held) if isinstance(held, dict) else {}


def _identity():
    """Our certificate, made once. Its CN is our KDE Connect id."""
    IDENTITY_DIR.mkdir(parents=True, exist_ok=True)
    key, cert = IDENTITY_DIR / 'key.pem', IDENTITY_DIR / 'cert.pem'
    if not key.exists() or not cert.exists():
        key_pem, cert_pem = P.make_identity(P.new_id())
        key.write_bytes(key_pem)
        try:
            os.chmod(key, 0o600)
        except OSError:
            pass
        cert.write_bytes(cert_pem)
        logger.info("[kdeconnect] made this Sapphire's certificate")
    name = P.clean_name(f"Sapphire {socket.gethostname().split('.')[0]}")
    return L.Identity(cert, key, name, INCOMING, OUTGOING)


def _lan(ip):
    return net.classify(ip) == 'lan'


def _ensure_link(wait=False):
    """The running Link, started if it was not. wait=True gives phones a
    moment to show up after a fresh start (the picker)."""
    global _link, _last_look
    fresh = False
    with _lock:
        if _link is None:
            link = L.Link(_identity(), _on_event, allow=_lan)
            for pid, rec in _trusted().items():
                if rec.get('cert'):
                    link.trust[pid] = rec['cert'].encode('ascii')
                    if rec.get('addr'):
                        link.known[pid] = rec['addr']
            link.start()                 # says our name once: the picker's look need not say it again
            _link, fresh = link, True
            _last_look = time.monotonic()
        link = _link
    if fresh and wait:
        end = time.monotonic() + LOOK
        while time.monotonic() < end and not link.seen:
            time.sleep(0.25)
    return link


def _link_now():
    return _link


def _stop_link():
    global _link
    with _lock:
        link, _link = _link, None
    if link is not None:
        link.stop()


def _remember(peer_id, **fields):
    """Keep what we know of a paired phone on disk (its certificate above all)."""
    held = _trusted()
    rec = dict(held.get(peer_id) or {})
    rec.update({k: v for k, v in fields.items() if v is not None})
    held[peer_id] = rec
    _state().save('trusted', held)


def _forget(peer_id):
    held = _trusted()
    if held.pop(peer_id, None) is not None:
        _state().save('trusted', held)


# --- rows and phones ------------------------------------------------------------

def _engine():
    from core.devices import engine
    return engine


def _peer_id(config):
    """The phone a device row names, or ''."""
    pick = (config or {}).get('phone')
    only = pick.get('only') if isinstance(pick, dict) else None
    if isinstance(only, list) and only and not pick.get('all', False):
        return str(only[0].get('id') or '')
    return ''


def _rows_for(peer_id):
    """Every enabled device row that names this phone, with its part."""
    out = []
    try:
        for row in _engine().rows().values():
            if not row.get('enabled', True):
                continue
            for part in row.get('parts', []):
                if part.get('driver') == DRIVER and _peer_id(part.get('config')) == peer_id:
                    out.append((row, part))
    except Exception as e:
        logger.warning(f"[kdeconnect] device rows could not be read: {e}")
    return out


def _configured():
    """The phones every enabled device row names."""
    out = set()
    try:
        for row in _engine().rows().values():
            if row.get('enabled', True):
                for part in row.get('parts', []):
                    if part.get('driver') == DRIVER and _peer_id(part.get('config')):
                        out.add(_peer_id(part.get('config')))
    except Exception:
        pass
    return out


def _poke_presence():
    if callable(_changed):
        try:
            _changed()
        except Exception:
            pass


# --- what the link tells us --------------------------------------------------------

def _on_event(kind, peer_id, **info):
    link = _link_now()
    if kind == 'seen':
        _poke_presence()
    elif kind == 'linked':
        _poke_presence()
        peer = link.peers.get(peer_id) if link else None
        if peer is not None and peer.paired:
            _remember(peer_id, name=peer.name, kind=peer.kind, addr=peer.addr)
            for row, part in _rows_for(peer_id):
                _health().seen(row['id'])
                _arm(peer, part.get('config'))
    elif kind == 'unlinked':
        _poke_presence()
        for row, _ in _rows_for(peer_id):
            _health().poke(row['id'])
    elif kind == 'pair_request':
        _pair_notes[peer_id] = f"'{info.get('name')}' asks to pair: key {info.get('key')}. Press Pair to accept."
        for row, _ in _rows_for(peer_id):
            _health().poke(row['id'])
    elif kind == 'paired':
        _pair_notes.pop(peer_id, None)
        _remember(peer_id, cert=info['cert'].decode('ascii'), name=info.get('name'), kind=info.get('device_kind'),
                  addr=link.known.get(peer_id) if link else None)
        for row, part in _rows_for(peer_id):
            _health().seen(row['id'])
            peer = link.peers.get(peer_id) if link else None
            if peer is not None:
                _arm(peer, part.get('config'))
    elif kind == 'unpaired':
        _pair_notes[peer_id] = 'unpaired'
        _forget(peer_id)
        for row, _ in _rows_for(peer_id):
            _health().poke(row['id'])
    elif kind == 'pair_failed':
        _pair_notes[peer_id] = f"the pairing failed: {info.get('why')}"
    elif kind == 'packet':
        _packet(info['peer'], info['pkt'], info.get('fed', False))


def _health():
    from core.devices import health
    return health


def _packet(peer, pkt, fed):
    kind, body = pkt['type'], pkt['body']
    if kind == 'kdeconnect.battery':
        charge = body.get('currentCharge')
        if isinstance(charge, (int, float)) and charge >= 0:
            peer.readings['battery'] = f"{int(charge)}%" + (', charging' if body.get('isCharging') else '')
        return
    if kind == 'kdeconnect.ping':
        for row, _ in _rows_for(peer.id):
            _said(row, _line(row, 'Ping from phone', 'Your reply stays in the chat.'),
                  str(body.get('message') or 'Ping.')[:500])
        return
    if kind == 'kdeconnect.share.request':
        for row, part in _rows_for(peer.id):
            threading.Thread(target=_shared, args=(row, part.get('config') or {}, peer, pkt),
                             daemon=True, name='kdeconnect-share').start()
        return
    if kind == 'kdeconnect.sms.messages':
        _texts(peer, body)
        return
    # contacts answers and the rest are only ever waited for (peer.ask)


# --- her words: lines above what a phone hands her ------------------------------

def _said(row, line, text, images=None):
    from core.devices import voice
    out = voice.said(row['id'], line, text, images)
    if not out.get('ok'):
        logger.warning(f"[kdeconnect] {row['id']}: not handed to her: {out.get('error')}")
    return out


def _line(row, what, how):
    """`[What from phone "id" (label). Location: x. How.]`, the way every
    device's line reads (voice.origin_line)."""
    from core.devices import voice
    spoken = voice.origin_line(row).replace('[Voice from device', f'[{what}', 1)
    return spoken.replace(voice.ALOUD, how, 1)


# --- sharing: the phone hands her something --------------------------------------

def _shared(row, config, peer, pkt):
    body = pkt['body']
    mode = str(config.get('share_to') or 'both')
    try:
        if 'filename' in body and P.payload_of(pkt):
            data = peer.download(pkt)
            _landed(row, config, peer, str(body.get('filename') or 'file'), data, mode)
        elif body.get('text'):
            _said(row, _line(row, 'Shared from phone', 'Some text shared to you. Your reply stays in the chat.'),
                  str(body['text']))
        elif body.get('url'):
            _said(row, _line(row, 'Shared from phone', 'A link shared to you. Your reply stays in the chat.'),
                  str(body['url']))
    except P.ProtocolError as e:
        logger.warning(f"[kdeconnect] {row['id']}: a share from {peer.name} was lost: {e}")
    except Exception as e:
        logger.error(f"[kdeconnect] {row['id']}: share failed: {e}", exc_info=True)


def _safe_name(name):
    name = re.sub(r'[^\w.\- ]+', '_', os.path.basename(str(name or 'file')).strip())[:120]
    return name or 'file'


def _landed(row, config, peer, filename, data, mode):
    """A file from the phone: kept, read, or both (the device's `share_to`)."""
    from core import images as ci
    name, size = _safe_name(filename), len(data)
    ext = os.path.splitext(name)[1].lower()
    saved = None
    if mode in ('both', 'files'):
        folder = INBOX / re.sub(r'[^A-Za-z0-9_-]', '', peer.id)[:40]
        folder.mkdir(parents=True, exist_ok=True)
        saved = folder / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{name}"
        saved.write_bytes(data)
        logger.info(f"[kdeconnect] {row['id']}: kept {name} ({size} bytes) from {peer.name}")
    kept = f" Kept at {saved}." if saved else ''
    picture = None
    try:
        if ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.heic', '.heif', '.bmp', '.tif', '.tiff') or \
                ci.media_type(data).startswith('image/'):
            picture = ci.for_chat(data)
    except Exception:
        picture = None
    text = None
    if picture is None:
        text = _document_text(ext, data)
    if mode == 'files':
        if text and saved:
            _to_knowledge(config, row, name, text)
        return
    how = 'Your reply stays in the chat.'
    if picture is not None:
        _said(row, _line(row, 'Picture shared from phone', how), f"{name}.{kept}",
              [{'type': 'image', 'data': base64.b64encode(picture).decode('ascii'), 'media_type': 'image/jpeg'}])
    elif text:
        more = f" The whole document is in this chat's documents." if len(text) > DOC_CHARS and saved else ''
        if len(text) > DOC_CHARS and saved:
            _to_knowledge(config, row, name, text)
        _said(row, _line(row, 'Document shared from phone', how),
              f"{name} ({size} bytes).{kept}{more}\n\n{text[:DOC_CHARS]}")
    else:
        _said(row, _line(row, 'File shared from phone', how),
              f"{name}, {size} bytes: a file you cannot read here.{kept}")


def _document_text(ext, data):
    """The words of a document, or None when it is not one we read."""
    if ext == '.pdf':
        try:
            from pypdf import PdfReader
            pages = [p.extract_text() or '' for p in PdfReader(io.BytesIO(data)).pages]
            return '\n\n'.join(p for p in pages if p.strip()).strip() or None
        except Exception as e:
            logger.info(f"[kdeconnect] a PDF could not be read: {e}")
            return None
    if ext in TEXT_EXT or (not ext and b'\x00' not in data[:4096]):
        for enc in ('utf-8', 'utf-8-sig', 'latin-1'):
            try:
                return data.decode(enc).strip() or None
            except (UnicodeDecodeError, ValueError):
                continue
    return None


def _to_knowledge(config, row, filename, text):
    """The document into the chat's own documents (per-chat RAG), the way the
    Knowledge upload door does it. Quiet when the memory plugin is away."""
    try:
        from core.devices import voice
        from plugins.memory.tools import knowledge_tools as kb
        chat, settings, _ = voice.chat_for(config, row['id'])
        if settings is None:
            return
        scope = f"__rag__:{chat}"
        kb.create_scope(scope)
        tab_id = kb.create_tab(filename, scope=scope, tab_type='user')
        if not tab_id:
            with kb._get_connection() as conn:
                hit = conn.cursor().execute('SELECT id FROM knowledge_tabs WHERE name = ? AND scope = ?',
                                            (filename, scope)).fetchone()
            if not hit:
                return
            tab_id = hit[0]
            kb.delete_entries_by_filename(tab_id, filename)
        for i, chunk in enumerate(kb._chunk_text(text)):
            kb.add_entry(tab_id, chunk, chunk_index=i, source_filename=filename)
        logger.info(f"[kdeconnect] {row['id']}: {filename} added to the documents of chat '{chat}'")
    except Exception as e:
        logger.info(f"[kdeconnect] {filename} was not added to the chat's documents: {e}")


# --- text messages -------------------------------------------------------------------

def _arm(peer, config):
    """Ask the phone for its conversations once: from then on it pushes every
    new text by itself (SMSPlugin.haveMessagesBeenRequested, kept on the
    phone across re-links). The answer doubles as the conversations cache.
    Not again on every re-link: listing is the slow job on the phone's one
    worker, and a `read` behind it waited or timed out (Krem, 2026-10-12).
    Asked again only when the cache is older than CONV_FRESH."""
    if peer.extra.get('armed') or 'kdeconnect.sms.request_conversations' not in peer.caps_in:
        return
    peer.extra['armed'] = True
    cache = _cache(peer)
    if cache['by'] and time.time() - cache['ts'] < CONV_FRESH:
        return
    threading.Thread(target=_conversations, args=(peer, True), daemon=True, name='kdeconnect-arm').start()


def _cache(peer):
    return _threads.setdefault(peer.id, {'by': {}, 'ts': 0.0, 'lock': threading.Lock()})


def _keep_newest(by, msgs):
    for m in msgs:
        tid = m.get('thread_id')
        if tid is None:
            continue
        held = by.get(tid)
        if held is None or (m.get('date') or 0) >= (held.get('date') or 0):
            by[tid] = m


def _conversations(peer, refresh=False):
    """The newest message of every conversation: {thread id: message}. The
    phone sends one packet per conversation, slowly, after reading its whole
    store, so this is patient (CONV_FIRST, CONV_QUIET), merges into a cache
    kept across re-links (a partial answer never loses what is known), and
    runs one request at a time (the phone works them off one by one; a second
    waits for the first instead of timing out behind it)."""
    cache = _cache(peer)
    fresh = lambda: cache['by'] and time.time() - cache['ts'] < CONV_FRESH
    # a COPY goes out: the reader thread adds to the cache as the phone talks, and a
    # caller walking the live dict blew up on 'changed size during iteration' (Krem, 2026-10-12)
    if not refresh and fresh():
        return dict(cache['by'])
    if not cache['lock'].acquire(timeout=CONV_FIRST + CONV_QUIET + 5):
        return dict(cache['by'])
    try:
        if not refresh and fresh():
            return dict(cache['by'])
        began = time.monotonic()
        got = peer.ask('kdeconnect.sms.request_conversations', {}, 'kdeconnect.sms.messages',
                       timeout=CONV_FIRST, quiet=CONV_QUIET)
        msgs = [m for p in got for m in (p['body'].get('messages') or []) if isinstance(m, dict)]
        logger.info(f"[kdeconnect] asked {peer.name} for its conversations: {len(got)} answer(s), {len(msgs)} "
                    f"message(s) in {time.monotonic() - began:.1f} s; {len(cache['by'])} known before"
                    + ("" if got else ". Nothing came: on the phone, is KDE Connect's SMS plugin enabled for this "
                       "Sapphire, with the SMS permission granted?"))
        _keep_newest(cache['by'], msgs)
        if msgs:
            cache['ts'] = time.time()
        _mark_seen(peer, msgs)
        return dict(cache['by'])
    finally:
        cache['lock'].release()


def _mark_seen(peer, msgs):
    seen = _seen_ids.setdefault(peer.id, set())
    for m in msgs:
        if m.get('_id') is not None:
            seen.add(m['_id'])
    if len(seen) > 5000:
        _seen_ids[peer.id] = set(list(seen)[-2500:])


def _texts(peer, body):
    """kdeconnect.sms.messages arrived. Whoever asked gets it through
    peer.feed (already done). Any INBOX message we have not seen, newer than
    this link, is a new text: gathered, then handed to her if the device
    says texts wake her."""
    msgs = [m for m in (body.get('messages') or []) if isinstance(m, dict)]
    seen = _seen_ids.setdefault(peer.id, set())
    since = peer.since * 1000
    fresh = [m for m in msgs if m.get('type') == 1 and m.get('_id') not in seen
             and (m.get('date') or 0) >= since - 5000]
    _mark_seen(peer, msgs)
    _keep_newest(_cache(peer)['by'], msgs)
    if not fresh:
        return
    for row, part in _rows_for(peer.id):
        config = part.get('config') or {}
        if not config.get('wake_sms') or 'sms' in _engine().locked(row):
            logger.info(f"[kdeconnect] {row['id']}: {len(fresh)} new text(s) on the phone; "
                        f"{'texts are locked for her' if 'sms' in _engine().locked(row) else 'texts do not wake her here'}")
            continue
        with _lock:
            held = _pending_sms.get(peer.id)
            if held is None:
                held = _pending_sms[peer.id] = {'messages': [], 'row': row, 'peer': peer, 'timer': None}
            held['messages'].extend(fresh)
            if held['timer'] is None:
                held['timer'] = threading.Timer(GATHER, _texts_flush, (peer.id,))
                held['timer'].daemon = True
                held['timer'].start()
        break               # one row carries the phone's texts; a second row for the same phone would double them


def _texts_flush(peer_id):
    with _lock:
        held = _pending_sms.pop(peer_id, None)
    if not held or not held['messages']:
        return
    row, peer = held['row'], held['peer']
    try:
        names = _contacts(peer)
    except Exception:
        names = {}
    msgs = sorted(held['messages'], key=lambda m: m.get('date') or 0)
    lines = []
    for m in msgs:
        who = _senders(m, names) or 'an unknown number'
        lines.append(f"From {who} at {_ago(m.get('date'))}:\n{str(m.get('body') or '').strip()}")
    how = (f'To answer, send a text from the phone: device_action("{row["id"]}","sms","send","<number or name> | <your words>"). '
           'You may also not reply. What you write here stays in the chat.')
    _said(row, _line(row, 'Text message on phone', how), '\n\n'.join(lines))


def _ago(ms, now=None):
    """When a text was: 'today 14:02', 'yesterday 09:10', 'Oct 3 18:40', or the date."""
    try:
        t = datetime.fromtimestamp(int(ms) / 1000)
    except (TypeError, ValueError, OSError):
        return 'some time'
    now = now or datetime.now()
    days = (now.date() - t.date()).days
    if days == 0:
        return 'today ' + t.strftime('%H:%M')
    if days == 1:
        return 'yesterday ' + t.strftime('%H:%M')
    if t.year == now.year:
        return t.strftime('%b %-d %H:%M') if os.name != 'nt' else t.strftime('%b %d %H:%M')
    return t.strftime('%Y-%m-%d')


_when = _ago


def _digits(number):
    """The digits of a number, country code dropped: the last ten."""
    d = re.sub(r'\D', '', str(number or ''))
    return d[-10:] if len(d) > 10 else d


def _same_number(a, b):
    """Loosely the same number: one's digits end with the other's, seven at
    least (a number stored without its area code still matches)."""
    a, b = _digits(a), _digits(b)
    if not a or not b:
        return False
    short, long_ = sorted((a, b), key=len)
    return long_.endswith(short) and (len(short) >= 7 or short == long_)


def _name_of(address, names):
    """A contact's name for a number, or ''. names: {digits: name}."""
    if not names:
        return ''
    d = _digits(address)
    hit = names.get(d)
    if hit or len(d) < 7:
        return hit or ''
    return next((n for k, n in names.items() if _same_number(k, d)), '')


def _who(address, names):
    """'Dana (+1 555-123-4567)', or the number alone."""
    address = str(address or '').strip()
    name = _name_of(address, names)
    return f"{name} ({address})" if name else address


def _addresses(message):
    """The numbers on a text, each once (an MMS lists the same number twice)."""
    out, seen = [], set()
    for a in (message.get('addresses') or []):
        address = str(a.get('address') or '').strip() if isinstance(a, dict) else ''
        key = _digits(address) or address.lower()
        if address and key not in seen:
            seen.add(key)
            out.append(address)
    return out


def _senders(message, names):
    """The other side of a text: every number once, named when known."""
    return ', '.join(_who(a, names) for a in _addresses(message))


def _contacts(peer, refresh=False):
    """{last digits of a number: name} from the phone's contacts. Cached."""
    held = peer.extra.get('contacts')
    if held and not refresh and time.time() - peer.extra.get('contacts_ts', 0) < CONTACTS_FRESH:
        return held
    if 'kdeconnect.contacts.request_all_uids_timestamps' not in peer.caps_in:
        return {}
    got = peer.ask('kdeconnect.contacts.request_all_uids_timestamps', {},
                   'kdeconnect.contacts.response_uids_timestamps', timeout=8)
    uids = [str(u) for u in ((got[0]['body'].get('uids') if got else None) or [])]
    by_number, by_name = {}, {}
    if uids:
        cards = peer.ask('kdeconnect.contacts.request_vcards_by_uid', {'uids': uids},
                         'kdeconnect.contacts.response_vcards', timeout=30)
        body = cards[0]['body'] if cards else {}
        for uid in uids:
            name, numbers = _vcard(str(body.get(uid) or ''))
            if not name:
                continue
            by_name.setdefault(name.lower(), [])
            for n in numbers:
                by_number.setdefault(_digits(n), name)
                if n not in by_name[name.lower()]:
                    by_name[name.lower()].append(n)
    peer.extra['contacts'], peer.extra['contacts_names'], peer.extra['contacts_ts'] = by_number, by_name, time.time()
    logger.info(f"[kdeconnect] {peer.name}'s contacts: {len(uids)} card(s), {len(by_number)} number(s) with a name")
    return by_number


def _vcard(text):
    """(full name, [numbers]) from one vCard. Folded lines are joined."""
    lines, name, numbers = [], '', []
    for raw in text.replace('\r\n', '\n').split('\n'):
        if raw[:1] in (' ', '\t') and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    for line in lines:
        key, _, value = line.partition(':')
        base = key.split(';')[0].split('.')[-1].upper()
        if base == 'FN' and not name:
            name = ' '.join(value.split())
        elif base == 'TEL' and value.strip():
            numbers.append(value.strip())
    return name, numbers


def _number_of(peer, who):
    """The number a text goes to. A number as typed, minus punctuation
    (dashes, dots, spaces, brackets) and a +1 or 1 country prefix. A name must
    be a contact's whole name, case aside: sending is never a loose match
    (Krem, 2026-10-12); the near misses are named so the right one is easy."""
    who = str(who or '').strip()
    if _NUMBER.match(who):
        digits = re.sub(r'\D', '', who)
        if len(digits) == 11 and digits.startswith('1'):
            digits = digits[1:]
        return digits, ''
    try:
        _contacts(peer)
    except P.ProtocolError as e:
        return '', f"the phone's contacts could not be read: {e}"
    names = peer.extra.get('contacts_names') or {}
    if not names and 'kdeconnect.contacts.request_all_uids_timestamps' not in peer.caps_in:
        return '', ("names need the Contacts permission for KDE Connect on the phone. Give the number itself.")
    exact = names.get(who.lower())
    if exact:
        return re.sub(r'\D', '', exact[0]), ''
    near = [n for n in names if who.lower() in n and names[n]]
    if near:
        return '', f"no contact is named exactly '{who}'. Close: " + ', '.join(n.title() for n in near[:8]) + '. Use the whole name.'
    return '', f"no contact named '{who}'. Give the number itself."


def _thread_of(peer, who):
    """The conversation `who` names: (thread id, its latest message, ''), or
    (None, None, why). A name is matched loosely against the contacts, a
    number loosely against the conversations' numbers (no area code is fine);
    a number of five digits or fewer is a conversation's own id."""
    threads = _conversations(peer)
    who = str(who or '').strip()
    if who.isdigit() and len(who) <= 5 and (int(who) in threads or who in threads):
        tid = int(who) if int(who) in threads else who
        return tid, threads[tid], ''
    newest = lambda hits: max(hits, key=lambda kv: kv[1].get('date') or 0)
    if _NUMBER.match(who):
        hits = [(tid, m) for tid, m in threads.items() if any(_same_number(a, who) for a in _addresses(m))]
        if hits:
            tid, m = newest(hits)
            return tid, m, ''
        return None, None, f"no conversation with {who} on the phone"
    try:
        names = _contacts(peer)
    except P.ProtocolError:
        names = {}
    want = who.lower()
    hits = [(tid, m) for tid, m in threads.items()
            if want in ' '.join(_name_of(a, names) for a in _addresses(m)).lower()]
    if hits:
        tid, m = newest(hits)
        return tid, m, ''
    if not names and 'kdeconnect.contacts.request_all_uids_timestamps' not in peer.caps_in:
        return None, None, (f"no conversation named '{who}': the phone's KDE Connect has no Contacts permission, "
                            f"so only numbers can be matched")
    return None, None, f"no conversation with anyone named '{who}' on the phone"


def _count_and_who(value, default):
    """'Dad 30' -> ('Dad', 30); '30' alone is a count only when something is before it."""
    words = str(value or '').split()
    count = default
    if len(words) >= 2 and words[-1].isdigit() and len(words[-1]) <= 3 and 1 <= int(words[-1]) <= 200:
        count, words = int(words[-1]), words[:-1]
    return ' '.join(words), count


def _names_note(peer, names):
    if names or 'kdeconnect.contacts.request_all_uids_timestamps' in peer.caps_in:
        return ''
    return "(Names need the Contacts permission for KDE Connect on the phone; numbers are shown instead.)"


def _sms_list(peer, value=''):
    """Who texted lately: names, numbers, when, the last words. Newest first,
    from what the phone has sent so far; 'fresh' asks it again."""
    words = str(value or '').lower().split()
    refresh = any(w in ('fresh', 'refresh', 'again') for w in words)
    count = next((int(w) for w in words if w.isdigit() and 1 <= int(w) <= 200), LIST_DEFAULT)
    count = min(count, LIST_MOST)
    threads = _conversations(peer, refresh=refresh)
    if not threads:
        return ("No conversations have come from the phone yet. It sends them slowly; try again in a moment. "
                "If none ever come: on the phone, does KDE Connect have the SMS permission, and is its SMS "
                "plugin switched on for this Sapphire?"), True
    names = {}
    try:
        names = _contacts(peer)
    except P.ProtocolError:
        pass
    rows = sorted(threads.items(), key=lambda kv: -(kv[1].get('date') or 0))[:count]
    out = [f"Texts on the phone: {len(threads)} conversations, the {len(rows)} newest first (who · when · last words):"]
    for tid, m in rows:
        who = _senders(m, names) or 'an unknown number'
        you = 'you: ' if m.get('type') != 1 else ''
        body = ' '.join(str(m.get('body') or '').split())
        if len(body) > LIST_WORDS:
            body = body[:LIST_WORDS].rstrip() + ' [...]'
        if m.get('attachments') and not body:
            body = f"[{len(m['attachments'])} attachment(s)]"
        out.append(f"{who} · {_ago(m.get('date'))} · {you}{body}")
    if len(threads) > len(rows):
        out.append(f"({len(threads) - len(rows)} older ones not shown; `conversations 40` shows more.)")
    age = time.time() - _cache(peer)['ts']
    if age > 120:
        out.append(f"(As the phone told it {int(age // 60)} min ago; `conversations fresh` asks again.)")
    out.append('Read one by name or number: device_action("<device>","sms","read","Dad 30").')
    note = _names_note(peer, names)
    if note:
        out.append(note)
    return '\n'.join(out), True


def _sms_read(peer, value):
    """One conversation by name or number, loosely matched; a count after it."""
    who, count = _count_and_who(value, READ_DEFAULT)
    if not who:
        return "read: who? A contact's name or a phone number (no area code is fine), then how many: \"Dad 30\".", False
    tid, latest, why = _thread_of(peer, who)
    if tid is None:
        return f"read: {why}.", False
    def mine(pkt):               # the answer is the packet whose texts are all of this conversation
        msgs = [m for m in (pkt['body'].get('messages') or []) if isinstance(m, dict)]
        return bool(msgs) and all(str(m.get('thread_id')) == str(tid) for m in msgs)
    got = peer.ask('kdeconnect.sms.request_conversation', {'threadID': tid, 'numberToRequest': count},
                   'kdeconnect.sms.messages', timeout=CONV_FIRST, accept=mine)
    msgs = sorted((m for p in got for m in (p['body'].get('messages') or []) if isinstance(m, dict)),
                  key=lambda m: m.get('date') or 0)
    logger.info(f"[kdeconnect] asked {peer.name} for one conversation: {len(msgs)} message(s) came back")
    _mark_seen(peer, msgs)
    names = {}
    try:
        names = _contacts(peer)
    except P.ProtocolError:
        pass
    with_whom = _senders(latest, names) or 'an unknown number'
    if not msgs:
        return (f"The phone sent nothing back for the texts with {with_whom} within {CONV_FIRST} seconds. "
                f"It may be asleep, or busy sending its conversation list; try again in a moment."), False
    out = [f"Texts with {with_whom}, the last {len(msgs)}, oldest first:"]
    for m in msgs:
        who_ = 'you' if m.get('type') != 1 else (
            ', '.join(_name_of(a, names) or a for a in _addresses(m)) or '?')
        body = str(m.get('body') or '').strip()
        if m.get('attachments'):
            body = (body + ' ' if body else '') + f"[{len(m['attachments'])} attachment(s)]"
        out.append(f"{_ago(m.get('date'))} {who_}: {body}")
    note = _names_note(peer, names)
    if note:
        out.append(note)
    return '\n'.join(out), True


def _sms_send(peer, value):
    who, sep, text = str(value or '').partition('|')
    who, text = who.strip(), text.strip()
    if not sep or not who or not text:
        return ('send: a phone number, or a contact\'s exact name, then a pipe, then the words: '
                '"555-123-4567 | On my way".'), False
    if len(text) > 1600:
        return 'send: that is over 1600 characters; texts are short.', False
    number, why = _number_of(peer, who)
    if not number:
        return f"send: {why}", False
    peer.send('kdeconnect.sms.request', {'version': 2, 'addresses': [{'address': number}], 'messageBody': text})
    logger.info(f"[kdeconnect] a text of {len(text)} chars was handed to {peer.name} for sending")
    names = peer.extra.get('contacts') or {}
    return (f"The phone was asked to send a text to {_who(number, names)}: {text} "
            f"(it does not report back; the sent text shows in `read` once it went)"), True


# --- the driver's doors ----------------------------------------------------------------

def validate(config):
    pick = config.get('phone') if isinstance(config.get('phone'), dict) else {}
    link = _link_now()
    if pick.get('all', True) or not pick.get('only'):
        # "whichever phone is here": fine when exactly one paired phone is linked
        paired = [p for p in (link.peers.values() if link else []) if p.paired]
        if len(paired) != 1:
            return config, ("Pick the phone in the list above. Open KDE Connect on it while it is on this network "
                            "and it appears there; pair it with the button under it, then pick it.")
        pick = {'all': False, 'only': [{'id': paired[0].id, 'name': paired[0].name}]}
    config['phone'] = {'all': False, 'only': pick['only'][:1]}
    pid = str(pick['only'][0].get('id') or '')
    if (link.trusted(pid) if link else _trusted().get(pid)) is None:
        name = str(pick['only'][0].get('name') or 'The phone')
        return config, (f"{name} is not paired yet, so it cannot be added. Press Pair under the phone and accept "
                        f"on the phone (the same key shows on both), or request pairing in the phone's app "
                        f"and press Accept here. Then add it.")
    from core.devices import voice
    config['chat'] = voice.chat_named(config.get('chat'))
    if config.get('share_to') not in ('both', 'chat', 'files'):
        config['share_to'] = 'both'
    return config, ''


def discover(config):
    """Phones that have been heard on the network lately, and every paired one.
    Each look says our name again (the phone links to us when it hears it);
    presence looks every 15 s, the picker on Look again, so once in 10 s."""
    global _last_look
    link = _ensure_link(wait=True)
    wanted = _configured()
    # a phone that hears us links to us again, dropping the link it has: so
    # only call while a phone we want (or any, before the first is picked) is missing
    if time.monotonic() - _last_look > 10 and (not wanted or any(pid not in link.peers for pid in wanted)):
        _last_look = time.monotonic()
        link.broadcast(why='the pick list looked and a phone is missing')
        end = time.monotonic() + LOOK
        while time.monotonic() < end and not link.seen:
            time.sleep(0.25)
    out, trusted = [], _trusted()
    for pid, s in list(link.seen.items()):
        out.append(_found_item(pid, s['name'], link.peers.get(pid), link.pending_key(pid), pid in trusted))
    for pid, rec in trusted.items():
        if not any(o['id'] == pid for o in out):
            out.append(_found_item(pid, rec.get('name') or pid[:8], None, None, True))
    return out


def _found_item(pid, name, peer, pending, trusted):
    """One line of the pick list: the phone, its pairing state in words, and
    the button for the next step. Pairing is done HERE, before the phone is a
    device (Krem, 2026-10-12: a setup that does not set up is no setup)."""
    item = {'id': pid, 'name': name}
    if peer is not None and peer.paired:
        return dict(item, kind='paired', note='Paired. Add it.')
    if pending and pending[0] == 'by_peer':
        return dict(item, kind='asks to pair',
                    note=f"The phone asks to pair with key {pending[1]}. If the phone shows the same key, accept.",
                    actions=[{'do': 'accept', 'label': 'Accept pairing'}])
    if pending:
        return dict(item, kind='pairing',
                    note=f"Waiting for the phone: on it, check that the key reads {pending[1]} and accept, "
                         f"within 30 seconds.",
                    actions=[{'do': 'pair', 'label': 'Ask again'}])
    if peer is not None:
        return dict(item, kind='NOT paired',
                    note="Not paired. Press Pair, then accept on the phone. Or request pairing in the "
                         "phone's app and accept here.",
                    actions=[{'do': 'pair', 'label': 'Pair'}])
    if trusted:
        return dict(item, kind='paired, away', note='Paired, but not here right now.')
    return dict(item, kind='heard', note='Heard on the network, not linked yet. Give it a moment, or open the app on it.')


def setup(thing_id, action, config):
    """The pick list's buttons: pair with a phone, or accept its request."""
    link = _ensure_link()
    peer = link.peers.get(thing_id)
    if peer is None:
        return 'The phone is not linked right now. Open KDE Connect on it and look again.', False
    try:
        if action == 'pair':
            if peer.paired:
                return f"{peer.name} is paired. Add it.", True
            pending = link.pending_key(thing_id)
            key = link.request_pair(thing_id)          # the phone asked first: this accepts
            if peer.paired:
                return f"Paired with {peer.name} (key {key}). Add it.", True
            return (f"Asked {peer.name} to pair. On the phone, check that the key reads {key}, then accept, "
                    f"within 30 seconds."), True
        if action == 'accept':
            pending = link.pending_key(thing_id)
            if not pending or pending[0] != 'by_peer':
                return 'The phone has not asked to pair. Press Pair instead.', False
            key = link.accept_pair(thing_id)
            return f"Paired with {peer.name} (key {key}). Add it.", True
    except P.ProtocolError as e:
        return str(e), False
    return f"'{action}' is not something to do with a phone here.", False


def watch(changed, stopped):
    """The link runs while a watcher does: for as long as a device of this
    kind exists. `changed` is how presence hears a phone come and go."""
    global _changed
    _changed = changed
    _ensure_link()
    try:
        stopped.wait()
    finally:
        _changed = None
        _stop_link()


def tend(device, config, secrets, present, leaving):
    if leaving:
        return                   # the link is shared; forgetting a phone is the unpair action
    link = _ensure_link()
    peer_id = _peer_id(config)
    if not peer_id:
        return
    peer = link.peers.get(peer_id)
    if peer is None:
        if link.trusted(peer_id) is not None:
            link.call(peer_id)   # our name in its ear: it links to us
        return
    if peer.paired:
        _arm(peer, config)


def apply(device, config, secrets):
    """After a save: the chat it names is made, and a linked phone is armed
    for texts. Pairing happened in the pick list (setup) before the save."""
    if config.get('chat'):
        from core.devices import voice
        voice.chat_for(config, device['id'])
    link = _ensure_link()
    peer_id = _peer_id(config)
    peer = link.peers.get(peer_id) if peer_id else None
    if peer is None:
        if peer_id:
            link.call(peer_id)
        return
    if peer.paired:
        _arm(peer, config)


def describe(device, config):
    return {
        # pairing sits with the device's health on the Status tab (tab hint), owner only
        'link': {'label': 'Link', 'tab': 'Status', 'help': 'the pairing with the phone', 'actions': {
            'pair': {'help': 'pair again with the phone, or accept its request. The same 8-character key '
                             'shows on both screens; accept on the phone within 30 seconds', 'owner': True, 'wait': 10},
            'unpair': {'help': 'forget the phone', 'owner': True}}},
        'battery': {'label': 'Battery', 'help': 'its charge', 'actions': {
            'read': {'help': 'charge and whether it is charging', 'example': ''}}},
        'share': {'label': 'Share', 'help': 'send words or a link to the phone; the phone shares to you by itself', 'actions': {
            'send': {'help': 'text to the phone; a link opens there', 'example': 'https://example.org/map'},
            'list': {'help': 'the files the phone shared lately, with their paths', 'example': ''}}},
        'sms': {'label': 'Text messages', 'help': "the owner's texts: who wrote lately, one conversation, send as the owner",
                'actions': {
            'conversations': {'help': 'who texted lately: names, numbers, when, the last words. A count after it; '
                                      "'fresh' asks the phone again", 'example': '15', 'wait': 30},
            'read': {'help': "one conversation: a contact's name or a number, loosely matched (no area code is "
                             "fine), then how many", 'example': 'Dad 30', 'wait': 30},
            'send': {'help': "send a text as the phone's owner: a phone number, or a contact's exact name "
                             "(case aside), then a pipe, then the words",
                     'example': '555-123-4567 | On my way'}}},
        'notify': {'label': 'Notify', 'help': 'reach the phone', 'actions': {
            'ping': {'help': 'a notification on the phone with these words (empty = a plain ping)',
                     'example': 'Dinner is ready'},
            'find': {'help': 'ring the phone until it is found (again to stop)', 'example': ''}}},
        'contacts': {'label': 'Contacts', 'help': "the phone's address book", 'actions': {
            'find': {'help': "the numbers of a name, or the name behind a number", 'example': 'Dana'}}},
    }


def _has(peer):
    out = ['link']
    for cap, (way, need) in NEEDS.items():
        if need in (peer.caps_out if way == 'out' else peer.caps_in):
            out.append(cap)
    return out


def status(device, config, secrets):
    peer_id = _peer_id(config)
    if not peer_id:
        return {'online': False, 'detail': 'No phone picked yet.'}
    link = _ensure_link()
    trusted = _trusted().get(peer_id) or {}
    peer = link.peers.get(peer_id)
    note = _pair_notes.get(peer_id, '')
    if peer is not None:
        pending = link.pending_key(peer_id)
        readings = dict(peer.readings)
        readings['phone'] = f"{peer.name} ({peer.kind})"
        if peer.paired:
            return {'online': True, 'detail': f"{peer.name} linked at {peer.addr}", 'readings': readings, 'has': _has(peer)}
        if pending and pending[0] == 'by_peer':
            detail = (f"NOT PAIRED YET. {peer.name} asks to pair with key {pending[1]}: if the phone shows the "
                      f"same key, press Pair here to accept.")
        elif pending:
            detail = (f"NOT PAIRED YET. Waiting for {peer.name}: on the phone, check that the key reads "
                      f"{pending[1]} and accept, within 30 seconds.")
        else:
            detail = (f"NOT PAIRED. {peer.name} is here. Press Pair (Link tab), then accept on the phone."
                      + (f" Last time: {note}." if note else ''))
        return {'online': False, 'detail': detail, 'readings': readings}
    seen = link.seen.get(peer_id)
    pending = link.pending_key(peer_id)
    if seen:
        return {'online': False, 'detail': f"{seen['name']} was heard at {seen['addr']} but is not linked right now"
                + (f"; a pairing is waiting (key {pending[1]})" if pending else '') + (f" ({note})" if note else '')}
    name = trusted.get('name') or peer_id[:8]
    return {'online': False, 'detail': f"{name} is not here. Open KDE Connect on it."
            + (f" ({note})" if note else '')}


def _peer(device, config):
    link = _ensure_link()
    peer_id = _peer_id(config)
    peer = link.peers.get(peer_id) if peer_id else None
    if peer is None:
        raise P.ProtocolError(f"the phone is not linked right now ({status(device, config, None)['detail']})")
    return link, peer


def _files_list(peer):
    folder = INBOX / re.sub(r'[^A-Za-z0-9_-]', '', peer.id)[:40]
    files = sorted(folder.glob('*'), key=lambda p: p.stat().st_mtime, reverse=True)[:20] if folder.exists() else []
    if not files:
        return 'Nothing has been shared from the phone yet (or the device keeps no files: Settings > Devices).', True
    return 'Files shared from the phone, newest first:\n' + '\n'.join(
        f"{p.name} ({p.stat().st_size} bytes): {p}" for p in files), True


def run(device, capability, action, value, config, secrets, call_tool):
    value = str(value or '').strip()
    try:
        link, peer = _peer(device, config)
        if capability == 'link':
            if action == 'pair':
                if peer.paired:
                    return f"Already paired with {peer.name}.", True
                key = link.request_pair(peer.id)          # accepts instead when the phone asked first
                if peer.paired:
                    return f"Paired with {peer.name} (key {key}). It is online now.", True
                return (f"Asked {peer.name} to pair. On the phone, check that the key reads {key}, then accept, "
                        f"within 30 seconds. Press Pair again if it timed out."), True
            if action == 'unpair':
                link.unpair(peer.id)
                return f"{peer.name} is forgotten. It can pair again any time.", True
        if capability == 'notify':
            if action == 'ping':
                peer.send('kdeconnect.ping', {'message': value[:200]} if value else {})
                return (f"Shown on {peer.name}: {value[:120]}" if value else f"Pinged {peer.name}."), True
            if action == 'find':
                peer.send('kdeconnect.findmyphone.request', {})
                return f"{peer.name} is ringing. `find` again stops it.", True
        if capability == 'battery' and action == 'read':
            return (f"{peer.name}: battery {peer.readings['battery']}." if peer.readings.get('battery')
                    else f"{peer.name} has not reported its battery yet; it does so on a change."), True
        if capability == 'share':
            if action == 'send':
                if not value:
                    return 'send: the words, or a link, to send to the phone.', False
                body = {'url': value} if re.match(r'^https?://\S+$', value) else {'text': value[:4000]}
                peer.send('kdeconnect.share.request', body)
                return f"Sent to {peer.name}: {value[:120]}", True
            if action == 'list':
                return _files_list(peer)
        if capability == 'sms':
            if action == 'conversations':
                return _sms_list(peer, value)
            if action == 'read':
                return _sms_read(peer, value)
            if action == 'send':
                return _sms_send(peer, value)
        if capability == 'contacts' and action == 'find':
            if not value:
                return 'find: a name, or a number.', False
            _contacts(peer)
            if _NUMBER.match(value):
                name = _name_of(value, peer.extra.get('contacts') or {})
                return (f"{value} is {name}." if name else f"No contact has the number {value}."), True
            names = peer.extra.get('contacts_names') or {}
            hits = [(n, nums) for n, nums in names.items() if value.lower() in n]
            if not hits:
                return f"No contact named '{value}'.", True
            return '\n'.join(f"{n.title()}: {', '.join(nums) or 'no number'}" for n, nums in hits[:10]), True
        return f"{capability}/{action} is not something this phone does.", False
    except P.ProtocolError as e:
        return f"{device['id']}: {e}", False
