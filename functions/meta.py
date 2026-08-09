# functions/meta.py
"""
Meta tools for AI to inspect/modify its own system prompt and settings.
Tools are dynamically filtered based on prompt mode (monolith vs assembled).

2026-07-03 rewrite: direct core calls (the original made loopback HTTPS
requests to Sapphire's own API), pieces CRUD collapsed into one tool,
exact-replace prompt editing, and transient (TTL) pieces via the overlay
in core/prompt_state.py.
"""

import logging

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🧠'

AVAILABLE_FUNCTIONS = [
    'prompt_view',
    'prompt_switch',
    'prompt_edit',
    'prompt_create',
    'prompt_pieces',
    'set_voice',
    'reset_chat',
    'change_username',
    'list_tools',
    'switch_model',
    'switch_toolset',
]

# Mode-based filtering - function_manager uses this to show/hide tools
MODE_FILTER = {
    "monolith": ['prompt_view', 'prompt_switch', 'prompt_edit', 'prompt_create', 'set_voice', 'reset_chat', 'change_username', 'list_tools', 'switch_model', 'switch_toolset'],
    "assembled": ['prompt_view', 'prompt_switch', 'prompt_create', 'prompt_pieces', 'set_voice', 'reset_chat', 'change_username', 'list_tools', 'switch_model', 'switch_toolset'],
}

# Settings-gated tools — function_manager hides these entirely (not just
# refuses) when the gating setting is off. Default off: a fresh install's
# AI can't switch its own model/toolset until the human opts in.
SETTINGS_GATED = {
    'switch_model': 'AI_MODEL_SWITCH_ENABLED',
    'switch_toolset': 'AI_TOOLSET_SWITCH_ENABLED',
}

VALID_COMPONENTS = ['character', 'location', 'relationship', 'goals', 'format', 'scenario', 'emotions', 'extras']
LIST_COMPONENTS = ('emotions', 'extras')
# Factory state values a single-value component returns to on 'remove'
STATE_DEFAULTS = {
    'character': 'sapphire', 'location': 'default', 'relationship': 'friend',
    'goals': 'none', 'format': 'conversational', 'scenario': 'default',
}
MAX_TRANSIENT_MINUTES = 1440
PITCH_MIN, PITCH_MAX = 0.5, 1.5

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "prompt_view",
            "description": "View a system prompt. No name = current active with status header.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Prompt name"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "prompt_switch",
            "description": "Switch system prompt. No name = list available.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Prompt name"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "prompt_edit",
            "description": "Edit the active monolith prompt by exact text replacement. old_text must match exactly (same whitespace) and appear once — prompt_view first and copy it precisely.",
            "parameters": {
                "type": "object",
                "properties": {
                    "old_text": {"type": "string", "description": "Exact text to replace"},
                    "new_text": {"type": "string", "description": "Replacement text"},
                    "reason": {"type": "string", "description": "Why you're changing it — recorded next to the change in your memory ledger (optional)"}
                },
                "required": ["old_text", "new_text"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "prompt_create",
            "description": "Create a new named prompt (monolith). Does NOT activate it — use prompt_switch when ready.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "New prompt name (lowercase, no spaces)"},
                    "content": {"type": "string", "description": "Full prompt text"},
                    "reason": {"type": "string", "description": "Why — recorded next to the change in your memory ledger (optional)"}
                },
                "required": ["name", "content"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "prompt_pieces",
            "description": "Manage assembled-prompt pieces. Actions: list (all types, or keys of one), view (full text of a piece), set (activate — add minutes for a temporary change that auto-reverts), remove (deactivate; single-value components reset to default), create (save new piece to the library — does NOT activate), delete (remove from library).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "view", "set", "remove", "create", "delete"],
                        "description": "What to do"
                    },
                    "component": {
                        "type": "string",
                        "description": "character | location | relationship | goals | format | scenario | emotions | extras"
                    },
                    "key": {"type": "string", "description": "Piece key"},
                    "value": {"type": "string", "description": "Piece text (create only)"},
                    "minutes": {"type": "integer", "description": "Set only: activate temporarily for N minutes, then auto-revert"},
                    "reason": {"type": "string", "description": "Why — recorded next to the change in your memory ledger (optional)"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "set_voice",
            "description": "Set TTS voice, speed, and/or pitch (1.0 = normal). No arguments = list voices and current settings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Voice name"},
                    "speed": {"type": "number", "description": "Speech speed, 1.0 = normal"},
                    "pitch": {"type": "number", "description": "Voice pitch, 1.0 = normal"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "reset_chat",
            "description": "Clear chat history. Start fresh.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Reason"}
                },
                "required": ["reason"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "change_username",
            "description": "Change the user's name. Updates the prompt-facing setting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "New user name"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "list_tools",
            "description": "List tools. Default: currently enabled. scope='all' = every tool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["enabled", "all"],
                        "description": "Default enabled"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "switch_model",
            "description": "Switch your LLM to another model on the configured roster. No name = list switchable models and which is current. Takes effect on your next message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Model name or friendly name from the roster"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "switch_toolset",
            "description": "Switch your active toolset (e.g. coding tools, then back to conversation tools). No name = list available toolsets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Toolset name"}
                },
                "required": []
            }
        }
    },
]


class MetaError(Exception):
    """User-facing tool error — the message goes straight back to the model."""


def _system():
    from core.api_fastapi import get_system
    system = get_system()
    if not system:
        raise MetaError("System not ready.")
    return system


def _normalize_component(component: str) -> str:
    """Normalize component name: lowercase, strip, handle plurals."""
    if not component:
        return ""

    c = component.lower().strip()
    c = ''.join(ch for ch in c if ch.isalnum())

    mappings = {
        'goal': 'goals',
        'emotion': 'emotions',
        'extra': 'extras',
        'locations': 'location',
        'characters': 'character',
        'persona': 'character',
        'personas': 'character',
        'relationships': 'relationship',
        'formats': 'format',
        'scenarios': 'scenario',
    }

    return mappings.get(c, c)


def _normalize_name(name: str) -> str:
    """Normalize prompt/key name: lowercase, strip, clean punctuation."""
    if not name:
        return ""
    n = name.lower().strip()
    n = ''.join(ch for ch in n if ch.isalnum() or ch in '_- ')
    n = n.replace(' ', '_').replace('-', '_')
    return n


def _resolve_name(name, candidates):
    """Strictly match a name against real candidates: exact, then case-insensitive,
    then dash/underscore/space-collapsed. Returns the store's own name or None."""
    if not name:
        return None
    if name in candidates:
        return name
    low = name.lower().strip()
    for c in candidates:
        if c.lower() == low:
            return c
    norm = _normalize_name(name)
    for c in candidates:
        if _normalize_name(c) == norm:
            return c
    return None


def _require_component(component):
    if component not in VALID_COMPONENTS:
        raise MetaError(f"Invalid component '{component}'. Valid: {', '.join(VALID_COMPONENTS)}")


def _unknown_key_msg(component, key):
    from core import prompts
    available = list(prompts.prompt_manager.components.get(component, {}).keys())[:15]
    return f"'{key}' not found in {component}. Available: {', '.join(available)}"


def _exact_replace(text, old, new, what):
    """Harness-style exact replacement with coaching errors."""
    if not old:
        raise MetaError("old_text is required.")
    if old == new:
        raise MetaError("old_text and new_text are identical.")
    count = text.count(old)
    if count == 0:
        raise MetaError(f"old_text not found in {what} — whitespace must match exactly. View it first and copy the text precisely.")
    if count > 1:
        raise MetaError(f"old_text appears {count} times in {what} — include surrounding text to make it unique.")
    return text.replace(old, new, 1)


def _get_current_preset_name() -> str:
    """Get current preset name, preferring existing non-generic names."""
    from core import prompts
    from core.prompt_state import _assembled_state

    current = prompts.get_active_preset_name()
    if current and current not in ['assembled', 'unknown', 'random', '']:
        return current

    return _assembled_state.get('character', 'custom')


def _pieces_summary() -> str:
    """Comma list of active pieces; transient ones annotated with time left."""
    from core.prompt_state import _assembled_state, get_transients

    trans = get_transients()  # component -> [(key, minutes_left)]
    tmap = {}
    for comp, entries in trans.items():
        for key, mins in entries:
            tmap[(comp, key)] = mins

    pieces = []

    def add(comp, key):
        if not key or key in ('default', 'none', ''):
            return
        mins = tmap.get((comp, key))
        pieces.append(f"{key}({mins}m)" if mins else key)

    for comp in ('character', 'goals', 'location', 'scenario'):
        entries = trans.get(comp)
        if entries:  # transient shadows the persistent value
            add(comp, entries[0][0])
        else:
            add(comp, _assembled_state.get(comp, ''))

    for comp in LIST_COMPONENTS:
        persistent = _assembled_state.get(comp, [])
        for k in persistent:
            add(comp, k)
        for key, mins in trans.get(comp, []):
            if key not in persistent:
                pieces.append(f"{key}({mins}m)")

    return ', '.join(p for p in pieces if p)


def _status_string() -> str:
    """Status like 'albert(556): albert, mars, excited(12m)'."""
    from core import prompts
    prompt_data = prompts.get_current_prompt()
    content = prompt_data.get('content') if isinstance(prompt_data, dict) else str(prompt_data)
    return f"{_get_current_preset_name()}({len(content)}): {_pieces_summary()}"


def _resnapshot(system):
    """Re-assemble the active prompt and push it into the live chat."""
    from core import prompts
    prompt_data = prompts.get_current_prompt()
    content = prompt_data.get('content') if isinstance(prompt_data, dict) else str(prompt_data)
    system.llm_chat.set_system_prompt(content)


def _save_and_activate_assembled(system) -> str:
    """Persist current _assembled_state as the active preset and re-activate."""
    from core import prompts
    from core.prompt_state import _assembled_state

    preset_name = _get_current_preset_name()
    components = {}
    for k in ['character', 'location', 'relationship', 'goals', 'format', 'scenario']:
        if _assembled_state.get(k):
            components[k] = _assembled_state[k]
    for k in LIST_COMPONENTS:
        if _assembled_state.get(k):
            components[k] = list(_assembled_state[k])

    # Carry the preset's privacy flag through the rebuild — omitting it
    # defaulted privacy_required to False, so her wearing/removing any piece
    # on a privacy-flagged preset silently stripped its privacy gate.
    prev = prompts.get_prompt(preset_name)
    privacy_required = bool(isinstance(prev, dict) and prev.get('privacy_required'))

    # audit=False: the caller's activation event already describes this
    # change ("piece X added to Y") — a preset-diff row would double-log it.
    # The audit snapshot still refreshes so nothing shows up as a phantom
    # change on the next diff.
    ok, msg = prompts.save_prompt(preset_name, {"type": "assembled", "components": components,
                                                "privacy_required": privacy_required},
                                  audit=False)
    if not ok:
        raise MetaError(f"Failed to save preset: {msg}")
    ok, msg = prompts.activate_prompt(preset_name, system)
    if not ok:
        raise MetaError(f"Saved but failed to activate: {msg}")
    return preset_name


# === Tools ===

def _prompt_view(args):
    from core import prompts

    name = args.get('name')
    if not name:
        prompt_data = prompts.get_current_prompt()
        content = prompt_data.get('content') if isinstance(prompt_data, dict) else str(prompt_data)
        active = prompts.get_active_preset_name()
        mode = prompts.get_prompt_mode()
        header = f"[Active: {active} ({mode}, {len(content)} chars)]"
        if mode == 'assembled':
            header += f"\n[Pieces: {_pieces_summary()}]"
        return f"{header}\n\n{content}", True

    name = _resolve_name(name, prompts.list_prompts())
    prompt_data = prompts.get_prompt(name) if name else None
    if not prompt_data:
        return f"Prompt '{args.get('name')}' not found. Available: {', '.join(prompts.list_prompts())}", False
    content = prompt_data.get('content') if isinstance(prompt_data, dict) else str(prompt_data)
    prompt_type = prompt_data.get('type', 'unknown') if isinstance(prompt_data, dict) else 'monolith'
    return f"[{name} - {prompt_type}]\n\n{content}", True


def _prompt_switch(args):
    from core import prompts

    name = args.get('name')
    if not name:
        current = prompts.get_active_preset_name()
        lines = [f"Current: {current}", "Available prompts:"]
        for n in prompts.list_prompts():
            pdata = prompts.get_prompt(n)
            ptype = pdata.get('type', 'unknown') if isinstance(pdata, dict) else 'monolith'
            content = pdata.get('content', '') if isinstance(pdata, dict) else str(pdata)
            marker = " *" if n == current else ""
            lines.append(f"  {n} ({ptype}, {len(content)} chars){marker}")
        return '\n'.join(lines), True

    name = _resolve_name(name, prompts.list_prompts())
    prompt_data = prompts.get_prompt(name) if name else None
    if not prompt_data:
        return f"Prompt '{args.get('name')}' not found. Available: {', '.join(prompts.list_prompts())}", False

    prompts.clear_transients()  # a mood doesn't survive becoming someone else
    ok, msg = prompts.activate_prompt(name, _system())
    if not ok:
        return msg, False
    prompt_type = prompt_data.get('type', 'monolith') if isinstance(prompt_data, dict) else 'monolith'
    return f"Switched to '{name}' ({prompt_type}).", True


def _audit_emit(event):
    """Fire-and-forget prompt-ledger emit (core.audit) — never blocks the
    tool; with no sink registered it's a no-op."""
    try:
        from core.audit import emit
        emit(event)
    except Exception:
        pass


def _reason(args):
    return (args.get('reason') or '').strip() or None


def _prompt_edit(args):
    from core import prompts
    from core.event_bus import publish, Events

    current = prompts.get_active_preset_name()
    if not current:
        return "No active prompt to edit.", False
    prompt_data = prompts.get_prompt(current)
    if not prompt_data:
        return f"Active prompt '{current}' not found.", False
    if isinstance(prompt_data, dict) and prompt_data.get('type') == 'assembled':
        return "Active prompt is assembled — edit pieces with prompt_pieces instead.", False

    content = prompt_data.get('content', '') if isinstance(prompt_data, dict) else str(prompt_data)
    new_content = _exact_replace(content, args.get('old_text', ''), args.get('new_text', ''), f"prompt '{current}'")

    ok, msg = prompts.save_prompt(current, {"type": "monolith", "content": new_content},
                                  reason=_reason(args))
    if not ok:
        return f"Failed to save: {msg}", False
    publish(Events.PROMPT_CHANGED, {"name": current, "action": "saved"})

    ok, msg = prompts.activate_prompt(current, _system())
    if not ok:
        return f"Saved but failed to reload: {msg}", False
    return f"Updated prompt '{current}' ({len(new_content)} chars).", True


def _prompt_create(args):
    from core import prompts
    from core.event_bus import publish, Events

    name = _normalize_name(args.get('name', ''))
    content = args.get('content', '')
    if not name or not content:
        return "Both name and content are required.", False
    existing = _resolve_name(args.get('name', ''), prompts.list_prompts())
    if existing:
        return f"Prompt '{existing}' already exists — pick another name, or prompt_switch to it and use prompt_edit.", False

    ok, msg = prompts.save_prompt(name, {"type": "monolith", "content": content},
                                  reason=_reason(args))
    if not ok:
        return f"Failed to create: {msg}", False
    publish(Events.PROMPT_CHANGED, {"name": name, "action": "saved"})
    return f"Created prompt '{name}' ({len(content)} chars). Not active — use prompt_switch('{name}') when ready.", True


def _prompt_pieces(args):
    from core import prompts
    from core.event_bus import publish, Events
    from core.prompt_state import _assembled_state, _state_lock, get_transients

    action = (args.get('action') or '').lower().strip()
    component = _normalize_component(args.get('component', ''))
    comps = prompts.prompt_manager.components
    key = args.get('key', '')
    key = _resolve_name(key, comps.get(component, {})) or _normalize_name(key)

    def _act(key_, active, ttl=None):
        e = {'kind': 'activation', 'comp_type': component, 'key': key_,
             'active': active, 'prompt': prompts.get_active_preset_name(),
             'actor': 'ai', 'reason': _reason(args)}
        if ttl:
            e['ttl_minutes'] = ttl
        _audit_emit(e)

    if action == 'list':
        if not component:
            trans = get_transients()
            lines = ["Component types (active marked, temporary pieces show minutes left):"]
            for c in VALID_COMPONENTS:
                available = len(comps.get(c, {}))
                if c in LIST_COMPONENTS:
                    active = list(_assembled_state.get(c, []))
                    active += [f"{k}({m}m)" for k, m in trans.get(c, []) if k not in active]
                    active_str = ', '.join(active) if active else 'none'
                else:
                    tv = trans.get(c)
                    active_str = f"{tv[0][0]}({tv[0][1]}m)" if tv else _assembled_state.get(c, 'none')
                lines.append(f"  {c} ({available} available) — active: {active_str}")
            lines.append("prompt_pieces(action='list', component='X') to see keys.")
            return '\n'.join(lines), True
        _require_component(component)
        items = comps.get(component, {})
        if not items:
            return f"No {component} pieces available.", True
        lines = [f"Available {component}:"]
        for k, v in items.items():
            preview = v[:80].replace('\n', ' ') + ('...' if len(v) > 80 else '')
            lines.append(f"  {k}: {preview}")
        return '\n'.join(lines), True

    if action == 'view':
        _require_component(component)
        if not key:
            return "key is required for view.", False
        value = comps.get(component, {}).get(key)
        if value is None:
            return _unknown_key_msg(component, key), False
        return f"[{component}/{key}]\n\n{value}", True

    if action == 'set':
        _require_component(component)
        if not key:
            return "key is required for set.", False
        if key not in comps.get(component, {}):
            return _unknown_key_msg(component, key), False
        system = _system()
        minutes = args.get('minutes')
        if minutes:
            minutes = max(1, min(int(minutes), MAX_TRANSIENT_MINUTES))
            prompts.set_transient_piece(component, key, minutes)
            _resnapshot(system)
            _act(key, True, ttl=minutes)
            return f"Set {component}='{key}' for {minutes}m (temporary — reverts automatically). {_status_string()}", True
        with _state_lock:
            if component in LIST_COMPONENTS:
                if key in _assembled_state.get(component, []):
                    return f"'{key}' already in {component}.", True
                _assembled_state.setdefault(component, []).append(key)
            else:
                _assembled_state[component] = key
        _save_and_activate_assembled(system)
        _act(key, True)
        verb = "Added" if component in LIST_COMPONENTS else "Set"
        return f"{verb} {component}='{key}'. {_status_string()}", True

    if action == 'remove':
        _require_component(component)
        system = _system()
        # Transient entries first — dropping one never touches the preset
        if key and prompts.remove_transient_piece(component, key):
            _resnapshot(system)
            _act(key, False)
            return f"Removed temporary {component} '{key}'. {_status_string()}", True
        if component in LIST_COMPONENTS:
            if not key:
                return "key is required to remove from emotions/extras.", False
            with _state_lock:
                if key not in _assembled_state.get(component, []):
                    return f"'{key}' not in current {component}.", False
                _assembled_state[component].remove(key)
            _save_and_activate_assembled(system)
            _act(key, False)
            return f"Removed '{key}' from {component}. {_status_string()}", True
        # Single-value component: clear any transient shadow, reset to default
        trans = get_transients()
        had_transient = component in trans
        if had_transient:
            prompts.remove_transient_piece(component, trans[component][0][0])
        default = STATE_DEFAULTS.get(component, 'default')
        with _state_lock:
            prev = _assembled_state.get(component)
            _assembled_state[component] = default
        # No-op guard (scout find 2026-07-22): already at default and no
        # transient to clear → nothing changed; a save + "piece removed"
        # ledger row here was ghost evidence.
        if not had_transient and prev in (None, default):
            return f"{component} is already '{default}'.", True
        _save_and_activate_assembled(system)
        if prev not in (None, default):
            _act(prev, False)
        return f"Reset {component} to '{default}'. {_status_string()}", True

    if action == 'create':
        _require_component(component)
        value = args.get('value', '')
        if not key or not value:
            return "Both key and value are required for create.", False
        # Write the PRIVATE dict — `.components` is a merged COPY when plugin
        # prompt-packs are registered; mutating it is silently lost (same trap
        # as the web routes, see content.py). Shadowing a pack piece is the
        # intended edit path (user wins the merge).
        prompts.prompt_manager._components.setdefault(component, {})[key] = value
        prompts.prompt_manager.save_components(reason=_reason(args))
        publish(Events.COMPONENTS_CHANGED, {"type": component, "key": key})
        return (f"Created {component}/'{key}' in the library. Not active — "
                f"prompt_pieces(action='set', component='{component}', key='{key}') to wear it."), True

    if action == 'delete':
        _require_component(component)
        if not key:
            return "key is required for delete.", False
        if key == 'default':
            return "Can't delete the 'default' piece.", False
        if key not in comps.get(component, {}):
            return _unknown_key_msg(component, key), False
        if component in LIST_COMPONENTS:
            active = key in _assembled_state.get(component, [])
        else:
            active = _assembled_state.get(component) == key
        active = active or any(k == key for k, _ in get_transients().get(component, []))
        if active:
            return f"'{key}' is currently active — remove it first, then delete.", False
        # Delete from the PRIVATE dict — pack pieces are read-only (deleting
        # from the merged copy would claim success while changing nothing).
        user_comps = prompts.prompt_manager._components
        if key not in user_comps.get(component, {}):
            from core import prompt_packs
            owner = prompt_packs.piece_source(component, key)
            return (f"'{component}/{key}' is shipped by plugin '{owner or 'a plugin'}' — "
                    f"read-only. Disable the plugin to remove it.", False)
        del user_comps[component][key]
        prompts.prompt_manager.save_components(reason=_reason(args))
        publish(Events.COMPONENTS_CHANGED, {"type": component, "key": key, "action": "deleted"})
        return f"Deleted {component}/{key} from the library.", True

    return f"Unknown action '{action}'. Valid: list, view, set, remove, create, delete.", False


def _set_voice(args):
    import config as app_config
    from core.event_bus import publish, Events

    system = _system()
    provider = getattr(system.tts, 'provider', None)
    prov_name = getattr(app_config, 'TTS_PROVIDER', 'none')
    voices = []
    if provider and hasattr(provider, 'list_voices'):
        try:
            voices = provider.list_voices() or []
        except Exception as e:
            logger.error(f"Failed to list voices: {e}")
    speed_min = float(getattr(provider, 'SPEED_MIN', 0.5) or 0.5)
    speed_max = float(getattr(provider, 'SPEED_MAX', 2.0) or 2.0)

    sm = system.llm_chat.session_manager
    current = sm.get_chat_settings() or {}
    name = (args.get('name') or '').strip()
    speed = args.get('speed')
    pitch = args.get('pitch')

    if not name and speed is None and pitch is None:
        lines = [
            f"Provider: {prov_name}",
            f"Current: voice={current.get('voice', 'default')}, speed={current.get('speed', 1.0)}, pitch={current.get('pitch', 1.0)}",
            f"Ranges: speed {speed_min}-{speed_max}, pitch {PITCH_MIN}-{PITCH_MAX} (1.0 = normal)",
            "Available voices:",
        ]
        categories = {}
        for v in voices:
            categories.setdefault(v.get('category', 'Other'), []).append(v)
        for cat, cat_voices in categories.items():
            lines.append(f"  {cat}: {', '.join(v['voice_id'] for v in cat_voices)}")
        return '\n'.join(lines), True

    updates = {}
    if name:
        name_lower = name.lower()
        match = next((v['voice_id'] for v in voices
                      if v.get('voice_id', '').lower() == name_lower or v.get('name', '').lower() == name_lower), None)
        if not match:
            return f"Voice '{name}' not found. Use set_voice without params to list available voices.", False
        updates['voice'] = match
    if speed is not None:
        updates['speed'] = round(min(max(float(speed), speed_min), speed_max), 2)
    if pitch is not None:
        updates['pitch'] = round(min(max(float(pitch), PITCH_MIN), PITCH_MAX), 2)

    if not sm.update_chat_settings(updates):
        return "Failed to update chat settings.", False
    # Apply ONLY the keys we changed. _apply_chat_settings(full dict) would
    # also re-apply prompt/scopes/spice/toolset — snapping a live toolset
    # back to the persisted one mid-conversation.
    try:
        if 'voice' in updates:
            system.tts.set_voice(updates['voice'])
        if 'speed' in updates:
            system.tts.set_speed(updates['speed'])
        if 'pitch' in updates:
            system.tts.set_pitch(updates['pitch'])
    except Exception as e:
        logger.error(f"Error applying TTS settings live: {e}")
        return f"Saved, but failed to apply live: {e}", False
    publish(Events.CHAT_SETTINGS_CHANGED, {"chat": sm._effective_chat_name(), "settings": updates, "origin": None})

    changed = ', '.join(f"{k}={v}" for k, v in updates.items())
    logger.info(f"Voice settings changed: {changed}")
    return f"Voice settings updated: {changed}.", True


def _reset_chat(args):
    from core import prompts

    reason = args.get('reason')
    if not reason:
        return "A reason is required.", False

    logger.info(f"AI INITIATED CHAT RESET - Reason: {reason}")
    sm = _system().llm_chat.session_manager
    sm.clear()  # clears + persists the EFFECTIVE chat and publishes CHAT_CLEARED
    prompts.clear_transients()
    return f"Chat reset. Reason: {reason}", True


def _change_username(args):
    from core.settings_manager import settings

    name = (args.get('name') or '').strip()
    if not name:
        return "Name is required.", False
    settings.set('DEFAULT_USERNAME', name, persist=True)
    logger.info(f"Username changed to: {name}")
    return f"Username changed to {name}. This will appear in prompts using {{user_name}}.", True


def _list_tools(args):
    scope = (args.get('scope') or 'enabled').lower().strip()
    fm = _system().llm_chat.function_manager

    def short(desc):
        return desc[:60] + ('...' if len(desc) > 60 else '')

    if scope == 'all':
        enabled = set(fm.get_enabled_function_names())
        lines = []
        total = 0
        for module_name, module_info in sorted(fm.function_modules.items()):
            for tool in module_info.get('tools', []):
                func_name = tool['function']['name']
                total += 1
                status = "✓" if func_name in enabled else "✗"
                lines.append(f"  [{status}] {func_name}: {short(tool['function'].get('description', ''))}")
        return '\n'.join([f"All tools ({total} total):"] + lines + ["\n✓ = enabled, ✗ = inactive"]), True

    enabled = list(fm.get_enabled_function_names())
    desc_map = {t['function']['name']: t['function'].get('description', '')
                for mi in fm.function_modules.values() for t in mi.get('tools', [])}
    lines = [f"Enabled tools ({len(enabled)}) - Toolset: {fm.current_toolset_name}"]
    for n in enabled:
        lines.append(f"  {n}: {short(desc_map.get(n, ''))}")
    if not enabled:
        lines.append("  (no tools enabled)")
    return '\n'.join(lines), True


def _switch_model(args):
    import config as app_config

    # Belt+suspenders — function_manager already hides this tool when the
    # gate is off, but never trust visibility alone (silent-default class).
    if not getattr(app_config, 'AI_MODEL_SWITCH_ENABLED', False):
        return "Model switching is disabled. Enable it in Settings > Tools.", False

    from core.chat.llm_providers import provider_registry, set_active_model
    roster = [k for k in (getattr(app_config, 'AI_MODEL_SWITCH_ROSTER', None) or [])]
    if not roster:
        return "No models are configured for switching — add them in Settings > Tools.", False

    providers = {p['key']: p for p in provider_registry.get_all_providers()}
    system = _system()
    current = (system.llm_chat.session_manager.get_chat_settings() or {}).get('llm_primary', 'auto')
    ratchet = bool(getattr(app_config, 'AI_MODEL_SWITCH_RATCHET', False))

    def label(key):
        p = providers.get(key, {})
        kind = 'local' if p.get('is_local') else 'cloud'
        return f"{p.get('display_name', key)} ({key}, {kind})"

    name = (args.get('name') or '').strip()
    if not name:
        lines = [f"Current model: {label(current) if current in providers else current}"]
        if ratchet:
            lines.append("Privacy ratchet is ON — switches only move DOWN this list, never back up.")
            if current not in roster:
                lines.append("You're above the list (least private) — you can switch to any model below, "
                             "then privacy only rolls downhill from there.")
        lines.append("Switchable models (top = least private):")
        for i, key in enumerate(roster, 1):
            if key not in providers:
                continue
            marker = "  <- current" if key == current else ""
            lines.append(f"  {i}. {label(key)}{marker}")
        lines.append("switch_model(name) to switch — takes effect on your next message.")
        return '\n'.join(lines), True

    name_lower = name.lower()
    target = next((k for k in roster
                   if k.lower() == name_lower
                   or providers.get(k, {}).get('display_name', '').lower() == name_lower), None)
    if not target:
        return f"'{name}' is not on the switch roster. Call switch_model without params to see options.", False
    if target == current:
        return f"Already on {label(target)}.", True
    # Privacy ratchet, option B: 'auto' (and any current not on the roster) is the
    # invisible top rung — least private. So a first switch away from 'auto' is
    # always "downhill" and allowed; the ratchet only locks once she's pinned to a
    # concrete roster model. `current in roster` being false here is intentional,
    # not a bypass — don't "fix" it.
    if ratchet and current in roster and roster.index(target) < roster.index(current):
        return (f"Privacy ratchet: can't switch back up to "
                f"{providers.get(target, {}).get('display_name', target)} — this chat moved to "
                f"{providers.get(current, {}).get('display_name', current)} and privacy only rolls "
                f"downhill. The user can change it in the sidebar."), False

    ok, msg = set_active_model(system, target)
    if not ok:
        return msg, False
    logger.info(f"AI switched model: {current} -> {target}")
    return f"Switching to {msg} — takes effect on my next message.", True


def _switch_toolset(args):
    import config as app_config

    if not getattr(app_config, 'AI_TOOLSET_SWITCH_ENABLED', False):
        return "Toolset switching is disabled. Enable it in Settings > Tools.", False

    from core.event_bus import publish, Events
    from core.toolsets import toolset_manager

    system = _system()
    fm = system.llm_chat.function_manager
    available = sorted(set(list(fm.get_available_toolsets())
                           + list(toolset_manager.get_toolset_names())
                           + ['all', 'none']))

    name = (args.get('name') or '').strip()
    if not name:
        current = fm.current_toolset_name
        lines = [f"Current toolset: {current}", "Available:"]
        lines += [f"  {n}{'  <- current' if n == current else ''}" for n in available]
        return '\n'.join(lines), True

    match = next((n for n in available if n.lower() == name.lower()), None)
    if not match:
        return f"Toolset '{name}' not found. Available: {', '.join(available)}", False

    # The chat's extra_toolsets stay on across a deliberate toolset switch —
    # settings still claim them (the "include story tools" checkbox), so the
    # runtime must keep matching (extras-decay site #9, 2026-08-05).
    extras = None
    try:
        extras = (system.llm_chat.session_manager.get_chat_settings()
                  or {}).get('extra_toolsets') or None
    except Exception:
        pass
    fm.update_enabled_functions([match], extra_toolsets=extras)
    system.llm_chat.session_manager.update_chat_settings({"toolset": match})
    publish(Events.TOOLSET_CHANGED, {"name": match})
    logger.info(f"AI switched toolset to: {match}")

    note = ""
    if 'switch_toolset' not in fm.get_enabled_function_names():
        note = (" Note: this toolset doesn't include switch_toolset — one-way door. "
                "The user switches back in the sidebar, or add switch_toolset to that set.")
    return f"Switched to toolset '{match}' ({len(fm.get_enabled_function_names())} tools).{note}", True


_HANDLERS = {
    'prompt_view': _prompt_view,
    'prompt_switch': _prompt_switch,
    'prompt_edit': _prompt_edit,
    'prompt_create': _prompt_create,
    'prompt_pieces': _prompt_pieces,
    'set_voice': _set_voice,
    'reset_chat': _reset_chat,
    'change_username': _change_username,
    'list_tools': _list_tools,
    'switch_model': _switch_model,
    'switch_toolset': _switch_toolset,
}


def execute(function_name, arguments, config):
    """Execute meta-related functions."""
    try:
        handler = _HANDLERS.get(function_name)
        if not handler:
            return f"Unknown function: {function_name}", False
        return handler(arguments or {})
    except MetaError as e:
        return str(e), False
    except Exception as e:
        logger.error(f"Meta function error for '{function_name}': {e}", exc_info=True)
        return f"Error in {function_name}: {str(e)}", False
