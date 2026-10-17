"""Serve docs/ markdown files for the in-app help encyclopedia."""

import json
import re
import logging
from pathlib import Path
from fastapi import APIRouter, Request, Depends, HTTPException
from core.auth import require_login

logger = logging.getLogger(__name__)
router = APIRouter()

DOCS_DIR = Path(__file__).parent.parent.parent / "docs"
ROOT_DIR = DOCS_DIR.parent  # project root
from core.paths import SYSTEM_PLUGINS_DIR


def _build_tree():
    """Walk docs/ and return a nested list of files/folders."""
    items = []
    # Root README as the top entry
    root_readme = ROOT_DIR / "README.md"
    if root_readme.exists():
        items.append({"name": "Sapphire", "path": "_root/README.md", "type": "file"})

    for p in sorted(DOCS_DIR.iterdir()):
        if p.name.startswith(('.', '_')) or p.name == 'videos':
            continue
        if p.is_file() and p.suffix == '.md' and p.name != 'CHANGELOG.md':
            items.append({"name": p.stem, "path": p.name, "type": "file"})
        elif p.is_dir():
            children = []
            for c in sorted(p.rglob('*.md')):
                rel = str(c.relative_to(DOCS_DIR))
                name = "Overview" if c.name == "README.md" else c.stem
                children.append({"name": name, "path": rel, "type": "file"})
            if children:
                items.append({"name": p.name, "path": p.name, "type": "folder", "children": children})
    # Plugin READMEs
    plugin_children = []
    for pdir in [SYSTEM_PLUGINS_DIR, ROOT_DIR / "user" / "plugins"]:
        if not pdir.exists():
            continue
        for readme in sorted(pdir.glob("*/README.md")):
            plugin_name = readme.parent.name
            display = plugin_name
            manifest = readme.parent / "plugin.json"
            if manifest.exists():
                try:
                    mdata = json.loads(manifest.read_text(encoding='utf-8'))
                    # Use description before the dash, or fall back to name
                    desc = mdata.get("description", "")
                    display = desc.split("\u2014")[0].split(" - ")[0].strip() if desc else mdata.get("name", plugin_name)
                except Exception:
                    pass
            plugin_children.append({"name": display, "path": f"_plugins/{plugin_name}/README.md", "type": "file"})
    if plugin_children:
        items.append({"name": "Plugins", "path": "plugins", "type": "folder", "children": plugin_children})

    # Changelog pinned last
    changelog = DOCS_DIR / "CHANGELOG.md"
    if changelog.exists():
        items.append({"name": "CHANGELOG", "path": "CHANGELOG.md", "type": "file"})
    return items


@router.get("/api/docs")
async def list_docs(request: Request, _=Depends(require_login)):
    """Return doc tree for sidebar nav."""
    return {"tree": _build_tree()}


@router.get("/api/docs/search")
async def search_docs(request: Request, q: str = "", _=Depends(require_login)):
    """Search across all doc files. Returns matching snippets."""
    if not q or len(q) < 2:
        return {"results": []}

    query = q.lower()
    results = []
    # Include root README and plugin READMEs in search
    search_files = list(DOCS_DIR.rglob("*.md"))
    root_readme = ROOT_DIR / "README.md"
    if root_readme.exists():
        search_files.append(root_readme)
    for pdir in [SYSTEM_PLUGINS_DIR, ROOT_DIR / "user" / "plugins"]:
        search_files.extend(pdir.glob("*/README.md")) if pdir.exists() else None
    for md in search_files:
        try:
            text = md.read_text(encoding='utf-8')
        except Exception:
            continue

        lines = text.split('\n')
        matches = []
        for i, line in enumerate(lines):
            if query in line.lower():
                # Grab surrounding context (1 line before, 1 after)
                start = max(0, i - 1)
                end = min(len(lines), i + 2)
                snippet = '\n'.join(lines[start:end]).strip()
                matches.append({"line": i + 1, "snippet": snippet[:300]})
                if len(matches) >= 3:
                    break

        if matches:
            if md == root_readme:
                rel = "_root/README.md"
            elif str(md).startswith(str(SYSTEM_PLUGINS_DIR)) or str(md).startswith(str(ROOT_DIR / "user" / "plugins")):
                rel = f"_plugins/{md.parent.name}/README.md"
            else:
                rel = str(md.relative_to(DOCS_DIR))
            # Extract title from first heading
            title = md.stem
            for line in lines[:5]:
                if line.startswith('# '):
                    title = line[2:].strip()
                    break
            results.append({"path": rel, "title": title, "matches": matches})

    # Sort by number of matches descending
    results.sort(key=lambda r: len(r["matches"]), reverse=True)
    return {"results": results[:20]}


@router.get("/api/docs/{path:path}")
async def get_doc(path: str, request: Request, _=Depends(require_login)):
    """Return raw markdown content of a doc file."""
    # Special prefix for root README
    if path == "_root/README.md":
        target = ROOT_DIR / "README.md"
    elif path.startswith("_plugins/"):
        # e.g. _plugins/telegram/README.md → plugins/telegram/README.md
        rel = path[len("_plugins/"):]
        target = (SYSTEM_PLUGINS_DIR / rel).resolve()
        # Also check user plugins
        if not target.exists():
            target = (ROOT_DIR / "user" / "plugins" / rel).resolve()
        plugins_root = str((SYSTEM_PLUGINS_DIR).resolve())
        user_plugins_root = str((ROOT_DIR / "user" / "plugins").resolve())
        if not (str(target).startswith(plugins_root) or str(target).startswith(user_plugins_root)):
            raise HTTPException(status_code=403, detail="Access denied")
    else:
        target = (DOCS_DIR / path).resolve()
        # Prevent directory traversal
        if not str(target).startswith(str(DOCS_DIR.resolve())):
            raise HTTPException(status_code=403, detail="Access denied")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Doc not found")
    return {"content": target.read_text(encoding='utf-8'), "path": path}
