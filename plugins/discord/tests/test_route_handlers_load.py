"""Every route handler in the manifest must load the way core's plugin loader
loads it: exec of the file source in a fresh namespace (2026-09-13: a module
with a NameError at import silently killed all five account routes at boot —
the daemon still said 'ready')."""

import json
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]


def _handlers():
    manifest = json.loads((PLUGIN / 'plugin.json').read_text(encoding='utf-8'))
    for route in manifest.get('capabilities', {}).get('routes', []) or manifest.get('routes', []):
        yield route['handler']


def test_every_manifest_route_handler_loads_like_the_plugin_loader():
    handlers = list(_handlers())
    assert handlers, 'no routes found in the manifest'
    failures = []
    for spec in handlers:
        rel, _, func = spec.partition(':')
        path = PLUGIN / rel
        namespace = {'__name__': f'plugins.discord.{rel[:-3].replace("/", ".")}', '__file__': str(path)}
        try:
            exec(compile(path.read_text(encoding='utf-8'), str(path), 'exec'), namespace)
        except Exception as exc:  # noqa: BLE001 — the report is the point
            failures.append(f'{spec}: {type(exc).__name__}: {exc}')
            continue
        if not callable(namespace.get(func)):
            failures.append(f'{spec}: function {func!r} not defined')
    assert failures == [], '\n'.join(failures)
