# Sapphire Store — browse and install plugins
"""
Browse the Sapphire plugin store and install plugins from GitHub.
Uses the public REST API at sapphireblue.dev (no auth needed for reads).
Install triggers the local plugin manager endpoint.
"""

import logging
import requests

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🏪'
AVAILABLE_FUNCTIONS = ['store_browse', 'store_install']

DEFAULT_STORE_URL = "https://sapphireblue.dev/wp-json/sapphire-store/v1/"

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "store_browse",
            "description": "Browse and search the Sapphire plugin store. Returns a list of plugins, or full detail if an exact slug match is found. Use with no arguments to see what's available.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {
                        "type": "string",
                        "description": "Search query — plugin name, keyword, or exact slug"
                    },
                    "category": {
                        "type": "string",
                        "description": "Filter by category (e.g. automation, finance, tools, security, entertainment)"
                    },
                    "sort": {
                        "type": "string",
                        "enum": ["newest", "votes", "name", "updated"],
                        "description": "Sort order. Default: newest"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "store_install",
            "description": "Install a plugin from the Sapphire Store by its slug. Downloads from GitHub and installs locally. Requires user confirmation before proceeding.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "The plugin slug to install (from store_browse results)"
                    }
                },
                "required": ["slug"]
            }
        }
    }
]


def _get_store_url(plugin_settings):
    if plugin_settings and plugin_settings.get("store_url"):
        return plugin_settings["store_url"].rstrip("/") + "/"
    return DEFAULT_STORE_URL


def _format_plugin_brief(p):
    trust = p.get("trust_level", "community")
    trust_tag = f" [{trust.upper()}]" if trust != "community" else ""
    votes = f"+{p['votes_up']}/-{p['votes_down']}"
    return f"  • {p['name']}{trust_tag} ({p['slug']}) — {p['description'][:100]} [{votes}, {p.get('category', '?')}]"


def _format_plugin_detail(p):
    trust = p.get("trust_level", "community")
    total = p["votes_up"] + p["votes_down"]
    ratio = round(p["votes_up"] / total * 100) if total > 0 else 0

    lines = [
        f"=== {p['name']} ===",
        f"Slug: {p['slug']}",
        f"Author: {p.get('author', 'Unknown')}",
        f"Category: {p.get('category', '?')}",
        f"Version: {p.get('version', '?')}",
        f"Trust: {trust.upper()}",
        f"Votes: +{p['votes_up']} / -{p['votes_down']} ({ratio}% positive)",
        f"GitHub: {p.get('github_url', 'N/A')}",
        "",
        p.get("long_description") or p.get("description", ""),
    ]
    if p.get("min_sapphire_version"):
        lines.insert(7, f"Requires Sapphire: {p['min_sapphire_version']}+")

    return "\n".join(lines)


def _browse(search=None, category=None, sort="newest", plugin_settings=None):
    base = _get_store_url(plugin_settings)

    try:
        # If search looks like an exact slug, try detail first
        if search and " " not in search.strip():
            try:
                r = requests.get(f"{base}items/{search.strip()}", timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    if not data.get("code"):  # not an error
                        return _format_plugin_detail(data), True
            except Exception:
                pass  # Fall through to list/search

        # Search or list
        if search:
            params = {"q": search, "per_page": 15}
            r = requests.get(f"{base}items/search", params=params, timeout=10)
        else:
            params = {"per_page": 15, "sort": sort or "newest"}
            if category:
                params["category"] = category
            r = requests.get(f"{base}items", params=params, timeout=10)

        if r.status_code != 200:
            return f"Store API error: HTTP {r.status_code}", False

        data = r.json()
        plugins = data.get("items", [])
        total = data.get("total", 0)

        if not plugins:
            msg = "No plugins found"
            if search:
                msg += f" matching '{search}'"
            if category:
                msg += f" in category '{category}'"
            return msg + ".", False

        lines = [f"Found {total} plugin{'s' if total != 1 else ''}:"]
        lines.append("")
        for p in plugins:
            lines.append(_format_plugin_brief(p))

        # If exactly one result, auto-expand to detail
        if len(plugins) == 1:
            try:
                slug = plugins[0]["slug"]
                dr = requests.get(f"{base}items/{slug}", timeout=10)
                if dr.status_code == 200:
                    return _format_plugin_detail(dr.json()), True
            except Exception:
                pass

        lines.append("")
        lines.append(f"Use store_browse(search=\"slug\") for detail on a specific plugin.")
        if total > 15:
            lines.append(f"Showing 15 of {total}. Refine your search for more specific results.")

        return "\n".join(lines), True

    except requests.Timeout:
        return "Store API timed out. Try again.", False
    except requests.ConnectionError:
        return "Could not connect to the Sapphire Store. Check network.", False
    except Exception as e:
        logger.error(f"[STORE] Browse error: {e}", exc_info=True)
        return f"Error browsing store: {e}", False


def _install(slug, plugin_settings=None):
    base = _get_store_url(plugin_settings)

    try:
        # Get plugin detail from store
        r = requests.get(f"{base}items/{slug}", timeout=10)
        if r.status_code != 200:
            return f"Plugin '{slug}' not found in the store.", False

        plugin = r.json()
        if plugin.get("code"):
            return f"Plugin '{slug}' not found.", False

        github_url = plugin.get("github_url")
        if not github_url:
            return f"Plugin '{slug}' has no GitHub URL — can't install.", False

        trust = plugin.get("trust_level", "community")
        name = plugin.get("name", slug)

        # Download GitHub zip, extract, install via plugin_loader directly
        import re
        import tempfile
        import zipfile
        import shutil
        import json
        from pathlib import Path

        m = re.match(r'https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$', github_url.strip())
        if not m:
            return f"Invalid GitHub URL: {github_url}", False

        owner, repo = m.group(1), m.group(2)
        zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/main.zip"
        zr = requests.get(zip_url, stream=True, timeout=30)
        if zr.status_code == 404:
            zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/master.zip"
            zr = requests.get(zip_url, stream=True, timeout=30)
        if zr.status_code != 200:
            return f"Failed to download from GitHub (HTTP {zr.status_code})", False

        # Save zip to temp
        tmp_zip = Path(tempfile.mktemp(suffix=".zip"))
        tmp_dir = None
        try:
            with open(tmp_zip, "wb") as f:
                for chunk in zr.iter_content(8192):
                    f.write(chunk)

            if not zipfile.is_zipfile(tmp_zip):
                return "Downloaded file is not a valid zip.", False

            tmp_dir = Path(tempfile.mkdtemp())
            with zipfile.ZipFile(tmp_zip, 'r') as zf:
                # Basic safety checks
                for info in zf.infolist():
                    if '..' in info.filename or info.filename.startswith('/'):
                        return f"Zip contains unsafe path: {info.filename}", False
                    if info.file_size > 10 * 1024 * 1024:
                        return f"File too large in zip: {info.filename}", False
                zf.extractall(tmp_dir)

            # Find plugin.json (root or one level deep — GitHub wraps in {repo}-main/)
            plugin_root = None
            if (tmp_dir / "plugin.json").exists():
                plugin_root = tmp_dir
            else:
                for child in tmp_dir.iterdir():
                    if child.is_dir() and (child / "plugin.json").exists():
                        plugin_root = child
                        break

            if not plugin_root:
                return "No plugin.json found in the repository. This may not be a Sapphire plugin.", False

            manifest = json.loads((plugin_root / "plugin.json").read_text(encoding="utf-8"))
            plugin_name = manifest.get("name", slug)
            plugin_version = manifest.get("version", "?")
            plugin_author = manifest.get("author", "unknown")

            # Check if already installed
            from core.plugin_loader import plugin_loader, PluginState
            # __file__ = plugins/sapphire-store/tools/store_tools.py → 4 parents up to project root
            PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
            USER_PLUGINS_DIR = PROJECT_ROOT / "user" / "plugins"
            dest = USER_PLUGINS_DIR / plugin_name

            if dest.exists():
                try:
                    existing = json.loads((dest / "plugin.json").read_text())
                    old_v = existing.get("version", "?")
                except Exception:
                    old_v = "?"
                return (
                    f"Plugin '{name}' is already installed (v{old_v}). "
                    f"Store has v{plugin_version}. Uninstall first to update.",
                    False
                )

            # Install
            USER_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copytree(plugin_root, dest, symlinks=False)

            # Write install metadata
            from datetime import datetime
            state = PluginState(plugin_name)
            state.save("installed_from", github_url)
            state.save("install_method", "github_url")
            state.save("installed_at", datetime.utcnow().isoformat() + "Z")

            # Add to enabled list so it actually loads
            plugins_json = PROJECT_ROOT / "user" / "webui" / "plugins.json"
            plugins_json.parent.mkdir(parents=True, exist_ok=True)
            pdata = {}
            if plugins_json.exists():
                try:
                    pdata = json.loads(plugins_json.read_text(encoding="utf-8"))
                except Exception:
                    pass
            enabled = pdata.get("enabled", [])
            if plugin_name not in enabled:
                enabled.append(plugin_name)
                pdata["enabled"] = enabled
                plugins_json.write_text(json.dumps(pdata, indent=2), encoding="utf-8")

            # Rescan to discover and load the new plugin
            plugin_loader.rescan()

            lines = [
                f"Installed '{name}' v{plugin_version} successfully!",
                f"Author: {plugin_author}",
                f"Trust level: {trust.upper()}",
                "",
                "The plugin is now available. Enable it in Settings > Plugins if needed.",
            ]

            if trust == "community":
                lines.append("")
                lines.append("Note: This is a community plugin. Review the source code if you have concerns.")

            return "\n".join(lines), True

        finally:
            if tmp_zip.exists():
                tmp_zip.unlink(missing_ok=True)
            if tmp_dir and tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)

    except requests.Timeout:
        return "GitHub download timed out. Try again.", False
    except requests.ConnectionError:
        return "Could not connect to GitHub. Check network.", False
    except Exception as e:
        logger.error(f"[STORE] Install error: {e}", exc_info=True)
        return f"Install failed: {e}", False


def execute(function_name, arguments, config, plugin_settings=None):
    if function_name == "store_browse":
        return _browse(
            search=arguments.get("search"),
            category=arguments.get("category"),
            sort=arguments.get("sort", "newest"),
            plugin_settings=plugin_settings,
        )
    elif function_name == "store_install":
        slug = arguments.get("slug")
        if not slug:
            return "Please specify a plugin slug to install.", False
        return _install(slug, plugin_settings=plugin_settings)
    else:
        return f"Unknown function: {function_name}", False
