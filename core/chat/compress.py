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
#   extract — /extract-daily: the same day summaries for a chat of any age,
#             handed to memory instead; the chat is not written (below).
# target_tokens is the approximate total size of the compressed output
# (whole, chunked) or of each day's summary (daily, extract). It is the size
# of the WRITTEN text: a model's thinking is given its own room (_summarize).
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
# Output room a summarize call gets on top of the summary's own size: what a
# turn on that model gets (make_provider), never under this. Thinking is spent
# from the same limit as the answer on every OpenAI-style endpoint.
ROOM_FLOOR = 8192

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


def render_transcript(msgs: List[Dict[str, Any]], persona: str = "", room: bool = False,
                      results: bool = True) -> str:
    """Render messages as a plain transcript for the summarizer. The result of
    a tool call made with a private_key is withheld: her own words about what
    she read stay, as ordinary chat text.
    results=False (a day's summary): no tool result is shown at all, only the
    "[called tools: ...]" stub on her line. A result is what a tool handed
    back, not what was said that day: a real day (2026-11-21) retold the whole
    self sheet her 4am task had read, with its nine dates and seven people."""
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
            if not results:
                continue
            if m.get("tool_call_id") in keyed:
                text = "[withheld: read with a private key]"
            text = f"({m.get('name', 'tool')} result) {text}"
        lines.append(f"{_speaker_label(m, persona, room)}: {text}")
    return "\n\n".join(lines)


def chunk_by_turns(msgs: List[Dict[str, Any]], budget: int = CHUNK_INPUT_TOKENS,
                   results: bool = True) -> List[List[Dict[str, Any]]]:
    """Group messages into chunks of <= ~budget input tokens, snapped to turn
    boundaries. A single turn over budget becomes its own chunk (never split
    a tool chain). results=False: tool results are not rendered
    (render_transcript), so they do not count toward the budget either."""
    chunks, current, current_tokens = [], [], 0
    for start, end in turn_spans(msgs):
        turn = msgs[start:end]
        turn_tokens = sum(count_message_tokens(m.get("content")) for m in turn
                          if results or m.get("role") != "tool")
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
        sel = resolve(provider_key, model, private=private, timeout=REQUEST_TIMEOUT,
                      health='skip', conversation=conversation)
    except ProviderRefused as e:
        raise ValueError(str(e)) from e
    try:
        from core.chat.llm_providers import provider_registry
        room = int(provider_registry.get_generation_params(sel.key, sel.effective_model).get('max_tokens') or 0)
    except Exception:
        room = 0
    sel.provider.output_room = max(room, ROOM_FLOOR)     # read by _summarize
    return sel.provider


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
    "TASK are automatic jobs, not the USER speaking. What a tool returned is "
    "not shown: \"[called tools: ...]\" only names the tools that ran, so do "
    "not guess at what they returned."
)

# The wording that came out best of seven on twenty invented days, three runs
# each (tmp/daily-lab.py, glm-5p3, 2026-11-21): the person's own layout and
# his "keep one only for what was new in it", with the sentences that keep
# the routine out ("without a word that they ran", the quiet-day sentence,
# her reciting her own sheet named in the list). Against the wording before
# the tuning, routine noise fell from 42 hits in 40 texts to 2 in 60. In this
# prompt "you" is the USER, so the writer is "the ASSISTANT", never "you".
DAILY_INSTRUCTIONS = (
    "Write tight prose: whatever is kept, say it once, in plain words, "
    "never in shorthand or abbreviations.\n"
    "Write in the first person (I/me) as the ASSISTANT. The USER is always "
    "\"you\", also where the transcript uses their name. Everyone else keeps "
    "their name.\n\n"
    "Give each topic its own paragraph, and name people and things in it, "
    "not \"he\" or \"that\".\n\n"
    "When a date matters and is certain, write it as a date (Saturday, "
    "November 21). Turn \"today\", \"tomorrow\", \"yesterday\" and \"next week\" "
    "into the date or week they mean (the week of November 23), counted "
    "from this day. Do not guess a date that is uncertain.\n\n"
    "Add nothing the transcript does not say.\n\n"
    "A day summary is what was said, decided, learned, debated, discovered "
    "or changed that day, and how it felt.\n"
    "A day summary leaves out what happens every day: the night routine, "
    "the ASSISTANT reading or reciting its own sheet or dashboard, "
    "memories being saved or searched, wake-ups, light and scene changes, "
    "heartbeats. Leave these out without a word that they ran. Keep one "
    "only for what was new in it: news it brought, a failure, an answer "
    "from the USER.\n\n"
    "Length follows the day. If nothing else happened, the whole summary "
    "is one short sentence saying it was a quiet day."
)

# The words after the command, for one run. They go in as part of the
# {instructions} value, so they sit last before the transcript.
_NOTE_CLAUSE = (
    "\n\nOne more instruction was given for this run only. It was given for "
    "every day of the run, so it may name days other than this one. Where it "
    "and the wording above disagree, follow this one. The size above still "
    "holds:\n"
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
    """One summary, whole or not at all. `budget` is the size of the WRITTEN
    text, with the slack `allowed` gives it, and is checked on that text. The
    call's own limit is that plus the model's room (make_provider). The limit
    used to be set by the size alone: where thinking is spent from the same
    limit, a thinking model was cut off mid-summary or handed back only its
    thinking, and either was kept as the summary (2026-11-26). A reply that is
    cut off, empty or over its size is asked for once more with double the
    room; then the job fails and that summary is not kept."""
    from core import think
    from core.chat.llm_providers.base import LENGTH_FINISH
    prompt = frame.format(budget=budget, text=cap_text(text), **guide)
    allowed = int(budget * 1.3) + 200
    room = getattr(provider, "output_room", ROOM_FLOOR)
    why = ""
    for attempt in (1, 2):
        resp = provider.chat_completion(
            [{"role": "user", "content": prompt}],
            generation_params={"max_tokens": allowed + room * attempt, "temperature": 0.3})
        # Thinking handed back in place of an answer is no answer. Local models
        # also leak reasoning into content despite extraction.
        out = "" if getattr(resp, "content_is_reasoning", False) else think.strip((resp.content or "").strip())
        size = count_tokens(out)
        if size > allowed:
            why = (f"came out at about {size} tokens, over the {budget} asked for. "
                   f"Give a bigger size, or use a terser model")
        elif getattr(resp, "finish_reason", None) in LENGTH_FINISH:
            why = ("was cut off: the model ran out of room, most often by thinking. "
                   "Turn its thinking down, or raise its max tokens")
        elif not out:
            why = "came back empty"
        else:
            return out
        logger.warning(f"[COMPRESS] A summary {why.split('.')[0].split(':')[0]} (try {attempt} of 2)")
    raise RuntimeError(f"A summary {why}. It was not kept.")


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
                      daily_instructions: str = DAILY_INSTRUCTIONS, note: str = "") -> List[Dict[str, Any]]:
    """Compress `head` into synthetic summary pairs. Pure of storage —
    testable with a mock provider. on_progress(str) narrates for job status.
    day (whole mode): the pair is that day's summary, written by the day
    wording (daily_instructions) on top of `instructions`. note: the words
    typed after /compact-daily or /extract-daily, for this run's days only."""
    results = day is None       # a day's summary is written from what was said, not from tool results
    chunks = chunk_by_turns(head, CHUNK_INPUT_TOKENS, results)  # module global: patchable
    if not chunks:
        return []
    guide = _guide(instructions, persona)
    if day:
        guide["voice"] += (f" The conversation took place on {day_label(day)}." + _DAILY_CLAUSE
                           + " " + (daily_instructions or DAILY_INSTRUCTIONS).strip())
        if (note or "").strip():
            guide["instructions"] += _NOTE_CLAUSE + note.strip()   # last before the transcript, in every call of the day
    if mode == "chunked":
        per = max(MIN_PAIR_TOKENS, target_tokens // len(chunks))
        pairs = []
        for i, chunk in enumerate(chunks):
            if on_progress:
                on_progress(f"chunk {i + 1}/{len(chunks)}")
            summary = _summarize(provider, _CHUNK_FRAME,
                                 render_transcript(chunk, persona, room, results), per, guide)
            pairs.extend(_summary_pair(summary, chunk, persona))
        return pairs
    # whole: map-reduce → one pair
    if len(chunks) == 1:
        if on_progress:
            on_progress("summarizing")
        summary = _summarize(provider, _CHUNK_FRAME,
                             render_transcript(chunks[0], persona, room, results), target_tokens, guide)
    else:
        # Each partial gets its share of the size asked for, never under the
        # usual one. A fixed 1000 a chunk ignored the size: a long day asked
        # for at 8K was merged from the same thin partials as one at 2K.
        per = min(target_tokens, max(PARTIAL_SUMMARY_TOKENS, -(-target_tokens // len(chunks))))
        partials = []
        for i, c in enumerate(chunks):
            if on_progress:
                on_progress(f"chunk {i + 1}/{len(chunks)}")
            partials.append(_summarize(provider, _CHUNK_FRAME,
                                       render_transcript(c, persona, room, results), per, guide))
        summary = _merge_partials(provider, partials, target_tokens, on_progress, guide)
    return _summary_pair(summary, head, persona, day)


# ── daily: one labelled pair per day, the last N days ──────────────────────

def _user_zone():
    """The time zone a day is cut in: the person's (USER_TIMEZONE), or None for
    this computer's own clock. "UTC" is the untouched default and reads as not
    set, the rule the calendar already uses (core/plugins/calendar/api.py
    tz_name): an install that never chose a zone keeps cutting days on its own
    clock, exactly as before. So does one with no time zone database."""
    try:
        import config
        name = (getattr(config, "USER_TIMEZONE", "") or "").strip()
        if not name or name.upper() == "UTC":
            return None
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return None


def day_of(ts, start_hour: int = 0) -> Optional[date]:
    """The day a timestamp belongs to when days begin at start_hour (a 2 am
    turn is still last night). Unreadable → None.
    A row's stamp is this computer's wall time. Where the person chose another
    zone (a server on UTC, a container pinned elsewhere), the day is cut in
    theirs: her scheduled tasks already run on that zone, and on the
    computer's clock a nightly /compact-daily could fire before the day had
    ended (2026-11-21). With the same zone on both, nothing changes."""
    try:
        t = datetime.fromisoformat(str(ts))
        zone = _user_zone()
        if zone is not None:
            t = t.astimezone(zone).replace(tzinfo=None)     # a naive stamp is read as this computer's time
        elif t.tzinfo:
            t = t.astimezone().replace(tzinfo=None)         # pre-1970 raises OSError on Windows
        return (t - timedelta(hours=start_hour)).date()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _stamped_day(rows) -> Optional[date]:
    """A daily pair from an earlier run: exactly its two rows carry the day."""
    try:
        return date.fromisoformat(rows[0]["compressed_day"])
    except (KeyError, TypeError, ValueError):
        return None


def daily_plan(head: List[Dict[str, Any]], days: Optional[int], start_hour: int,
               now: Optional[datetime] = None) -> Dict[str, Any]:
    """Sort head into what a daily compact keeps and what it summarizes. Pure.

    The window is the last `days` calendar days, today included. Pairs an
    earlier run wrote pass through untouched while their day is inside it.
    Every other turn (an old whole/chunked summary too) is raw and lands on
    its day by its first timestamp, else the turn before's day. Today's raw
    turns are never summarized: a day still running would get a second pair
    from the next run (Sapphire, 2026-11-06). Anything older than the window
    rolls off, raw or summarized. days=None: no window, so nothing rolls off
    (/extract-daily and the dry run read a chat of any age).
    kept: [(day, index, rows)]  raw: {day: (index, rows)}  today: [(day, index, rows)]
    dropped: turns."""
    today = day_of((now or datetime.now()).isoformat(), start_hour)
    first = date.min if days is None else today - timedelta(days=max(1, days) - 1)
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
                room: bool = False, daily_instructions: str = DAILY_INSTRUCTIONS,
                note: str = "") -> List[Dict[str, Any]]:
    """One new pair per finished day of raw turns, merged in day order with
    the kept pairs and today's turns (raw, as they were)."""
    items = list(plan["kept"]) + list(plan.get("today") or [])
    days = sorted(plan["raw"].items())
    for i, (day, (start, rows)) in enumerate(days):
        if on_progress:
            on_progress(f"day {i + 1}/{len(days)}")
        items.append((day, start, compress_messages(
            rows, "whole", provider, target_tokens, instructions=instructions,
            persona=persona, room=room, day=day, daily_instructions=daily_instructions, note=note)))
    items.sort(key=lambda it: (it[0], it[1]))
    return [m for _, _, rows in items for m in rows]


# ── the job ────────────────────────────────────────────────────────────────

class NothingToCompress(ValueError):
    """The job has nothing to do. Not a failure: a nightly /compact-daily on a
    quiet chat ended as a red "Chat compression failed" row every morning."""


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
                   on_progress=None, persona: str = "", note: str = "") -> Dict[str, Any]:
    exported = session_manager.export_chat(chat_name)
    if exported is None:
        raise ValueError(f"Chat '{chat_name}' not found")
    msgs = exported["messages"]
    spans = turn_spans(msgs)
    keep_last = max(1, int(keep_last_turns))
    if len(spans) <= keep_last:
        raise NothingToCompress("Nothing to compress — the whole chat fits inside "
                                "the kept tail")
    cut = spans[len(spans) - keep_last][0]
    head, tail = msgs[:cut], msgs[cut:]

    # A private chat's export is fully decrypted while the vault is open —
    # writing it to exports/ would park PLAINTEXT on disk that outlives the
    # next lock (the vault scrub only cleans at lock time). Unreadable
    # settings fail closed.
    try:
        chat_settings = session_manager.read_chat_settings(chat_name)
    except Exception:
        chat_settings = None
    if chat_settings is None:
        # read_chat_settings answers None on a locked database or a sealed row.
        # "or {}" read that as "not private": a plaintext backup and any
        # provider. Guessing "private" instead would skip the backup of a chat
        # that is not. Nothing is written either way (hunt, 2026-11-21).
        raise ValueError("The chat's settings could not be read just now, so nothing was "
                         "compressed. Try again.")
    _private = bool(chat_settings.get("private_chat"))
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
            raise NothingToCompress("Nothing new to compact since the last run "
                                    "(today stays as it is until it ends)")
    if backup and _private:
        logger.info(f"[COMPRESS] Skipping plaintext backup for private chat '{chat_name}'")
    backup_path = _write_backup(session_manager, chat_name, exported) if (backup and not _private) else None
    provider = make_provider(provider_key, model, conversation=chat_name,
                             private=_private)  # fail BEFORE any LLM spend
    if plan is not None:
        pairs = daily_pairs(plan, provider, int(target_tokens), on_progress=on_progress,
                            instructions=instructions, persona="" if room else persona, room=room,
                            daily_instructions=day_wording, note=note)
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


def _preview_days(session_manager, chat_name: str):
    """(the chat's settings, {day: (rows, finished)}): the days of a chat that
    are still raw turns, today's included. A private chat is refused."""
    exported = session_manager.export_chat(chat_name)
    if exported is None:
        raise ValueError(f"Chat '{chat_name}' not found")
    try:
        chat_settings = session_manager.read_chat_settings(chat_name)
    except Exception:
        chat_settings = None
    # Settings that cannot be read are a private chat's until shown otherwise:
    # read_chat_settings answers None on a locked database or a sealed row, and
    # "or {}" read that as not private (hunt, 2026-11-21).
    if chat_settings is None or chat_settings.get("private_chat"):
        raise ValueError("A private chat is not previewed")
    if chat_settings.get("mode") in ("room", "game"):
        raise ValueError("A room's or a game's chat is not compacted by day, so there is nothing to try on it")
    import config
    # No window: an old chat's days can be tried too. Inside it they read as
    # dropped, and the dialog said every turn was already a day summary.
    plan = daily_plan(exported["messages"], None, _int_setting(config, "DAY_START_HOUR", 4, 0, 23))
    days = {day: (rows, True) for day, (_, rows) in plan["raw"].items()}
    for day, _, turn in plan["today"]:
        days.setdefault(day, ([], False))[0].extend(turn)
    return chat_settings, days


def preview_days(session_manager, chat_name: str) -> List[Dict[str, Any]]:
    """The days preview_day can be tried on, newest first. No model call."""
    _, days = _preview_days(session_manager, chat_name)
    return [{"day": d.isoformat(), "label": day_label(d), "finished": fin, "messages": len(rows)}
            for d, (rows, fin) in sorted(days.items(), reverse=True)]


def preview_day(session_manager, chat_name: str, provider_key: str, model: str,
                target_tokens: int, persona: str = "",
                daily_instructions: Optional[str] = None,
                day: Optional[str] = None) -> Dict[str, Any]:
    """What /compact-daily would write for ONE day of this chat, with nothing
    written: no backup, no change to the chat, no event (so nothing is kept in
    memory either). For tuning the day wording: every real run costs a night
    and a day that cannot be summarized again. day: an ISO date from
    preview_days; None = the newest finished day still raw, else today so far.
    daily_instructions: the text to try (the box's, saved or not); None = the
    wording in force. One real model call, or several for a long day."""
    chat_settings, days = _preview_days(session_manager, chat_name)
    room = chat_settings.get("mode") == "room"
    wording = (daily_instructions or "").strip()
    if len(wording) > MAX_INSTRUCTIONS_CHARS:           # the box's text only: a saved file is the job's to judge
        raise ValueError(f"Instructions are capped at {MAX_INSTRUCTIONS_CHARS} characters")
    wording = wording or load_instructions(daily=True)
    if not days:
        raise NothingToCompress("Nothing to try it on: every turn in this chat is already a day summary")
    if day:
        try:
            day = date.fromisoformat(str(day))
        except ValueError:
            day = None
        if day not in days:
            raise ValueError("That day has no raw turns in this chat")
    else:
        day = max((d for d, (_, fin) in days.items() if fin), default=None) or max(days)
    rows, finished = days[day]
    provider = make_provider(provider_key, model, conversation=chat_name)
    pair = compress_messages(rows, "whole", provider, int(target_tokens),
                             instructions=ROOM_INSTRUCTIONS if room else load_instructions(),
                             persona="" if room else persona, room=room, day=day,
                             daily_instructions=wording)
    text = _text_of(pair[1].get("content"))
    return {"chat": chat_name, "day": day.isoformat(), "label": day_label(day), "finished": finished,
            "messages": len(rows), "text": text, "tokens_est": count_tokens(text)}


# ── extract: a chat's days into memory, the chat left as it is ─────────────
#
# /extract-daily. /compact-daily keeps a window of days and lets the rest roll
# off, so on an old chat it removes every turn and keeps no day. This reads
# the whole chat, writes one summary per finished day exactly as the daily
# compact would, and hands each to the keeper as it is written. The chat is
# never written. Core owns the reading and the summarizing; what is kept, and
# which days a chat has already given, is the keeper's (the Mind Palace's
# daily layer registers itself: tools/daily.py).

_day_keeper = None
_extract_stop = threading.Event()


def set_day_keeper(keeper) -> None:
    """keeper.extract_scope(sm, chat) -> (scope or None, why);
    keeper.extracted_days(sm, chat) -> the ISO days of this chat already kept;
    keeper.keep_extracted(sm, chat, day) -> '' or why it was not kept.
    None: no memory keeps days (the palace is off or unloading)."""
    global _day_keeper
    _day_keeper = keeper


def extract_plan(session_manager, chat_name: str) -> Dict[str, Any]:
    """What /extract-daily would do with this chat. No model call, nothing
    written. days: [(day, rows)] oldest first, the finished days still raw
    turns that are not kept yet. One entry per chat per day: a day this chat
    has already given (extracted before, or summarized by /compact-daily) is
    counted in `held` and left alone. A plain compress summary covers many
    days under one timestamp, so it is no day's turn: left out, counted in
    `blends`. Today is still running: `today` says it was left out."""
    keeper = _day_keeper
    if keeper is None:
        raise ValueError("Days are kept by the Mind Palace, and it is not on.")
    exported = session_manager.export_chat(chat_name)
    if exported is None:
        raise ValueError(f"Chat '{chat_name}' not found")
    try:
        chat_settings = session_manager.read_chat_settings(chat_name)
    except Exception:
        chat_settings = None
    if chat_settings is None:
        raise ValueError("The chat's settings could not be read just now. Try again.")
    scope, why = keeper.extract_scope(session_manager, chat_name)
    if scope is None:
        raise ValueError(f"This chat's days are not kept in memory: {why}.")
    import config
    plan = daily_plan(exported["messages"], None, _int_setting(config, "DAY_START_HOUR", 4, 0, 23))
    held = set(keeper.extracted_days(session_manager, chat_name)) | {d.isoformat() for d, _, _ in plan["kept"]}
    def blend(m):
        return bool(m.get("compressed")) and not m.get("compressed_day")
    days, skipped, blends = [], 0, 0
    for day, (_, rows) in sorted(plan["raw"].items()):
        own = [m for m in rows if not blend(m)]
        blends += sum(1 for m in rows if blend(m) and m.get("role") == "assistant")
        if not any(m.get("role") in ("user", "assistant") for m in own):
            continue
        if day.isoformat() in held:
            skipped += 1
            continue
        days.append((day, own))
    return {"settings": chat_settings, "scope": scope, "days": days, "held": skipped,
            "blends": blends, "today": bool(plan["today"])}


def _extract_chat(session_manager, chat_name: str, provider_key: str, model: str,
                  target_tokens: int, persona: str = "", on_progress=None, note: str = "") -> Dict[str, Any]:
    plan = extract_plan(session_manager, chat_name)
    days = plan["days"]
    if not days:
        raise NothingToCompress("No days to save: every finished day of this chat is already kept in memory")
    keeper = _day_keeper
    instructions, wording = load_instructions(), load_instructions(daily=True)
    provider = make_provider(provider_key, model, conversation=chat_name)   # a private chat was refused by the keeper
    saved = []
    for i, (day, rows) in enumerate(days):
        if _extract_stop.is_set():
            break
        if on_progress:
            on_progress(f"day {i + 1}/{len(days)}")
        try:
            pair = compress_messages(rows, "whole", provider, int(target_tokens), instructions=instructions,
                                     persona=persona, day=day, daily_instructions=wording, note=note)
            # Kept the moment it is written: a long chat is hours of model
            # work, and a failure at day 150 must not cost the 149 before it.
            why = keeper.keep_extracted(session_manager, chat_name, {
                "day": day.isoformat(), "at": pair[0]["compressed_at"],
                "text": _text_of(pair[1].get("content")).strip(), "persona": persona, "messages": len(rows)})
            if why:
                raise RuntimeError(f"{day.isoformat()} was not kept: {why}")
        except Exception as e:
            raise RuntimeError(f"{e} ({len(saved)} of {len(days)} days were saved before this; "
                               f"/extract-daily takes up where it stopped)") from e
        saved.append(day.isoformat())
    return {"chat": chat_name, "mode": "extract", "days": len(saved), "of": len(days),
            "stopped": len(saved) < len(days), "first": saved[0] if saved else None,
            "last": saved[-1] if saved else None}


def stop_extract() -> bool:
    """Ask a running extract to end after the day it is writing. The days
    already saved stay. False: no extract is running."""
    with _job_lock:
        if not (_job.get("running") and _job.get("mode") == "extract"):
            return False
    _extract_stop.set()
    return True


def start_extract_job(session_manager, chat_name: str, *, provider_key: str, model: str,
                      target_tokens: int, persona: str = "", note: str = ""):
    """Start the background extract. It holds the compress job's one slot: two
    jobs would be two streams on one model. Returns (ok, error)."""
    def done(report):
        n, of = report["days"], report["of"]
        return (f"Stopped: {n} of {of} days saved to memory" if report["stopped"]
                else f"{n} day{'s' if n != 1 else ''} saved to memory")

    def work(on_progress):
        _extract_stop.clear()
        return _extract_chat(session_manager, chat_name, provider_key, model, target_tokens,
                             persona, on_progress=on_progress, note=note)
    return _start_job(session_manager, chat_name, "extract", work, begin="Saving a chat's days to memory",
                      done=done, failed="Saving days to memory failed", go={'view': 'layer-daily'})


def get_job_status() -> Dict[str, Any]:
    with _job_lock:
        return dict(_job)


def start_compress_job(session_manager, chat_name: str, *, mode: str,
                       provider_key: str, model: str, target_tokens: int,
                       keep_last_turns: int, backup: bool, persona: str = "", note: str = ""):
    """Start the one-at-a-time background compress. Returns (ok, error).
    note: this run's own instruction for the days (daily mode only)."""
    def work(on_progress):
        return _compress_chat(session_manager, chat_name, mode, provider_key, model, target_tokens,
                              keep_last_turns, backup, on_progress=on_progress, persona=persona, note=note)
    return _start_job(
        session_manager, chat_name, mode, work, begin=f"Compressing a chat ({mode})",
        done=lambda r: f"Chat compressed: {r.get('messages_before')} to {r.get('messages_after')} messages",
        failed='Chat compression failed', go={'view': 'chat-manage'},
        after=lambda r: publish(Events.CHAT_COMPRESSED, {"chat_name": chat_name, "report": r}))


def _start_job(session_manager, chat_name: str, mode: str, work, *, begin: str, done, failed: str,
               go: Dict[str, Any], after=None):
    """The one background job at a time (compress, extract). work(on_progress)
    returns the report; done(report) is the finished row's title; after(report)
    runs once it has ended well. Returns (ok, error)."""
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
        _read = session_manager.read_chat_settings(chat_name)
        _priv = _read is None or bool(_read.get("private_chat"))    # unreadable: a private chat's until shown otherwise
    except Exception:
        _priv = True
    nid = _notify.begin(begin, key='compress', go=go)

    def _run():
        def on_progress(msg):
            with _job_lock:
                _job["progress"] = msg
            _notify.tick(nid, text=msg)

        try:
            report = work(on_progress)
            with _job_lock:
                _job.update({"running": False, "done": True, "ok": True,
                             "result": report,
                             "finished": datetime.now().isoformat()})
            logger.info(f"Compress '{chat_name}' done: {report}")
            _notify.finish(nid, title=done(report))
            if after:
                after(report)
        except NothingToCompress as e:
            # Done, with nothing written and nothing published. The words name
            # no chat, so a private chat's row may carry them.
            logger.info(f"Compress '{chat_name}': {e}")
            _notify.finish(nid, title=str(e))
            with _job_lock:
                _job.update({"running": False, "done": True, "ok": True,
                             "result": {"chat": chat_name, "mode": mode, "nothing": str(e)},
                             "finished": datetime.now().isoformat()})
        except Exception as e:
            logger.exception(f"Compress '{chat_name}' failed")
            _notify.finish(nid, failed=True, title=failed,
                           body='See the log.' if _priv else str(e))
            with _job_lock:
                _job.update({"running": False, "done": True, "ok": False,
                             "error": str(e),
                             "finished": datetime.now().isoformat()})

    threading.Thread(target=_run, daemon=True,
                     name=f"compress-{chat_name}").start()
    return True, ""
