# functions/docs.py
"""
Self-help documentation tool. AI can search and read Sapphire docs.

=============================================================================
TWO MODES FOR MARKING AI-READABLE CONTENT
=============================================================================

MODE 1: FULL FILE INCLUDE (for docs that are already AI-friendly)
-----------------------------------------------------------------
Add HTML comment at TOP of file:

    <!-- AI_INCLUDE_FULL: Brief summary -->
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
- search_help_docs()         -> App abstract (SELF.md's AI section)
- search_help_docs("name")   -> Returns AI content for that doc
- Tool description names only TOP_DOCS; query= finds the rest
=============================================================================
"""

import logging
import math
import re
from pathlib import Path

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '📚'
TOOL_CATEGORY = 'knowledge'
AI_SECTION_MARKER = "## Reference for AI"
AI_FULL_INCLUDE_PATTERN = r'<!--\s*AI_INCLUDE_FULL:\s*(.+?)\s*-->'
# The no-arg call answers with this doc's AI section: what the app is.
ABSTRACT_DOC = "self"
# The only docs the tool description names. It rides every system prompt,
# so it stays tiny; query= finds everything else.
TOP_DOCS = ("self", "tools", "toolmaker", "plugin-author/ai-reference", "troubleshooting")

# Docs directory relative to this file
DOCS_DIR = Path(__file__).parent.parent / "docs"


# History, not help: readable by name, never ranked in search (it matched
# nearly every query and pushed the real doc out of the results).
UNSEARCHED = ("changelog",)
# A read longer than this comes back cut, with its section list.
READ_CAP = 16000
_STOP = set("a an and are can do does for from how i in is it me my of on or "
            "the to what when where which who why with you your".split())


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

    # Plugin READMEs, the same set Help shows people. A core plugin wins
    # its name; a user plugin only fills a name core doesn't have.
    for pdir in (DOCS_DIR.parent / "core" / "plugins", DOCS_DIR.parent / "user" / "plugins"):
        for readme in sorted(pdir.glob("*/README.md")) if pdir.is_dir() else ():
            docs.setdefault("plugin/" + readme.parent.name.lower(), readme)

    return docs


def _terms(query: str) -> list:
    """Search words: lowercased, stopwords out, a plural 's' off ('devices'
    finds 'device'). A query of only stopwords keeps its words."""
    words = re.findall(r"[a-z0-9/_.-]+", query.lower())
    kept = [w for w in words if w not in _STOP and len(w) > 1] or words
    return list(dict.fromkeys(w[:-1] if len(w) > 4 and w.endswith("s") else w for w in kept))


def _sections(content: str) -> list:
    """[(heading, text)] split at ## / ### headings; text before the first
    heading is under the doc's title."""
    out, head, buf = [], "", []
    for line in content.splitlines():
        if re.match(r"#{2,3} ", line):
            if buf:
                out.append((head, "\n".join(buf)))
            head, buf = line.lstrip("#").strip(), [line]
        else:
            buf.append(line)
    if buf:
        out.append((head, "\n".join(buf)))
    return out


def _find_section(content: str, want: str) -> str | None:
    """The section whose heading best matches `want`, else the one whose text
    holds the most of its words."""
    secs = _sections(content)
    w = want.lower().strip()
    for head, text in secs:
        if head.lower() == w:
            return text
    for head, text in secs:
        if w and w in head.lower():
            return text
    terms = _terms(want)
    best = max(secs, key=lambda s: sum(t in s[1].lower() for t in terms), default=None)
    if best and any(t in best[1].lower() for t in terms):
        return best[1]
    return None


def _cap(content: str, name: str) -> str:
    """A long read comes back cut at a line, with its section list so the
    next call can ask for one part."""
    if len(content) <= READ_CAP:
        return content
    cut = content.rfind("\n", 0, READ_CAP)
    heads = [h for h, _ in _sections(content) if h]
    return (content[:cut if cut > 0 else READ_CAP]
            + f"\n\n[Cut at {READ_CAP} of {len(content)} chars. Sections: "
            + " · ".join(heads)
            + f". Read one: search_help_docs(doc_name='{name}', section='<heading>').]")


def _extract_ai_section(filepath: Path, full: bool = False) -> str:
    """
    Extract AI content from a doc file. Empty string = nothing to show.

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
        return ""

    # Check for full-include marker first (in first 500 chars)
    if re.search(AI_FULL_INCLUDE_PATTERN, content[:500]):
        # Full-include docs return entire file regardless of full param
        return re.sub(AI_FULL_INCLUDE_PATTERN, '', content, count=1).strip()

    # Section mode - behavior depends on full param
    if AI_SECTION_MARKER not in content:
        # No AI marker anywhere → the whole doc IS the reference, for both
        # full modes. Returning empty here turned direct reads of unmarked
        # docs into errors — the read-path half of the invisible-docs bug.
        return content

    # Everything BEFORE the marker is the human doc, everything after the AI section
    human_content, ai_section = content.split(AI_SECTION_MARKER, 1)
    return human_content.strip() if full else ai_section.strip()


def _search_across_docs(query: str, available: dict, max_results: int = 6,
                        snippet_chars: int = 180) -> list:
    """Word search across every doc's FULL text.

    Returns (doc_name, match_count, snippet, section) tuples, best first.
    She asks in sentences, so the query is split into words: a doc ranks by
    how many of them it holds, then by the words in its name and headings,
    then by how often they appear (damped, so a long doc can't win on bulk
    alone). The whole phrase appearing as typed is a bonus. The snippet is
    the line holding the most of the words; `section` is the heading above
    it, for a section= read.

    Search is a router, so it scans raw files — human prose included. The
    old AI-section-only scan made every unmarked doc invisible: searching
    'ghost message' whiffed while GHOST_MESSAGES.md sat in the corpus
    (the "search has no search" AIX bug, 2026-08-05).
    """
    terms = _terms(query or "")
    if not terms:
        return []
    phrase = query.lower().strip()
    # Adjacent word pairs as typed ('cut off', 'chat room'): common words that
    # mean something together
    raw = re.findall(r"[a-z0-9/_.-]+", phrase)
    pairs = [f"{a} {b}" for a, b in zip(raw, raw[1:]) if not (a in _STOP and b in _STOP)]
    texts = {}
    for name, filepath in available.items():
        if name in UNSEARCHED:
            continue
        try:
            texts[name] = filepath.read_text(encoding='utf-8')
        except Exception:
            continue
    # A word most docs share ('control', 'set') says little; a rare one says a lot.
    # Words match at a word start: 'cut' is not in 'execute', 'device' finds 'devices'
    pat = {t: re.compile(r'(?<![a-z0-9])' + re.escape(t)) for t in terms}
    df = {t: sum(bool(pat[t].search(c.lower())) for c in texts.values()) for t in terms}
    idf = {t: 1 + math.log((len(texts) + 1) / (df[t] + 1)) for t in terms}
    scored = []
    for name, content in texts.items():
        lower = content.lower()
        counts = {t: len(pat[t].findall(lower)) for t in terms}
        hit = [t for t in terms if counts[t]]
        if not hit:
            continue
        heads = " ".join(h.lower() for h, _ in _sections(content))
        score = (sum(idf[t] * (4 + min(counts[t], 25) ** 0.5) for t in hit)
                 + sum(10 for t in hit if t in name)
                 + sum(2 for t in hit if t in heads)
                 + sum(6 for p in pairs if p in lower)
                 + (8 if len(terms) > 1 and phrase in lower else 0))
        # Author guides answer "how do I build"; her questions are mostly "how do I use"
        if name.startswith("plugin-author/"):
            score *= 0.75
        # Snippet: the line with the most distinct words, body before headings,
        # earliest on a tie
        lines = content.splitlines()
        best_i = max(range(len(lines)),
                     key=lambda i: (sum(bool(pat[t].search(lines[i].lower())) for t in terms),
                                    not lines[i].startswith("#"), -i))
        line = lines[best_i]
        at = min((m.start() for t in hit for m in [pat[t].search(line.lower())] if m), default=0)
        start = max(0, at - snippet_chars // 2)
        snippet = line[start:start + snippet_chars].strip()
        if start > 0:
            snippet = '...' + snippet
        if start + snippet_chars < len(line):
            snippet += '...'
        section = next((l.lstrip("#").strip() for l in reversed(lines[:best_i + 1])
                        if re.match(r"#{2,3} ", l)), "")
        scored.append((score, name, sum(counts.values()), re.sub(r'\s+', ' ', snippet), section))
    scored.sort(key=lambda r: -r[0])
    return [r[1:] for r in scored[:max_results]]


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

    # Never decided by the filesystem's glob order: user docs before the
    # prefixed sets, then the shortest name.
    def pick(names):
        return min(names, key=lambda n: ("/" in n, n.startswith("plugin/"), len(n), n), default=None)

    variants = {query, query[:-1] if query.endswith("s") else query + "s"}

    def words(n):
        return set(n.split("/")[-1].split("-"))

    # Whole word of a user doc ('commands' -> 'slash-commands', 'room' -> 'chat-room')
    found = pick([n for n in available if "/" not in n and variants & words(n)])
    if found:
        return found
    # Exact basename of a prefixed doc ('hooks' -> 'plugin-author/hooks')
    # before substring matching, which would hit 'daemons-webhooks' first
    found = pick([n for n in available if "/" in n and n.split("/", 1)[1] in variants])
    if found:
        return found
    # Whole word of a prefixed doc
    found = pick([n for n in available if "/" in n and variants & words(n)])
    if found:
        return found
    # Partial match either way round
    return pick([n for n in available if query in n or n.split("/")[-1] in query])


AVAILABLE_FUNCTIONS = ['search_help_docs']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "search_help_docs",
            "description": f"Sapphire docs. No args: what this app is. Top docs: {', '.join(TOP_DOCS)}. self + full=true: feature list. plugin/<name>: a plugin's README.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search all docs, plain words"
                    },
                    "doc_name": {
                        "type": "string",
                        "description": "Doc to read, fuzzy match"
                    },
                    "section": {
                        "type": "string",
                        "description": "One heading of that doc"
                    },
                    "full": {
                        "type": "boolean",
                        "description": "Whole doc, not the AI summary"
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
    
    doc_name = str(arguments.get('doc_name') or '').strip()
    query = str(arguments.get('query') or '').strip()
    section = str(arguments.get('section') or '').strip()
    full = arguments.get('full', False)
    if isinstance(full, str):
        full = full.strip().lower() in ('true', '1', 'yes')
    available = _get_available_docs()

    if not available:
        return "No documentation files found in docs/ directory.", False

    def doc_list():
        names = sorted(n for n in available if not n.startswith("plugin/"))
        return ", ".join(names) + " (and plugin/<name> for plugin READMEs)"

    # Query mode: cross-doc search. Routes the AI to the right doc without
    # it having to read each one blindly. With doc_name too, the query
    # picks a section of that doc instead (below).
    if query and not doc_name:
        hits = _search_across_docs(query, available)
        if not hits:
            return f"No matches for '{query}'. Available docs: {doc_list()}", True
        lines = [f"Matches for '{query}':", ""]
        for name, count, snippet, sec in hits:
            where = f" § {sec}" if sec else ""
            lines.append(f"[{name}]{where} ({count} match{'es' if count != 1 else ''})")
            lines.append(f"  {snippet}")
            lines.append("")
        lines.append("Read: doc_name='<name>' = its AI reference; add section='<heading>' "
                     "for one part, or full=true for the human doc.")
        return "\n".join(lines), True

    # No argument: the app abstract. Kept short in the doc itself — this
    # answer may ride a wake tool, so it is never padded with a doc list.
    if not doc_name:
        abstract = _extract_ai_section(available[ABSTRACT_DOC]) if ABSTRACT_DOC in available else ""
        if abstract:
            return abstract, True
        return f"Available docs: {', '.join(sorted(available.keys()))}", True

    # With argument: get specific doc's content
    matched = _match_doc_name(doc_name, available)
    
    if not matched:
        # A name that names nothing is usually a topic: answer with the docs
        # that hold it, not a dead end.
        hits = _search_across_docs(doc_name, available, max_results=3)
        if hits:
            near = ", ".join(f"{n}" + (f" § {s}" if s else "") for n, _, _, s in hits)
            return f"No doc named '{doc_name}'. Docs about it: {near}", False
        return f"Doc '{doc_name}' not found. Available: {doc_list()}", False

    filepath = available[matched]

    # One part: by heading, or the part that holds the query's words.
    # Searched over the whole file, human prose included.
    want = section or query
    if want:
        try:
            part = _find_section(filepath.read_text(encoding='utf-8'), want)
        except Exception:
            part = None
        if part:
            return _cap(part.strip(), matched), True
        heads = [h for h, _ in _sections(filepath.read_text(encoding='utf-8')) if h]
        return f"No section '{want}' in {matched}. Sections: {' · '.join(heads)}", False

    content = _extract_ai_section(filepath, full=full)

    if not content:
        return f"Doc '{matched}' exists but has no '## Reference for AI' section yet.", False

    return _cap(content, matched), True