"""Root requirements.txt must equal the union of install/requirements-*.txt
(minus the on-demand sets core/extras.py installs from a page when first needed).

The Dockerfile installs from the install/ split (minimal + tts + stt) and
INSTALLATION.md's minimal path uses the same files, while `pip install -r
requirements.txt` users get the root file. The two drifted for four months:
six packages landed only in root (telethon, mcp, ebooklib, pillow-heif,
httpx[socks], tzlocal), so the published Docker image shipped without the
Telegram and MCP plugins' deps and a non-root container can't pip its way
out (2026-09-20 release check). Same specifier on both sides, so an upper
bound added on one side travels to the other.
"""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
# The main install: root requirements.txt is exactly these sets. The voice
# sets are ALSO in core/extras.py EXTRAS (the setup wizard installs one when
# it is missing) but stay part of the main install. Any other EXTRAS set (the
# board flasher's esptool) is on-demand only and stands outside the parity
# rule (2026-10-08).
MAIN = {"minimal", "tts", "stt", "wakeword"}


def _on_demand():
    try:
        from core import extras
        return {(ROOT / spec['file']).resolve() for spec in extras.EXTRAS.values()
                if spec.get('file') and Path(spec['file']).stem.removeprefix('requirements-') not in MAIN}
    except Exception:
        return set()


SPLIT = sorted(p for p in (ROOT / "install").glob("requirements-*.txt") if p.resolve() not in _on_demand())


def _specs(path: Path) -> dict:
    out = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        spec = raw.split("#", 1)[0].strip()
        if not spec:
            continue
        name = re.split(r"[<>=!~;\s]", spec, 1)[0].lower()   # keeps extras: httpx[socks]
        out[name] = spec
    return out


def test_split_files_exist():
    names = {p.name for p in SPLIT}
    assert {f"requirements-{n}.txt" for n in MAIN} <= names


def test_install_split_matches_root():
    root = _specs(ROOT / "requirements.txt")
    union = {}
    for p in SPLIT:
        for name, spec in _specs(p).items():
            assert name not in union, f"{name} listed twice across install/ ({p.name})"
            union[name] = spec

    only_root = sorted(set(root) - set(union))
    only_split = sorted(set(union) - set(root))
    assert not only_root, f"in requirements.txt but missing from install/*.txt: {only_root}"
    assert not only_split, f"in install/*.txt but missing from requirements.txt: {only_split}"

    drift = {n: (root[n], union[n]) for n in root if root[n] != union[n]}
    assert not drift, f"specifier differs root vs install/: {drift}"


def test_untested_majors_are_bounded():
    """The bounds themselves — lift one only after porting that provider."""
    root = _specs(ROOT / "requirements.txt")
    for name, bound in (("openai", "<3"), ("anthropic", "<1"), ("mcp", "<2")):
        assert bound in root[name].replace(".0.0", ""), f"{name} lost its upper bound: {root[name]}"
