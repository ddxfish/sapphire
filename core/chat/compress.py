# compress.py - Chat history compression (Chat Manager v1b)
#
# Two modes, shared plumbing (tmp/chat-manager.md):
#   whole   — everything except the kept tail becomes ONE summary pair.
#             Internally map-reduce: chunk → partial summaries → merge.
#   chunked — each ~CHUNK_INPUT_TOKENS span of history becomes one summary
#             pair IN PLACE, chronological chain preserved.
# target_tokens means the same thing in both modes: the approximate total
# size of the compressed output.
#
# One job at a time, module-level state, thread-run: a local model chewing a
# big chat takes minutes and must not hold an HTTP worker. Routes 409 while
# running; the UI polls get_job_status().

import json
import logging
import re
import threading
from datetime import datetime
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


def render_transcript(msgs: List[Dict[str, Any]]) -> str:
    """Render messages as a plain transcript for the summarizer."""
    lines = []
    for m in msgs:
        role = m.get("role", "?")
        text = _text_of(m.get("content"))
        if role == "assistant" and m.get("tool_calls"):
            names = ", ".join(tc.get("function", {}).get("name", "?")
                              for tc in m["tool_calls"] if isinstance(tc, dict))
            text = f"{text}\n[called tools: {names}]".strip()
        elif role == "tool":
            text = f"({m.get('name', 'tool')} result) {text}"
        lines.append(f"{role.upper()}: {text}")
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


def _summary_pair(summary: str, source_msgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build the synthetic user/assistant pair that replaces source_msgs.
    Timestamp inherits from the newest compressed message so the visual
    chronology holds; `compressed` flags let the UI badge the seam."""
    ts = next((m.get("timestamp") for m in reversed(source_msgs)
               if m.get("timestamp")), None) or datetime.now().isoformat()
    stamp = {"timestamp": ts, "compressed": True,
             "compressed_at": datetime.now().isoformat()}
    first_ts = next((m.get("timestamp") for m in source_msgs
                     if m.get("timestamp")), "?")
    marker = (f"[Compressed history: {len(source_msgs)} messages "
              f"({str(first_ts)[:10]} to {str(ts)[:10]}) summarized below]")
    return [{"role": "user", "content": marker, **stamp},
            {"role": "assistant", "content": summary, **stamp}]


# ── LLM plumbing ───────────────────────────────────────────────────────────

def make_provider(provider_key: str, model: str = ""):
    """One-shot provider for summarize calls (same pattern as routes/settings
    test endpoints). Raises on unknown/unbuildable provider."""
    import config
    from core.chat.llm_providers import get_provider_by_key
    providers_config = {**dict(getattr(config, "LLM_PROVIDERS", {})),
                        **dict(getattr(config, "LLM_CUSTOM_PROVIDERS", {}))}
    if provider_key not in providers_config:
        raise ValueError(f"Unknown provider: {provider_key}")
    provider = get_provider_by_key(provider_key, providers_config,
                                   REQUEST_TIMEOUT, model_override=model or "")
    if not provider:
        raise ValueError(f"Could not create provider '{provider_key}' — "
                         f"check credentials and settings")
    return provider


_CHUNK_PROMPT = (
    "You are compressing a saved chat history. Summarize the conversation "
    "excerpt below into a dense summary of at most about {budget} tokens. "
    "Preserve: concrete facts, names, decisions made, emotional beats, "
    "running jokes, open threads, and anything either participant would "
    "need to remember later. Write chronologically in the third person "
    "past tense. Output ONLY the summary — no preamble, no headers.\n\n"
    "EXCERPT:\n{text}"
)

_MERGE_PROMPT = (
    "Below are sequential partial summaries of ONE long conversation. Merge "
    "them into a single chronological summary of at most about {budget} "
    "tokens. Keep concrete facts, names, decisions, emotional beats, and "
    "open threads; drop repetition. Output ONLY the summary.\n\n"
    "PARTIAL SUMMARIES:\n{text}"
)


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


def _summarize(provider, prompt_template: str, text: str, budget: int) -> str:
    prompt = prompt_template.format(budget=budget, text=cap_text(text))
    resp = provider.chat_completion(
        [{"role": "user", "content": prompt}],
        generation_params={"max_tokens": int(budget * 1.3) + 200,
                           "temperature": 0.3})
    out = (resp.content or "").strip()
    # Local models sometimes leak reasoning into content despite extraction.
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.DOTALL).strip()
    if not out:
        raise RuntimeError("Summarizer returned empty content")
    return out


def _merge_partials(provider, partials: List[str], target_tokens: int,
                    on_progress=None) -> str:
    """Reduce partial summaries to ONE, in rounds. A single merge call's
    input never exceeds INPUT_CEILING_TOKENS — on a very long chat the
    partials themselves outgrow one call, so group → merge → repeat until
    one remains. Each round strictly shrinks the list, so this terminates."""
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
            merged.append(_summarize(provider, _MERGE_PROMPT,
                                     "\n\n---\n\n".join(g), target_tokens))
        if len(merged) >= len(partials):
            # Every group was a singleton — can't shrink (pathological
            # ceiling). Force-merge the lot; cap_text guards the input.
            if on_progress:
                on_progress(f"merging (final round {rnd})")
            return _summarize(provider, _MERGE_PROMPT,
                              "\n\n---\n\n".join(partials), target_tokens)
        partials = merged
    return partials[0]


def compress_messages(head: List[Dict[str, Any]], mode: str, provider,
                      target_tokens: int, on_progress=None) -> List[Dict[str, Any]]:
    """Compress `head` into synthetic summary pairs. Pure of storage —
    testable with a mock provider. on_progress(str) narrates for job status."""
    chunks = chunk_by_turns(head, CHUNK_INPUT_TOKENS)  # module global: patchable
    if not chunks:
        return []
    if mode == "chunked":
        per = max(MIN_PAIR_TOKENS, target_tokens // len(chunks))
        pairs = []
        for i, chunk in enumerate(chunks):
            if on_progress:
                on_progress(f"chunk {i + 1}/{len(chunks)}")
            summary = _summarize(provider, _CHUNK_PROMPT,
                                 render_transcript(chunk), per)
            pairs.extend(_summary_pair(summary, chunk))
        return pairs
    # whole: map-reduce → one pair
    if len(chunks) == 1:
        if on_progress:
            on_progress("summarizing")
        summary = _summarize(provider, _CHUNK_PROMPT,
                             render_transcript(chunks[0]), target_tokens)
    else:
        partials = []
        for i, c in enumerate(chunks):
            if on_progress:
                on_progress(f"chunk {i + 1}/{len(chunks)}")
            partials.append(_summarize(provider, _CHUNK_PROMPT,
                                       render_transcript(c),
                                       min(PARTIAL_SUMMARY_TOKENS, target_tokens)))
        summary = _merge_partials(provider, partials, target_tokens, on_progress)
    return _summary_pair(summary, head)


# ── the job ────────────────────────────────────────────────────────────────

def _write_backup(session_manager, chat_name: str, exported: Dict[str, Any]) -> str:
    exports_dir = Path(session_manager.history_dir) / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = exports_dir / f"{chat_name}_{stamp}.json"
    path.write_text(json.dumps(exported, indent=2), encoding="utf-8")
    return str(path)


def _compress_chat(session_manager, chat_name: str, mode: str,
                   provider_key: str, model: str, target_tokens: int,
                   keep_last_turns: int, backup: bool,
                   on_progress=None) -> Dict[str, Any]:
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

    backup_path = _write_backup(session_manager, chat_name, exported) if backup else None
    provider = make_provider(provider_key, model)  # fail BEFORE any LLM spend
    pairs = compress_messages(head, mode, provider, int(target_tokens),
                              on_progress=on_progress)

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
                       keep_last_turns: int, backup: bool):
    """Start the one-at-a-time background compress. Returns (ok, error)."""
    global _job
    with _job_lock:
        if _job.get("running"):
            return False, (f"A compress job is already running "
                           f"(chat '{_job.get('chat')}')")
        _job = {"running": True, "done": False, "chat": chat_name,
                "mode": mode, "started": datetime.now().isoformat()}

    def _run():
        def on_progress(msg):
            with _job_lock:
                _job["progress"] = msg

        try:
            report = _compress_chat(session_manager, chat_name, mode,
                                    provider_key, model, target_tokens,
                                    keep_last_turns, backup,
                                    on_progress=on_progress)
            with _job_lock:
                _job.update({"running": False, "done": True, "ok": True,
                             "result": report,
                             "finished": datetime.now().isoformat()})
            logger.info(f"Compress '{chat_name}' done: {report}")
            publish(Events.CHAT_COMPRESSED, {"chat_name": chat_name,
                                             "report": report})
        except Exception as e:
            logger.exception(f"Compress '{chat_name}' failed")
            with _job_lock:
                _job.update({"running": False, "done": True, "ok": False,
                             "error": str(e),
                             "finished": datetime.now().isoformat()})

    threading.Thread(target=_run, daemon=True,
                     name=f"compress-{chat_name}").start()
    return True, ""
