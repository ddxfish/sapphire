"""The doors between the two memory engines (migration hunt 2026-11-01, wave D):
a palace module imported while its switch is off does nothing at import,
contacts fail closed, and a tool category whose scope the engine never
registered follows that engine's memory scope."""
import importlib
import sys

import pytest

import core.plugin_loader as pl


def _knows(monkeypatch, **enabled):
    monkeypatch.setattr(pl.plugin_loader, 'get_plugin_info',
                        lambda n: {'enabled': enabled[n]} if n in enabled else None)


def test_switched_on_is_what_the_loader_knows(monkeypatch):
    _knows(monkeypatch, mindpalace=False, memory=True)
    assert pl.plugin_loader.switched_on('memory') and not pl.plugin_loader.switched_on('mindpalace')
    assert pl.plugin_loader.switched_on('unknown')               # knows nothing: the status quo


def test_layer_api_refuses_to_import_while_the_palace_is_off(monkeypatch):
    """The documented guard is `except ImportError`; it could never fire."""
    _knows(monkeypatch, mindpalace=False)
    monkeypatch.delitem(sys.modules, 'plugins.mindpalace.tools.layer_api', raising=False)
    with pytest.raises(ImportError, match='switched off'):
        importlib.import_module('plugins.mindpalace.tools.layer_api')
    _knows(monkeypatch, mindpalace=True)
    assert importlib.import_module('plugins.mindpalace.tools.layer_api')


def test_doc_pictures_say_the_palace_is_off_instead_of_importing_it(monkeypatch):
    from core import images
    _knows(monkeypatch, mindpalace=False)
    with pytest.raises(images.ImageError, match='off'):
        images._library()


def test_contacts_fail_closed_when_the_engine_provider_breaks(monkeypatch):
    from core import contacts
    from plugins.memory.tools import knowledge_tools
    asked = []
    monkeypatch.setattr(contacts, '_providers',
                        {'mindpalace': lambda scope: (_ for _ in ()).throw(RuntimeError('locked'))})
    monkeypatch.setattr(knowledge_tools, 'get_people', lambda scope: asked.append(scope) or [{'name': 'stale'}])
    assert contacts.get_people('default') == [] and asked == []
    monkeypatch.setattr(contacts, '_providers', {})            # no engine provider: the classic book answers
    assert contacts.get_people('default') == [{'name': 'stale'}] and asked == ['default']


def test_palace_goal_tools_follow_its_memory_scope(monkeypatch):
    from core.chat.function_manager import FunctionManager, SCOPE_REGISTRY
    fm = FunctionManager.__new__(FunctionManager)
    monkeypatch.setattr(fm, '_meta', lambda: {
        'create_goal': {'category': 'goals', 'plugin': 'mindpalace'},
        'old_goal': {'category': 'goals', 'plugin': 'memory'},
        'save_memory': {'category': 'memory', 'plugin': 'mindpalace'},
        'web_search': {'category': 'web', 'plugin': None},
    })
    monkeypatch.setitem(SCOPE_REGISTRY, 'memory', {'var': None, 'default': 'default',
                                                   'setting': 'memory_scope', 'plugin': 'mindpalace'})
    monkeypatch.delitem(SCOPE_REGISTRY, 'goal', raising=False)
    assert fm.tool_scope('create_goal') == 'memory'            # shed with memory under the palace (D3)
    assert fm.tool_scope('old_goal') == 'goal'                 # another plugin's: as before
    assert fm.tool_scope('save_memory') == 'memory'
    assert fm.tool_scope('web_search') is None
    monkeypatch.setitem(SCOPE_REGISTRY, 'goal', {'var': None, 'default': 'default',
                                                 'setting': 'goal_scope', 'plugin': 'memory'})
    assert fm.tool_scope('create_goal') == 'goal'              # registered: the category's own scope
