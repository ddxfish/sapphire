#!/usr/bin/env python3
"""
Malbolge — Level 5 scope bleed gauntlet (live LLM).

Fires 5 concurrent continuity tasks, each with a unique scope, each
instructing the LLM to save a memory + search for it. Then verifies
no canary leaked into another scope. Full cleanup after.

Usage:
    python tools/malbolge.py <password>

Requires Sapphire running on localhost:8073.
"""
import argparse
import json
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://localhost:8073"
N = 5
PROJECT = Path(__file__).parent.parent
MEM_DB = PROJECT / "user" / "memory.db"
KNOW_DB = PROJECT / "user" / "knowledge.db"
TAG = "malbolge"  # prefix for all test data — makes cleanup easy


def login(password):
    s = requests.Session()
    s.verify = False

    # GET login page → extract CSRF + establish session
    r = s.get(f"{BASE}/login")
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', r.text)
    if not m:
        print("FAIL: couldn't find CSRF token on login page")
        sys.exit(1)
    csrf = m.group(1)

    # POST login
    r = s.post(f"{BASE}/login", data={"password": password, "csrf_token": csrf},
               allow_redirects=False)
    if r.status_code not in (302, 303) or '/login' in r.headers.get('Location', '/login'):
        print(f"FAIL: login failed (status {r.status_code})")
        sys.exit(1)

    # The CSRF token is now in the session cookie. We need to send it
    # as X-CSRF-Token header on all state-changing requests. Fetch any
    # page to get the token established, then read it from the session.
    # The token IS the csrf value we already have (it persists in session).
    s.headers['X-CSRF-Token'] = csrf
    print(f"  Logged in.")
    return s


def create_tasks(s):
    """Create N tasks, each with unique scopes and a tool-calling instruction."""
    task_ids = []
    for i in range(N):
        scope = f"{TAG}_{i}"
        msg = (
            f"You MUST call these tools in this exact order. Do not skip any.\n"
            f"1. Call save_memory with content: 'canary_{i}_{TAG}_verification_data'\n"
            f"2. Call search_memory with query: 'canary_{i}'\n"
            f"3. Tell me what you found. Include the word 'done' in your response."
        )
        task_data = {
            "name": f"{TAG}_task_{i}",
            "type": "task",
            "enabled": True,
            "schedule": "0 0 31 2 *",  # Feb 31 = never fires via cron
            "toolset": "all",
            "prompt": "agent",
            "max_tool_rounds": 5,
            "initial_message": msg,
            "tts_enabled": False,
            "memory_scope": scope,
            "knowledge_scope": scope,
            "people_scope": scope,
            "goal_scope": "none",
            "email_scope": "none",
            "bitcoin_scope": "none",
            "gcal_scope": "none",
            "telegram_scope": "none",
            "discord_scope": "none",
            "inject_datetime": False,
            "delete_after_run": False,
        }
        r = s.post(f"{BASE}/api/continuity/tasks", json=task_data)
        if r.status_code != 200:
            print(f"  FAIL creating task {i}: {r.status_code} {r.text[:200]}")
            continue
        data = r.json()
        tid = data.get("task_id") or data.get("id")
        if isinstance(tid, dict):
            tid = tid.get("id")
        task_ids.append(tid)
        print(f"  Task {i}: {tid[:12]}... (scope={scope})")

    return task_ids


def fire_all(s, task_ids):
    """Fire all tasks simultaneously from N threads."""
    barrier = threading.Barrier(len(task_ids))
    results = {}
    errors = []

    def fire(idx, tid):
        try:
            barrier.wait(timeout=10)
            start = time.time()
            r = s.post(f"{BASE}/api/continuity/tasks/{tid}/run", timeout=120)
            elapsed = time.time() - start
            data = r.json() if r.status_code == 200 else {"error": r.text[:200]}
            results[idx] = {"status": r.status_code, "elapsed": elapsed, **data}
        except Exception as e:
            errors.append(f"Task {idx}: {e}")

    threads = [threading.Thread(target=fire, args=(i, tid))
               for i, tid in enumerate(task_ids)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=180)

    if errors:
        print(f"  Errors during fire: {errors}")

    for idx in sorted(results):
        r = results[idx]
        ok = r.get("success", False)
        elapsed = r.get("elapsed", 0)
        status = "PASS" if ok else "FAIL"
        responses = r.get("responses", [])
        print(f"  Task {idx}: {status} ({elapsed:.1f}s, {len(responses)} responses)")
        for resp in responses[:2]:
            # Show the actual LLM response, not just the input
            if isinstance(resp, dict):
                output = resp.get('response', resp.get('output', ''))
                if output:
                    print(f"           LLM: {str(output)[:150].replace(chr(10), ' ')}")
                else:
                    print(f"           raw: {json.dumps(resp, default=str)[:200]}")

    return results


def verify_no_bleed():
    """Check memory DB: each canary must be in its scope ONLY."""
    if not MEM_DB.exists():
        print("  WARN: memory DB not found, skipping verification")
        return True

    conn = sqlite3.connect(MEM_DB)
    all_good = True

    for i in range(N):
        canary = f"canary_{i}_{TAG}"
        expected_scope = f"{TAG}_{i}"

        # Check canary exists in expected scope
        row = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE content LIKE ? AND scope = ?",
            (f"%{canary}%", expected_scope)
        ).fetchone()
        found = row[0] if row else 0

        # Check canary doesn't exist in ANY other scope
        bleed = conn.execute(
            "SELECT scope FROM memories WHERE content LIKE ? AND scope != ? AND scope LIKE ?",
            (f"%{canary}%", expected_scope, f"{TAG}%")
        ).fetchall()

        if found == 0:
            print(f"  Task {i}: canary not saved (LLM may not have called save_memory)")
        elif bleed:
            leaked_to = [r[0] for r in bleed]
            print(f"  Task {i}: *** BLEED DETECTED *** leaked to {leaked_to}")
            all_good = False
        else:
            print(f"  Task {i}: CLEAN (canary in {expected_scope} only)")

    conn.close()
    return all_good


def cleanup(s, task_ids):
    """Delete test tasks + test memories from DB."""
    # Delete tasks
    for tid in task_ids:
        try:
            s.delete(f"{BASE}/api/continuity/tasks/{tid}")
        except Exception:
            pass
    print(f"  Deleted {len(task_ids)} tasks")

    # Clean memory DB
    if MEM_DB.exists():
        conn = sqlite3.connect(MEM_DB)
        deleted = conn.execute(
            "DELETE FROM memories WHERE scope LIKE ?", (f"{TAG}%",)
        ).rowcount
        # Clean scopes too
        conn.execute("DELETE FROM memory_scopes WHERE name LIKE ?", (f"{TAG}%",))
        conn.commit()
        conn.close()
        print(f"  Cleaned {deleted} test memories from DB")

    # Clean knowledge DB
    if KNOW_DB.exists():
        conn = sqlite3.connect(KNOW_DB)
        conn.execute("DELETE FROM people WHERE scope LIKE ?", (f"{TAG}%",))
        conn.execute("DELETE FROM knowledge_entries WHERE tab_id IN "
                     "(SELECT id FROM knowledge_tabs WHERE scope LIKE ?)", (f"{TAG}%",))
        conn.execute("DELETE FROM knowledge_tabs WHERE scope LIKE ?", (f"{TAG}%",))
        conn.execute("DELETE FROM knowledge_scopes WHERE name LIKE ?", (f"{TAG}%",))
        conn.execute("DELETE FROM people_scopes WHERE name LIKE ?", (f"{TAG}%",))
        conn.commit()
        conn.close()
        print(f"  Cleaned knowledge/people test data")


def main():
    parser = argparse.ArgumentParser(description="Malbolge — Level 5 scope bleed gauntlet")
    parser.add_argument("password", help="Sapphire login password")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  MALBOLGE — 5 concurrent LLM tasks, 5 scopes")
    print(f"{'='*60}\n")

    print("[1/5] Login")
    s = login(args.password)

    print(f"\n[2/5] Creating {N} tasks")
    task_ids = create_tasks(s)
    if len(task_ids) < N:
        print(f"  WARN: only {len(task_ids)}/{N} tasks created")
    if not task_ids:
        print("  ABORT: no tasks created")
        return

    print(f"\n[3/5] Firing all {len(task_ids)} simultaneously...")
    results = fire_all(s, task_ids)

    print(f"\n[4/5] Verifying scope isolation")
    clean = verify_no_bleed()

    print(f"\n[5/5] Cleanup")
    cleanup(s, task_ids)

    print(f"\n{'='*60}")
    if clean:
        print("  RESULT: NO BLEED DETECTED")
    else:
        print("  RESULT: *** SCOPE BLEED FOUND ***")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
