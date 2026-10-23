"""Dashboard font bootstrap.

Sapphire's dashboard hero uses Dancing Script (SIL OFL 1.1) for the name.
Rather than ship the woff2 binary in the repo, we download it from Google
Fonts on first boot and stash it per-install in user/fonts/. All other UI
text falls back to system monospace, which renders fine without a custom
download.

Why first-run download instead of bundling:
- Keeps the GitHub repo free of font binaries (some maintainers prefer
  this for licensing peace-of-mind, even though OFL 1.1 explicitly
  permits redistribution)
- Users can opt out via DASHBOARD_FONTS_AUTOFETCH=false
- If download fails (offline, upstream changes), the dashboard's CSS
  falls back to system 'cursive' / 'monospace' — non-fatal degradation

The download happens once at startup. Idempotent — only fetches files
that don't already exist.
"""
import logging
import re
from pathlib import Path
from core.fs_utils import replace_with_retry

logger = logging.getLogger(__name__)

# Chrome user-agent — Google Fonts serves woff2 only to modern browsers.
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

_CSS_URL = (
    "https://fonts.googleapis.com/css2?"
    "family=Dancing+Script:wght@500;600&"
    "display=swap"
)

# We pick the latin-only subset (unicode-range starts with U+0000-00FF).
# Covers ASCII + accented Latin chars — enough for English and most
# European languages. Full Unicode coverage would mean ~6 woff2 files
# per family; latin alone keeps each family to a single ~30-50 KB file.
_LATIN_RANGE_MARKER = "U+0000-00FF"

# What we want, in user/fonts/. Single entry — JetBrains Mono was tried
# but system mono fallbacks render the small UI text fine without it.
_TARGETS = {
    "dancing-script.woff2": "Dancing Script",
}


def _http_get(url: str, timeout: float) -> bytes:
    """All font fetches ride core.socks_proxy.get_session() — raw urllib went
    DIRECT even with the SOCKS proxy on, a boot-time leak on a privacy app
    (X1 warning / wave-3 B3, 2026-08-31). The sibling fonts route already
    did it right; this module didn't."""
    from core.socks_proxy import get_session
    resp = get_session().get(url, headers={"User-Agent": _UA}, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def _fetch_css(timeout: float = 15.0) -> str:
    return _http_get(_CSS_URL, timeout).decode("utf-8")


def _extract_latin_url(css: str, family: str) -> str | None:
    """Find the latin-subset woff2 URL for `family`.

    Google Fonts CSS contains many @font-face blocks per family — one
    per (weight × subset). We pick the first block that mentions our
    target family AND whose unicode-range covers basic Latin.
    """
    blocks = re.split(r"@font-face\s*\{", css)
    for block in blocks:
        if family.lower() in block.lower() and _LATIN_RANGE_MARKER in block:
            m = re.search(r"src:\s*url\(([^)]+)\)", block)
            if m:
                return m.group(1).strip()
    return None


def _download(url: str, dest: Path, timeout: float = 20.0) -> bool:
    """Download `url` to `dest`. Validates woff2 magic bytes. Logs on
    failure; returns success bool. Atomic via temp + rename so a partial
    download doesn't leave a broken file."""
    try:
        data = _http_get(url, timeout)
        if not data.startswith(b"wOF2"):
            logger.warning(f"font_bootstrap: {url} returned non-woff2 ({len(data)} bytes)")
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(data)
        replace_with_retry(tmp, dest)
        return True
    except Exception as e:
        logger.warning(f"font_bootstrap: download failed {url} -> {type(e).__name__}: {e}")
        return False


def ensure_dashboard_fonts(user_dir: Path) -> dict[str, bool]:
    """Ensure dashboard fonts exist in `user_dir/fonts/`.

    Idempotent: skips files that are already present. Honors the
    DASHBOARD_FONTS_AUTOFETCH setting (default true) — if disabled,
    only existing files are reported and nothing is fetched.

    Returns {filename: present_after_call}. False entries indicate the
    CSS will fall back to system fonts for that family.
    """
    fonts_dir = Path(user_dir) / "fonts"

    # Fast path — all files in place.
    if all((fonts_dir / fname).exists() for fname in _TARGETS):
        return {fname: True for fname in _TARGETS}

    # Honor the opt-out setting.
    try:
        import config
        if not getattr(config, "DASHBOARD_FONTS_AUTOFETCH", True):
            logger.info("font_bootstrap: DASHBOARD_FONTS_AUTOFETCH disabled; skipping fetch")
            return {fname: (fonts_dir / fname).exists() for fname in _TARGETS}
    except Exception:
        # config not available yet (very early boot) — proceed with default-on.
        pass

    # Need at least one file. Fetch in a BACKGROUND thread — this used to run
    # inline at api_fastapi import and could block boot ~35s of socket
    # timeouts on a firewalled box (X1 warning / wave-3 B3). Missing files
    # 404 cleanly and the CSS falls back, so boot never needs to wait.
    def _fetch_missing():
        try:
            css = _fetch_css()
        except Exception as e:
            logger.warning(f"font_bootstrap: could not fetch Google Fonts CSS: {type(e).__name__}: {e}")
            return
        fonts_dir.mkdir(parents=True, exist_ok=True)
        for fname, family in _TARGETS.items():
            dest = fonts_dir / fname
            if dest.exists():
                continue
            url = _extract_latin_url(css, family)
            if not url:
                logger.warning(f"font_bootstrap: no latin URL found for {family} in CSS")
                continue
            if _download(url, dest):
                logger.info(f"font_bootstrap: downloaded {family} -> {dest}")

    import threading
    threading.Thread(target=_fetch_missing, daemon=True,
                     name="font-bootstrap").start()
    # Report current presence; the thread fills gaps shortly after boot.
    return {fname: (fonts_dir / fname).exists() for fname in _TARGETS}
