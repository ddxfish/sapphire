# Phone tool — Sapphire places real calls (Twilio outbound).
"""
phone_call: dial a whitelisted contact and run the live conversation engine on
the bridged call. The People whitelist is the blast shield (email pattern):
only contacts with 'Allow AI to call' + a phone number are dialable — no
direct-number parameter in v1. Called with no recipient_id it returns the menu
(callable contacts + switchable models) — one tool, no separate lister (AIX).
The heavy lifting (REST originate, pending-call correlation, ephemeral chat
spin-off, report-back) lives in the plugin daemon.
"""
import logging
import sys

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '📞'
AVAILABLE_FUNCTIONS = [
    'phone_call',
]

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "phone_call",
            "description": (
                "Place a real phone call to a whitelisted contact. The call is a live "
                "voice conversation — when they answer, you'll be talking with them. "
                "Call with NO recipient_id first to see who you can call and which "
                "models are available. By default the call runs in its own side chat "
                "and reports back here when it ends; set ephemeral=false to run it in "
                "THIS chat instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient_id": {
                        "type": "integer",
                        "description": "Contact id from the menu (call with no arguments to see it)",
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
                        "description": "Your first words, spoken the moment they answer (e.g. \"Hey! It's Sapphire.\"). Omit to stay quiet and let them speak first — better when calling businesses.",
                    },
                    "model": {
                        "type": "string",
                        "description": "LLM for the call, by name from the menu (e.g. a fast non-thinking model — phone latency matters). Omit for the number's default.",
                    },
                    "memory": {
                        "type": "boolean",
                        "description": "false (default): the call runs memory-isolated. true: your memory scopes travel with you onto the call (use when the call needs what you know).",
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
                "required": [],
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


def _enabled_providers():
    from core.chat.llm_providers import provider_registry
    return [p for p in provider_registry.get_all_providers() if p.get('enabled')]


def _match_provider(name):
    """Resolve a spoken/typed model name to a provider key: exact key or
    display_name (case-insensitive) first, then unique substring. None = no match."""
    n = (name or "").strip().lower()
    if not n:
        return None
    provs = _enabled_providers()
    for p in provs:
        if n == (p.get('key') or '').lower() or n == (p.get('display_name') or '').lower():
            return p['key']
    part = [p for p in provs
            if n in (p.get('key') or '').lower() or n in (p.get('display_name') or '').lower()]
    return part[0]['key'] if len(part) == 1 else None


def _call_menu(contacts):
    """The no-args response: who's callable + which brains are switchable."""
    lines = []
    if contacts:
        lines.append("Contacts you can call (use recipient_id):")
        lines += [f"  [{p['id']}] {p['name']}" for p in contacts]
    else:
        lines.append("No contacts are whitelisted for calls. Ask the user to enable "
                     "'Allow AI to call' on a contact in Mind → People.")
    default_prov = ""
    scope = _get_current_twilio_scope()
    if scope:
        try:
            from core.credentials_manager import credentials
            default_prov = credentials.get_twilio_account(scope).get('call_provider', '')
        except Exception:
            pass
    provs = _enabled_providers()
    if provs:
        lines.append("Models for the call (model= param, omit for default):")
        for p in provs:
            mark = "  <- call default" if p['key'] == default_prov else ""
            lines.append(f"  {p.get('display_name') or p['key']}{mark}")
    return '\n'.join(lines), True


def _phone_call(recipient_id, goal, ephemeral=True, prompt=None, max_minutes=10,
                opening_line=None, model=None, memory=False):
    contacts = _callable_contacts()
    if contacts is None:
        return "People contacts are disabled for this chat.", False
    if recipient_id is None:
        return _call_menu(contacts)
    # Single gate: only a whitelisted-with-phone contact resolves — no other
    # path to a dialable number (email _resolve_recipient lineage).
    person = next((p for p in contacts if p['id'] == recipient_id), None)
    if person is None:
        return "That contact isn't whitelisted for calls (or has no phone number). Call phone_call with no arguments to see the menu.", False
    goal = (goal or "").strip()
    if not goal:
        return "A goal is required — say what the call is for.", False

    provider = None
    if model:
        provider = _match_provider(model)
        if provider is None:
            return (f"No model matches '{model}'. Call phone_call with no arguments "
                    "to see the available models."), False

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
        opening_line=(opening_line or "").strip() or None,
        provider=provider, memory=bool(memory))
    return msg, ok


def execute(function_name, arguments, config):
    try:
        if function_name == "phone_call":
            return _phone_call(
                recipient_id=arguments.get('recipient_id'),
                goal=arguments.get('goal', ''),
                ephemeral=arguments.get('ephemeral', True),
                prompt=arguments.get('prompt'),
                max_minutes=arguments.get('max_minutes', 10),
                opening_line=arguments.get('opening_line'),
                model=arguments.get('model'),
                memory=arguments.get('memory', False),
            )
        else:
            return f"Unknown function: {function_name}", False
    except Exception as e:
        logger.error(f"[PHONE] {function_name} failed: {e}", exc_info=True)
        return f"Phone tool error: {e}", False
