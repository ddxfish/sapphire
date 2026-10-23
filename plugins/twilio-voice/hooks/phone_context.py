"""ghost_inject hook — tells Sapphire she's on a live phone call.

Per-turn, invisible-to-the-caller, never-persisted medium context delivered on the
ghost rail (see docs/GHOST_MESSAGES.md). This is ambient-state context ONLY — the
voice medium and the caller's number — it never reads or fingerprints the caller's
words, so it stays clear of the Vanta-shape anti-pattern the rail is gated against.

Gated tightly: fires only for a chat that is CURRENTLY hosting a live call
(system._twilio_active_calls, keyed by chat), so a concurrent web/cron turn —
or another simultaneous call — never picks up the wrong call's context.

Conduct rails: the daemon resolves the public-line safety text per call (plugin
setting `safety_rails`, per-rule "Public line" checkbox, outbound always on) and
stamps it on the call record as `rails` — this hook just appends what it's given.
An UNCHECKED trusted line gets no rails at all: she's fully herself.
"""


def ghost_inject(event):
    system = event.metadata.get("system")
    if not system:
        return
    # The chat IS the gate: each live call registers under its own chat, so a
    # turn only gets phone context when it runs in a chat hosting a call. With
    # N concurrent calls each stream picks up its OWN call's context; any other
    # turn (web/cron/another call) resolves to a different chat and misses.
    calls = getattr(system, "_twilio_active_calls", None)
    if not calls:
        return
    # This turn's chat: the hook runner's stamp first (event.chat_name, F2
    # resolver) — the system walk broke silently on streaming turns
    # (ghost-rail finding 2026-08-21); keep it only as fallback.
    current = getattr(event, "chat_name", None)
    if not current:
        try:
            current = system.llm_chat.session_manager._effective_chat_name()
        except Exception:
            return
    call = calls.get(current)
    if not call:
        return

    caller = call.get("caller") or "an unknown number"
    rails = (call.get("rails") or "").strip()
    if call.get("direction") == "outbound":
        # A call SHE placed (phone_call tool) — goal-centric context, no rule note.
        base = (
            f"You're on a live phone call YOU placed to {caller}. The messages are "
            "their voice, transcribed — infer intent, don't nitpick wording. Your "
            "reply is spoken aloud: brief, conversational, no markdown or emoji."
        )
        goal = (call.get("goal") or "").strip()
        if goal:
            base += f" Your goal for this call: {goal}"
    else:
        # The rule's custom Phone-context text (from the Realtime modal) wins;
        # {caller} is substituted. Blank -> the sensible default below.
        note = (call.get("note") or "").strip()
        if note:
            base = note.replace("{caller}", caller)
        else:
            base = (
                f"You're on a live phone call with {caller}. The user's messages are voice "
                "transcriptions and may contain small errors — infer intent, don't nitpick "
                "wording. Your reply is spoken aloud, so keep it brief and conversational: "
                "no markdown, lists, code blocks, or emoji."
            )
    if rails:
        base += " " + rails
    # Always appended — even under a custom note. An outside line must never
    # leave her unable to hang up (see hooks/hangup_sentinel.py).
    event.ghost_text = base + (
        " To hang up: say your goodbye and put <<HANG UP>> at the end of that same "
        "reply. Writing the tag IS the action — the call ends right after your final "
        "words play (the tag itself is never heard). Don't write it unless you mean "
        "to hang up now; even quoting it triggers it."
    )
