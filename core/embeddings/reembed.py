"""Re-embed pipeline — re-stamp stored vectors under the current provider.

When a user swaps embedding providers, existing stored vectors are stamped
with the previous provider's id and are filtered out of vector search.
This module walks all three vector-storing tables (memories, knowledge_entries,
people), re-generates embeddings under the active provider, and re-stamps.

Runs in a background thread, publishes progress via the event bus, supports
cancel. Only one re-embed runs at a time.
"""
import logging
import threading
import time

logger = logging.getLogger(__name__)

# ─── State ──────────────────────────────────────────────────────────────────


class _ReembedState:
    """All mutable state lives here; callers read via get_status()."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.cancel_requested = False
        self.thread = None
        # Progress counters
        self.total = 0
        self.done = 0
        self.current_table = None
        self.errors = 0
        self.last_error = None
        self.started_at = None
        self.finished_at = None


_state = _ReembedState()


def _snapshot():
    """Lock-respecting snapshot of state (used for status + events)."""
    with _state.lock:
        return {
            'running': _state.running,
            'total': _state.total,
            'done': _state.done,
            'current_table': _state.current_table,
            'errors': _state.errors,
            'last_error': _state.last_error,
            'started_at': _state.started_at,
            'finished_at': _state.finished_at,
            'cancel_requested': _state.cancel_requested,
        }


def get_status():
    return _snapshot()


def _publish():
    """Fire a progress event. Swallows exceptions so the worker never dies
    because the event bus has an issue."""
    try:
        from core.event_bus import publish
        publish('reembed_progress', _snapshot())
    except Exception as e:
        logger.debug(f"reembed progress publish failed: {e}")


# ─── Public controls ────────────────────────────────────────────────────────


def start_reembed():
    """Kick off a background re-embed. Returns (ok, message).

    Refuses if another re-embed is running. Runs the entire pipeline on a
    daemon thread so the HTTP caller returns immediately.
    """
    with _state.lock:
        if _state.running:
            return False, "Re-embed is already running"
        _state.running = True
        _state.cancel_requested = False
        _state.total = 0
        _state.done = 0
        _state.current_table = None
        _state.errors = 0
        _state.last_error = None
        _state.started_at = time.time()
        _state.finished_at = None

    thread = threading.Thread(target=_run, daemon=True, name='embed-reembed')
    thread.start()
    with _state.lock:
        _state.thread = thread

    _publish()
    return True, "Re-embed started"


def cancel_reembed():
    """Request a graceful stop at the next batch boundary. The worker will
    finish the current batch (so no row is left half-stamped) then exit."""
    with _state.lock:
        if not _state.running:
            return False, "No re-embed running"
        _state.cancel_requested = True
    return True, "Cancel requested"


# ─── Worker ─────────────────────────────────────────────────────────────────


def _count_pending(embedder):
    """Count rows across all tables that would be re-embedded. Rows with
    matching provenance are skipped (already current)."""
    from core.embeddings import current_provenance
    import sqlite3 as _sql
    active_pid = getattr(embedder, 'provider_id', None)
    counts = {}

    def _count(open_conn, table):
        try:
            with open_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    f'SELECT COUNT(*) FROM {table} WHERE embedding_provider IS NOT ? '
                    f'OR embedding_provider IS NULL',
                    (active_pid,)
                )
                return cur.fetchone()[0]
        except _sql.OperationalError:
            return 0

    try:
        from plugins.memory.tools import memory_tools as _mt
        counts['memories'] = _count(_mt._get_connection, 'memories')
    except Exception as e:
        logger.debug(f"reembed count memories failed: {e}")
        counts['memories'] = 0
    try:
        from plugins.memory.tools import knowledge_tools as _kt
        counts['knowledge_entries'] = _count(_kt._get_connection, 'knowledge_entries')
        counts['people'] = _count(_kt._get_connection, 'people')
    except Exception as e:
        logger.debug(f"reembed count knowledge failed: {e}")
        counts.setdefault('knowledge_entries', 0)
        counts.setdefault('people', 0)
    return counts


BATCH_SIZE = 16


def _cancel_check():
    with _state.lock:
        return _state.cancel_requested


def _bump_done(n, current_table=None):
    with _state.lock:
        _state.done += n
        if current_table:
            _state.current_table = current_table


def _bump_error(msg):
    with _state.lock:
        _state.errors += 1
        _state.last_error = msg


def _process_memories(embedder):
    from plugins.memory.tools import memory_tools as mt
    from core.embeddings import stamp_embedding
    active_pid = getattr(embedder, 'provider_id', None)

    with mt._get_connection() as conn:
        rows = conn.execute(
            'SELECT id, content FROM memories WHERE embedding_provider IS NOT ? '
            'OR embedding_provider IS NULL',
            (active_pid,)
        ).fetchall()

    for i in range(0, len(rows), BATCH_SIZE):
        if _cancel_check():
            return
        batch = rows[i:i + BATCH_SIZE]
        ids = [r[0] for r in batch]
        texts = [r[1] for r in batch]
        embs = embedder.embed(texts, prefix='search_document')
        if embs is None:
            _bump_error(f"embedder returned None on memories batch starting at id {ids[0]}")
            return
        try:
            with mt._get_connection() as conn:
                cur = conn.cursor()
                for row_id, emb in zip(ids, embs):
                    blob, pid, dim = stamp_embedding(emb, embedder)
                    cur.execute(
                        'UPDATE memories SET embedding = ?, embedding_provider = ?, '
                        'embedding_dim = ? WHERE id = ?',
                        (blob, pid, dim, row_id)
                    )
                conn.commit()
            _bump_done(len(batch), 'memories')
            _publish()
        except Exception as e:
            _bump_error(f"memories batch failed: {e}")
            return


def _process_knowledge_entries(embedder):
    from plugins.memory.tools import knowledge_tools as kt
    from core.embeddings import stamp_embedding
    active_pid = getattr(embedder, 'provider_id', None)

    with kt._get_connection() as conn:
        rows = conn.execute(
            'SELECT id, content FROM knowledge_entries WHERE embedding_provider IS NOT ? '
            'OR embedding_provider IS NULL',
            (active_pid,)
        ).fetchall()

    for i in range(0, len(rows), BATCH_SIZE):
        if _cancel_check():
            return
        batch = rows[i:i + BATCH_SIZE]
        ids = [r[0] for r in batch]
        texts = [r[1] for r in batch]
        embs = embedder.embed(texts, prefix='search_document')
        if embs is None:
            _bump_error(f"embedder returned None on knowledge_entries batch starting at id {ids[0]}")
            return
        try:
            with kt._get_connection() as conn:
                cur = conn.cursor()
                for row_id, emb in zip(ids, embs):
                    blob, pid, dim = stamp_embedding(emb, embedder)
                    cur.execute(
                        'UPDATE knowledge_entries SET embedding = ?, embedding_provider = ?, '
                        'embedding_dim = ? WHERE id = ?',
                        (blob, pid, dim, row_id)
                    )
                conn.commit()
            _bump_done(len(batch), 'knowledge_entries')
            _publish()
        except Exception as e:
            _bump_error(f"knowledge_entries batch failed: {e}")
            return


def _process_people(embedder):
    """People get one-at-a-time treatment since their embed text is composed
    from multiple columns — matches the shape that create_or_update_person uses."""
    from plugins.memory.tools import knowledge_tools as kt
    from core.embeddings import stamp_embedding
    active_pid = getattr(embedder, 'provider_id', None)

    with kt._get_connection() as conn:
        rows = conn.execute(
            'SELECT id, name, relationship, phone, email, address, notes '
            'FROM people WHERE embedding_provider IS NOT ? OR embedding_provider IS NULL',
            (active_pid,)
        ).fetchall()

    for pid_row, name, rel, phone, email, addr, notes in rows:
        if _cancel_check():
            return
        parts = [name or '']
        if rel: parts.append(f"relationship: {rel}")
        if phone: parts.append(f"phone: {phone}")
        if email: parts.append(f"email: {email}")
        if addr: parts.append(f"address: {addr}")
        if notes: parts.append(f"notes: {notes}")
        embed_text = '. '.join(parts)
        embs = embedder.embed([embed_text], prefix='search_document')
        if embs is None:
            _bump_error(f"embedder returned None on person id {pid_row}")
            return
        try:
            blob, prov, dim = stamp_embedding(embs[0], embedder)
            with kt._get_connection() as conn:
                conn.execute(
                    'UPDATE people SET embedding = ?, embedding_provider = ?, '
                    'embedding_dim = ? WHERE id = ?',
                    (blob, prov, dim, pid_row)
                )
                conn.commit()
            _bump_done(1, 'people')
            _publish()
        except Exception as e:
            _bump_error(f"person {pid_row} re-embed failed: {e}")
            return


def _run():
    """Main worker. Walks all 3 tables, re-embedding rows not stamped with
    the active provider. Cancellable between batches. Never raises — writes
    any failure to _state.last_error so the UI can surface it."""
    try:
        from core.embeddings import get_embedder, current_provenance
        embedder = get_embedder()
        if not embedder or not embedder.available:
            with _state.lock:
                _state.last_error = "Active embedding provider is not available"
            return

        # Snapshot the provenance we started with. If the active provider
        # changes mid-run (user toggles, plugin unloads, etc.), stamped rows
        # would land under the OLD provider id in a DB whose active provider
        # is NEW — making integrity strictly worse. Between-table checks
        # below bail cleanly if drift is detected. Scout finding #4.
        start_prov = current_provenance()

        counts = _count_pending(embedder)
        with _state.lock:
            _state.total = counts['memories'] + counts['knowledge_entries'] + counts['people']
        _publish()

        if _state.total == 0:
            with _state.lock:
                _state.current_table = 'done'
            logger.info("Re-embed: nothing to do (all rows already current)")
            return

        def _provenance_changed():
            now_prov = current_provenance()
            if now_prov != start_prov:
                with _state.lock:
                    _state.last_error = (
                        f"Provider changed mid-run ({start_prov} -> {now_prov}) — aborting"
                    )
                    _state.cancel_requested = True
                logger.error(f"Re-embed aborted: provenance drift {start_prov} -> {now_prov}")
                return True
            return False

        _process_memories(embedder)
        if _cancel_check() or _provenance_changed():
            return
        _process_knowledge_entries(embedder)
        if _cancel_check() or _provenance_changed():
            return
        _process_people(embedder)

        logger.info(f"Re-embed complete: {_state.done}/{_state.total} rows processed, "
                    f"{_state.errors} errors")

    except Exception as e:
        logger.error(f"Re-embed worker crashed: {e}", exc_info=True)
        with _state.lock:
            _state.last_error = f"Worker crashed: {e}"
    finally:
        with _state.lock:
            _state.running = False
            _state.finished_at = time.time()
            if _state.cancel_requested:
                _state.current_table = 'cancelled'
            elif _state.last_error:
                _state.current_table = 'error'
            else:
                _state.current_table = 'done'
        _publish()
