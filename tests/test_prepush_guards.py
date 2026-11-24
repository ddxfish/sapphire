"""Guards for whole classes of slip, each one found by hand before a release
(pre-push hunt 2026-11-24) and cheap to check by machine from then on. They
read the tree; nothing runs and nothing is written.
"""
import ast
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS = REPO / 'core' / 'plugins'


def _shipped_python():
    for top in ('core', 'functions', 'interfaces'):
        for p in sorted((REPO / top).rglob('*.py')):
            if '__pycache__' in p.parts or 'tests' in p.parts:
                continue
            yield p


def test_no_listener_is_taken_off_the_bus_as_a_fresh_bound_method():
    """The bus removes a listener by identity, and `self.method` is a new object every
    time it is read: `off(..., self._listen)` removes nothing, the old object stays on
    the bus and a reload stacks another (the avatar's Brain did). Keep the bound method
    in an attribute and hand that same object to both on() and off()."""
    found = []
    for path in _shipped_python():
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError:
            continue
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            methods = {f.name for f in cls.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
            for call in (n for n in ast.walk(cls) if isinstance(n, ast.Call)):
                if not (isinstance(call.func, ast.Attribute) and call.func.attr == 'off'):
                    continue
                for arg in call.args:
                    if (isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name)
                            and arg.value.id == 'self' and arg.attr in methods):
                        found.append(f"{path.relative_to(REPO)}:{call.lineno} off(..., self.{arg.attr})")
    assert not found, "a listener that can never be removed:\n  " + "\n  ".join(found)


def test_every_shipped_plugin_is_signed_for_the_files_on_disk():
    """An edit under core/plugins/<name>/ without `python tools/sign_plugin.py` after it
    ships a plugin that is blocked at boot: Sapphire starts, and its tools are just gone.
    The pure check only (verify_plugin itself would clear every plugin's bytecode)."""
    from core import plugin_verify as pv
    bad = []
    for d in sorted(p for p in PLUGINS.iterdir() if (p / 'plugin.json').exists()):
        ok, msg, meta = pv._verify_signed(d)
        if not ok or meta.get('tier') != 'official':
            bad.append(f"{d.name}: {msg}")
    assert not bad, "re-sign before the push (python tools/sign_plugin.py core/plugins/<name>):\n  " + "\n  ".join(bad)


def _req_names(text):
    out = set()
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if line and not line.startswith('-'):
            out.add(re.split(r'[\s\[<>=!~@;]', line, 1)[0].lower().replace('_', '-'))
    return out


# Known today, on the post-push list (tmp/core-v-2-16-7.txt): a fresh install shows a
# "missing dependencies" bell row for these two. Fixing one means taking it out of here.
KNOWN_UNDECLARED = {'screenshot': {'mss'}, 'discord': {'py-cord', 'davey', 'pynacl'}}


def test_a_plugin_that_is_on_by_default_needs_only_what_the_install_brings():
    """A new user never chose a default-on plugin, so its packages have to arrive with
    `pip install -r requirements.txt`; otherwise their first boot greets them with a
    plugin that did not load (email + imapclient was caught by hand, 2026-11-18)."""
    have = _req_names((REPO / 'requirements.txt').read_text(encoding='utf-8'))
    gaps = {}
    for d in sorted(p for p in PLUGINS.iterdir() if (p / 'plugin.json').exists()):
        m = json.loads((d / 'plugin.json').read_text(encoding='utf-8'))
        if not m.get('default_enabled'):
            continue
        need = {re.split(r'[\s\[<>=!~@;]', dep, 1)[0].lower().replace('_', '-') for dep in m.get('pip_dependencies') or []}
        if need - have:
            gaps[d.name] = need - have
    assert gaps == KNOWN_UNDECLARED


# -- packages the core code imports ------------------------------------------------------

# requirement name -> the name it is imported by, where they differ
_IMPORT_NAME = {'pillow': 'PIL', 'beautifulsoup4': 'bs4', 'pyyaml': 'yaml', 'python-dateutil': 'dateutil',
                'faster-whisper': 'faster_whisper', 'python-multipart': 'multipart', 'pyjwt': 'jwt', 'pysocks': 'socks',
                'pillow-heif': 'pillow_heif', 'websocket-client': 'websocket'}
# imported, never named in a requirements file, and safe: each arrives with the package beside it
COMES_WITH = {'starlette': 'fastapi', 'pydantic': 'fastapi', 'huggingface_hub': 'kokoro and faster-whisper',
              'serial': 'esptool (install/requirements-flash.txt), asked for only by the USB flasher'}
# Known today, on the post-push list: tests/test_post_push_core_list.py holds its proof.
KNOWN_UNDECLARED_IMPORTS = {'scipy'}


class _Unguarded(ast.NodeVisitor):
    """Third-party imports outside any try: body (a guarded import has its own answer)."""

    def __init__(self, skip):
        self.skip, self.depth, self.found = skip, 0, {}

    def visit_Try(self, node):
        self.depth += 1
        for n in node.body:
            self.visit(n)
        self.depth -= 1
        for part in (node.handlers, node.orelse, node.finalbody):
            for n in part:
                self.visit(n)

    def _note(self, name, node):
        top = name.split('.')[0]
        if not self.depth and top not in self.skip:
            self.found.setdefault(top, node.lineno)

    def visit_Import(self, node):
        for a in node.names:
            self._note(a.name, node)

    def visit_ImportFrom(self, node):
        if node.level == 0 and node.module:
            self._note(node.module, node)


def test_core_imports_only_packages_the_install_names():
    """The developer's machine has every package ever installed, so an import nobody
    declared works here and fails on a minimal or Docker install (scipy in
    core/stt/utterance.py, found by a scout 2026-11-24). Core plugins are not covered:
    theirs go in their own plugin.json."""
    import sys
    have = set()
    for f in [REPO / 'requirements.txt'] + sorted((REPO / 'install').glob('requirements*.txt')):
        have |= {_IMPORT_NAME.get(n, n).replace('-', '_').lower() for n in _req_names(f.read_text(encoding='utf-8'))}
    local = {p.stem for p in REPO.iterdir() if p.suffix == '.py' or p.is_dir()} | {'plugins'}
    skip = set(sys.stdlib_module_names) | local
    files = [REPO / 'sapphire.py', REPO / 'main.py'] + [
        p for p in _shipped_python() if 'plugins' not in p.relative_to(REPO).parts[:2]]
    undeclared = {}
    for path in files:
        v = _Unguarded(skip)
        v.visit(ast.parse(path.read_text(encoding='utf-8')))
        for top, line in v.found.items():
            if top.lower() not in have and top not in COMES_WITH:
                undeclared.setdefault(top, f"{path.relative_to(REPO)}:{line}")
    assert set(undeclared) == KNOWN_UNDECLARED_IMPORTS, (
        "declare it in requirements.txt (and install/requirements-*.txt), guard the import, "
        f"or say in COMES_WITH what brings it: {undeclared}")
