"""Plugin route handlers must not block the event loop.

The dispatcher (core/routes/plugins.py) awaits `async def` handlers ON the
loop and threads plain `def` ones. An async handler that never awaits runs its
whole body on the loop — every request and WebSocket freezes, and past the
20 s keepalive every live socket drops (browser conversation mode, 2026-10-24:
status poll + mcp_client connect). Such a handler must be a plain def, or be
listed below with the reason it is safe on the loop.
"""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# plugin -> {handler: why it may stay an await-less async def}
ON_LOOP_OK = {
    "avatar": dict.fromkeys(
        ("list_models", "delete_model", "get_tracks", "get_config", "save_config"),
        "milliseconds; save_config's read-modify-write has no lock, and running "
        "on the loop is what serializes it — threading it would lose updates"),
    "mcp_client": dict.fromkeys(("list_servers", "get_server_tools"), "in-memory reads"),
    "telegram": {"list_accounts": "glob + in-memory dict"},
}


def _await_less_async_handlers():
    found = []
    for manifest in sorted(ROOT.glob("core/plugins/*/plugin.json")):
        plugin_dir = manifest.parent
        routes = json.loads(manifest.read_text()).get("capabilities", {}).get("routes", [])
        for r in routes:
            file, _, name = r.get("handler", "").partition(":")
            src = plugin_dir / file
            if not name or not src.exists():
                continue
            for node in ast.parse(src.read_text()).body:
                if (isinstance(node, ast.AsyncFunctionDef) and node.name == name
                        and not any(isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith))
                                    for n in ast.walk(node))):
                    found.append((plugin_dir.name, name))
    return found


def test_no_new_await_less_async_handlers():
    bad = [f"{p}:{h}" for p, h in _await_less_async_handlers() if h not in ON_LOOP_OK.get(p, {})]
    assert not bad, ("async def handlers that never await run on the event loop — "
                     f"make them plain def (the dispatcher threads them): {bad}")


def test_allowlist_has_no_stale_entries():
    live = set(_await_less_async_handlers())
    stale = [f"{p}:{h}" for p, hs in ON_LOOP_OK.items() for h in hs if (p, h) not in live]
    assert not stale, f"no longer await-less async handlers — drop from ON_LOOP_OK: {stale}"
