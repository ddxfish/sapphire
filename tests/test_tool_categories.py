"""Tool metadata (2026-10-08): every tool carries a category (+ writes), the
first thing Sapphire knows about a tool beyond its schema. The twelve
categories compose an agent's toolset, carry the scope their tools need, and
tell a reader from a writer. This file keeps the taxonomy from rotting."""
import ast
import re
from pathlib import Path

from core.chat.function_manager import CATEGORIES, CATEGORY_SCOPE, FunctionManager, SCOPE_REGISTRY

ROOT = Path(__file__).resolve().parent.parent
# labeled with their OWN name on purpose: asked for by name, never swept in by a category
OWN_NAME = {'bitcoin', 'wordpress', 'game-room'}


def _tool_files():
    files = sorted(ROOT.glob('functions/*.py')) + sorted(ROOT.glob('plugins/*/tools/*.py'))
    for f in files:
        if f.name.startswith('_') or '/tests/' in str(f):
            continue
        src = f.read_text(encoding='utf-8')
        if not re.search(r'^TOOLS\s*=', src, re.M):
            continue
        if re.search(r'^TOOLS\s*=\s*\[\s*\]', src, re.M):
            continue                                   # dynamic (mcp_client): nothing to label
        yield f, src


def test_every_tool_file_names_its_category():
    missing, bad = [], []
    for f, src in _tool_files():
        m = re.search(r"^TOOL_CATEGORY\s*=\s*['\"]([^'\"]+)['\"]", src, re.M)
        if not m:
            missing.append(str(f.relative_to(ROOT)))
            continue
        cat = m.group(1)
        plugin = f.parts[-3] if 'plugins' in f.parts else None
        if cat not in CATEGORIES and cat != plugin:
            bad.append((str(f.relative_to(ROOT)), cat))
    assert not missing, f"tool files without TOOL_CATEGORY: {missing}"
    assert not bad, f"TOOL_CATEGORY must be one of {CATEGORIES} or the plugin's own name: {bad}"
    own = {m.group(1) for _, src in _tool_files() for m in [re.search(r"^TOOL_CATEGORY\s*=\s*['\"]([^'\"]+)['\"]", src, re.M)] if m} - set(CATEGORIES)
    assert own == OWN_NAME, f"plugins standing alone by name changed: {own}"


def test_per_tool_overrides_name_real_categories():
    for f, src in _tool_files():
        for m in re.finditer(r'''["']category["']\s*:\s*["']([^"']+)["']''', src):
            assert m.group(1) in CATEGORIES, (str(f.relative_to(ROOT)), m.group(1))


def test_the_twelve_and_their_scopes():
    assert CATEGORIES == ('web', 'memory', 'knowledge', 'people', 'goals', 'files', 'system',
                          'devices', 'comms', 'media', 'meta_danger', 'agents')
    assert set(CATEGORY_SCOPE) == {'memory', 'knowledge', 'people', 'goals'}


def _fm(meta):
    fm = FunctionManager.__new__(FunctionManager)
    names = list(meta)
    fm.all_possible_tools = [{"type": "function", "function": {"name": n, "parameters": {}}} for n in names]
    fm._hidden_tools = {n for n, m in meta.items() if m.get('hidden')}
    fm.function_modules = {"web": {"available_functions": ['web_search']}}
    fm._mode_filters = {}
    fm._settings_gates = {}
    fm._enabled_tools = []
    fm._tool_meta = {n: {'category': m['category'], 'writes': m.get('writes', False),
                         'module': m.get('module', 'm'), 'plugin': m.get('plugin', '')} for n, m in meta.items()}
    return fm


META = {
    'web_search': {'category': 'web', 'module': 'web'},
    'search_memory': {'category': 'memory', 'plugin': 'mindpalace'},
    'save_memory': {'category': 'memory', 'writes': True, 'plugin': 'mindpalace'},
    'run_librarian': {'category': 'memory', 'writes': True, 'plugin': 'mindpalace', 'hidden': True},
    'library': {'category': 'knowledge', 'plugin': 'mindpalace'},
    'send_email': {'category': 'comms', 'writes': True, 'plugin': 'email'},
    'send_bitcoin': {'category': 'bitcoin', 'writes': True, 'plugin': 'bitcoin'},
}


def test_a_category_or_a_plugin_name_resolves_like_a_toolset(monkeypatch):
    fm = _fm(META)
    with __import__('unittest.mock', fromlist=['patch']).patch('core.chat.function_manager.toolset_manager') as tm:
        tm.toolset_exists.return_value = False
        assert fm.resolve_tool_names(['memory']) == (['search_memory', 'save_memory'], 'memory', None)   # hidden out
        assert fm.resolve_tool_names(['mindpalace'])[0] == ['search_memory', 'save_memory', 'library']   # a plugin by name
        assert fm.resolve_tool_names(['bitcoin']) == (['send_bitcoin'], 'bitcoin', None)                # own-name category
        assert fm.resolve_tool_names(['web'])[0] == ['web_search']                                      # the module still wins
        assert fm.resolve_tool_names(['ghost']) == ([], 'none', 'ghost')
        got = {t['function']['name'] for t in fm.resolve_tools('web', extra_toolsets=['memory', 'comms'])}
        assert got == {'web_search', 'search_memory', 'save_memory', 'send_email'}
    assert fm.categories() == {'web': ['web_search'], 'memory': ['search_memory', 'save_memory'],
                               'knowledge': ['library'], 'comms': ['send_email'], 'bitcoin': ['send_bitcoin']}
    assert fm.tools_in_category('memory', writes=False) == ['search_memory']
    assert fm.tools_in_category('memory', writes=True) == ['save_memory']


def test_a_closed_scope_sheds_the_tools_that_need_it(monkeypatch):
    fm = _fm(META)
    monkeypatch.setitem(SCOPE_REGISTRY, 'email', {'var': None, 'default': 'default', 'setting': 'email_scope', 'plugin': 'email'})
    assert fm.tool_scope('search_memory') == 'memory'          # by category
    assert fm.tool_scope('send_email') == 'email'              # the ONE scope its plugin registered
    assert fm.tool_scope('web_search') is None                 # works with every scope closed
    with __import__('unittest.mock', fromlist=['patch']).patch('core.chat.function_manager.toolset_manager') as tm:
        tm.toolset_exists.return_value = False
        got = {t['function']['name'] for t in fm.resolve_tools('web', extra_toolsets=['memory', 'comms'],
                                                               closed_scopes={'memory', 'email'})}
    assert got == {'web_search'}


def test_closed_scopes_mirror_the_executors_force_disable(monkeypatch):
    from core.continuity.execution_context import closed_scopes
    monkeypatch.setitem(SCOPE_REGISTRY, 'memory', {'var': None, 'default': 'default', 'setting': 'memory_scope', 'plugin': 'mindpalace'})
    monkeypatch.setitem(SCOPE_REGISTRY, 'email', {'var': None, 'default': 'default', 'setting': 'email_scope', 'plugin': 'email'})
    closed = closed_scopes({'toolset': 'default', 'memory_scope': 'personal', 'email_scope': 'none'})
    assert 'memory' not in closed and 'email' in closed
    assert 'private' not in closed and 'rag' not in closed, 'flags and settingless vars are not scopes'
    assert 'memory' in closed_scopes({'toolset': 'default'}), 'unlisted = disabled, as _build_scopes does'


def test_the_toolsets_ui_groups_by_category():
    for rel in ('core/routes/content.py', 'core/routes/chat.py'):
        src = (ROOT / rel).read_text(encoding='utf-8')
        assert "meta.get('category') or module_info.get('group') or module_name" in src, rel
        assert '"writes": bool(meta.get(\'writes\'))' in src, rel
    src = (ROOT / 'functions' / 'agents.py').read_text(encoding='utf-8')
    joined = ''.join(re.findall(r'"([^"]*)"', src.split('"options": {"type": "object",')[1].split('}')[0]))
    assert 'web, memory, knowledge, people, goals, files, system, devices, comms, media, meta_danger, agents' in joined, \
        'the spawn description front-loads the twelve toolset words'
    src = (ROOT / 'core' / 'agents' / 'engine.py').read_text(encoding='utf-8')
    assert 'def _toolset_words(self)' in src and "lines.extend(self._toolset_words())" in src


def test_only_type_and_function_reach_the_wire():
    """Metadata lives BESIDE the schema; a strict server rejects anything else
    ("Extra inputs are not permitted, field: 'tools[7].writes'" killed every
    agent turn on dev, 2026-10-08). Allow-list, so the next flag is safe too."""
    from core.chat.llm_providers.base import BaseProvider
    tool = {"type": "function", "function": {"name": "x", "parameters": {}}, "category": "memory", "writes": True,
            "is_local": True, "network": False, "hidden": False, "loop_warn_after": 2, "whatever_next": 1}
    out = BaseProvider.convert_tools_for_api(None, [tool])   # self unused
    assert out == [{"type": "function", "function": {"name": "x", "parameters": {}}}]
