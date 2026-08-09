import logging
import random
import threading
import time
from .prompt_manager import prompt_manager

logger = logging.getLogger(__name__)

# Runtime state (not in JSON) — guarded by _state_lock for thread safety
_state_lock = threading.Lock()
_assembled_state = {
    "character": "sapphire",
    "location": "default",
    "relationship": "friend",
    "goals": "none",
    "format": "conversational",
    "scenario": "default",
    "extras": [],
    "emotions": [],
    "spice": "",
    "active_preset": "default"
}

# Transient piece overlay — try-on pieces with a TTL. Shadows _assembled_state
# during assembly but never mutates it or the preset files: expiry just deletes
# the overlay entry and the persistent value shows through again. Expiry is
# lazy — expire_transients() runs on the same per-turn rail as spice refresh.
# Guarded by _state_lock. Shape: {component: (key, expires_at)} for
# single-value components, {component: [(key, expires_at), ...]} for
# extras/emotions.
_transient_overlay = {}


def set_transient_piece(component, key, minutes):
    """Activate a piece for N minutes without touching persistent state."""
    expires = time.time() + minutes * 60
    with _state_lock:
        if component in ("extras", "emotions"):
            entries = [e for e in _transient_overlay.get(component, []) if e[0] != key]
            entries.append((key, expires))
            _transient_overlay[component] = entries
        else:
            _transient_overlay[component] = (key, expires)


def remove_transient_piece(component, key):
    """Drop a transient entry. Returns True if something was removed."""
    with _state_lock:
        entry = _transient_overlay.get(component)
        if entry is None:
            return False
        if component in ("extras", "emotions"):
            kept = [e for e in entry if e[0] != key]
            if len(kept) == len(entry):
                return False
            if kept:
                _transient_overlay[component] = kept
            else:
                del _transient_overlay[component]
            return True
        if entry[0] == key:
            del _transient_overlay[component]
            return True
        return False


def clear_transients():
    """Drop all transient pieces (prompt switch, chat reset)."""
    with _state_lock:
        had = bool(_transient_overlay)
        _transient_overlay.clear()
    return had


def expire_transients():
    """Drop expired transient pieces. Returns True if anything was dropped."""
    now = time.time()
    dropped = False
    with _state_lock:
        for comp in list(_transient_overlay.keys()):
            entry = _transient_overlay[comp]
            if isinstance(entry, list):
                kept = [e for e in entry if e[1] > now]
                if len(kept) != len(entry):
                    dropped = True
                    if kept:
                        _transient_overlay[comp] = kept
                    else:
                        del _transient_overlay[comp]
            elif entry[1] <= now:
                del _transient_overlay[comp]
                dropped = True
    return dropped


def get_transients():
    """Snapshot of live transients: {component: [(key, minutes_left), ...]}."""
    now = time.time()
    out = {}
    with _state_lock:
        for comp, entry in _transient_overlay.items():
            entries = entry if isinstance(entry, list) else [entry]
            live = [(k, max(1, int(round((exp - now) / 60)))) for k, exp in entries if exp > now]
            if live:
                out[comp] = live
    return out


def get_current_state():
    """Get the current prompt's component state for UI display."""
    active_name = get_active_preset_name()
    if not active_name or active_name == 'unknown':
        return {}

    if active_name in prompt_manager.scenario_presets:
        # Underscore keys (_privacy_required) are preset metadata, not
        # components — don't ship them as phantom pieces in status payloads.
        return {k: v for k, v in prompt_manager.scenario_presets[active_name].items()
                if not k.startswith('_')}
    if active_name in prompt_manager.monoliths:
        return {
            'character': 'monolith',
            'location': 'n/a',
            'goals': 'n/a'
        }
    return {}


def get_active_preset_name():
    """Get the name of the currently active prompt."""
    if hasattr(prompt_manager, '_active_preset_name'):
        return prompt_manager._active_preset_name
    return 'unknown'


def is_current_prompt_private():
    """Check if the currently active prompt requires privacy mode."""
    preset = get_active_preset_name()
    if not preset or preset == 'unknown':
        return False

    # Check monoliths
    if preset in prompt_manager.monoliths:
        mono = prompt_manager.monoliths[preset]
        if isinstance(mono, dict):
            return mono.get('privacy_required', False)
        return False

    # Check scenario presets
    if preset in prompt_manager.scenario_presets:
        components = prompt_manager.scenario_presets[preset]
        return components.get('_privacy_required', False)

    return False


def set_active_preset_name(name: str):
    """Track which preset is currently active. Publishes event for all consumers."""
    global _assembled_state
    prompt_manager._active_preset_name = name
    _assembled_state["active_preset"] = name
    from core.event_bus import publish, Events
    publish(Events.PROMPT_CHANGED, {"name": name, "action": "loaded"})


def get_prompt_char_count():
    """Get character count of active prompt."""
    from .prompt_crud import get_prompt
    
    active_name = get_active_preset_name()
    if not active_name or active_name == 'unknown':
        return 0
    
    prompt_data = get_prompt(active_name)
    if not prompt_data:
        return 0
    
    content = prompt_data.get('content', '')
    return len(content)


def get_current_prompt():
    """Get the currently active prompt (monolith or assembled).

    If the active_preset names a prompt that doesn't exist (e.g. deleted
    after a chat was configured with it), log a WARN and fall back to
    assembled default. Pre-2026-04-22 this was silent. H3 fix — surfaces
    the silent-default class so a deleted prompt doesn't quietly become
    whatever happens to be in _assembled_state.
    """
    preset = _assembled_state.get("active_preset", "default")

    if preset in prompt_manager.monoliths:
        mono = prompt_manager.monoliths[preset]
        text = mono.get('content', '') if isinstance(mono, dict) else mono
        return {"role": "system", "content": prompt_manager._replace_templates(text)}

    # Not a monolith — is it a known scenario preset, or a missing name?
    if preset != "default" and preset not in prompt_manager.scenario_presets:
        logger.warning(
            f"active_preset='{preset}' not found in monoliths or scenario "
            f"presets — falling back to assembled default. Was it deleted "
            f"without updating the active state?"
        )

    return assemble_prompt()


def reset_to_defaults():
    """Reset to default assembled state."""
    global _assembled_state
    with _state_lock:
        _assembled_state = {
            "character": "sapphire",
            "location": "default",
            "relationship": "friend",
            "goals": "none",
            "format": "conversational",
            "scenario": "default",
            "extras": [],
            "emotions": [],
            "spice": "",
            "next_spice": "",
            "active_preset": "default"
        }
    return "Reset to default state"


def _pick_spice(exclude=""):
    """Pick a random spice, avoiding exclude if possible."""
    all_spices = prompt_manager.get_enabled_spices()
    if not all_spices:
        return ""
    candidates = [s for s in all_spices if s != exclude]
    return random.choice(candidates) if candidates else random.choice(all_spices)


def set_random_spice():
    """Set a random spice from enabled categories in the pool."""
    global _assembled_state
    # Use pre-picked next if available, otherwise pick fresh
    if _assembled_state.get("next_spice"):
        _assembled_state["spice"] = _assembled_state["next_spice"]
    else:
        _assembled_state["spice"] = _pick_spice()

    if not _assembled_state["spice"]:
        _assembled_state["next_spice"] = ""
        return "No spices available"

    # Pre-pick next (avoid repeating current)
    _assembled_state["next_spice"] = _pick_spice(exclude=_assembled_state["spice"])
    return f"Random spice: {_assembled_state['spice']}"


def clear_spice():
    """Clear the current and next spice."""
    global _assembled_state
    _assembled_state["spice"] = ""
    _assembled_state["next_spice"] = ""
    return "Spice cleared"


def invalidate_spice_picks():
    """Re-pick current and next spice from the updated enabled pool.
    Called when spice categories change (enable/disable)."""
    global _assembled_state
    enabled = prompt_manager.get_enabled_spices()
    # Clear next so it gets lazy-re-picked from new pool
    _assembled_state["next_spice"] = ""
    # If current spice is no longer in enabled pool, clear it too
    if _assembled_state.get("spice") and _assembled_state["spice"] not in enabled:
        _assembled_state["spice"] = ""


def get_current_spice():
    """Get the currently active spice text, or empty string if none."""
    return _assembled_state.get("spice", "")


def get_next_spice():
    """Get the pre-picked next spice text. Lazy-picks if empty and pool has spices."""
    global _assembled_state
    if not _assembled_state.get("next_spice"):
        _assembled_state["next_spice"] = _pick_spice(exclude=_assembled_state.get("spice", ""))
    return _assembled_state.get("next_spice", "")


def assemble_prompt():
    """Assemble the live prompt from current state + transient overlay.

    Rendering itself lives in prompt_manager.assemble_from_components — the
    single renderer shared with every CRUD/preview path. This function only
    contributes the runtime pieces: the state snapshot (thread-safe), the
    transient overlay, and template replacement for going live.

    Spice DOES NOT go into the assembled system prompt. Pre-2026-05-08 this
    appended `URGENT ALERT: {spice}`, which mutated the system prompt every
    rotation and broke Claude's prompt cache. Spice rides the ghost-message
    rail (core/ghost_messages.py) or, with SPICE_DELIVERY='system', is woven
    in by _get_system_prompt — never here. The prompt stays cacheable.
    """
    # Snapshot mutable state under lock to prevent iteration crash
    with _state_lock:
        state = {k: (list(v) if isinstance(v, list) else v) for k, v in _assembled_state.items()}
        # Transient overlay shadows persistent state (unexpired entries only).
        # Expired entries are skipped here and physically removed by
        # expire_transients() on the per-turn rail.
        now = time.time()
        for comp, entry in _transient_overlay.items():
            if isinstance(entry, list):
                for key, exp in entry:
                    if exp > now and key not in state.get(comp, []):
                        state.setdefault(comp, []).append(key)
            elif entry[1] > now:
                state[comp] = entry[0]

    assembled = prompt_manager.assemble_from_components(state)
    return {"role": "system", "content": prompt_manager._replace_templates(assembled)}


def is_assembled_mode():
    """Check if currently using piece-based assembly.

    'default' IS assembled mode — it's the sentinel for "current assembled
    state", where delete-active / missing-prompt / pack-unregister handoffs
    land. Before 2026-08-09 it fell through to monolith mode there: spice
    never rotated again, expired transient pieces stuck in the live prompt,
    and the piece tools were mode-filtered away while prompt_edit refused
    with "use prompt_pieces" — a lockout with contradictory errors.
    """
    preset = _assembled_state.get("active_preset", "default")
    return (preset in ("assembled", "default")
            or preset in prompt_manager.scenario_presets)


def get_prompt_mode() -> str:
    """Get current prompt mode as string for tool filtering."""
    return "assembled" if is_assembled_mode() else "monolith"


def apply_scenario(scenario_name):
    """Apply a scenario preset (piece-based)."""
    global _assembled_state
    if scenario_name not in prompt_manager.scenario_presets:
        return f"Unknown scenario: {scenario_name}"

    scenario = prompt_manager.scenario_presets[scenario_name]
    with _state_lock:
        for component_type, value in scenario.items():
            # Underscore keys (_privacy_required) are preset metadata, not
            # components — keep them out of runtime state.
            if component_type.startswith('_'):
                continue
            _assembled_state[component_type] = value.copy() if component_type in ["extras", "emotions"] else value
        _assembled_state["active_preset"] = scenario_name
    return f"Applied scenario: {scenario_name}"