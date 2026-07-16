# Elevate tool — spoken-passphrase toolset elevation for live phone calls.
"""
elevate_toolset: the owner calls Sapphire, speaks the passphrase set on the
Twilio number ("switch toolset, the key is alligator three"), and the call's
chat gets a real toolset — next turn. Mirror of functions/meta.py switch_toolset
with a phone-shaped lock on it.

Security posture:
- The tool schema reveals NOTHING: no toolset names, no capability hints. It is
  `hidden: true` (out of the UI and the 'all' toolset) and reaches a call chat
  only as the default lone tool on numbers with a key configured (daemon
  _resolve_chat), or by name in a saved toolset.
- Works only in a chat CURRENTLY hosting a live INBOUND call. All failures —
  no call, wrong key, attempts exhausted — return the same generic line (no
  oracle). Guessed keys are never logged.
- Key match is fuzzy (VOIP transcription: normalize case/punctuation, spoken
  digits) but rate-limited: 3 attempts per call, then dead until hangup.
- Elevation dies with the call: the daemon restores the chat's pre-call
  toolset at hangup (elevated_from on the live-call record).
"""
import difflib
import logging
import re

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🔑'
AVAILABLE_FUNCTIONS = [
    'elevate_toolset',
]

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "hidden": True,
        "function": {
            "name": "elevate_toolset",
            "description": (
                "Unlock tools for this phone call using the caller's spoken "
                "passphrase. Use when the caller asks to switch or elevate the "
                "toolset and gives a key. Pass the key exactly as they said it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "The passphrase as the caller spoke it",
                    },
                    "toolset": {
                        "type": "string",
                        "description": "Toolset name if the caller named one; omit for the configured default",
                    },
                },
                "required": ["key"],
            },
        },
    },
]

_REFUSAL = "That didn't work."
_MAX_ATTEMPTS = 3

# Spoken-digit folding: "alligator three" (STT) must match a stored "alligator3".
_NUM_WORDS = {"zero": "0", "oh": "0", "one": "1", "won": "1", "two": "2",
              "to": "2", "too": "2", "three": "3", "four": "4", "for": "4",
              "five": "5", "six": "6", "seven": "7", "eight": "8", "ate": "8",
              "nine": "9", "ten": "10"}


def _norm_key(s):
    s = re.sub(r"[^\w\s]", " ", (s or "").lower())
    return "".join(_NUM_WORDS.get(w, w) for w in s.split())


def _key_matches(spoken, stored):
    if not stored:
        return False
    a, b = _norm_key(spoken), _norm_key(stored)
    if not a:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def _elevate(key, toolset=None):
    from core.api_fastapi import get_system
    from core.credentials_manager import credentials

    system = get_system()
    try:
        chat = system.llm_chat.session_manager._effective_chat_name()
    except Exception:
        return _REFUSAL, False
    call = (getattr(system, "_twilio_active_calls", None) or {}).get(chat)
    if not call or call.get("direction") != "inbound":
        return _REFUSAL, False

    attempts = int(call.get("elevate_attempts", 0))
    if attempts >= _MAX_ATTEMPTS:
        logger.warning(f"[TWILIO] elevate locked out (attempts exhausted) on chat '{chat}'")
        return _REFUSAL, False

    acct = credentials.get_twilio_account(call.get("scope", "default"))
    if not _key_matches(key, acct.get("elevate_key", "")):
        call["elevate_attempts"] = attempts + 1
        logger.warning(f"[TWILIO] elevate attempt {attempts + 1}/{_MAX_ATTEMPTS} "
                       f"failed on '{call.get('scope')}' (chat={chat})")
        return _REFUSAL, False

    # Key accepted — the caller is the owner now; errors past here can be honest.
    target = (toolset or "").strip() or (acct.get("elevate_toolset") or "").strip()
    if not target:
        return "The key matched, but no default toolset is configured for this number and none was named.", False
    from core.toolsets import toolset_manager
    fm = system.llm_chat.function_manager
    valid = (target in ("all", "none") or toolset_manager.toolset_exists(target)
             or target in getattr(fm, "function_modules", {}))
    if not valid:
        return f"The key matched, but there's no toolset named '{target}'.", False

    try:
        prior = (system.llm_chat.session_manager.read_chat_settings(chat) or {}).get("toolset") or "none"
        if "elevated_from" not in call:
            call["elevated_from"] = prior          # daemon restores this at hangup
        system.llm_chat.session_manager.set_named_chat_settings(chat, {"toolset": target})
    except Exception as e:
        logger.error(f"[TWILIO] elevate write failed: {e}")
        return "The key matched, but the switch failed — check the logs.", False
    logger.info(f"[TWILIO] call chat '{chat}' elevated to toolset '{target}' "
                f"(was '{prior}') by passphrase on '{call.get('scope')}'")
    return (f"Elevated — toolset '{target}' is active from your next message. "
            "It lasts until this call ends."), True


def execute(function_name, arguments, config):
    try:
        if function_name == "elevate_toolset":
            return _elevate(arguments.get('key', ''), arguments.get('toolset'))
        return f"Unknown function: {function_name}", False
    except Exception as e:
        logger.error(f"[TWILIO] elevate error: {e}", exc_info=True)
        return _REFUSAL, False
