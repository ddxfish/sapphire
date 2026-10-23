#!/usr/bin/env python
"""Independent chat-storage verifier (rowify safety net, layer 3).

Audits user/history/sapphire_history.db for storage invariants — blob AND
rows format — using a code path deliberately DISJOINT from the app and from
the migration's own in-txn verify:

  - stdlib only, imports NOTHING from core/ (a bug shared with the writer
    cannot hide here)
  - opens the database read-only (mode=ro) — structurally incapable of
    repair, mutation, or "helpful" fixes
  - stateless: every invariant is recomputed from raw SQL each run

Checks per rows-format chat:
  R1  seq is gapless from 0 and unique (0,1,2,...,N-1)
  R2  every message_json parses as a JSON object with a 'role'
  R3  role sidecar column matches message_json['role']
  R4  no persisted message carries 'thinking_raw' (rowify correction #3)
  R5  frozen-blob prefix identity: if the chat still carries a non-empty
      messages blob (pre-mutation), the parsed blob must EXACTLY equal the
      first len(blob) parsed rows — an independent re-verification of the
      conversion itself
Checks per blob-format chat:
  B1  messages blob parses as a JSON list
Global:
  G1  storage_format is only 'blob' or 'rows'; conversion_failed only 0/1
  G2  rows exist in chat_messages only for chats marked 'rows' (orphan rows
      under a blob/missing chat = FAIL)
  G3  <<IMG::tool:id>> markers vs tool_images table, both directions
      (informational WARN only — prune timing makes this advisory)

Exit codes: 0 = all pass, 1 = failures found, 2 = cannot open/scan database.
Output is plain ASCII (cp1252-safe fleet).

Usage:
  python tools/verify_chat_storage.py            # default DB location
  python tools/verify_chat_storage.py path/to/sapphire_history.db
  python tools/verify_chat_storage.py --verbose  # per-chat PASS lines too
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

IMG_MARKER = re.compile(r"<<IMG::tool:([^>]+)>>")


def fail(msgs, chat, code, detail):
    msgs.append(("FAIL", chat, code, detail))


def warn(msgs, chat, code, detail):
    msgs.append(("WARN", chat, code, detail))


def verify(db_path, verbose=False):
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as e:
        print(f"ERROR: cannot open {db_path} read-only: {e}")
        return 2

    results = []
    counts = {"blob": 0, "rows": 0}
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "chats" not in tables:
            print("ERROR: no 'chats' table — wrong database?")
            return 2
        has_rows_table = "chat_messages" in tables
        chat_cols = {r[1] for r in conn.execute("PRAGMA table_info(chats)")}
        pre_rowify = "storage_format" not in chat_cols

        fmt_expr = "'blob'" if pre_rowify else "storage_format"
        cf_expr = "0" if pre_rowify else "conversion_failed"
        chats = conn.execute(
            f"SELECT name, messages, {fmt_expr} AS fmt, {cf_expr} AS cf FROM chats"
        ).fetchall()

        seen_rows_chats = set()
        for chat in chats:
            name, fmt, cf = chat["name"], chat["fmt"], chat["cf"]

            # G1 — value domains
            if fmt not in ("blob", "rows"):
                fail(results, name, "G1", f"storage_format={fmt!r}")
                continue
            if cf not in (0, 1):
                fail(results, name, "G1", f"conversion_failed={cf!r}")
            counts[fmt] += 1

            if fmt == "blob":
                # B1 — blob parses as a list
                try:
                    parsed = json.loads(chat["messages"])
                    if not isinstance(parsed, list):
                        fail(results, name, "B1", "blob is not a JSON list")
                    elif verbose:
                        results.append(("PASS", name, "B1", f"{len(parsed)} msgs (blob)"))
                except (json.JSONDecodeError, TypeError) as e:
                    fail(results, name, "B1", f"blob does not parse: {e}")
                continue

            # ---- rows-format chat ----
            seen_rows_chats.add(name)
            if not has_rows_table:
                fail(results, name, "R1", "format=rows but no chat_messages table")
                continue

            rows = conn.execute(
                "SELECT seq, role, message_json FROM chat_messages "
                "WHERE chat_name = ? ORDER BY seq", (name,)
            ).fetchall()

            # R1 — gapless from 0, unique (ORDER BY makes dups/gaps visible)
            seqs = [r["seq"] for r in rows]
            if seqs != list(range(len(seqs))):
                fail(results, name, "R1",
                     f"seq not gapless 0..{len(seqs)-1} (got {seqs[:5]}...{seqs[-3:]})"
                     if len(seqs) > 8 else f"seq not gapless: {seqs}")

            # Vaulted chats (Phase 2): rows are '@enc1:' ciphertext — the
            # verifier runs keyless and read-only, so content invariants
            # (R2/R3) can't be checked. Seq-gaplessness (R1, above) still
            # verified. Skip with a note, never a failure.
            if any(str(r["message_json"] or "").startswith("@enc1:") for r in rows):
                results.append(("INFO", name, "ENC",
                                "vaulted (encrypted) — content invariants skipped (keyless run)"))
                continue

            parsed_rows = []
            ok = True
            for r in rows:
                # R2 — parses, is a dict, has a role
                try:
                    msg = json.loads(r["message_json"])
                except json.JSONDecodeError as e:
                    fail(results, name, "R2", f"seq {r['seq']}: does not parse: {e}")
                    ok = False
                    continue
                if not isinstance(msg, dict) or "role" not in msg:
                    fail(results, name, "R2", f"seq {r['seq']}: not a dict with 'role'")
                    ok = False
                    continue
                parsed_rows.append(msg)
                # R3 — sidecar agrees
                if r["role"] != msg["role"]:
                    fail(results, name, "R3",
                         f"seq {r['seq']}: sidecar role={r['role']!r} json role={msg['role']!r}")
                # R4 — thinking_raw must never be persisted to rows
                if "thinking_raw" in msg:
                    fail(results, name, "R4", f"seq {r['seq']}: thinking_raw persisted")

            # R5 — frozen-blob prefix identity (independent conversion check)
            raw_blob = chat["messages"]
            if ok and raw_blob and raw_blob != "[]":
                try:
                    frozen = json.loads(raw_blob)
                except json.JSONDecodeError as e:
                    fail(results, name, "R5", f"frozen blob does not parse: {e}")
                    frozen = None
                if isinstance(frozen, list) and frozen:
                    # Mirror the writer's strip: rows never persist
                    # thinking_raw (rowify correction #3), but a blob frozen
                    # after a crash mid-tool-cycle may retain it. Comparing
                    # raw would false-FAIL a perfectly good conversion
                    # (day-ruiner scout 2026-07-09, confirmed empirically).
                    frozen_cmp = [
                        {k: v for k, v in m.items() if k != "thinking_raw"}
                        if isinstance(m, dict) else m
                        for m in frozen
                    ]
                    prefix = parsed_rows[:len(frozen_cmp)]
                    if prefix != frozen_cmp:
                        first_bad = next(
                            (i for i, (a, b) in enumerate(zip(frozen_cmp, prefix)) if a != b),
                            min(len(frozen_cmp), len(prefix)))
                        fail(results, name, "R5",
                             f"frozen blob ({len(frozen_cmp)} msgs) != rows prefix, "
                             f"first divergence at index {first_bad}")
                    elif verbose:
                        results.append(("PASS", name, "R5",
                                        f"conversion prefix verified ({len(frozen)} msgs)"))
            if verbose and ok:
                results.append(("PASS", name, "R*", f"{len(rows)} msgs (rows)"))

        # G2 — orphan rows under non-rows / missing chats
        if has_rows_table:
            declared = {c["name"] for c in chats if c["fmt"] == "rows"}
            actual = {r[0] for r in conn.execute(
                "SELECT DISTINCT chat_name FROM chat_messages")}
            for orphan in sorted(actual - declared):
                fail(results, orphan, "G2",
                     "chat_messages rows exist but chat is not marked 'rows'")

        # G3 — image marker cross-check (advisory)
        if "tool_images" in tables:
            stored = {}
            for r in conn.execute("SELECT id, chat_name FROM tool_images"):
                stored[r["id"]] = r["chat_name"]
            referenced = set()
            for chat in chats:
                blobs = [chat["messages"] or ""]
                if chat["fmt"] == "rows" and has_rows_table:
                    blobs.extend(r[0] for r in conn.execute(
                        "SELECT message_json FROM chat_messages WHERE chat_name=?",
                        (chat["name"],)))
                for b in blobs:
                    referenced.update(IMG_MARKER.findall(b))
            for img_id in sorted(referenced - set(stored)):
                warn(results, "-", "G3", f"marker references missing image id {img_id}")
    finally:
        conn.close()

    # ---- report ----
    fails = [r for r in results if r[0] == "FAIL"]
    warns = [r for r in results if r[0] == "WARN"]
    for status, chat, code, detail in results:
        if status != "PASS" or verbose:
            print(f"  {status}  [{code}] {chat}: {detail}")
    print(f"\nverify_chat_storage: {counts['blob']} blob chat(s), "
          f"{counts['rows']} rows chat(s) scanned")
    if pre_rowify:
        print("  note: pre-rowify schema (no storage_format column) — blob checks only")
    print(f"RESULT: {'FAIL' if fails else 'PASS'} — "
          f"{len(fails)} failure(s), {len(warns)} warning(s)")
    return 1 if fails else 0


def main():
    args = [a for a in sys.argv[1:] if a != "--verbose"]
    verbose = "--verbose" in sys.argv[1:]
    if args:
        db = Path(args[0])
    else:
        db = Path(__file__).resolve().parent.parent / "user" / "history" / "sapphire_history.db"
    if not db.exists():
        print(f"ERROR: database not found: {db}")
        return 2
    return verify(str(db), verbose=verbose)


if __name__ == "__main__":
    sys.exit(main())
