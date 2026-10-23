#!/usr/bin/env python
"""Revert a rows-format chat back to blob storage (rowify safety net #2).

Standalone, stdlib-only — mirrors ChatSessionManager.revert_chat_to_blob
without importing core/, so it works even if the app can't boot. Serializes
the LIVE rows (not the frozen conversion-point blob), so nothing is lost
and it works after the frozen blob was privacy-nulled.

Prefer stopping Sapphire first (systemctl --user stop sapphire). Running
against a live instance is tolerated — every save re-probes storage_format
— but stopped is safer.

Usage:
  python tools/revert_chat_storage.py <chat_name> [db_path]
  python tools/revert_chat_storage.py --list [db_path]     # show rows chats

After reverting, run:  python tools/verify_chat_storage.py
"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def default_db():
    return Path(__file__).resolve().parent.parent / "user" / "history" / "sapphire_history.db"


def list_rows_chats(db):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT name, (SELECT COUNT(*) FROM chat_messages cm WHERE cm.chat_name = chats.name) "
            "FROM chats WHERE storage_format = 'rows' ORDER BY name").fetchall()
    finally:
        conn.close()
    if not rows:
        print("No rows-format chats in this database.")
        return 0
    print("rows-format chats:")
    for name, n in rows:
        print(f"  {name}  ({n} messages)")
    return 0


def revert(db, chat_name):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    # Patience against a live instance holding the write lock (WAL).
    conn.execute("PRAGMA busy_timeout=10000")
    try:
        row = conn.execute(
            "SELECT storage_format FROM chats WHERE name = ?", (chat_name,)).fetchone()
        if not row:
            print(f"ERROR: chat '{chat_name}' not found")
            return 1
        if row["storage_format"] != "rows":
            print(f"ERROR: chat '{chat_name}' is '{row['storage_format']}', not 'rows' — nothing to revert")
            return 1
        msgs = [json.loads(r["message_json"]) for r in conn.execute(
            "SELECT message_json FROM chat_messages WHERE chat_name = ? ORDER BY seq",
            (chat_name,))]
        conn.execute(
            "UPDATE chats SET messages = ?, storage_format = 'blob', updated_at = ? WHERE name = ?",
            (json.dumps(msgs), datetime.now().isoformat(), chat_name))
        conn.execute("DELETE FROM chat_messages WHERE chat_name = ?", (chat_name,))
        conn.commit()
        # Read-back sanity: blob parses and counts match.
        back = json.loads(conn.execute(
            "SELECT messages FROM chats WHERE name = ?", (chat_name,)).fetchone()["messages"])
        if len(back) != len(msgs):
            print(f"ERROR: post-revert count mismatch ({len(back)} vs {len(msgs)}) — inspect manually")
            return 1
        print(f"OK: '{chat_name}' reverted to blob ({len(msgs)} messages).")
        print("Run: python tools/verify_chat_storage.py")
        print("Note: if Sapphire is running, its next write to this chat may lazily re-convert it.")
        return 0
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"ERROR: revert failed, rolled back: {e}")
        return 1
    finally:
        conn.close()


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    if args[0] == "--list":
        db = Path(args[1]) if len(args) > 1 else default_db()
        if not db.exists():
            print(f"ERROR: database not found: {db}")
            return 2
        return list_rows_chats(db)
    chat_name = args[0]
    db = Path(args[1]) if len(args) > 1 else default_db()
    if not db.exists():
        print(f"ERROR: database not found: {db}")
        return 2
    return revert(db, chat_name)


if __name__ == "__main__":
    sys.exit(main())
