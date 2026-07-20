# plugins/mindpalace/schedule/nightly.py
# The nightly tending round (2026-07-12; alpha split 2026-07-15; residency
# 2026-07-19). Fired by the continuity scheduler (settings-linked:
# librarian_nightly_time / librarian_nightly_enabled).
#
# Two independent halves, each behind its own alpha master toggle:
#   decay tick — importance_enabled  (dynamics are importance physiology)
#   passes     — librarian_enabled   (the groundskeeper's own switch)
# Order matters: the decay tick runs FIRST and is day-guarded via plugin
# state, so a restart-refire can't double-decay; passes run after in
# pipeline order (dates → link → dedup → sort → self), scope by scope,
# through librarian.run_blocking — one groundskeeper, sequential.
#
# WHICH scopes get tended DERIVES from per-scope residency (the Self page
# Resident strip, default OFF — librarian_nightly_scopes retired
# 2026-07-19): effective(pass, scope) = master AND global pass gate
# (Admin card) AND the scope's own pass pill. Each tended scope gets its
# OWN session chat under its OWN resident prompt/model — Anita's memories
# never share a transcript (or a voice) with Sapphire's.

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def run(event):
    from core.plugin_loader import plugin_loader
    from plugins.mindpalace.tools import dynamics, librarian
    from plugins.mindpalace.tools import palace_tools as pt

    s = plugin_loader.get_plugin_settings('mindpalace')
    if not s.get('librarian_nightly_enabled'):
        return "Nightly tending: disabled in settings."
    lib_on = bool(s.get('librarian_enabled'))
    imp_on = bool(s.get('importance_enabled'))
    if not lib_on and not imp_on:
        return "Nightly tending: librarian and importance both disabled (alpha toggles)."

    all_scopes = [x['name'] for x in pt.get_scopes()]
    # Tend-list = scopes with ANY pass pill on (Self page, default off).
    tended = {}
    for sc in all_scopes:
        passes = pt.scope_resident(sc).get('passes', {})
        on = {k for k in librarian.PASS_KINDS if passes.get(k)}
        if on:
            tended[sc] = on

    notes = []

    # Decay tick — importance's half, deliberately NOT gated by librarian
    # opt-ins (ratings drift wherever they exist). Once per local calendar
    # day, over every scope.
    if not imp_on:
        notes.append("decay: importance disabled")
    else:
        state = event.get('plugin_state')
        today = datetime.now().strftime('%Y-%m-%d')
        if state is not None and state.get('decay_day') == today:
            notes.append("decay: already ticked today")
        else:
            touched = sum(dynamics.decay_tick(scope) for scope in all_scopes)
            if state is not None:
                state.save('decay_day', today)
            notes.append(f"decay: {touched} chunks drifted")

    if not lib_on:
        notes.append("passes: librarian disabled")
    elif not tended:
        notes.append("passes: no scopes opted in (Self page Resident strip)")
    else:
        # One session chat PER SCOPE for the round — each resident's night
        # is its own transcript under its own prompt.
        chats = {sc: librarian.mint_session_chat(sc) for sc in tended}
        for kind in librarian.PASS_KINDS:
            if not librarian.pass_enabled(kind):
                notes.append(f"{kind}: off (Admin toggle)")
                continue
            for scope, on in tended.items():
                if kind not in on:
                    continue
                # One crashed pass must not abort the rest of the round.
                try:
                    msg, ok = librarian.run_blocking(scope, kind=kind,
                                                     chat=chats[scope])
                except Exception as e:
                    msg, ok = f"crashed: {e}", False
                    logger.error(f"[LIBRARIAN] Nightly {kind} pass crashed "
                                 f"for '{scope}': {e}", exc_info=True)
                notes.append(f"{kind} {scope}: {msg}")
                if not ok:
                    logger.warning(f"[LIBRARIAN] Nightly {kind} pass skipped "
                                   f"for '{scope}': {msg}")

    result = " | ".join(notes)
    logger.info(f"[LIBRARIAN] Nightly round done — {result}")
    return result
