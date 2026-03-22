"""Verify all markdown links between docs resolve to real files."""
import re
from pathlib import Path

DOCS = Path(__file__).parent.parent / "docs"
ROOT = Path(__file__).parent.parent


def find_md_links(path: Path):
    """Yield (source_file, link_target) for all relative .md links."""
    for md in path.rglob("*.md"):
        for m in re.finditer(r'\[.*?\]\(([^)]+\.md)\)', md.read_text()):
            target = m.group(1)
            if target.startswith("http"):
                continue
            yield md, target


def test_doc_internal_links():
    broken = []
    for src, link in find_md_links(DOCS):
        resolved = (src.parent / link).resolve()
        if not resolved.exists():
            broken.append(f"{src.relative_to(ROOT)} -> {link}")
    assert not broken, "Broken doc links:\n" + "\n".join(broken)


def test_readme_doc_links():
    broken = []
    readme = ROOT / "README.md"
    if not readme.exists():
        return
    for m in re.finditer(r'\[.*?\]\(([^)]+\.md)\)', readme.read_text()):
        target = m.group(1)
        if target.startswith("http"):
            continue
        resolved = (ROOT / target).resolve()
        if not resolved.exists():
            broken.append(f"README.md -> {target}")
    assert not broken, "Broken README links:\n" + "\n".join(broken)
