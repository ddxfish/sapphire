# core/contacts.py — Contacts provider registry.
#
# Memory plugins register a get_people-shaped provider at load; consumers
# (email, twilio-voice) call get_people() and never care which memory system
# is active. With no provider registered (or a provider error), falls back to
# the classic knowledge store read — a direct module import that works even
# with the classic plugin toggled off, which is exactly how email/twilio
# behaved before this registry existed.
#
# Contract (classic get_people shape): list of dicts with id, name,
# relationship, phone, email, address, notes, email_whitelisted,
# call_whitelisted.

import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_providers = {}  # plugin_name -> callable(scope) -> list[dict]


def register_provider(plugin_name: str, fn):
    with _lock:
        _providers[plugin_name] = fn
    logger.info(f"[CONTACTS] Provider registered: {plugin_name}")


def unregister_provider(plugin_name: str):
    with _lock:
        if _providers.pop(plugin_name, None):
            logger.info(f"[CONTACTS] Provider unregistered: {plugin_name}")


def get_people(scope: str = 'default') -> list:
    """People list from the active memory system, classic fallback."""
    with _lock:
        providers = list(_providers.items())
    for name, fn in providers:
        try:
            return fn(scope)
        except Exception as e:
            logger.warning(f"[CONTACTS] Provider '{name}' failed ({e}); falling back to classic store")
    from plugins.memory.tools.knowledge_tools import get_people as classic_get_people
    return classic_get_people(scope)
