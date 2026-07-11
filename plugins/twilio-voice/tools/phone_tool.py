# Phone tool — Sapphire places real calls (Twilio outbound).
"""
phone_call: dial a whitelisted contact and run the live conversation engine on
the bridged call. The People whitelist is the blast shield (email pattern):
only contacts with 'Allow AI to call' + a phone number are dialable — no
direct-number parameter in v1. The heavy lifting (REST originate, pending-call
correlation, ephemeral chat spin-off, report-back) lives in the plugin daemon.
"""
import logging
import sys

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '📞'
AVAILABLE_FUNCTIONS = [
    'get_phone_contacts',
    'phone_call',
]

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "get_phone_contacts",
            "description": "List contacts you are allowed to call (id + name). Use the id with phone_call().",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "phone_call",
            "description": (
                "Place a real phone call to a whitelisted contact. The call is a live "
                "voice conversation — when they answer, you'll be talking with them. "
                "By default the call runs in its own side chat and reports back here "
                "when it ends; set ephemeral=false to run it in THIS chat instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient_id": {
                        "type": "integer",
                        "description": "Contact id from get_phone_contacts()",
                    },
                    "goal": {
                        "type": "string",
                        "description": "What the call is for — you'll see this during the call (e.g. 'wake Krem up gently', 'order a large pepperoni pizza for delivery')",
                    },
                    "ephemeral": {
                        "type": "boolean",
                        "description": "true (default): run the call in a throwaway side chat and report back. false: run it in the current chat.",
                    },
                    "opening_line": {
                        "type": "string",
                        "description": "Your first words, spoken the moment they answer (e.g. \"Hey! It's Sapphire.\"). Recommended — it sets the cadence. Omit to wait for them to speak first.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Optional prompt/persona name for the call's side chat (ephemeral mode only).",
                    },
                    "max_minutes": {
                        "type": "number",
                        "description": "Hard call-duration cap in minutes (default 10, carrier-enforced).",
                    },
                },
                "required": ["recipient_id", "goal"],
            },
        },
    },
]


def _daemon():
    """The LIVE daemon module instance (loaded by plugin_loader under its
    path-derived name — the hyphen blocks normal import syntax)."""
    return sys.modules.get("plugins.twilio-voice.daemon")


def _get_current_people_scope():
    """None when unset/disabled — caller must refuse, never fall back to a
    real scope (silent-default class invariant)."""
    try:
        from core.chat.function_manager import scope_people
        return scope_people.get()
    except Exception as e:
        logger.debug(f"phone: people scope resolution failed: {e}")
        return None


def _get_current_twilio_scope():
    """The chat's dial-from account (sidebar Mind > twilio number dropdown).
    None when unset/disabled — refuse, never fall back (silent-default class)."""
    try:
        from core.chat.function_manager import scope_twilio
        return scope_twilio.get()
    except Exception as e:
        logger.debug(f"phone: twilio scope resolution failed: {e}")
        return None


def _callable_contacts():
    from core.contacts import get_people
    scope = _get_current_people_scope()
    if scope is None:
        return None
    return [p for p in get_people(scope) if p.get('call_whitelisted') and p.get('phone')]


def _get_phone_contacts():
    contacts = _callable_contacts()
    if contacts is None:
        return "People contacts are disabled for this chat.", False
    if not contacts:
        return ("No contacts are whitelisted for calls. Ask the user to enable "
                "'Allow AI to call' on a contact in Mind → People."), False
    lines = ["Contacts you can call:"]
    lines += [f"  [{p['id']}] {p['name']}" for p in contacts]
    return '\n'.join(lines), True


def _phone_call(recipient_id, goal, ephemeral=True, prompt=None, max_minutes=10,
                opening_line=None):
    contacts = _callable_contacts()
    if contacts is None:
        return "People contacts are disabled for this chat.", False
    # Single gate: only a whitelisted-with-phone contact resolves — no other
    # path to a dialable number (email _resolve_recipient lineage).
    person = next((p for p in contacts if p['id'] == recipient_id), None)
    if person is None:
        return "That contact isn't whitelisted for calls (or has no phone number). Use get_phone_contacts().", False
    goal = (goal or "").strip()
    if not goal:
        return "A goal is required — say what the call is for.", False

    daemon = _daemon()
    if daemon is None or not hasattr(daemon, "place_call"):
        return "The phone system isn't running.", False

    scope = _get_current_twilio_scope()
    if scope is None:
        return "Outbound calling is disabled for this chat (twilio number set to None in the sidebar).", False

    try:
        from core.api_fastapi import get_system
        origin_chat = get_system().llm_chat.session_manager._effective_chat_name()
    except Exception:
        origin_chat = "default"

    try:
        mins = max(1.0, min(float(max_minutes or 10), 60.0))
    except (TypeError, ValueError):
        mins = 10.0
    ok, msg = daemon.place_call(
        to_number=person['phone'], to_name=person['name'], goal=goal,
        origin_chat=origin_chat, ephemeral=bool(ephemeral),
        prompt=(prompt or "").strip() or None, max_minutes=mins, scope=scope,
        opening_line=(opening_line or "").strip() or None)
    return msg, ok


def execute(function_name, arguments, config):
    try:
        if function_name == "get_phone_contacts":
            return _get_phone_contacts()
        elif function_name == "phone_call":
            return _phone_call(
                recipient_id=arguments.get('recipient_id'),
                goal=arguments.get('goal', ''),
                ephemeral=arguments.get('ephemeral', True),
                prompt=arguments.get('prompt'),
                max_minutes=arguments.get('max_minutes', 10),
                opening_line=arguments.get('opening_line'),
            )
        else:
            return f"Unknown function: {function_name}", False
    except Exception as e:
        logger.error(f"[PHONE] {function_name} failed: {e}", exc_info=True)
        return f"Phone tool error: {e}", False
