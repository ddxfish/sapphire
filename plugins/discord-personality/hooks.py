"""Hook entry points — one function per host hook, each just dispatches.

Core exec()s this file once PER HOOK NAME (plugin_loader._load_handler), so
nothing stateful lives here: the module registry and settings reads live in
the discord_personality package, imported below.
"""
import sys
from pathlib import Path

# .absolute(), never .resolve(): a symlinked plugin dir must not escape.
_PLUGIN_ROOT = str(Path(__file__).absolute().parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

from discord_personality import dispatch  # noqa: E402


def discord_message_observed(event):
    dispatch('discord_message_observed', event)


def discord_prompt_context(event):
    dispatch('discord_prompt_context', event)


def discord_reply_planned(event):
    dispatch('discord_reply_planned', event)


def discord_reply_sent(event):
    dispatch('discord_reply_sent', event)


def discord_voice_utterance(event):
    dispatch('discord_voice_utterance', event)


def discord_tick(event):
    dispatch('discord_tick', event)
