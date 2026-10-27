# functions/stage.py — Sapphire opens an app beside the chat (tmp/stage-plan.md).
#
# The Chat page's stage (interfaces/web/static/surface/stage.js) holds one
# plugin app beside the conversation. This tool doesn't push anything at the
# page: its result carries a STAGE marker (core/ui_markers.py), the tab that
# ran her turn opens the app, and every tab painting this chat shows a card
# with the same button. Nothing goes on the event bus and nothing is written
# to the chat's settings (privacy scout, 2026-10-25), and a private chat has
# no stage at all.

import json
import logging

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = "\U0001F5A5"  # desktop computer
TOOL_CATEGORY = 'media'

AVAILABLE_FUNCTIONS = ['open_stage']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "open_stage",
            "description": (
                "Open an app beside this chat so the user sees it while you talk, e.g. your browser "
                "(app 'browser') to browse together. Call with no app to list what can open."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {"type": "string", "description": "The app's name, e.g. browser"},
                },
            },
        },
    }
]


def stage_apps():
    """{name: {label, icon}} of loaded plugin apps that may open on the stage."""
    out = {}
    try:
        from core.plugin_loader import plugin_loader
        for name, info in list(plugin_loader._plugins.items()):
            if not info.get("loaded"):
                continue
            app = ((info.get("manifest") or {}).get("capabilities") or {}).get("app") or {}
            if "stage" in (app.get("places") or []):
                out[name] = {"label": str(app.get("label") or name)[:40], "icon": str(app.get("icon") or "")[:8]}
    except Exception as e:
        logger.warning(f"[STAGE] could not list stage apps: {e}")
    return out


def _private_turn():
    try:
        from core.chat.function_manager import scope_private
        return bool(scope_private.get())
    except Exception:
        return True                                    # unknown counts as private


def execute(function_name, arguments, config=None):
    if function_name != "open_stage":
        return f"Unknown function: {function_name}", False
    apps = stage_apps()
    menu = ", ".join(sorted(apps)) or "(none enabled)"
    name = str((arguments or {}).get("app") or "").strip().lower()
    if not name:
        return f"Apps that can open beside this chat: {menu}.", True
    if name not in apps:
        return f"open_stage: no app named '{name}' can open beside the chat. Available: {menu}.", False
    from core.ui_markers import no_surface
    why = no_surface()
    if why:
        return f"open_stage: there is no chat page to open it on ({why}).", False
    if _private_turn():
        return "open_stage: private chats have no stage, so nothing opened. Tell the user in words.", False
    a = apps[name]
    from core.ui_markers import mint
    marker = mint("<!--STAGE:" + json.dumps({"app": name, "label": a["label"], "icon": a["icon"]}, ensure_ascii=True) + "-->")
    return (f"Opened {a['label']} beside the chat for the user (a card in your reply opens it on any "
            f"screen showing this chat).\n{marker}"), True
