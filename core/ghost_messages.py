"""Ghost-message handler — per-turn ephemeral context that bypasses cache invalidation.

Background:
    Mutating the system prompt every turn (with rotating spice, current datetime,
    or plugin-injected one-turn context) breaks Claude's prompt cache on every
    request. For long chats with rotating per-turn content, that's effectively
    100% cache miss → full re-tokenization of all history → multi-turn cost
    growth that scales with chat length.

    The ghost rail keeps the system prompt stable. Per-turn ephemera lives in a
    separate user-role message inserted just before the new user input. The
    cached prefix (system prompt + history) survives turn-to-turn; only the
    ghost-message + new-user-message portion is fresh tokens.

    On a 30-turn Claude chat with ~50K tokens of history, ghost migration takes
    input cost from ~$1.85 (every turn re-tokenized) to ~$0.55 (cached prefix).
    Strength of spice/datetime/plugin context goes UP because of recency bias —
    the model sees them right before generating, not buried 50K tokens deep.

Design:
    Built-in core sources (hardcoded, no plugin involvement):
        - Spice (when chat_settings.spice_enabled and a spice is selected)
        - Datetime (when chat_settings.inject_datetime is true)

    Plugin sources via the `ghost_inject` hook:
        - Plugins declare `"hooks": ["ghost_inject"]` in their manifest
        - Handler sets `event.ghost_text = "..."` to contribute
        - Runner attributes each contribution to the plugin name in the envelope

    Envelope (visible to the assistant, INVISIBLE to the user):
        [System context from Sapphire's own app — not written by the user]
        - Time: Tuesday, May 8, 8:55 PM
        - Spice: Speak more urgently in this reply.
        - weather: Light rain in user's area.

    The envelope is the consent surface. The assistant CAN see who is speaking
    (per-line plugin attribution), so a ghost asking it to argue against the
    user's stance can be flagged out loud rather than complied with silently.
    Without the labeling, ghost messages would be puppetry. With the labeling,
    they're operator-orchestrated context that the assistant can choose how to
    handle.

Anti-pattern (REJECTED at plugin store review):
    Plugins that fingerprint user message content (embedding cosine, regex,
    keyword match) and inject opinion-shaping or behavior-shaping text in
    response. Combination of (a) targeting on user input + (b) invisible to
    user + (c) instructive content = manipulation primitive. The store does
    not accept these — see `docs/PLUGINS.md` for review criteria.

Persistence:
    Ghost messages are NEVER saved to chat history. They live only in the
    LLM-call payload of a single turn. When the user reloads the chat, no
    ghost content appears in the message log. Same pattern as today's
    `prompt_inject` per-turn `context_parts`.

2026-05-08.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Tuple

import config
from core.hooks import hook_runner, HookEvent

logger = logging.getLogger(__name__)


# Envelope sentinel — used by the Claude provider to detect ghost messages
# for prompt-cache boundary placement. Stays first-line so detection works,
# but is now a compact marker rather than a wordy framing. Each plugin
# emits its own "System Note:" or context line inside its contribution so
# the AI gets actionable per-plugin guidance instead of generic boilerplate.
# 2026-05-14.
#
# Reworded 2026-08-03: the old header said "operator-injected" — the literal
# vocabulary of a prompt-injection attack, self-asserting authority from
# inside a user-role message. Models (correctly) flagged it as suspicious,
# especially next to images. Plain words now: what it is, who wrote it.
# Same consent design — attribution per line, nothing hidden.
_ENVELOPE_HEADER = "[System context from Sapphire's own app — not written by the user]"


def _datetime_contribution() -> Optional[str]:
    """Build the current-datetime line. None if unavailable."""
    try:
        from zoneinfo import ZoneInfo
        tz_name = getattr(config, 'USER_TIMEZONE', 'UTC') or 'UTC'
        now = datetime.now(ZoneInfo(tz_name))
        return f"Time: {now.strftime('%A, %B %d, %Y at %I:%M %p')} ({tz_name})"
    except Exception:
        try:
            now = datetime.now()
            return f"Time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}"
        except Exception:
            return None


def _spice_contribution() -> Optional[str]:
    """Build the active-spice line. None when no spice is set."""
    try:
        from core import prompts
        spice = prompts.get_current_spice()
        if not spice:
            return None
        return f"Spice: {spice.strip()}"
    except Exception as e:
        logger.debug(f"[GHOST] spice lookup failed: {e}")
        return None


def build_ghost_message(
    system,
    chat_settings: dict,
    user_input: str = "",
) -> Optional[str]:
    """Build the ghost-message envelope for one LLM call.

    Returns the full envelope string ready to be sent as a user-role message,
    or None if there's nothing to inject. Caller is responsible for adding
    the message to the LLM payload at the right position (just before the
    new user message) and ensuring it never reaches chat history persistence.

    Args:
        system: VoiceChatSystem instance (for plugin hook metadata)
        chat_settings: current chat's settings dict (spice_enabled, inject_datetime)
        user_input: current user input — passed to the hook so plugins can
                    react to context, NOT for content fingerprinting (see
                    anti-pattern note in module docstring; store review
                    rejects content-fingerprinting plugins)
    """
    contributions: list[Tuple[str, str]] = []

    # Built-in: datetime
    if chat_settings.get('inject_datetime', False):
        dt_line = _datetime_contribution()
        if dt_line:
            contributions.append(("(core)", dt_line))

    # Built-in: spice — unless SPICE_DELIVERY routes it into the system prompt
    # instead (unattributed there; here it's labeled app context, which some
    # models narrate as "my app told me to"). See _get_system_prompt.
    if (chat_settings.get('spice_enabled', True)
            and getattr(config, 'SPICE_DELIVERY', 'ghost') != 'system'):
        sp_line = _spice_contribution()
        if sp_line:
            contributions.append(("(core)", sp_line))

    # Built-in: operator's per-chat ghost override (sidebar "Ghost Message" box).
    # Overlays onto THIS envelope as one attributed line — never a second ghost
    # message. Empty → contributes nothing (and if it's the only would-be source,
    # the `if not contributions` guard below means no ghost message at all).
    ghost_ctx = (chat_settings.get('ghost_context') or '').strip()
    if ghost_ctx:
        contributions.append(("(operator)", ghost_ctx))

    # Plugins via ghost_inject hook
    if hook_runner.has_handlers("ghost_inject"):
        from core.hooks import surface_for as _surface_for
        ghost_event = HookEvent(
            input=user_input,
            metadata={"system": system},
            surface=_surface_for(chat_settings),   # F1: derived from mode when unstamped
        )
        try:
            hook_runner.fire("ghost_inject", ghost_event)
        except Exception as e:
            logger.warning(f"[GHOST] ghost_inject hook fire raised: {e}")
        for plugin_name, text in (ghost_event.ghost_contributions or []):
            # Defensive: a plugin returning a non-string (dict, list, int)
            # would crash here via `.strip()` and wedge the chat turn —
            # `add_user_message` runs after `_build_base_messages`, so the
            # exception lands BEFORE the user message is persisted. The
            # outer except in chat_streaming.py:759 then adds `[Error: ...]`
            # as assistant, producing two consecutive asst messages that
            # break Claude's strict alternation requirement. Coerce to str
            # and skip empty silently. 2026-05-14.
            if not isinstance(text, str):
                try:
                    text = str(text)
                except Exception:
                    logger.warning(
                        f"[GHOST] {plugin_name} contributed non-stringifiable "
                        f"value of type {type(text).__name__} — skipping"
                    )
                    continue
            if text and text.strip():
                # Truncate paranoid long contributions — a runaway plugin
                # shouldn't be able to dominate the model's context window
                # by emitting a 100KB ghost. 2KB per plugin is generous.
                clean = text.strip()
                if len(clean) > 2048:
                    logger.warning(
                        f"[GHOST] {plugin_name} contributed {len(clean)} chars "
                        f"— truncating to 2048 to prevent context dominance"
                    )
                    clean = clean[:2048] + "…"
                contributions.append((plugin_name, clean))

    if not contributions:
        return None

    # Format envelope. Each contribution is one line, attributed.
    body_lines = []
    for label, text in contributions:
        # If a plugin's text already starts with a label like "Time:" or
        # "Spice:", don't double-attribute. Otherwise prefix with plugin name.
        if label == "(core)":
            body_lines.append(f"- {text}")
        else:
            body_lines.append(f"- {label}: {text}")
    return _ENVELOPE_HEADER + "\n" + "\n".join(body_lines)


def is_ghost_message(content: str) -> bool:
    """True if content looks like a ghost-message envelope.

    Used by history-save paths to refuse to persist ghost content if it ever
    leaks into the message stream. Belt-and-suspenders — `build_ghost_message`
    output should never reach the persistence layer in normal flow, but if
    a future refactor accidentally appends it to the in-memory messages list
    that gets saved, this check is the last line of defense.
    """
    if not content or not isinstance(content, str):
        return False
    return content.startswith(_ENVELOPE_HEADER[:32])
