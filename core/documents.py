# core/documents.py - files a person gives one chat (tmp/documents-plan.md). The one
# owner: routes, the turn, her tools and the row above the composer all come through
# here, and it works with Mind Palace or the classic memory engine, on or off.
#
# Rows live in knowledge.db as they always have: one tab per file in scope
# `__rag__:<chat>`, chunks as entries, no overlap so joining them rebuilds the text.
# A tab's description holds {"kind", "pages"}. A PDF's original stays beside them at
# user/documents/<tab id>.pdf, so a page can be shown as a picture on request.
#
# Each turn she gets at most the chat's budget of tokens: everything whole if it
# fits, otherwise the best excerpts plus a list saying what she isn't seeing. The
# tools in functions/documents.py let her read the rest, so a miss is never the end.
import json
import logging
import math
import os
import re
import threading
import uuid
from collections import Counter
from functools import lru_cache
from pathlib import Path

import config
from core import text_extract as tx
from core.fs_utils import replace_with_retry

logger = logging.getLogger(__name__)

MAX_BYTES = 25 * 1024 * 1024
LEVELS = {'off': 0, 'light': 2000, 'normal': 8000, 'heavy': 24000}   # tokens per turn
WINDOW_SHARE = 0.25         # documents never take more of the model's window than this
PAGE_TOKENS = 4000          # a page picture at full size: budget.image_tokens' dearest family
MAX_READ = 10               # parts in one read_attachment
MAX_PAGE_CHARS = 20000
EMBED_BATCH = 32
RRF_K = 60
MAX_QUERY_TERMS = 40
TOOL_NAMES = ('read_attachment', 'search_attachments')
_lock = threading.RLock()   # a swap, a rename and a delete of one scope never interleave
_STOP = frozenset("a an and are as at be but by do does for from had has have how i if in into is it its "
                  "me my no not of on or our she so that the their them then there these they this to "
                  "us was we were what when where which who why will with would you your".split())


class Refused(Exception):
    """Words for the person: why this file wasn't taken."""


def _kb():
    from plugins.memory.tools import knowledge_tools
    return knowledge_tools


def scope_of(chat) -> str:
    return f"__rag__:{chat}"


def chat_of(scope):
    return scope[len('__rag__:'):] if isinstance(scope, str) and scope.startswith('__rag__:') else None


def _dir() -> Path:
    return Path(config.__file__).absolute().parent / 'user' / 'documents'


def _original(tab_id) -> Path:
    return _dir() / f'{int(tab_id)}.pdf'


def _unlink(tab_id):
    try:
        _original(tab_id).unlink(missing_ok=True)
    except OSError as e:
        logger.warning(f"[Documents] couldn't remove the PDF of tab {tab_id}: {e}")


def _changed(chat):
    try:
        from core.event_bus import publish
        publish('documents_changed', {'chat': chat})
    except Exception as e:
        logger.debug(f"[Documents] change event skipped: {e}")


def _meta(description) -> dict:
    try:
        got = json.loads(description or '{}')
        return got if isinstance(got, dict) else {}
    except ValueError:
        return {}       # a document from before this module: plain text


def _clean_name(name) -> str:
    name = str(name or '').replace('\\', '/').rsplit('/', 1)[-1]
    name = re.sub(r'[\x00-\x1f]', '', name).strip() or 'upload.txt'
    if len(name) > 200:         # the end stays: the extension says how to read it
        stem, dot, ext = name.rpartition('.')
        name = f"{stem[:190]}.{ext[:9]}" if dot else name[:200]
    return name


def _read_file(name, raw):
    """(text, meta). Raises Refused. Text is '' for a scan, whose pages are pictures."""
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    if ext == 'pdf':
        pages, _, err = tx.pdf_pages(raw)
        if err:
            raise Refused(err)
        if not pages:
            raise Refused("That PDF has no pages.")
        if not tx.has_text_layer(pages):
            _refuse_unreadable_scan(raw, len(pages))
            return '', {'kind': 'scan', 'pages': len(pages)}
        text = '\n\n'.join(f'[page {i}]\n{p}' for i, p in enumerate(pages, 1) if p)
        return text, {'kind': 'pdf', 'pages': len(pages)}
    if ext == 'epub':
        text, _, err = tx.extract_epub(raw)
        if err:
            raise Refused(err)
        return text.strip(), {'kind': 'text'}
    text, err = tx.decode_text(raw)
    if err:
        raise Refused(err)
    text = text.strip()
    if not text:
        raise Refused("That file is empty.")
    return text, {'kind': 'text'}


def _refuse_unreadable_scan(raw, pages):
    """A scan is only its pictures. If the first pages give none, taking it would
    mean a pill that fails every turn (a JBIG2 scan with no jbig2dec, say)."""
    why = None
    for n in range(1, min(pages, 3) + 1):
        pics, why = tx.page_pictures(raw, n)
        if pics:
            return
    raise Refused(f"That PDF has no text and its pages can't be shown. {why or ''}".strip())


def _embed(chunks, report=None):
    """[(blob, provider, dim)] one per chunk, (None, None, None) where embedding
    isn't possible: the document is stored anyway and keyword search still finds it."""
    empty = (None, None, None)
    embedder = _kb()._get_embedder()
    if not chunks or not embedder or not embedder.available:
        return [empty] * len(chunks)
    from core.embeddings import stamp_embedding
    out = []
    for at in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[at:at + EMBED_BATCH]
        try:
            vecs = embedder.embed(batch, prefix='search_document')
        except Exception as e:
            logger.warning(f"[Documents] embedding failed: {e}")
            vecs = None
        out += [stamp_embedding(v, embedder) for v in vecs] if vecs is not None else [empty] * len(batch)
        if report:
            report(len(out), len(chunks))
    return out


def add(chat, filename, raw, still_there=None) -> dict:
    """Store a file for a chat, replacing one of the same name. Everything slow runs
    BEFORE the swap and the swap is one transaction: a failure leaves the old copy.
    still_there() is asked again right before the swap: a big PDF reads for minutes,
    and a chat renamed or deleted meanwhile must not get a ghost scope a later chat of
    that name inherits. Blocking: never call it on the event loop. Raises Refused."""
    if not chat:
        raise Refused("No chat to attach that to.")
    if len(raw) > MAX_BYTES:
        raise Refused(f"That file is too large (max {MAX_BYTES // (1024 * 1024)}MB).")
    name = _clean_name(filename)
    text, meta = _read_file(name, raw)
    kb = _kb()
    chunks = kb._chunk_text(text, overlap_tokens=0) if text else []
    if len(chunks) > EMBED_BATCH:
        from core.notify import progress
        with progress(f"Reading {name}", source='core', key=f'document-{name}', done_title=f"{name} is ready") as p:
            stamps = _embed(chunks, p.update)
    else:
        stamps = _embed(chunks)

    # The original goes to disk BEFORE the swap: a full disk refuses a scan with the
    # old copy untouched. A name of its own: two uploads of one file don't collide.
    tmp = None
    if meta['kind'] in ('pdf', 'scan'):
        try:
            _dir().mkdir(parents=True, exist_ok=True)
            tmp = _dir() / f'{uuid.uuid4().hex}.tmp'
            tmp.write_bytes(raw)
        except OSError as e:
            if meta['kind'] == 'scan':      # a scan IS its pictures: nothing left to read
                raise Refused(f"Couldn't keep that PDF on disk: {e}")
            logger.warning(f"[Documents] page pictures unavailable for {name}: {e}")
            tmp = None
    scope = scope_of(chat)
    try:
        with _lock:
            if still_there and not still_there():
                raise Refused("That chat was renamed or deleted while the file was being read. Attach it again.")
            kb.create_scope(scope)
            with kb._get_connection() as conn:
                cur = conn.cursor()
                cur.execute('SELECT id FROM knowledge_tabs WHERE name = ? AND scope = ?', (name, scope))
                row = cur.fetchone()
                cur.execute('SELECT COUNT(*) FROM knowledge_entries WHERE tab_id IN '
                            '(SELECT id FROM knowledge_tabs WHERE scope = ? AND name != ?)', (scope, name))
                if cur.fetchone()[0] + len(chunks) > kb.MAX_ENTRIES_PER_SCOPE:
                    raise Refused("This chat holds too much document text already. Remove something first.")
                if row:
                    tab_id = row[0]
                    cur.execute('DELETE FROM knowledge_entries WHERE tab_id = ?', (tab_id,))
                    cur.execute('UPDATE knowledge_tabs SET description = ? WHERE id = ?', (json.dumps(meta), tab_id))
                else:
                    cur.execute('INSERT INTO knowledge_tabs (name, description, type, scope) VALUES (?, ?, ?, ?)',
                                (name, json.dumps(meta), 'user', scope))
                    tab_id = cur.lastrowid
                cur.executemany(
                    'INSERT INTO knowledge_entries (tab_id, content, chunk_index, source_filename, '
                    'embedding, embedding_provider, embedding_dim) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    [(tab_id, c, i, name, *stamps[i]) for i, c in enumerate(chunks)])
                conn.commit()
            if tmp:
                try:
                    replace_with_retry(tmp, _original(tab_id))
                except OSError as e:
                    # new text must never serve the old version's pictures
                    logger.warning(f"[Documents] couldn't keep the PDF of {name}: {e}")
                    _unlink(tab_id)
            else:
                _unlink(tab_id)             # the name was a PDF once
    finally:
        if tmp:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass                        # the next boot's sweep takes it
    _changed(chat)
    return {'filename': name, 'chunks': len(chunks), **meta}


def _tabs(chat):
    with _kb()._get_connection() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, name, description FROM knowledge_tabs WHERE scope = ? ORDER BY id', (scope_of(chat),))
        return cur.fetchall()


def listing(chat) -> list:
    """[{filename, kind, chunks, chars, pages, status}]. status: searchable (vectors
    match the active embedder), keyword (they don't, or there's no embedder), pictures
    (a scan: no text at all)."""
    tabs = _tabs(chat)
    if not tabs:
        return []
    kb = _kb()
    embedder = kb._get_embedder()
    active = getattr(embedder, 'provider_id', None) if embedder and embedder.available else None
    out, stale = [], False
    with kb._get_connection() as conn:
        cur = conn.cursor()
        for tab_id, name, description in tabs:
            cur.execute('SELECT COUNT(*), COALESCE(SUM(LENGTH(content)), 0), '
                        'COALESCE(SUM(embedding IS NOT NULL AND embedding_provider = ?), 0) '
                        'FROM knowledge_entries WHERE tab_id = ?', (active, tab_id))
            chunks, chars, embedded = cur.fetchone()
            meta = _meta(description)
            kind = meta.get('kind', 'text')
            if kind == 'scan':
                status = 'pictures'
            elif active and embedded >= chunks:
                status = 'searchable'
            else:
                status = 'keyword'
                stale = stale or bool(active)
            out.append({'filename': name, 'kind': kind, 'chunks': chunks, 'chars': chars,
                        'pages': meta.get('pages'), 'status': status})
    if stale:
        _heal_later(chat)
    return out


_healing = set()
_heal_lock = threading.Lock()


def _heal_later(chat):
    """Vectors missing (no embedder at upload) or from another provider: embed them on
    a thread so a turn never waits. Search works by keyword until then. This can't
    lean on the memory plugin's backfill: nothing runs it with that plugin off."""
    with _heal_lock:
        if chat in _healing:
            return
        _healing.add(chat)
    threading.Thread(target=_heal, args=(chat,), daemon=True, name='documents-heal').start()


def _heal(chat):
    try:
        kb = _kb()
        embedder = kb._get_embedder()
        if not embedder or not embedder.available:
            return
        active = getattr(embedder, 'provider_id', None)
        with kb._get_connection() as conn:
            cur = conn.cursor()
            cur.execute('SELECT e.id, e.content FROM knowledge_entries e JOIN knowledge_tabs t ON e.tab_id = t.id '
                        'WHERE t.scope = ? AND (e.embedding IS NULL OR e.embedding_provider IS NOT ?)',
                        (scope_of(chat), active))
            rows = cur.fetchall()
        if not rows:
            return
        stamps = _embed([r[1] for r in rows])
        done = [(*s, r[0]) for r, s in zip(rows, stamps) if s[0] is not None]
        if done:
            with kb._get_connection() as conn:
                conn.executemany('UPDATE knowledge_entries SET embedding = ?, embedding_provider = ?, '
                                 'embedding_dim = ? WHERE id = ?', done)
                conn.commit()
            _changed(chat)
    except Exception as e:
        logger.warning(f"[Documents] healing vectors failed: {e}")
    finally:
        with _heal_lock:
            _healing.discard(chat)


def remove(chat, filename) -> bool:
    kb = _kb()
    scope = scope_of(chat)
    with _lock:
        with kb._get_connection() as conn:
            cur = conn.cursor()
            cur.execute('SELECT id FROM knowledge_tabs WHERE scope = ? AND name = ?', (scope, filename))
            ids = [r[0] for r in cur.fetchall()]
            for tab_id in ids:
                cur.execute('DELETE FROM knowledge_entries WHERE tab_id = ?', (tab_id,))
                cur.execute('DELETE FROM knowledge_tabs WHERE id = ?', (tab_id,))
            cur.execute('SELECT COUNT(*) FROM knowledge_tabs WHERE scope = ?', (scope,))
            empty = cur.fetchone()[0] == 0
            conn.commit()
        if not ids:
            return False
        for tab_id in ids:
            _unlink(tab_id)
        if empty:
            kb.delete_scope(scope)
    _changed(chat)
    return True


def delete_chat(chat):
    """The chat is going: its documents and their PDFs go with it."""
    with _lock:
        ids = [t[0] for t in _tabs(chat)]
        _kb().delete_scope(scope_of(chat))
        for tab_id in ids:
            _unlink(tab_id)


def rename_chat(old, new):
    """PDFs are named by tab id, so only the scope moves. The new name just became a
    chat, so anything already under it belongs to no one: it would block the move."""
    with _lock:
        delete_chat(new)
        if not _kb().rename_scope(scope_of(old), scope_of(new)):
            logger.warning(f"[Documents] a renamed chat's documents didn't move")


def sweep_orphans(valid_chats):
    """At boot: documents of chats that no longer exist, then any PDF or temp file with
    no document. valid_chats must include sealed chats, or this would delete theirs."""
    kb = _kb()
    with kb._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT scope FROM knowledge_tabs WHERE scope LIKE '\\_\\_rag\\_\\_:%' ESCAPE '\\'")
        scopes = [r[0] for r in cur.fetchall()]
    valid = set(valid_chats)
    for chat in filter(None, map(chat_of, scopes)):
        if chat not in valid:
            delete_chat(chat)
            logger.info(f"[Documents] removed the documents of a chat that no longer exists")
    if not _dir().is_dir():
        return
    with kb._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM knowledge_tabs WHERE scope LIKE '\\_\\_rag\\_\\_:%' ESCAPE '\\'")
        keep = {f'{r[0]}.pdf' for r in cur.fetchall()}
    for f in _dir().iterdir():
        if f.is_file() and f.name not in keep:
            try:
                f.unlink()
            except OSError as e:
                logger.warning(f"[Documents] couldn't remove the stray {f.name}: {e}")


def _find(chat, filename):
    """(tab_id, name, meta) for what she called it: exact, else one case-insensitive
    or partial match. None when there's no single answer."""
    tabs = _tabs(chat)
    wanted = str(filename or '').strip().lower()
    hits = [t for t in tabs if t[1] == filename] or [t for t in tabs if t[1].lower() == wanted] \
        or [t for t in tabs if wanted and wanted in t[1].lower()]
    if len(hits) != 1:
        return None
    return hits[0][0], hits[0][1], _meta(hits[0][2])


def _parts(tab_id):
    with _kb()._get_connection() as conn:
        cur = conn.cursor()
        cur.execute('SELECT content FROM knowledge_entries WHERE tab_id = ? ORDER BY chunk_index, id', (tab_id,))
        return [r[0] for r in cur.fetchall()]


def _names(chat) -> str:
    return ', '.join(t[1] for t in _tabs(chat)) or 'none'


def read(chat, filename, part=1, count=3, page=None):
    """(text, pictures) for read_attachment. pictures: [(bytes, media_type)]."""
    found = _find(chat, filename)
    if not found:
        return f"No single document matches '{filename}'. This chat has: {_names(chat)}.", []
    tab_id, name, meta = found
    parts = _parts(tab_id)
    if page is not None:
        return _read_page(tab_id, name, meta, parts, int(page))
    if not parts:
        return f"{name} is scanned, with {meta.get('pages')} pages and no text. Ask for a page: page=N.", []
    part = max(1, int(part or 1))
    if part > len(parts):
        return f"{name} has {len(parts)} parts, there is no part {part}.", []
    end = min(len(parts), part + max(1, min(MAX_READ, int(count or 3))) - 1)
    head = f"{name}, part {part}" + (f"-{end}" if end > part else '') + f" of {len(parts)}"
    more = f"\n\n(More: part={end + 1}.)" if end < len(parts) else "\n\n(End of the document.)"
    return f"{head}\n\n" + '\n\n'.join(parts[part - 1:end]) + more, []


def _read_page(tab_id, name, meta, parts, page):
    if meta.get('kind') not in ('pdf', 'scan'):
        return f"{name} isn't a PDF, so it has no pages. Read it by part instead.", []
    words = ''
    if parts:
        pieces = re.split(r'(?m)^\[page (\d+)\]$', '\n\n'.join(parts))
        for n, body in zip(pieces[1::2], pieces[2::2]):
            if int(n) == page:
                words = body.strip()[:MAX_PAGE_CHARS]
    pics, why = [], None
    try:
        pics, why = tx.page_pictures(_original(tab_id).read_bytes(), page)
    except OSError:
        why = "The original PDF isn't stored for this one, re-attach it to see pictures."
    lines = [f"{name}, page {page} of {meta.get('pages') or '?'}"]
    if words:
        lines.append(words)
    if why and (not words or 'no pictures' not in why):     # a page of plain text needs no apology
        lines.append(why)
    return '\n\n'.join(lines), pics


def _rows(chat, filename=None):
    """Every text chunk: (id, content, name, index, blob, provider, dim, total)."""
    with _kb()._get_connection() as conn:
        cur = conn.cursor()
        sql = ('SELECT e.id, e.content, t.name, e.chunk_index, e.embedding, e.embedding_provider, e.embedding_dim '
               'FROM knowledge_entries e JOIN knowledge_tabs t ON e.tab_id = t.id WHERE t.scope = ?')
        args = [scope_of(chat)]
        if filename:
            sql += ' AND t.name = ?'
            args.append(filename)
        cur.execute(sql + ' ORDER BY t.id, e.chunk_index, e.id LIMIT 50000', args)
        return cur.fetchall()


def _terms(text):
    return [w for w in re.findall(r"\w[\w'-]*", (text or '').lower()) if len(w) > 1 and w not in _STOP]


@lru_cache(maxsize=60000)
def _term_counts(entry_id, content):
    """Entry ids never come back (AUTOINCREMENT) and content is in the key: a replaced
    chunk just misses. Tokenizing a big document every turn would stall the turn."""
    return Counter(_terms(content))


def _keyword_rank(rows, query) -> list:
    """BM25 over this chat's chunks, in Python: a part number, an error code or a name,
    and it needs no embedder. A word in most of the chunks scores nothing."""
    terms = list(dict.fromkeys(_terms(query)))[:MAX_QUERY_TERMS]
    if not terms or not rows:
        return []
    docs = [_term_counts(r[0], r[1]) for r in rows]
    sizes = [sum(d.values()) for d in docs]
    avg = sum(sizes) / len(docs) or 1
    df = {t: sum(1 for d in docs if t in d) for t in terms}
    idf = {t: math.log((len(docs) - n + 0.5) / (n + 0.5)) for t, n in df.items() if n}
    scored = []
    for i, d in enumerate(docs):
        score = 0.0
        for t, weight in idf.items():
            if weight > 0 and (tf := d.get(t)):
                score += weight * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * sizes[i] / avg))
        if score > 0:
            scored.append((score, i))
    return [i for _, i in sorted(scored, reverse=True)]


def _vector_rank(rows, query) -> list:
    import numpy as np
    embedder = _kb()._get_embedder()
    if not embedder or not embedder.available or not rows:
        return []
    try:
        vecs = embedder.embed([query], prefix='search_query')
    except Exception as e:
        logger.warning(f"[Documents] query embedding failed: {e}")
        return []
    if vecs is None:
        return []
    q = vecs[0]
    dim, active = int(q.shape[0]), getattr(embedder, 'provider_id', None)
    floor = float(getattr(config, 'RAG_SIMILARITY_THRESHOLD', 0.40) or 0)
    scored = []
    for i, r in enumerate(rows):
        if r[4] is None or r[5] != active or r[6] != dim:
            continue
        sim = float(np.dot(q, np.frombuffer(r[4], dtype=np.float32)))
        if sim >= floor:
            scored.append((sim, i))
    return [i for _, i in sorted(scored, reverse=True)]


def _ranked(rows, query) -> list:
    """Row indexes best first: vector and keyword ranks merged (reciprocal rank)."""
    score = {}
    for rank in (_vector_rank(rows, query), _keyword_rank(rows, query)):
        for at, i in enumerate(rank):
            score[i] = score.get(i, 0.0) + 1.0 / (RRF_K + at)
    return sorted(score, key=score.get, reverse=True)


def search(chat, query, filename=None, limit=5) -> str:
    """For search_attachments: hits labeled with the part to read onward from."""
    name = None
    if filename:
        found = _find(chat, filename)
        if not found:
            return f"No single document matches '{filename}'. This chat has: {_names(chat)}."
        name = found[1]
    rows = _rows(chat, name)
    if not rows:
        return "No searchable text here. A scanned PDF has only pictures: read_attachment(file, page=N)."
    totals = {}
    for r in rows:
        totals[r[2]] = totals.get(r[2], 0) + 1
    hits = _ranked(rows, query)[:max(1, min(20, int(limit or 5)))]
    if not hits:
        return f"Nothing matched '{query}'. Try other words, or walk the file with read_attachment."
    lines = [f"{len(hits)} match{'es' if len(hits) != 1 else ''}:"]
    for i in hits:
        r = rows[i]
        lines.append(f"--- {r[2]}, part {_position(rows, i)} of {totals[r[2]]} ---\n{r[1]}")
    return '\n\n'.join(lines)


def _position(rows, i) -> int:
    """1-based place of row i within its document (older chunk indexes may have gaps)."""
    return sum(1 for r in rows[:i + 1] if r[2] == rows[i][2])


def fence_tools(tools, chat, all_tools, level=None) -> list:
    """The turn's tools with read_attachment and search_attachments present exactly when
    this chat has documents to read, whatever the toolset says: a toolset can't strand
    her. A turn with no tools at all stays that way: toolset 'none' is how a model that
    can't take tools runs, and being sent tools can make one refuse."""
    kept = [t for t in tools if t.get('function', {}).get('name') not in TOOL_NAMES]
    if not kept or not chat:
        return kept
    try:
        if (level or 'normal') == 'off' or not _tabs(chat):
            return kept
    except Exception as e:
        logger.warning(f"[Documents] tool check failed: {e}")
        return kept
    return kept + [t for t in all_tools if t.get('function', {}).get('name') in TOOL_NAMES]


def _budget_for(level, window=0) -> int:
    want = LEVELS.get(level, LEVELS['normal'])
    return min(want, int(window * WINDOW_SHARE)) if window else want


def context(chat, query, level='normal', tools=True, window=0, sees=True):
    """(row text, pictures) for this turn, or (None, []). pictures: [(bytes, media
    type)]. window: the model's real window, 0 = no limit. tools: she can call
    read_attachment this turn. sees: the model takes pictures; one that doesn't gets a
    scan listed, and read_attachment hands it the page in words. The text always opens
    [Reference Documents]: the Claude provider's cache belt looks for that
    (core/chat/llm_providers/claude.py)."""
    if level == 'off' or not chat:
        return None, []
    tabs = _tabs(chat)
    if not tabs:
        return None, []
    from core.chat.budget import count_tokens
    room = _budget_for(level, window)

    whole, scans, unseen = [], [], []
    cost = 0
    for tab_id, name, description in tabs:
        meta = _meta(description)
        if meta.get('kind') == 'scan':
            pages = int(meta.get('pages') or 0)
            if sees:
                scans.append((tab_id, name, pages))
                cost += pages * PAGE_TOKENS
            else:
                unseen.append((name, pages))
        else:
            parts = _parts(tab_id)
            text = '\n\n'.join(parts)
            # a chunk's words can't be fewer than chars/12, so a huge file skips the count
            n = len(text) // 12 if len(text) > room * 12 else count_tokens(text)
            whole.append((name, text, len(parts)))
            cost += n

    blind = [f"- {n} (scanned, {p} pages, not shown: this model can't see pictures"
             + ("; read_attachment(file, page=N) describes a page)" if tools else ")") for n, p in unseen]
    if cost <= room:
        return _render_whole(whole, scans, blind)
    return _render_excerpts(chat, whole, scans, blind, query, room, tools, count_tokens)


def _render_whole(whole, scans, blind):
    head = ["[Reference Documents]", "Attached to this chat, shown in full:"]
    head += [f"- {n}" for n, _, _ in whole] + [f"- {n} (scanned, {p} pages, as pictures)" for _, n, p in scans] + blind
    pics, notes = [], []
    for tab_id, name, pages in scans:
        try:
            raw = _original(tab_id).read_bytes()
        except OSError:
            notes.append(f"{name}: the pages aren't available, it needs attaching again.")
            continue
        for n in range(1, pages + 1):
            got, why = tx.page_pictures(raw, n)
            pics += got
            if why:
                notes.append(f"{name}: {why}")
    body = [f"--- {name} ---\n{text}" for name, text, _ in whole]
    return '\n\n'.join(['\n'.join(head)] + body + notes), pics


def _render_excerpts(chat, whole, scans, blind, query, room, tools, count_tokens):
    """The best chunks up to the budget, then their neighbors while room lasts, each
    run of parts in document order under a label she can read onward from."""
    query = (query or '').strip()
    rows = _rows(chat) if query else []
    picked, used = {}, 0
    ranked = _ranked(rows, query) if rows else []
    def take(i):
        nonlocal used
        n = count_tokens(rows[i][1])
        if used + n > room:
            return False
        picked[i] = True
        used += n
        return True
    for i in ranked:
        if not take(i):
            break
    for i in list(picked):
        for j in (i + 1, i - 1):
            if 0 <= j < len(rows) and j not in picked and rows[j][2] == rows[i][2]:
                take(j)

    how = "read_attachment or search_attachments" if tools else None
    totals = {n: c for n, _, c in whole}
    lines = ["[Reference Documents]", "Attached to this chat. You see only part of these:"]
    for name, _, parts in whole:
        lines.append(f"- {name} ({parts} parts)" + (f": more with {how}" if how else ''))
    for _, name, pages in scans:
        lines.append(f"- {name} (scanned, {pages} pages, no text)"
                     + (": read_attachment(file, page=N) shows a page" if how else ''))
    lines += blind
    out = ['\n'.join(lines)]
    run = []
    def flush():
        if run:
            first, last = _position(rows, run[0]), _position(rows, run[-1])
            name = rows[run[0]][2]
            label = f"part {first}" + (f"-{last}" if last > first else '')
            out.append(f"--- {name}, {label} of {totals.get(name, '?')} ---\n" + '\n\n'.join(rows[i][1] for i in run))
            run.clear()
    for i in sorted(picked):
        if run and (i != run[-1] + 1 or rows[i][2] != rows[run[-1]][2]):
            flush()
        run.append(i)
    flush()
    return '\n\n'.join(out), []
