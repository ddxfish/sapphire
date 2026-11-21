# compress.py - Chat history compression (Chat Manager v1b)
#
# Two modes, shared plumbing (tmp/chat-manager.md):
#   whole   — everything except the kept tail becomes ONE summary pair.
#             Internally map-reduce: chunk → partial summaries → merge.
#   chunked — each ~CHUNK_INPUT_TOKENS span of history becomes one summary
#             pair IN PLACE, chronological chain preserved.
#   daily   — /compact-daily: one labelled pair per day for the last
#             COMPACT_DAILY_DAYS calendar days (days start at DAY_START_HOUR);
#             earlier runs' pairs pass through, older days roll off, and today
#             stays raw until it ends (one pair per day, never a split day).
# target_tokens is the approximate total size of the compressed output
# (whole, chunked) or of each day's summary (daily).
#
# One job at a time, module-level state, thread-run: a local model chewing a
# big chat takes minutes and must not hold an HTTP worker. Routes 409 while
# running; the UI polls get_job_status().

import json
import logging
import os
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.chat.history import count_message_tokens, count_tokens, turn_spans
from core.event_bus import publish, Events

logger = logging.getLogger(__name__)

CHUNK_INPUT_TOKENS = 6000      # turn-snapped input span per summarize call
PARTIAL_SUMMARY_TOKENS = 1000  # per-chunk budget for whole-mode partials
MIN_PAIR_TOKENS = 300          # floor per summary pair in chunked mode
REQUEST_TIMEOUT = 600          # background job — generous beats truncated
# Hard per-call input ceiling. A chunk can exceed CHUNK_INPUT_TOKENS when a
# single turn is huge (one 30k-token tool result), and whole-mode merge input
# grows with chat length — both get middle-truncated to this before any LLM
# call, so no call can blow a destination model's context window. The chat
# itself is NEVER touched until every call has succeeded (write-last).
INPUT_CEILING_TOKENS = 12000

_job_lock = threading.Lock()
_job: Dict[str, Any] = {"running": False, "done": False}


# ── text plumbing ──────────────────────────────────────────────────────────

def _text_of(content) -> str:
    """Flatten message content (str or multimodal list) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") in ("text", "file"):
                    parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p)
    return str(content or "")


def _speaker_label(m: Dict[str, Any], persona: str, room: bool) -> str:
    """Who a transcript line belongs to. A room's rows name their speaker. In
    a plain chat, ASSISTANT is the persona writing the summary ("I"), and a
    reply stamped with another persona keeps that persona's name. A user row
    the person did not write (origin: a task, cadence, an agent, the MCP
    door) says so: labelled USER, the summary would tell them "you asked me
    to". Such a row's own text usually names its sender."""
    role = m.get("role", "?")
    if room and role in ("user", "assistant") and m.get("name"):
        return str(m["name"])
    if role == "user" and m.get("origin"):
        kind, _, name = str(m["origin"]).partition(":")
        if kind == "task":
            return f"SCHEDULED TASK ({name})" if name else "SCHEDULED TASK"
        return f"NOT THE USER ({m['origin']})"
    if role == "assistant" and persona and m.get("persona") not in (None, "", persona):
        return str(m["persona"])
    return role.upper()


def _keyed_calls(msgs: List[Dict[str, Any]]) -> set:
    """Ids of the tool calls made with a private_key. What such a call read is
    kept behind that key; a summary carries no key, and a day's summary is
    kept in memory for every reader of the scope (2026-11-20). Arguments that
    cannot be read but name the key count as keyed."""
    ids = set()
    for m in msgs:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            if not isinstance(tc, dict) or not tc.get("id"):
                continue
            args = (tc.get("function") or {}).get("arguments")
            try:
                parsed = json.loads(args) if isinstance(args, str) else args
                keyed = isinstance(parsed, dict) and bool(str(parsed.get("private_key") or "").strip())
            except ValueError:
                keyed = "private_key" in str(args)
            if keyed:
                ids.add(tc["id"])
    return ids


def render_transcript(msgs: List[Dict[str, Any]], persona: str = "", room: bool = False) -> str:
    """Render messages as a plain transcript for the summarizer. The result of
    a tool call made with a private_key is withheld: her own words about what
    she read stay, as ordinary chat text."""
    from core.chat.llm_errors import is_error_row
    keyed = _keyed_calls(msgs)
    lines = []
    for m in msgs:
        if is_error_row(m):
            continue      # a failed turn's words are for the person, not the summarizer
        role = m.get("role", "?")
        text = _text_of(m.get("content"))
        if role == "assistant" and m.get("tool_calls"):
            names = ", ".join(tc.get("function", {}).get("name", "?")
                              for tc in m["tool_calls"] if isinstance(tc, dict))
            text = f"{text}\n[called tools: {names}]".strip()
        elif role == "tool":
            if m.get("tool_call_id") in keyed:
                text = "[withheld: read with a private key]"
            text = f"({m.get('name', 'tool')} result) {text}"
        lines.append(f"{_speaker_label(m, persona, room)}: {text}")
    return "\n\n".join(lines)


def chunk_by_turns(msgs: List[Dict[str, Any]],
                   budget: int = CHUNK_INPUT_TOKENS) -> List[List[Dict[str, Any]]]:
    """Group messages into chunks of <= ~budget input tokens, snapped to turn
    boundaries. A single turn over budget becomes its own chunk (never split
    a tool chain)."""
    chunks, current, current_tokens = [], [], 0
    for start, end in turn_spans(msgs):
        turn = msgs[start:end]
        turn_tokens = sum(count_message_tokens(m.get("content")) for m in turn)
        if current and current_tokens + turn_tokens > budget:
            chunks.append(current)
            current, current_tokens = [], 0
        current.extend(turn)
        current_tokens += turn_tokens
    if current:
        chunks.append(current)
    return chunks


def day_label(day: date) -> str:
    """Monday, November 2, 2026 (no %-d: Windows strftime has no such flag)."""
    return f"{day:%A, %B} {day.day}, {day.year}"


def _summary_pair(summary: str, source_msgs: List[Dict[str, Any]],
                  persona: str = "", day: Optional[date] = None) -> List[Dict[str, Any]]:
    """Build the synthetic user/assistant pair that replaces source_msgs.
    Timestamp inherits from the newest compressed message so the visual
    chronology holds; `compressed` flags let the UI badge the seam; `persona`
    gives the summary the face of whoever it speaks as. `day` makes it one
    day's summary: the date she reads, and compressed_day for the next run."""
    ts = next((m.get("timestamp") for m in reversed(source_msgs)
               if m.get("timestamp")), None) or datetime.now().isoformat()
    stamp = {"timestamp": ts, "compressed": True,
             "compressed_at": datetime.now().isoformat()}
    first_ts = next((m.get("timestamp") for m in source_msgs
                     if m.get("timestamp")), "?")
    marker = (f"[Compressed history: {len(source_msgs)} messages "
              f"({str(first_ts)[:10]} to {str(ts)[:10]}) summarized below]")
    if day:
        stamp["compressed_day"] = day.isoformat()
        marker = f"[Day summary: {day_label(day)} ({len(source_msgs)} messages)]"
    reply = {"role": "assistant", "content": summary, **stamp}
    if persona:
        reply["persona"] = persona
    return [{"role": "user", "content": marker, **stamp}, reply]


# ── LLM plumbing ───────────────────────────────────────────────────────────

def make_provider(provider_key: str, model: str = "", conversation: str = None,
                  private: bool = False):
    """One-shot provider for summarize calls via the ONE resolver (2026-09-21):
    no health probe (the summary call is the signal). The route refuses
    auto/none; /compact-daily rides the chat's own pin, auto included, and
    private holds that to local providers. Raises ValueError on an unknown,
    unbuildable or refused provider."""
    from core.chat.llm_providers.resolve import resolve, ProviderRefused
    try:
        return resolve(provider_key, model, private=private, timeout=REQUEST_TIMEOUT,
                       health='skip', conversation=conversation).provider
    except ProviderRefused as e:
        raise ValueError(str(e)) from e


# What to keep and whose voice: the only part a person edits. The frames below
# stay in code, so an edit can never lose the transcript (a summary of nothing
# would pass every guard and replace real history). Instructions go in as a
# .format() VALUE, so a brace typed in them is just a brace.
DEFAULT_INSTRUCTIONS = (
    "Preserve concrete facts, names, decisions made, emotional beats, running "
    "jokes, open threads, and anything either participant would need to "
    "remember later; drop repetition. Write chronologically in the past tense, "
    "in the first person as the ASSISTANT, as the ASSISTANT's own memory of "
    "the conversation: \"I\" always means the ASSISTANT and \"you\" always "
    "means the USER. Anyone else keeps their name."
)

# A room has no one "I". Fixed, not editable: the box is for one-voice chats.
ROOM_INSTRUCTIONS = (
    "Preserve concrete facts, names, decisions made, emotional beats, running "
    "jokes, open threads, and anything a participant would need to remember "
    "later; drop repetition. Write chronologically in the third person past "
    "tense, attributing what was said and done to each speaker by the name "
    "the transcript gives them."
)

_CHUNK_FRAME = (
    "You are compressing a saved chat history. Summarize the conversation "
    "excerpt below into a dense summary of at most about {budget} tokens."
    "{voice}\n\n{instructions}\n\n"
    "Output ONLY the summary — no preamble, no headers.\n\n"
    "EXCERPT:\n{text}"
)

_MERGE_FRAME = (
    "Below are sequential partial summaries of ONE long conversation. Merge "
    "them into a single chronological summary of at most about {budget} "
    "tokens, keeping the perspective they are written in.{voice}\n\n"
    "{instructions}\n\n"
    "Output ONLY the summary — no preamble, no headers.\n\n"
    "PARTIAL SUMMARIES:\n{text}"
)

# A day's summary only. Two parts. _DAILY_CLAUSE is mechanics and stays in
# code: what the size means and what the transcript's labels mean. An edit can
# never lose those. DAILY_INSTRUCTIONS is how she writes a day and what she
# leaves out: the person edits it (Settings > Tools > Daily Compact), stored
# like the instructions above and kept only while it differs from the default.
_DAILY_CLAUSE = (
    " That size is a ceiling, not a goal: write only as much as the day needs, "
    "and a quiet day may take a few hundred tokens. Lines labelled SCHEDULED "
    "TASK are automatic jobs, not the USER speaking."
)

DAILY_INSTRUCTIONS = (
    "Write tight prose in the first person: whatever is kept, say it once, in "
    "plain words, without padding, and never in shorthand or abbreviations. "
    "Give each topic its own paragraph and name its people and things in it, "
    "not \"he\" or \"that\": a paragraph may later be read on its own. Write "
    "dates as dates (Saturday, November 21), not \"tomorrow\". Leave a "
    "scheduled task that found or changed nothing out entirely, without a "
    "word that it ran, and keep one only when something came of it. Do not "
    "recount tool housekeeping either (that a memory was saved, searched or "
    "read): keep what was learned or decided, not that it was stored."
)

INSTRUCTIONS_PATH = Path(__file__).parent.parent.parent / "user" / "prompts" / "compress_instructions.txt"
DAILY_INSTRUCTIONS_PATH = INSTRUCTIONS_PATH.with_name("compact_daily_instructions.txt")
MAX_INSTRUCTIONS_CHARS = 8000


def _instructions_file(daily: bool):
    """(path, default) of the editable text: the compress instructions, or
    the day-summary ones. Read at call time: tests point the paths elsewhere."""
    return (DAILY_INSTRUCTIONS_PATH, DAILY_INSTRUCTIONS) if daily else (INSTRUCTIONS_PATH, DEFAULT_INSTRUCTIONS)


def load_instructions(daily: bool = False) -> str:
    """The person's override, else the default. An unreadable override raises
    (the job fails loud) rather than quietly summarizing in the wrong voice."""
    path, default = _instructions_file(daily)
    try:
        # utf-8-sig: Notepad's "UTF-8 with BOM" would otherwise put an
        # invisible character at the head of the prompt.
        text = path.read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        return default
    except UnicodeDecodeError:
        raise ValueError(f"{path.name} in user/prompts is not saved as UTF-8 (an editor saved it as ANSI). "
                         f"Save it as UTF-8, or reset the text in the app.") from None
    return text or default


def save_instructions(text: str, daily: bool = False) -> None:
    """Keep the override only while it differs from the default, so a later
    fix to the default still reaches everyone who never edited it."""
    path, default = _instructions_file(daily)
    text = (text or "").strip()
    if len(text) > MAX_INSTRUCTIONS_CHARS:
        raise ValueError(f"Instructions must be under {MAX_INSTRUCTIONS_CHARS} characters")
    if not text or text == default:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    from core.fs_utils import replace_with_retry     # Windows: an indexer or scanner may hold the file a moment
    replace_with_retry(tmp, path)


def _guide(instructions: str = DEFAULT_INSTRUCTIONS, persona: str = "") -> Dict[str, str]:
    """The frame fields every summarize call shares."""
    return {"instructions": instructions,
            "voice": f" The ASSISTANT is {persona}." if persona else ""}


def cap_text(text: str, ceiling: int = INPUT_CEILING_TOKENS) -> str:
    """Middle-truncate text to ~ceiling tokens. Head and tail survive (the
    opening frames the scene, the end links to what follows); the omission
    is declared so the summarizer knows it saw a gap."""
    total = count_tokens(text)
    if total <= ceiling:
        return text
    # Proportional char split: 60% head, 40% tail of the allowed window
    keep_chars = int(len(text) * (ceiling / total))
    head = text[:int(keep_chars * 0.6)]
    tail = text[-int(keep_chars * 0.4):]
    omitted = total - ceiling
    return f"{head}\n[... ~{omitted} tokens omitted for length ...]\n{tail}"


def _summarize(provider, frame: str, text: str, budget: int, guide: Dict[str, str]) -> str:
    prompt = frame.format(budget=budget, text=cap_text(text), **guide)
    resp = provider.chat_completion(
        [{"role": "user", "content": prompt}],
        generation_params={"max_tokens": int(budget * 1.3) + 200,
                           "temperature": 0.3})
    out = (resp.content or "").strip()
    # Local models sometimes leak reasoning into content despite extraction.
    from core import think
    out = think.strip(out)
    if not out:
        raise RuntimeError("Summarizer returned empty content")
    return out


def _merge_partials(provider, partials: List[str], target_tokens: int,
                    on_progress=None, guide: Optional[Dict[str, str]] = None) -> str:
    """Reduce partial summaries to ONE, in rounds. A single merge call's
    input never exceeds INPUT_CEILING_TOKENS — on a very long chat the
    partials themselves outgrow one call, so group → merge → repeat until
    one remains. Each round strictly shrinks the list, so this terminates."""
    guide = guide or _guide()
    rnd = 0
    while len(partials) > 1:
        rnd += 1
        groups, cur, cur_tokens = [], [], 0
        for p in partials:
            t = count_tokens(p)
            if cur and cur_tokens + t > INPUT_CEILING_TOKENS:
                groups.append(cur)
                cur, cur_tokens = [], 0
            cur.append(p)
            cur_tokens += t
        if cur:
            groups.append(cur)
        merged = []
        for gi, g in enumerate(groups):
            if len(g) == 1:
                merged.append(g[0])  # lone partial rides to the next round free
                continue
            if on_progress:
                on_progress(f"merging (round {rnd}, {gi + 1}/{len(groups)})")
            merged.append(_summarize(provider, _MERGE_FRAME,
                                     "\n\n---\n\n".join(g), target_tokens, guide))
        if len(merged) >= len(partials):
            # Every group was a singleton — can't shrink (pathological
            # ceiling). Force-merge the lot; cap_text guards the input.
            if on_progress:
                on_progress(f"merging (final round {rnd})")
            return _summarize(provider, _MERGE_FRAME,
                              "\n\n---\n\n".join(partials), target_tokens, guide)
        partials = merged
    return partials[0]


def compress_messages(head: List[Dict[str, Any]], mode: str, provider,
                      target_tokens: int, on_progress=None,
                      instructions: str = DEFAULT_INSTRUCTIONS, persona: str = "",
                      room: bool = False, day: Optional[date] = None,
                      daily_instructions: str = DAILY_INSTRUCTIONS) -> List[Dict[str, Any]]:
    """Compress `head` into synthetic summary pairs. Pure of storage —
    testable with a mock provider. on_progress(str) narrates for job status.
    day (whole mode): the pair is that day's summary, written by the day
    wording (daily_instructions) on top of `instructions`."""
    chunks = chunk_by_turns(head, CHUNK_INPUT_TOKENS)  # module global: patchable
    if not chunks:
        return []
    guide = _guide(instructions, persona)
    if day:
        guide["voice"] += (f" The conversation took place on {day_label(day)}." + _DAILY_CLAUSE
                           + " " + (daily_instructions or DAILY_INSTRUCTIONS).strip())
    if mode == "chunked":
        per = max(MIN_PAIR_TOKENS, target_tokens // len(chunks))
        pairs = []
        for i, chunk in enumerate(chunks):
            if on_progress:
                on_progress(f"chunk {i + 1}/{len(chunks)}")
            summary = _summarize(provider, _CHUNK_FRAME,
                                 render_transcript(chunk, persona, room), per, guide)
            pairs.extend(_summary_pair(summary, chunk, persona))
        return pairs
    # whole: map-reduce → one pair
    if len(chunks) == 1:
        if on_progress:
            on_progress("summarizing")
        summary = _summarize(provider, _CHUNK_FRAME,
                             render_transcript(chunks[0], persona, room), target_tokens, guide)
    else:
        partials = []
        for i, c in enumerate(chunks):
            if on_progress:
                on_progress(f"chunk {i + 1}/{len(chunks)}")
            partials.append(_summarize(provider, _CHUNK_FRAME,
                                       render_transcript(c, persona, room),
                                       min(PARTIAL_SUMMARY_TOKENS, target_tokens), guide))
        summary = _merge_partials(provider, partials, target_tokens, on_progress, guide)
    return _summary_pair(summary, head, persona, day)


# ── daily: one labelled pair per day, the last N days ──────────────────────

def day_of(ts, start_hour: int = 0) -> Optional[date]:
    """The day a timestamp belongs to when days begin at start_hour (a 2 am
    turn is still last night). Unreadable → None."""
    try:
        t = datetime.fromisoformat(str(ts))
        if t.tzinfo:
            t = t.astimezone().replace(tzinfo=None)     # pre-1970 raises OSError on Windows
        return (t - timedelta(hours=start_hour)).date()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _stamped_day(rows) -> Optional[date]:
    """A daily pair from an earlier run: exactly its two rows carry the day."""
    try:
        return date.fromisoformat(rows[0]["compressed_day"])
    except (KeyError, TypeError, ValueError):
        return None


def daily_plan(head: List[Dict[str, Any]], days: int, start_hour: int,
               now: Optional[datetime] = None) -> Dict[str, Any]:
    """Sort head into what a daily compact keeps and what it summarizes. Pure.

    The window is the last `days` calendar days, today included. Pairs an
    earlier run wrote pass through untouched while their day is inside it.
    Every other turn (an old whole/chunked summary too) is raw and lands on
    its day by its first timestamp, else the turn before's day. Today's raw
    turns are never summarized: a day still running would get a second pair
    from the next run (Sapphire, 2026-11-06). Anything older than the window
    rolls off, raw or summarized.
    kept: [(day, index, rows)]  raw: {day: (index, rows)}  today: [(day, index, rows)]
    dropped: turns."""
    today = day_of((now or datetime.now()).isoformat(), start_hour)
    first = today - timedelta(days=max(1, days) - 1)
    kept, raw, today_rows, dropped, last = [], {}, [], 0, None
    for start, end in turn_spans(head):
        rows = head[start:end]
        day = _stamped_day(rows)
        if day is not None:
            if day >= first:
                kept.append((day, start, rows[:2]))
            else:
                dropped += 1
            last, rows, start = day, rows[2:], start + 2   # rows glued after a pair are raw
            if not rows:
                continue
        day = next((d for d in (day_of(m.get("timestamp"), start_hour) for m in rows) if d),
                   None) or last or today
        last = day
        if day < first:
            dropped += 1
            continue
        if day >= today:
            today_rows.append((day, start, rows))
            continue
        raw.setdefault(day, (start, []))[1].extend(rows)
    return {"kept": kept, "raw": raw, "today": today_rows, "dropped": dropped}


def daily_pairs(plan: Dict[str, Any], provider, target_tokens: int, on_progress=None,
                instructions: str = DEFAULT_INSTRUCTIONS, persona: str = "",
                room: bool = False, daily_instructions: str = DAILY_INSTRUCTIONS) -> List[Dict[str, Any]]:
    """One new pair per finished day of raw turns, merged in day order with
    the kept pairs and today's turns (raw, as they were)."""
    items = list(plan["kept"]) + list(plan.get("today") or [])
    days = sorted(plan["raw"].items())
    for i, (day, (start, rows)) in enumerate(days):
        if on_progress:
            on_progress(f"day {i + 1}/{len(days)}")
        items.append((day, start, compress_messages(
            rows, "whole", provider, target_tokens, instructions=instructions,
            persona=persona, room=room, day=day, daily_instructions=daily_instructions)))
    items.sort(key=lambda it: (it[0], it[1]))
    return [m for _, _, rows in items for m in rows]


# ── the job ────────────────────────────────────────────────────────────────

def _write_backup(session_manager, chat_name: str, exported: Dict[str, Any]) -> str:
    exports_dir = Path(session_manager.history_dir) / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = exports_dir / f"{chat_name}_{stamp}.json"
    path.write_text(json.dumps(exported, indent=2), encoding="utf-8")
    return str(path)


def _int_setting(config, key: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(getattr(config, key, default))))
    except (TypeError, ValueError):
        return default


def _compress_chat(session_manager, chat_name: str, mode: str,
                   provider_key: str, model: str, target_tokens: int,
                   keep_last_turns: int, backup: bool,
                   on_progress=None, persona: str = "") -> Dict[str, Any]:
    exported = session_manager.export_chat(chat_name)
    if exported is None:
        raise ValueError(f"Chat '{chat_name}' not found")
    msgs = exported["messages"]
    spans = turn_spans(msgs)
    keep_last = max(1, int(keep_last_turns))
    if len(spans) <= keep_last:
        raise ValueError("Nothing to compress — the whole chat fits inside "
                         "the kept tail")
    cut = spans[len(spans) - keep_last][0]
    head, tail = msgs[:cut], msgs[cut:]

    # A private chat's export is fully decrypted while the vault is open —
    # writing it to exports/ would park PLAINTEXT on disk that outlives the
    # next lock (the vault scrub only cleans at lock time). Unreadable
    # settings fail closed.
    try:
        chat_settings = session_manager.read_chat_settings(chat_name) or {}
        _private = bool(chat_settings.get("private_chat"))
    except Exception:
        chat_settings, _private = {}, True
    room = chat_settings.get("mode") == "room"
    instructions = ROOM_INSTRUCTIONS if room else load_instructions()
    # Read here, with the other text: an unreadable file fails the job before
    # the backup is written (it was read after, and left a backup every night).
    day_wording = load_instructions(daily=True) if mode == "daily" else DAILY_INSTRUCTIONS
    plan = None
    if mode == "daily":
        import config
        plan = daily_plan(head, _int_setting(config, "COMPACT_DAILY_DAYS", 5, 1, 365),
                          _int_setting(config, "DAY_START_HOUR", 4, 0, 23))
        if not plan["raw"] and not plan["dropped"]:
            raise ValueError("Nothing new to compact since the last run "
                             "(today stays as it is until it ends)")
    if backup and _private:
        logger.info(f"[COMPRESS] Skipping plaintext backup for private chat '{chat_name}'")
    backup_path = _write_backup(session_manager, chat_name, exported) if (backup and not _private) else None
    provider = make_provider(provider_key, model, conversation=chat_name,
                             private=_private)  # fail BEFORE any LLM spend
    if plan is not None:
        pairs = daily_pairs(plan, provider, int(target_tokens), on_progress=on_progress,
                            instructions=instructions, persona="" if room else persona, room=room,
                            daily_instructions=day_wording)
    else:
        pairs = compress_messages(head, mode, provider, int(target_tokens),
                                  on_progress=on_progress, instructions=instructions,
                                  persona="" if room else persona, room=room)

    # Fork 4A (2026-07-16): "compression" that grows the chat is refused —
    # chatty local models can summarize a small head into MORE tokens than
    # the original. Write-last means the chat is untouched by this abort.
    summary_tokens = sum(count_tokens(_text_of(p.get("content"))) for p in pairs)
    head_tokens = sum(count_tokens(_text_of(m.get("content"))) for m in head)
    if summary_tokens >= head_tokens:
        raise RuntimeError(
            f"Compression would grow the chat (summary ~{summary_tokens} tokens vs "
            f"~{head_tokens} in the compressed span) — aborted, nothing written. "
            f"Try a lower target or a terser model.")

    new_msgs = pairs + tail
    # expected_count + expected_digest: if anyone (heartbeat, daemon, the
    # operator) wrote to this chat during the minutes of LLM work, abort
    # instead of clobbering their turns — the job fails with a clear error,
    # the chat keeps them. The digest also catches equal-count mutations
    # (in-place edit, remove + regenerate) the count check is blind to.
    ok, err = session_manager.replace_messages(
        chat_name, new_msgs,
        expected_count=len(msgs),
        expected_digest=session_manager.messages_digest(msgs))
    if not ok:
        raise RuntimeError(err)
    session_manager._prune_orphaned_tool_images(chat_name)
    return {
        "chat": chat_name, "mode": mode,
        "messages_before": len(msgs), "messages_after": len(new_msgs),
        "summary_pairs": len(pairs) // 2,
        "summary_tokens_est": sum(count_tokens(_text_of(p.get("content")))
                                  for p in pairs),
        "backup_path": backup_path,
    }


def get_job_status() -> Dict[str, Any]:
    with _job_lock:
        return dict(_job)


def start_compress_job(session_manager, chat_name: str, *, mode: str,
                       provider_key: str, model: str, target_tokens: int,
                       keep_last_turns: int, backup: bool, persona: str = ""):
    """Start the one-at-a-time background compress. Returns (ok, error)."""
    global _job
    with _job_lock:
        if _job.get("running"):
            return False, (f"A compress job is already running "
                           f"(chat '{_job.get('chat')}')")
        _job = {"running": True, "done": False, "chat": chat_name,
                "mode": mode, "started": datetime.now().isoformat()}
    # One row in the notification center (core/notify.py). Never the chat's
    # name: a private chat's name stays off every tab's wire, and a failure's
    # words are kept generic for one.
    from core import notify as _notify
    try:
        _priv = bool((session_manager.read_chat_settings(chat_name) or {}).get("private_chat"))
    except Exception:
        _priv = True
    nid = _notify.begin(f"Compressing a chat ({mode})", key='compress', go={'view': 'chat-manage'})

    def _run():
        def on_progress(msg):
            with _job_lock:
                _job["progress"] = msg
            _notify.tick(nid, text=msg)

        try:
            report = _compress_chat(session_manager, chat_name, mode,
                                    provider_key, model, target_tokens,
                                    keep_last_turns, backup,
                                    on_progress=on_progress, persona=persona)
            with _job_lock:
                _job.update({"running": False, "done": True, "ok": True,
                             "result": report,
                             "finished": datetime.now().isoformat()})
            logger.info(f"Compress '{chat_name}' done: {report}")
            _notify.finish(nid, title=f"Chat compressed: {report.get('messages_before')} to "
                                      f"{report.get('messages_after')} messages")
            publish(Events.CHAT_COMPRESSED, {"chat_name": chat_name,
                                             "report": report})
        except Exception as e:
            logger.exception(f"Compress '{chat_name}' failed")
            _notify.finish(nid, failed=True, title='Chat compression failed',
                           body='See the log.' if _priv else str(e))
            with _job_lock:
                _job.update({"running": False, "done": True, "ok": False,
                             "error": str(e),
                             "finished": datetime.now().isoformat()})

    threading.Thread(target=_run, daemon=True,
                     name=f"compress-{chat_name}").start()
    return True, ""
