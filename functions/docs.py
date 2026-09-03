# functions/docs.py
"""
Self-help documentation tool. AI can search and read Sapphire docs.

=============================================================================
TWO MODES FOR MARKING AI-READABLE CONTENT
=============================================================================

MODE 1: FULL FILE INCLUDE (for docs that are already AI-friendly)
-----------------------------------------------------------------
Add HTML comment at TOP of file:

    <!-- AI_INCLUDE_FULL: Brief summary for listings -->
    # Troubleshooting
    
    Full doc content here...

The comment is invisible in rendered markdown. Entire file becomes AI content.
Use for: troubleshooting guides, cheatsheets, reference docs.

MODE 2: SECTION AT BOTTOM (for docs with human-focused content)
---------------------------------------------------------------
Add section at END of file:

    # Human-Readable Title
    
    Prose, screenshots, examples for humans...
    
    ## Reference for AI
    
    Terse instructions for AI consumption.

Use for: tutorials, guides with images, docs needing different AI summary.

=============================================================================
WHAT THE TOOL DOES
=============================================================================
- search_help_docs()         -> Lists all docs with summaries
- search_help_docs("name")   -> Returns AI content for that doc
- Tool description auto-lists available docs
=============================================================================
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '📚'
AI_SECTION_MARKER = "## Reference for AI"
AI_FULL_INCLUDE_PATTERN = r'<!--\s*AI_INCLUDE_FULL:\s*(.+?)\s*-->'

# Docs directory relative to this file
DOCS_DIR = Path(__file__).parent.parent / "docs"


def _get_available_docs() -> dict:
    """Scan docs/ for .md files and include README. Returns {name: path} dict."""
    docs = {}
    if not DOCS_DIR.exists():
        logger.warning(f"Docs directory not found: {DOCS_DIR}")
        return docs
    
    for md_file in DOCS_DIR.glob("*.md"):
        # Normalize name: INSTALLATION.md -> installation
        name = md_file.stem.lower().replace("_", "-")
        docs[name] = md_file

    # Plugin-author dev guides live one level down; the prefix avoids name
    # collisions with user docs (signing, prompts, tools, routes...)
    for md_file in (DOCS_DIR / "plugin-author").glob("*.md"):
        name = "plugin-author/" + md_file.stem.lower().replace("_", "-")
        docs[name] = md_file
    
    # Include README.md from project root
    readme_path = DOCS_DIR.parent / "README.md"
    if readme_path.exists():
        docs["readme"] = readme_path
    
    return docs


def _extract_ai_section(filepath: Path, full: bool = False) -> tuple[str, str]:
    """
    Extract AI content from a doc file.
    Returns (summary_line, content) tuple.
    
    Modes:
    1. full=False (default): Returns AI-optimized content
       - AI_INCLUDE_FULL marker: returns entire file
       - ## Reference for AI section: returns section only
    
    2. full=True: Returns full human docs
       - AI_INCLUDE_FULL marker: returns entire file (same as full=False)
       - ## Reference for AI section: returns everything ABOVE section
    """
    try:
        content = filepath.read_text(encoding='utf-8')
    except Exception as e:
        logger.error(f"Failed to read {filepath}: {e}")
        return ("Error reading file", "")
    
    # Check for full-include marker first (in first 500 chars)
    header = content[:500]
    match = re.search(AI_FULL_INCLUDE_PATTERN, header)
    if match:
        summary = match.group(1).strip()
        # Full-include docs return entire file regardless of full param
        clean_content = re.sub(AI_FULL_INCLUDE_PATTERN, '', content, count=1).strip()
        return (summary, clean_content)
    
    # Section mode - behavior depends on full param
    if AI_SECTION_MARKER not in content:
        # No AI marker anywhere → the whole doc IS the reference, for both
        # full modes. Returning empty here turned direct reads of unmarked
        # docs into errors — the read-path half of the invisible-docs bug.
        first_line = content.split('\n')[0].strip().lstrip('#').strip()
        summary = first_line[:100] if first_line else "Full document"
        return (summary, content)
    
    # Split on marker
    parts = content.split(AI_SECTION_MARKER, 1)
    
    if full:
        # Return everything BEFORE the AI section
        human_content = parts[0].strip()
        first_line = human_content.split('\n')[0].strip().lstrip('#').strip()
        summary = first_line[:100] if first_line else "Full document"
        return (summary, human_content)
    
    # Default: return AI section only
    ai_section = parts[1].strip() if len(parts) > 1 else ""
    
    if not ai_section:
        return ("No AI reference section", "")
    
    # First non-empty line is summary
    lines = ai_section.split('\n')
    summary = ""
    for line in lines:
        line = line.strip()
        if line and not line.startswith('#'):
            summary = line[:100]
            if len(line) > 100:
                summary += "..."
            break
    
    if not summary:
        summary = "AI reference available"
    
    return (summary, ai_section)


def _search_across_docs(query: str, available: dict, max_results: int = 6,
                        snippet_chars: int = 180) -> list:
    """Case-insensitive substring search across every doc's FULL text.

    Returns list of (doc_name, match_count, snippet) tuples, highest count
    first. Snippet centers on the first match with surrounding context so
    the caller sees enough to judge relevance.

    Search is a router, so it scans raw files — human prose included. The
    old AI-section-only scan made every unmarked doc invisible: searching
    'ghost message' whiffed while GHOST_MESSAGES.md sat in the corpus
    (the "search has no search" AIX bug, 2026-08-05).
    """
    if not query:
        return []
    q = query.lower().strip()
    results = []
    for name, filepath in available.items():
        try:
            content = filepath.read_text(encoding='utf-8')
        except Exception:
            continue
        if not content:
            continue
        lower = content.lower()
        count = lower.count(q)
        if count == 0:
            continue
        # Snippet around first match
        idx = lower.find(q)
        start = max(0, idx - snippet_chars // 2)
        end = min(len(content), idx + len(q) + snippet_chars // 2)
        snippet = content[start:end].strip()
        if start > 0:
            snippet = '...' + snippet
        if end < len(content):
            snippet = snippet + '...'
        # Collapse whitespace for compact display
        snippet = re.sub(r'\s+', ' ', snippet)
        results.append((name, count, snippet))
    results.sort(key=lambda r: -r[1])
    return results[:max_results]


def _match_doc_name(query: str, available: dict) -> str | None:
    """Loose matching for doc names. Returns matched key or None."""
    query = query.lower().strip()
    
    # Remove .md extension if provided
    if query.endswith('.md'):
        query = query[:-3]
    
    # Normalize common variations
    query = query.replace("_", "-").replace(" ", "-")
    
    # Exact match
    if query in available:
        return query

    # Exact basename of a prefixed doc ('hooks' -> 'plugin-author/hooks')
    # before substring matching, which would hit 'daemons-webhooks' first
    for name in available:
        if "/" in name and name.split("/", 1)[1] == query:
            return name
    
    # Partial match (query is substring of doc name)
    for name in available:
        if query in name or name in query:
            return name
    
    # Prefix match
    for name in available:
        if name.startswith(query) or query.startswith(name):
            return name
    
    return None


# Build dynamic tool description with available docs
_available_docs = _get_available_docs()
# Keep the tool description compact: it rides every system prompt, so the
# 20+ plugin-author guides are summarized rather than enumerated.
_top_docs = sorted(k for k in _available_docs if not k.startswith("plugin-author/"))
_pa_count = len(_available_docs) - len(_top_docs)
_doc_list = ", ".join(_top_docs) if _top_docs else "none found"
if _pa_count:
    _doc_list += f", plus {_pa_count} plugin-author/* dev guides (e.g. plugin-author/hooks)"

AVAILABLE_FUNCTIONS = ['search_help_docs']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "search_help_docs",
            "description": f"Get Sapphire docs. Available: {_doc_list}\n  query='X' — search all docs (snippets + doc pointers)\n  doc_name='X' — full AI reference for that doc\n  doc_name='X' + full=true — human docs\n  (none) — list all",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term across all doc AI sections. Returns top matches with snippets and which doc has more."
                    },
                    "doc_name": {
                        "type": "string",
                        "description": "Doc name (e.g. 'agents', 'installation'). Fuzzy match."
                    },
                    "full": {
                        "type": "boolean",
                        "description": "Return human-readable doc instead of AI ref. Default false."
                    }
                },
                "required": []
            }
        }
    }
]


def execute(function_name: str, arguments: dict, config) -> tuple[str, bool]:
    """Execute the help docs tool."""
    
    if function_name != "search_help_docs":
        return f"Unknown function: {function_name}", False
    
    doc_name = arguments.get('doc_name', '').strip()
    query = arguments.get('query', '').strip()
    full = arguments.get('full', False)
    available = _get_available_docs()

    if not available:
        return "No documentation files found in docs/ directory.", False

    # Query mode: cross-doc search. Routes the AI to the right doc without
    # it having to read each one blindly. Paired with schemas that cite
    # search_help_docs('topic') when the AI vaguely remembers a topic but
    # not the doc name.
    if query:
        hits = _search_across_docs(query, available)
        if not hits:
            return f"No matches for '{query}'. Available docs: {', '.join(sorted(available.keys()))}", True
        lines = [f"Matches for '{query}':", ""]
        for name, count, snippet in hits:
            lines.append(f"[{name}] ({count} match{'es' if count != 1 else ''})")
            lines.append(f"  {snippet}")
            lines.append("")
        lines.append(f"Use search_help_docs(doc_name='<name>') for full AI reference.")
        return "\n".join(lines), True

    # No argument: list all docs with summaries
    if not doc_name:
        lines = ["SAPPHIRE DOCUMENTATION", ""]

        for name in sorted(available.keys()):
            summary, _ = _extract_ai_section(available[name])
            lines.append(f"  {name} - {summary}")

        lines.append("")
        lines.append("Use search_help_docs(doc_name='<name>') for full AI reference.")
        lines.append("Use search_help_docs(query='<term>') to search across all docs.")
        lines.append("Use search_help_docs(doc_name='<name>', full=true) for human docs.")

        return "\n".join(lines), True
    
    # With argument: get specific doc's content
    matched = _match_doc_name(doc_name, available)
    
    if not matched:
        doc_list = ", ".join(sorted(available.keys()))
        return f"Doc '{doc_name}' not found. Available: {doc_list}", False
    
    filepath = available[matched]
    summary, content = _extract_ai_section(filepath, full=full)
    
    if not content:
        return f"Doc '{matched}' exists but has no '## Reference for AI' section yet.", False
    
    return content, True