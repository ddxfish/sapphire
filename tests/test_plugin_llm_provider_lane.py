"""N12 (negspace 2026-08-31): the manifest lane for plugin LLM providers
wrote a dict nothing reads — registered, logged success, never usable. Now it
delegates into the real template registry (_classes/_plugin_classes)."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class FakeProvider:
    def __init__(self, **kw):
        self.kw = kw


def _fresh_registry():
    from core.chat.llm_providers import ProviderRegistry
    return ProviderRegistry()


def test_manifest_lane_lands_in_real_registry():
    reg = _fresh_registry()
    reg.register_plugin('fakeprov', FakeProvider, 'Fake Prov', 'fakeplugin')
    assert reg._classes.get('fakeprov') is FakeProvider          # instantiable
    assert reg._plugin_classes['fakeprov']['plugin_name'] == 'fakeplugin'


def test_unregister_plugin_cleans_both_maps():
    reg = _fresh_registry()
    reg.register_plugin('fakeprov', FakeProvider, 'Fake Prov', 'fakeplugin')
    reg.unregister_plugin('fakeplugin')
    assert 'fakeprov' not in reg._classes
    assert 'fakeprov' not in reg._plugin_classes


def test_core_template_key_collision_refused():
    reg = _fresh_registry()
    from core.chat.llm_providers import ClaudeProvider
    reg.register_plugin('claude', FakeProvider, 'Evil', 'fakeplugin')
    assert reg._classes['claude'] is ClaudeProvider              # untouched
    assert 'claude' not in reg._plugin_classes
