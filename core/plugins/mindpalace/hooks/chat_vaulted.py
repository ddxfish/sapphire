# chat_vaulted hook — full-scrub of chat-name provenance (vaulted chats
# ruling F5, 2026-08-15). When a chat flips private, the rows it stamped into
# plaintext mind.db while public get their chat provenance re-keyed to
# '__private__'. Placeholder, not dropped: the added_by migration infers
# 'ai' from meta.chat being present. One-way by design — unvaulting can't
# restore what was scrubbed, and shouldn't.
import logging

logger = logging.getLogger(__name__)


def chat_vaulted(event):
    name = (event.metadata or {}).get("name")
    if not name:
        return
    try:
        from plugins.mindpalace.tools.palace_tools import _get_connection
        with _get_connection() as conn:
            total = 0
            for table in ("chunks", "entities"):
                cur = conn.execute(
                    f"UPDATE {table} SET meta = json_set(meta, '$.chat', '__private__') "
                    f"WHERE json_extract(meta, '$.chat') = ?", (name,))
                total += cur.rowcount
            conn.commit()
        if total:
            # No name in the log — it just became a secret.
            logger.info(f"[mindpalace] scrubbed chat provenance on {total} row(s) for a vaulted chat")
    except Exception as e:
        logger.warning(f"[mindpalace] chat_vaulted scrub failed: {e}")
