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
