"""The memory-engine switch (memory ↔ mindpalace share tool names).

Hunt 2026-10-31: turning the second engine on wrote it enabled BEFORE the
load failed on the collision, and the next boot's engine pin read that as
the user's choice. The toggle route refuses while the partner is on.
"""
import core.api_fastapi  # noqa: F401  (core.routes.plugins imports back into it)
import core.plugin_loader as pl
from core.routes import plugins as pr


def test_group_partner_is_found_only_while_it_is_on(monkeypatch):
    infos = {
        'memory': {'name': 'memory', 'enabled': True, 'manifest': {'essential': 'memory'}},
        'mindpalace': {'name': 'mindpalace', 'enabled': False, 'manifest': {'essential': 'memory'}},
        'shell': {'name': 'shell', 'enabled': True, 'manifest': {}},
        'core-thing': {'name': 'core-thing', 'enabled': True, 'manifest': {'essential': True}},
    }
    monkeypatch.setattr(pl.plugin_loader, 'get_plugin_info', lambda n: infos.get(n))
    monkeypatch.setattr(pl.plugin_loader, 'get_all_plugin_info', lambda: list(infos.values()))
    assert pr._group_partner_on('mindpalace') == 'memory'
    assert pr._group_partner_on('memory') is None          # the palace is off
    assert pr._group_partner_on('shell') is None
    assert pr._group_partner_on('core-thing') is None       # boolean essential is not a group
    infos['memory']['enabled'] = False
    assert pr._group_partner_on('mindpalace') is None


# --- hunt of the hunt 2026-11-01: the load/unload hooks of every palace tool module ---

def _palace_tool_modules():
    import importlib, json
    from pathlib import Path
    manifest = json.loads((Path(__file__).resolve().parents[1] / 'plugin.json').read_text(encoding='utf-8'))
    for rel in manifest['capabilities']['tools']:
        yield importlib.import_module('plugins.mindpalace.tools.' + Path(rel).stem)


def test_every_palace_tool_module_can_shut_down_and_load(monkeypatch):
    """shutdown() referenced `sys` that palace_tools never imported: a NameError
    on every unload and exit, unseen because nothing called it (H1). on_load()
    is where core registrations live now (H2); each must run clean."""
    from core import contacts, audit
    for mod in _palace_tool_modules():
        for hook in ('shutdown', 'on_load'):
            fn = getattr(mod, hook, None)
            if fn:
                fn()                                            # raises → the test fails, as it should
    try:
        assert 'mindpalace' in contacts._providers and 'mindpalace' in audit._sinks
    finally:
        contacts.unregister_provider('mindpalace')
        audit.unregister_sink('mindpalace')


def test_the_palace_registers_nothing_at_import():
    """A copy imported while the switch is off (a leftover drain's lazy import,
    a layer plugin) used to register at import, or stay silent for the whole
    session once the loader reused it (H2). The registrations live in
    on_load() now: no module-level call reaches core's registries. Checked
    statically: re-importing the palace in-process makes a second module whose
    import-time wiring leaks into shared registries (the first draft of this
    test poisoned the suite)."""
    import ast
    from pathlib import Path
    tools = Path(__file__).resolve().parents[1] / 'tools'
    banned = {'register_provider', 'register_sink', 'register_consumer', 'Timer'}
    for name in ('palace_tools.py', 'prompt_audit.py', 'library_tools.py'):
        tree = ast.parse((tools / name).read_text(encoding='utf-8'))
        for node in tree.body:                      # module level only: defs and classes are fine
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, 'id', '')
                    assert called not in banned, f"{name}: module-level {called}() registers at import"
