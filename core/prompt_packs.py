# core/prompt_packs.py
"""Prompt-pack registry — plugins ship prompts via capabilities.prompts.

Mirror-only: pack content lives here in memory, merged into the prompt
system at READ time (PromptManager properties). It never touches
user/prompts/*.json — the save paths persist only the private user dicts.
Disabling the plugin makes its prompts vanish (dark, never deleted).
User entries always win name collisions; shadowed pack entries are logged.

Same registry pattern as core/memory_layers.py (2026-07-12).
"""
import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# plugin_name -> {"monoliths": {name: {content, privacy_required, kind}},
#                 "components": {type: {key: text}},
#                 "scenario_presets": {name: {component: value}},
#                 "kind": pack-level kind for pieces/presets}
_packs = {}

# kind taxonomy — visibility is DERIVED (kind != 'user' hides from pickers):
#   user     — default; visible everywhere
#   story    — rendered story costumes (game-room); hidden from all pickers
#   internal — engine scaffolding pieces; hidden from accordions
KINDS = ('user', 'story', 'internal')


def _norm_kind(kind, plugin_name, where=''):
    """Coerce unknown kinds to 'user' (fail-open to VISIBLE — a typo should
    not silently vanish someone's prompts)."""
    if kind is None:
        return 'user'
    if kind in KINDS:
        return kind
    logger.warning(f"[PROMPT-PACKS] {plugin_name}: unknown kind '{kind}'{where} — treating as 'user'")
    return 'user'


def register_pack(plugin_name, monoliths=None, pieces=None, kind=None):
    """Register a plugin's prompt pack. `monoliths` and `pieces` use the same
    JSON shapes as the user files (pieces = {"components": ..., "scenario_presets": ...}).
    `kind` is the pack-level default for every entry; a monolith dict may
    carry its own 'kind' to override it (dynamic story costumes do).
    Returns (counts dict, message). Re-registration replaces the pack."""
    pack_kind = _norm_kind(kind, plugin_name)
    norm_monoliths = {}
    for k, v in (monoliths or {}).items():
        if k.startswith('_'):
            continue
        if isinstance(v, str):
            norm_monoliths[k] = {'content': v, 'privacy_required': False, 'kind': pack_kind}
        elif isinstance(v, dict) and isinstance(v.get('content'), str):
            v = dict(v)
            v['kind'] = _norm_kind(v.get('kind', pack_kind), plugin_name, f" (monolith '{k}')")
            norm_monoliths[k] = v
        else:
            logger.warning(f"[PROMPT-PACKS] {plugin_name}: skipping monolith '{k}' (bad shape)")

    pieces = pieces or {}
    components = {}
    for ctype, entries in (pieces.get('components') or {}).items():
        if isinstance(entries, dict):
            components[ctype] = {k: v for k, v in entries.items()
                                 if isinstance(v, str) and not k.startswith('_')}
    presets = {k: v for k, v in (pieces.get('scenario_presets') or {}).items()
               if isinstance(v, dict) and not k.startswith('_')}

    with _lock:
        _packs[plugin_name] = {
            'monoliths': norm_monoliths,
            'components': components,
            'scenario_presets': presets,
            'kind': pack_kind,
        }

    counts = {'monoliths': len(norm_monoliths),
              'components': sum(len(v) for v in components.values()),
              'scenario_presets': len(presets)}
    logger.info(f"[PROMPT-PACKS] {plugin_name}: registered {counts}")
    _publish_changed()
    return counts


def unregister_plugin(plugin_name):
    """Drop a plugin's pack (plugin disable/unload). If the active preset was
    one of its names and no user entry shadows it, hand off to default loudly
    — same silent-default discipline as prompt_crud.delete_prompt (H3)."""
    with _lock:
        pack = _packs.pop(plugin_name, None)
    if not pack:
        return
    logger.info(f"[PROMPT-PACKS] {plugin_name}: unregistered")
    try:
        from core import prompt_state
        from core.prompt_manager import prompt_manager
        active = prompt_state.get_active_preset_name()
        gone = set(pack['monoliths']) | set(pack['scenario_presets'])
        if active in gone and active not in prompt_manager._monoliths \
                and active not in prompt_manager._scenario_presets:
            prompt_state.set_active_preset_name('default')
            logger.warning(
                f"[PROMPT-PACKS] Active prompt '{active}' came from disabled "
                f"plugin '{plugin_name}' — active preset reset to 'default'."
            )
            # Re-render the live prompt — without this the running chat kept
            # speaking with the unregistered pack's text until the next
            # activation (ghost-costume window).
            from core.prompt_crud import revalidate_active
            revalidate_active(reason=f"pack '{plugin_name}' unregistered")
    except Exception as e:
        logger.warning(f"[PROMPT-PACKS] active-preset handoff failed: {e}")
    _publish_changed()


def overlay_monoliths():
    """Merged monoliths across all packs. Cross-pack collisions: first
    registrant (dict order) wins; later ones are logged and skipped."""
    out = {}
    with _lock:
        for pname, pack in _packs.items():
            for k, v in pack['monoliths'].items():
                if k in out:
                    logger.warning(f"[PROMPT-PACKS] monolith '{k}' from {pname} shadowed by another pack")
                    continue
                out[k] = v
    return out


def overlay_components():
    """Merged components across all packs: {type: {key: text}}."""
    out = {}
    with _lock:
        for pname, pack in _packs.items():
            for ctype, entries in pack['components'].items():
                slot = out.setdefault(ctype, {})
                for k, v in entries.items():
                    if k in slot:
                        logger.warning(f"[PROMPT-PACKS] piece '{ctype}/{k}' from {pname} shadowed by another pack")
                        continue
                    slot[k] = v
    return out


def overlay_presets():
    """Merged scenario presets across all packs."""
    out = {}
    with _lock:
        for pname, pack in _packs.items():
            for k, v in pack['scenario_presets'].items():
                if k not in out:
                    out[k] = v
    return out


def get_sources():
    """{name: plugin_name} for monoliths+presets (name-level badge lookup)."""
    out = {}
    with _lock:
        for pname, pack in _packs.items():
            for k in pack['monoliths']:
                out.setdefault(k, pname)
            for k in pack['scenario_presets']:
                out.setdefault(k, pname)
    return out


def piece_source(ctype, key):
    """Owning plugin of a component piece, or None."""
    with _lock:
        for pname, pack in _packs.items():
            if key in pack['components'].get(ctype, {}):
                return pname
    return None


def component_sources():
    """{type: {key: plugin_name}} for every pack piece — UI badge lookup."""
    out = {}
    with _lock:
        for pname, pack in _packs.items():
            for ctype, entries in pack['components'].items():
                slot = out.setdefault(ctype, {})
                for k in entries:
                    slot.setdefault(k, pname)
    return out


def get_kinds():
    """{name: kind} for monoliths + presets across all packs. First
    registrant wins, matching the overlay collision rule."""
    out = {}
    with _lock:
        for pname, pack in _packs.items():
            for k, v in pack['monoliths'].items():
                out.setdefault(k, v.get('kind', 'user'))
            for k in pack['scenario_presets']:
                out.setdefault(k, pack.get('kind', 'user'))
    return out


def component_kinds():
    """{type: {key: kind}} for every pack piece (pack-level kind)."""
    out = {}
    with _lock:
        for pname, pack in _packs.items():
            for ctype, entries in pack['components'].items():
                slot = out.setdefault(ctype, {})
                for k in entries:
                    slot.setdefault(k, pack.get('kind', 'user'))
    return out


def has_packs():
    with _lock:
        return bool(_packs)


def _publish_changed():
    try:
        from core.event_bus import publish, Events
        publish(Events.PROMPT_CHANGED, {"name": "", "action": "packs_changed"})
    except Exception:
        pass  # Event bus may not be up during early boot / tests
