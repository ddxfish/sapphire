# functions/ask_user.py - a question card with clickable options (core/ask_card.py)
"""
The AI's AskUserQuestion. The result carries the ASK marker; the browser draws
the card in her bubble and the user's picks come back as THEIR next message, so
the answer is an ordinary turn (inbox, history, voice all unchanged). The tool
never waits: her turn ends, the user's starts.
"""
import logging

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '❓'
AVAILABLE_FUNCTIONS = ['ask_user']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "ask_user",
            "description": (
                "Show the user a question card: clickable options plus a type-your-own box, right in the "
                "chat. For a choice that is genuinely theirs - a fork, a preference, a confirmation with "
                "real alternatives. Up to 4 questions per card (they appear as tabs and come back "
                "together), 2-6 options each. Their picks arrive as their NEXT message, led by the line "
                "`[Question card <id> (ask_user) — what the user clicked; not typed by the user]` then one "
                "`Question? → Answer` line per question. So: ask in your own words, call this, then END "
                "your turn (one short line after is fine) - do not wait or re-ask. The user may also dismiss "
                "the card or just type. On voice there is no screen: say the options aloud."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 4,
                        "description": "The questions, in order.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {"type": "string", "description": "The question, a full sentence."},
                                "header": {"type": "string", "description": "Tab label, 1-2 words (e.g. 'Color')."},
                                "options": {
                                    "type": "array",
                                    "minItems": 2,
                                    "maxItems": 6,
                                    "description": "2-6 choices. A 'type your own' box is always added.",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {"type": "string", "description": "The choice, 1-5 words."},
                                            "description": {"type": "string", "description": "What picking it means (optional)."}
                                        },
                                        "required": ["label"]
                                    }
                                },
                                "multi_select": {"type": "boolean", "description": "Several may be picked (default false)."}
                            },
                            "required": ["question", "options"]
                        }
                    }
                },
                "required": ["questions"]
            }
        }
    }
]


def _screen_note():
    """A hint when no browser seems to be on this chat right now - the card
    waits in history either way, but she should say the options aloud."""
    try:
        from core.api_fastapi import get_system
        from core.event_bus import get_event_bus
        sm = get_system().llm_chat.session_manager
        here = sm._effective_chat_name() or ''
        on_screen = get_event_bus().subscriber_count() > 0 and here == (sm.get_active_chat_name() or '')
    except Exception:
        return ''
    if on_screen:
        return ''
    return " No browser seems to be on this chat right now - the card is there when they look; say the options aloud too."


def _no_surface():
    """Why no card can be drawn for THIS turn, or ''. An agent's run, a
    chatless background task and a turn that came in over MCP have no chat
    bubble for the card to land in: the tool used to say "Card shown" to all
    three and the agent ended its turn waiting for an answer that could never
    come (seam scout, 2026-10-07)."""
    try:
        from core import mcp_server
        if mcp_server.answering.get():
            return "this turn came in over MCP - the client sees text, not a card"
    except Exception:
        pass
    try:
        from core.chat.stream_brain import get_override
        o = get_override() or {}
        if o.get('agent'):
            return "an agent's run has no chat bubble for a card"
        if o.get('ephemeral'):
            return "a chatless background turn has no chat bubble for a card"
    except Exception:
        pass
    try:
        from core.api_fastapi import get_system
        if not (get_system().llm_chat.session_manager._effective_chat_name() or ''):
            return "there is no chat for a card to land in"
    except Exception:
        pass
    return ''


def execute(function_name, arguments, config):
    if function_name != 'ask_user':
        return f"Unknown function: {function_name}", False
    from core import ask_card
    why = _no_surface()
    if why:
        return f"ask_user: no one can see a card here ({why}). Ask the question in your text instead.", False
    try:
        payload = ask_card.normalize((arguments or {}).get('questions'))
    except ValueError as e:
        return f"ask_user: {e}", False
    n = len(payload['questions'])
    text = (f"Card shown with {n} question{'s' if n != 1 else ''}. Their answers arrive as their next "
            f"message - end your turn now.{_screen_note()}")
    return text + "\n" + ask_card.marker(payload['questions'], payload['id']), True
