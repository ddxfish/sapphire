# core/routes/videos.py — In-app Video Guide: a small multi-channel YouTube
# viewer for the Sapphire community.
#
# Keyless (channel RSS + public page JSON), in-memory cached, fails-graceful.
# Only contacts YouTube when /api/videos is requested AND the cache is stale
# (or ?refresh=1). Auth-gated like the rest of /api/* — keeps it inside
# Sapphire's session perimeter. Mirrors core/routes/store.py.
#
# Channel kinds:
#   primary   — has the curated Crash Course playlist + a Latest feed (Krem's)
#   community — Latest feed + channel header only (we vouch for the channel,
#               not each upload; user-toggleable via VIDEO_GUIDE_CHANNELS_DISABLED)

import asyncio
import html as _htmllib
import json
import logging
import re
import time
from typing import Optional
from xml.etree import ElementTree as ET

import httpx
from fastapi import APIRouter, Depends, Request

import config
from core.auth import require_login

logger = logging.getLogger(__name__)
router = APIRouter()

PLAYLIST_ID = "PL3x22_N-oxJEdAHy_GsokrMW9UzB13oTF"

# Curated channel manifest (shipped). channel_ids resolved from handles.
CHANNELS = [
    {"key": "sapphire", "name": "Sapphire Blue", "kind": "primary",
     "channel_id": "UCRzV1b0XUwczhHRBJk_lUzA", "handle": "@SapphireBlueAi",
     "playlist_id": PLAYLIST_ID},
    {"key": "cisco", "name": "Cisco (DZebra)", "kind": "community",
     "channel_id": "UCa1LIkxIMjcEJ7mS-GnvePQ", "handle": "@ciscocomputertech"},
    {"key": "raven", "name": "Raven", "kind": "community",
     "channel_id": "UCbMXqU7Vj63vhscBq4Bc8qA", "handle": "@Raven_AI_31Sapphire"},
]

# Crash Course seed — used as fallback if the playlist scrape ever breaks so
# the course tab never goes blank. Kept in playlist order.
COURSE_SEED = [
    {"id": "oFpBjfDMfEk", "title": "Developer rants about features", "dur": "16:06"},
    {"id": "S6DEs-dwmFI", "title": "Install Guide (Windows)", "dur": "10:24"},
    {"id": "T7sw7svnFek", "title": "First Run Guide", "dur": "44:40"},
    {"id": "JxgNAk4Y2qI", "title": "Prompt System Guide", "dur": "18:43"},
    {"id": "9noDUc6bWss", "title": "Tool and Toolset Guide", "dur": "19:59"},
    {"id": "nM__u1fiWCw", "title": "Memory, Mind and Knowledge Guide", "dur": "11:57"},
    {"id": "I3g3tzukpV0", "title": "Mind Guide", "dur": "20:13"},
    {"id": "pu0dauGBhgY", "title": "Spice System Guide", "dur": "11:03"},
    {"id": "5kqW-o35OU4", "title": "Personas Guide", "dur": "22:43"},
    {"id": "-XGqK8MsIK8", "title": "Tasks and Heartbeats", "dur": "11:59"},
    {"id": "1DiQ4oUC6R0", "title": "Daemons and Webhooks Guide", "dur": "20:45"},
    {"id": "LcgzudD20AA", "title": "Plugins and Toolmaker", "dur": "15:45"},
    {"id": "x4DFMXlb5NY", "title": "Settings Guide", "dur": "22:53"},
    {"id": "JFxwJHQkEDM", "title": "Help Guide", "dur": "7:01"},
    {"id": "i9reqW0dX90", "title": "Chat Management Guide", "dur": "13:28"},
]

_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

# ── Cache (mirror store.py) ──────────────────────────────────────────
_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = asyncio.Lock()


def _cache_ttl() -> float:
    try:
        return float(config.VIDEO_GUIDE_CACHE_TTL_SECONDS or 43200)
    except (AttributeError, TypeError, ValueError):
        return 43200.0  # 12h


async def _cache_get(key: str) -> Optional[dict]:
    async with _cache_lock:
        entry = _cache.get(key)
        if not entry:
            return None
        expires_at, payload = entry
        if time.monotonic() > expires_at:
            _cache.pop(key, None)
            return None
        return payload


async def _cache_set(key: str, payload: dict) -> None:
    async with _cache_lock:
        _cache[key] = (time.monotonic() + _cache_ttl(), payload)


def _disabled() -> set:
    try:
        return set(config.VIDEO_GUIDE_CHANNELS_DISABLED or [])
    except (AttributeError, TypeError):
        return set()


# ── YouTube parsing (keyless) ────────────────────────────────────────

def _extract_initial_data(html: str) -> Optional[dict]:
    """Pull the ytInitialData JSON blob out of a YouTube page (one line, minified)."""
    for pat in (r"var ytInitialData = (\{.*?\});</script>",
                r"ytInitialData\"\]\s*=\s*(\{.*?\});",
                r"ytInitialData\s*=\s*(\{.*?\});</script>"):
        m = re.search(pat, html)
        if m:
            try:
                return json.loads(m.group(1))
            except ValueError:
                continue
    return None


def _walk(obj, key: str) -> list:
    """Collect every value found under `key` anywhere in a nested dict/list."""
    found = []

    def rec(x):
        if isinstance(x, dict):
            if key in x:
                found.append(x[key])
            for v in x.values():
                rec(v)
        elif isinstance(x, list):
            for v in x:
                rec(v)
    rec(obj)
    return found


def _parse_playlist(html: str) -> list:
    data = _extract_initial_data(html)
    if not data:
        return []
    vids = []
    for r in _walk(data, "playlistVideoRenderer"):
        vid = r.get("videoId")
        if not vid:
            continue
        t = r.get("title", {})
        title = t["runs"][0]["text"] if "runs" in t else t.get("simpleText", "")
        dur = r.get("lengthText", {}).get("simpleText")
        vids.append({"id": vid, "title": title, "dur": dur})
    return vids


def _parse_channel_videos(html: str) -> list:
    """Latest uploads from a channel's /videos tab. The current YouTube layout
    uses lockupViewModel (not videoRenderer), and crucially includes the video
    duration — the one thing the RSS feed lacks. Newest first."""
    data = _extract_initial_data(html)
    if not data:
        return []
    out, seen = [], set()
    for r in _walk(data, "lockupViewModel"):
        if r.get("contentType") != "LOCKUP_CONTENT_TYPE_VIDEO":
            continue
        vid = r.get("contentId")
        if not vid or vid in seen:
            continue
        title = (((r.get("metadata") or {}).get("lockupMetadataViewModel") or {})
                 .get("title") or {}).get("content") or ""
        # The duration overlay lives in the thumbnail (contentImage) as the only
        # M:SS string there; the exact key path churns, so match the value.
        dur = None
        m = re.search(r'(\d{1,2}:\d{2}(?::\d{2})?)', json.dumps(r.get("contentImage", {})))
        if m:
            dur = m.group(1)
        seen.add(vid)
        out.append({"id": vid, "title": title, "dur": dur,
                    "thumb": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"})
    return out


def _parse_rss(xml_text: str) -> list:
    """Channel RSS (Atom) → latest videos. Newest first (YouTube's order)."""
    vids = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return vids
    ns = {"a": "http://www.w3.org/2005/Atom",
          "yt": "http://www.youtube.com/xml/schemas/2015"}
    for e in root.findall("a:entry", ns):
        vid = e.findtext("yt:videoId", namespaces=ns)
        if not vid:
            continue
        vids.append({
            "id": vid,
            "title": e.findtext("a:title", namespaces=ns) or "",
            "published": e.findtext("a:published", namespaces=ns) or "",
            "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        })
    return vids


def _parse_channel_header(html: str) -> dict:
    """Channel name + avatar + sub count for the mini-YouTube header strip.
    Best-effort — YouTube has two header layouts; missing fields degrade
    gracefully (the manifest name is the fallback)."""
    hdr = {"name": None, "avatar": None, "desc": None}
    m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    if m:
        hdr["name"] = _htmllib.unescape(m.group(1))
    m = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    if m:
        hdr["avatar"] = m.group(1)
    m = re.search(r'<meta property="og:description" content="([^"]*)"', html)
    if m:
        hdr["desc"] = _htmllib.unescape(m.group(1)).strip()
    # Sub count omitted — no reliable keyless source (the channel page also
    # shows OTHER channels' counts, so a naive match grabs the wrong number).
    return hdr


async def _get(client: httpx.AsyncClient, url: str) -> str:
    r = await client.get(url, headers=_UA)
    r.raise_for_status()
    return r.text


async def _build() -> dict:
    """Fetch every enabled channel (header + latest, + course for primary)."""
    out = []
    disabled = _disabled()
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for ch in CHANNELS:
            if ch["key"] in disabled:
                continue
            entry = {
                "key": ch["key"], "name": ch["name"], "kind": ch["kind"],
                "url": f"https://www.youtube.com/{ch['handle']}",
            }
            # One fetch of the channel's /videos tab supplies BOTH the header
            # (og: meta) and the latest uploads WITH durations.
            page = None
            try:
                page = await _get(client, f"https://www.youtube.com/channel/{ch['channel_id']}/videos")
            except Exception as e:
                logger.warning(f"[videos] channel page {ch['key']} failed: {e}")
            if page:
                hdr = _parse_channel_header(page)
                entry["avatar"] = hdr.get("avatar")
                entry["desc"] = hdr.get("desc")
                # Use the live channel name for the tab, truncated. Falls back to
                # the manifest name (set above) if og:title was missing.
                if hdr.get("name"):
                    nm = hdr["name"]
                    entry["name"] = nm if len(nm) <= 20 else nm[:20].rstrip() + "…"
            # Latest — from the /videos scrape (has durations); RSS is the
            # fallback if YouTube's markup shifts (no durations there).
            latest = _parse_channel_videos(page) if page else []
            if not latest:
                try:
                    latest = _parse_rss(
                        await _get(client, f"https://www.youtube.com/feeds/videos.xml?channel_id={ch['channel_id']}"))
                except Exception as e:
                    logger.warning(f"[videos] rss {ch['key']} failed: {e}")
            entry["latest"] = latest
            # Course (primary only) — playlist scrape, seeded fallback
            if ch["kind"] == "primary" and ch.get("playlist_id"):
                course = []
                try:
                    course = _parse_playlist(
                        await _get(client, f"https://www.youtube.com/playlist?list={ch['playlist_id']}"))
                except Exception as e:
                    logger.warning(f"[videos] playlist failed: {e}")
                entry["course"] = course or COURSE_SEED
            out.append(entry)
    return {"channels": out}


@router.get("/api/videos")
async def get_videos(request: Request, refresh: int = 0, _=Depends(require_login)):
    """Multi-channel video feed for the in-app Video Guide. Cached; ?refresh=1
    forces a re-fetch. Fails graceful to last-cached / empty."""
    key = "all"
    if not refresh:
        cached = await _cache_get(key)
        if cached is not None:
            return cached
    try:
        data = await _build()
    except Exception as e:
        logger.warning(f"[videos] build failed: {e}")
        cached = await _cache_get(key)
        return cached if cached is not None else {"channels": [], "unreachable": True}
    # Only cache a result that actually has channels (don't pin an empty error)
    if data.get("channels"):
        await _cache_set(key, data)
    return data
