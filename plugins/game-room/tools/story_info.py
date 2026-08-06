# tools/story_info.py — story_status as its OWN module (Krem's ruling
# 2026-08-03): NOT part of the in-story checkbox module (story sessions get
# act+end only — in-story it was pure ghost-dashboard duplicate and fueled
# first-turn tool flailing). Lives in 'all'/the toolset picker so MAIN
# Sapphire can ask "what games are open?" from any plain chat. Becomes the
# story-ledger reader in v2 (tmp/story-primitive-plan.md).
import sys
from pathlib import Path

# .absolute(), never .resolve(): symlinked plugin dirs must not escape.
_PLUGIN_ROOT = str(Path(__file__).absolute().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "story_status",
            "description": "What interactive stories are open: every active playthrough across sessions (story, session, room, turn) plus the installed story list. For PLAIN chats — inside a running story your turn context already carries the live state, so you never need this there.",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]


def execute(function_name, arguments, config):
    if function_name != "story_status":
        return f"Unknown function: {function_name}", False
    try:
        from core.api_fastapi import get_system
        from gameroom_story import rooms, state as st
        system = get_system()
        active_chat = None
        try:
            sm = system.llm_chat.session_manager if system else None
            if sm is not None:
                # Effective chat, not the global active one: on a phone/
                # background turn the "← this chat" marker pointed at the
                # operator's web chat (last survivor of the 1.2 sweep,
                # post-fix review 2026-08-05).
                eff = getattr(sm, "_effective_chat_name", None)
                active_chat = eff() if callable(eff) else sm.get_active_chat_name()
        except Exception:
            pass

        lines = []
        for chat, entry in (st.get_active() or {}).items():
            try:
                state = st.replay(entry["story"], chat)
                story = rooms.load_story(entry["story"])
                room = story["rooms"].get(state["room"])
                flags = []
                if entry.get("paused"):
                    flags.append("paused")
                if state.get("ended"):
                    flags.append("ended")
                here = " ← this chat" if chat == active_chat else ""
                lines.append(
                    f"- {story['meta'].get('title', entry['story'])} — session '{chat}': "
                    f"turn {state['turn']}, {room['title'] if room else state['room']}"
                    f"{' (' + ', '.join(flags) + ')' if flags else ''}{here}")
            except Exception:
                lines.append(f"- {entry.get('story', '?')} — session '{chat}' (unreadable save)")

        installed = rooms.list_stories()
        inst = [f"- {slug}: {meta['title']} — {meta['description']}"
                for slug, meta in installed.items()]

        out = []
        if active_chat and active_chat in (st.get_active() or {}):
            out.append("(You're INSIDE a running story right now — its live state is "
                       "already in your turn context; no tool needed for that.)")
        out.append("Open playthroughs:\n" + ("\n".join(lines) or "(none)"))
        out.append("Installed stories:\n" + ("\n".join(inst) or "(none installed)"))
        return "\n\n".join(out), True
    except Exception as e:
        return f"story_status failed: {e}", False
