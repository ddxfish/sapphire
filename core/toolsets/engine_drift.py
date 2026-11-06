# core/toolsets/engine_drift.py - toolsets that still name the OTHER memory
# engine's tools (release hunt follow-up, 2026-11-06).
#
# user/toolsets.json is seeded once and is the user's from then on, so a switch
# between Memory (legacy) and Mind Palace leaves every toolset listing the old
# engine's tools: they drop silently at resolve time and the new engine's tools
# were never added. A tester switched to the palace and lost save/search/
# delete_knowledge with nothing in their place.
#
# check() runs when plugins are ready (boot, an engine toggle). Drift → a row in
# the notification center wearing a Repair button (notify.register_repair);
# none → the row goes. Mirror-symmetric: classic installs get the same repair.
#   swap: an old engine's tool becomes its like-for-like replacements, in EVERY
#         toolset (custom ones too). A name with no replacement is left alone:
#         it drops harmlessly, and removing it would lose the user's curation
#         if they switch back.
#   add:  the running engine's tools the SHIPPED toolsets carry, into the
#         user's toolsets of those names only.
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_CORE = Path(__file__).absolute().parent.parent
BUILTIN = ('limited_web', 'personality', 'default')
NOTIFY_KEY = 'toolset-engine-drift'
REPAIR = 'toolset-engine'

# Keyed by the engine now running: an old engine's tool -> what replaces it.
SWAPS = {
    'mindpalace': {
        'save_knowledge': ['save_memory'],
        'search_knowledge': ['search_memory', 'library', 'read_document'],
        'delete_knowledge': ['delete_memory'],
        'save_person': ['save_memory', 'list_entities'],
    },
    'memory': {
        # Only swaps that come back whole: search_knowledge returns library +
        # read_document on the way back. update_memory -> save_memory did not
        # (save_memory stays itself), so it is left to drop harmlessly.
        'library': ['search_knowledge'],
        'read_document': ['search_knowledge'],
    },
}
# What the shipped toolsets carried for classic (main, 2026-09): the palace's
# adds come from today's core toolsets.json instead.
CLASSIC_ADDS = {
    'personality': ['save_knowledge', 'search_knowledge', 'delete_knowledge'],
    'default': ['save_knowledge', 'search_knowledge'],
}
_LABEL = {'mindpalace': 'Mind Palace', 'memory': 'Memory (legacy)'}
_NAME_RE = re.compile(r'^\s+"name":\s*"([a-z0-9_]+)"', re.M)


def tools_of(plugin):
    """A memory plugin's tool names, read from its source (never imported:
    the engine that is off must not load)."""
    names = set()
    for f in (_CORE / 'plugins' / plugin / 'tools').glob('*.py'):
        try:
            names.update(_NAME_RE.findall(f.read_text(encoding='utf-8')))
        except OSError:
            pass
    return names


def _adds(engine):
    if engine == 'memory':
        return CLASSIC_ADDS
    if engine != 'mindpalace':
        return {}
    palace = tools_of('mindpalace')
    try:
        shipped = json.loads((_CORE / 'toolsets' / 'toolsets.json').read_text(encoding='utf-8'))
    except Exception as e:
        logger.warning(f"[TOOLSETS] shipped toolsets unreadable: {e}")
        return {}
    return {n: [t for t in (shipped.get(n) or {}).get('functions') or [] if t in palace] for n in BUILTIN}


def plan(toolsets, engine):
    """{toolset: {'swap': {old: [new]}, 'add': [names]}} for what is out of
    step with `engine`; {} when nothing is (or no engine runs)."""
    swaps, adds = SWAPS.get(engine) or {}, _adds(engine)
    out = {}
    for name, ts in (toolsets or {}).items():
        funcs = (ts or {}).get('functions') if isinstance(ts, dict) else None
        if not isinstance(funcs, list):
            continue
        swap = {f: swaps[f] for f in funcs if f in swaps}
        coming = {t for new in swap.values() for t in new}
        add = [t for t in adds.get(name, []) if t not in funcs and t not in coming] if name in BUILTIN else []
        if swap or add:
            out[name] = {'swap': swap, 'add': add}
    return out


def apply(funcs, entry):
    """One toolset's functions with its plan applied: swaps in place,
    order kept, no repeats, adds at the end."""
    out, seen = [], set()
    for f in list(funcs) + list(entry.get('add') or []):
        for one in entry['swap'].get(f, [f]):
            if one not in seen:
                seen.add(one)
                out.append(one)
    return out


def _engine():
    try:
        from core.plugin_loader import plugin_loader
        return plugin_loader.read_engine()
    except Exception:
        return None


def _summary(p, label):
    """Counts, not lists: the names are in the log (toolset_manager) and the
    Toolsets view; a row is read at a glance (Krem, 2026-11-06)."""
    old = sum(1 for e in p.values() if e['swap'])
    short = sum(1 for e in p.values() if e['add'])
    bits = []
    if old:
        bits.append(f"{old} toolset{'s' if old != 1 else ''} still list{'' if old != 1 else 's'} the old memory tools")
    if short:
        bits.append(f"{short} built-in{'s are' if short != 1 else ' is'} missing {label} tools")
    return ' and '.join(bits)


def check():
    """Post or clear the drift row for the engine that runs now."""
    from core import notify
    from core.toolsets import toolset_manager
    engine = _engine()
    p = plan(toolset_manager.get_all_toolsets(), engine) if engine in SWAPS else {}
    if not p:
        notify.forget(NOTIFY_KEY)
        return None
    label = _LABEL.get(engine, engine)
    return notify.notify(
        f"Toolsets need a {label} repair", 'warning',
        f"{_summary(p, label)}. Repair fixes them; your other tools stay.",
        source='toolsets', key=NOTIFY_KEY, action={'repair': REPAIR, 'label': 'Repair'},
        go={'view': 'toolsets'})


def repair():
    """The Repair button: apply the plan, hot-reload the active chat's toolset."""
    from core.toolsets import toolset_manager
    engine = _engine()
    if engine not in SWAPS:
        raise RuntimeError("no memory engine is running")
    changed = toolset_manager.repair_engine_drift(engine)
    if not changed:
        return "Nothing to repair: your toolsets already match."
    try:
        from core.api_fastapi import get_system, reapply_if_active
        from core.event_bus import publish, Events
        system = get_system()
        for name in changed:
            if system is not None:
                reapply_if_active(system, 'toolset', name)
            publish(Events.TOOLSET_CHANGED, {"name": name})
    except Exception as e:
        logger.warning(f"[TOOLSETS] repaired, but the live toolset did not reload: {e}")
    return f"{len(changed)} toolset{'s' if len(changed) != 1 else ''} now match {_LABEL.get(engine, engine)}."


def settle():
    """The Repair button's work without the button, for a flow that already
    says the user chose this engine (the Mind Palace import, 2026-11-06):
    repair(), then the row is re-checked (gone when in step). Words for the
    caller's summary, or None when nothing was out of step. Never raises."""
    try:
        from core.toolsets import toolset_manager
        engine = _engine()
        if engine not in SWAPS or not plan(toolset_manager.get_all_toolsets(), engine):
            return None
        words = repair()
    except Exception as e:
        logger.warning(f"[TOOLSETS] automatic repair failed: {e}")
        words = f"not repaired ({e}); the Repair button in Notifications can try again"
    try:
        check()
    except Exception as e:
        logger.debug(f"[TOOLSETS] drift row not re-checked: {e}")
    return words


def register():
    from core import notify
    notify.register_repair(REPAIR, repair)
