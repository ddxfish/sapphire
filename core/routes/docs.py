"""Serve docs/ markdown files for the in-app help encyclopedia."""

import re
import logging
from pathlib import Path
from fastapi import APIRouter, Request, Depends, HTTPException
from core.auth import require_login

logger = logging.getLogger(__name__)
router = APIRouter()

DOCS_DIR = Path(__file__).parent.parent.parent / "docs"
ROOT_DIR = DOCS_DIR.parent  # project root


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
    # Include root README in search
    search_files = list(DOCS_DIR.rglob("*.md"))
    root_readme = ROOT_DIR / "README.md"
    if root_readme.exists():
        search_files.append(root_readme)
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
            rel = "_root/README.md" if md == root_readme else str(md.relative_to(DOCS_DIR))
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
    else:
        target = (DOCS_DIR / path).resolve()
        # Prevent directory traversal
        if not str(target).startswith(str(DOCS_DIR.resolve())):
            raise HTTPException(status_code=403, detail="Access denied")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Doc not found")
    return {"content": target.read_text(encoding='utf-8'), "path": path}
