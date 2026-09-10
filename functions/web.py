# functions/web.py

import json
import logging
import re
import urllib.parse
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.socks_proxy import get_session, clear_session_cache, SocksAuthError
import config

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🌐'

WORK_SEARCH_MAX_RESULTS = 8
WORK_WEBSITE_MAX_CONTENT = 12000
WORK_WEBSITE_STRIP_ELEMENTS = ["script", "style", "nav", "footer", "header", "aside", "iframe"]

AVAILABLE_FUNCTIONS = [
    'web_search',
    'get_website',
    'get_wikipedia',
    'research_topic',
    'get_site_links',
    'web_view_images',
]

TOOLS = [
    {
        "type": "function",
        "network": True,
        "is_local": False,
        "function": {
            "name": "web_search",
            "description": "Search the web. Returns titles + URLs. Use get_website to read content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search phrase"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "network": True,
        "is_local": False,
        "function": {
            "name": "get_website",
            "description": ("Fetch full content of a webpage. show_image_urls='true' appends the page's image "
                            "URLs (alt, size); 'only' returns just that list — hand one to web_view_images(url=) "
                            "to look at it."),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL"},
                    "show_image_urls": {"type": "string", "enum": ["false", "true", "only"],
                                        "description": "false (default) = text only; true = text + image URLs; only = image URLs alone"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "network": True,
        "is_local": False,
        "function": {
            "name": "get_wikipedia",
            "description": "Wikipedia article summary for a topic.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic"}
                },
                "required": ["topic"]
            }
        }
    },
    {
        "type": "function",
        "network": True,
        "is_local": False,
        "function": {
            "name": "research_topic",
            "description": "Advanced research. Returns multiple pages of data on a topic.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Topic or question to research"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "network": True,
        "is_local": False,
        "function": {
            "name": "get_site_links",
            "description": "Internal text links from a webpage (anchor + URL). Explore a site's structure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL"},
                    "strip_nav": {"type": "boolean", "description": "Strip header/footer/nav (default true)"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "network": True,
        "is_local": False,
        "function": {
            "name": "web_view_images",
            "description": ("Search the web for images, or look at one image URL. query + count (default 6, "
                            "max 12) + page (default 1) → the user gets numbered tiles and you get the same "
                            "pictures as a numbered contact sheet (view=true, the default; count=1 = the one "
                            "image). Every hit carries an img: handle = its thumbnail, kept in this chat: "
                            "memory_view_image(image_id=img:...) to look again, memory_save_image(img:...) to keep it. "
                            "'full' is the original on its host (bigger, may refuse). url= views one image. "
                            "Bing answers well-known subjects best — lead with the famous part."),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for"},
                    "url": {"type": "string", "description": "An image URL to view instead of searching"},
                    "count": {"type": "integer", "description": "Results per page, 1-12 (default 6)"},
                    "page": {"type": "integer", "description": "Page of results, from 1 (default 1) — the next page has new pictures"},
                    "view": {"type": "boolean", "description": "Look at them yourself (default true; false = tiles for the user only)"}
                },
                "required": []
            }
        }
    }
]

def _parse_ddg_results(html: str, max_results: int = 15) -> list:
    """Parse DDG HTML response into result dicts."""
    soup = BeautifulSoup(html, 'html.parser')
    result_divs = soup.find_all('div', class_='result')
    results = []
    for div in result_divs[:max_results]:
        if div.find('div', class_='badge--ad__tooltip-wrap'):
            continue
        title_link = div.find('a', class_='result__a')
        url_link = div.find('a', class_='result__url')
        snippet_link = div.find('a', class_='result__snippet')
        if title_link and url_link:
            href = url_link.get('href', '')
            if href.startswith('//'):
                href = 'https:' + href
            if 'duckduckgo.com/l/?uddg=' in href:
                try:
                    parsed = urllib.parse.urlparse(href)
                    params = urllib.parse.parse_qs(parsed.query)
                    if 'uddg' in params:
                        href = urllib.parse.unquote(params['uddg'][0])
                except Exception:
                    continue
            results.append({
                'title': title_link.get_text(strip=True),
                'href': href,
                'body': snippet_link.get_text(strip=True)[:50] if snippet_link else ''
            })
    return results


def search_ddg_html(query: str, max_results: int = 15) -> list:
    logger.info(f"[WEB] DDG search requested")
    encoded = urllib.parse.quote_plus(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded}&kp=-1&kl=us-en"

    try:
        logger.info(f"[WEB] Fetching DDG: {url}")
        resp = get_session().get(url, timeout=12)
    except (SocksAuthError, requests.exceptions.ProxyError, requests.exceptions.ConnectionError) as e:
        # Stale session or transient failure — clear cache and retry once
        logger.warning(f"[WEB] DDG request failed ({type(e).__name__}), retrying with fresh session...")
        # Drop only net's pooled sessions (fresh connection pool) — NOT
        # clear_session_cache(), which also re-derives proxy env and drops the
        # LLM httpx pool mid-conversation for one flaky search (longevity/
        # day-ruiner blast-radius finding, net-facade wave 2).
        from core import net as _net
        _net._invalidate()
        try:
            resp = get_session().get(url, timeout=12)
        except (SocksAuthError, ValueError) as e2:
            logger.error(f"[WEB] DDG retry SOCKS/auth failed: {e2}")
            raise
        except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError) as e2:
            logger.error(f"[WEB] DDG retry network failed: {e2}")
            raise
    except ValueError as e:
        logger.error(f"[WEB] SOCKS config error: {e}")
        raise
    except requests.exceptions.Timeout as e:
        logger.error(f"[WEB] DDG request timed out: {e}")
        return []
    except Exception as e:
        logger.error(f"[WEB] DDG request failed: {type(e).__name__}: {e}")
        return []

    try:
        logger.info(f"[WEB] DDG response: {resp.status_code}")
        if resp.status_code not in [200, 202]:
            logger.warning(f"[WEB] DDG bad status: {resp.status_code}")
            return []
        if not resp.text or len(resp.text) < 100:
            logger.warning(f"[WEB] DDG returned empty/minimal response ({len(resp.text)} chars)")
            return []
    except Exception as e:
        logger.error(f"[WEB] DDG response processing failed: {type(e).__name__}: {e}")
        return []

    logger.info(f"[WEB] Parsing DDG HTML ({len(resp.text)} chars)")
    results = _parse_ddg_results(resp.text, max_results)
    logger.info(f"[WEB] DDG found {len(results)} results")

    # DDG returns a challenge page on fresh sessions (HTTP 200 but no result divs)
    # Retry once on the warm connection — second request usually gets real results
    if not results and resp.text:
        preview = resp.text[:200].replace('\n', ' ')
        logger.warning(f"[WEB] No results, retrying (challenge page?): {preview}")
        try:
            resp = get_session().get(url, timeout=12)
            if resp.status_code == 200 and resp.text and len(resp.text) >= 100:
                results = _parse_ddg_results(resp.text, max_results)
                logger.info(f"[WEB] DDG retry found {len(results)} results")
        except Exception as e:
            logger.warning(f"[WEB] DDG retry failed: {e}")

    return results


def extract_content(html: str) -> str:
    """Extract readable content from HTML using BeautifulSoup."""
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(WORK_WEBSITE_STRIP_ELEMENTS + ['form']):
        tag.decompose()
    
    text = soup.get_text(separator=' ', strip=True)
    lines = (line.strip() for line in text.splitlines())
    result = '\n'.join(chunk for line in lines for chunk in line.split("  ") if chunk)
    logger.info(f"[WEB] Extracted {len(result)} chars")
    return result

def _px(v):
    """'600', '600px', ' 600 ' → 600; anything else → None."""
    digits = ''.join(ch for ch in str(v or '') if ch.isdigit())
    return int(digits) if digits else None


def extract_image_urls(html: str, base_url: str, limit: int = 40) -> list:
    """Content images on a page (get_website show_image_urls): og:image first,
    then every <img src|data-src> resolved absolute with its alt and declared
    WxH. Skips data: URIs, SVGs and declared tracking pixels (≤ 32px)."""
    soup = BeautifulSoup(html, 'html.parser')
    out, seen = [], set()
    og = soup.find('meta', property='og:image')
    if og and og.get('content'):
        u = urllib.parse.urljoin(base_url, og['content'].strip())
        seen.add(u)
        out.append({'url': u, 'alt': 'og:image', 'dims': ''})
    for img in soup.find_all('img'):
        src = (img.get('src') or img.get('data-src') or img.get('data-lazy-src') or '').strip()
        if not src or src.startswith('data:') or src.split('?')[0].lower().endswith('.svg'):
            continue
        u = urllib.parse.urljoin(base_url, src)
        if u in seen:
            continue
        w, h = _px(img.get('width')), _px(img.get('height'))
        if (w and w <= 32) or (h and h <= 32):
            continue
        seen.add(u)
        out.append({'url': u, 'alt': (img.get('alt') or '').strip()[:120],
                    'dims': f"{w}x{h}" if w and h else ''})
        if len(out) >= limit:
            break
    return out


def image_urls_text(imgs: list) -> str:
    if not imgs:
        return "No content images found on the page."
    return "\n".join(f"{i}. {im['alt'] or '(no alt)'}{' — ' + im['dims'] if im['dims'] else ''} — {im['url']}"
                     for i, im in enumerate(imgs, 1))


_BING_IMAGES = "https://www.bing.com/images/search"
_SAFESEARCH = ('off', 'moderate', 'strict')


def _parse_bing_images(html: str, limit: int) -> list:
    """Bing's results ride <a class="iusc" m="{json}">: murl (full), turl (thumb),
    purl (source page), t (title); dims sit in the card's .img_info span.
    Deduped by full URL; broken anchors skipped."""
    soup = BeautifulSoup(html, 'html.parser')
    out, seen = [], set()
    for a in soup.find_all('a', class_='iusc'):
        try:
            m = json.loads(a.get('m') or '')
        except ValueError:
            continue
        full, thumb = m.get('murl'), m.get('turl')
        if not full or not thumb or full in seen:
            continue
        seen.add(full)
        dims = ''
        card = a.find_parent(class_='iuscp')
        info = card.select_one('.img_info') if card else None
        if info:
            first = info.get_text(' ', strip=True).split(' ')[0]
            if '×' in first or 'x' in first:
                dims = first.replace('×', 'x')
        out.append({'full': full, 'thumb': thumb, 'page': m.get('purl') or '',
                    'title': (m.get('t') or '').strip(), 'dims': dims})
        if len(out) >= limit:
            break
    return out


_STOP = {'the', 'and', 'for', 'with', 'from', 'that', 'this', 'photo', 'photos', 'image', 'images',
         'picture', 'pictures', 'pic', 'pics', 'wallpaper', 'closeup', 'close', 'shot', 'shots'}


def _content_words(query: str) -> list:
    return [w for w in re.findall(r'[a-z0-9]+', (query or '').lower()) if len(w) >= 3 and w not in _STOP]


def looks_answered(query: str, results: list) -> bool:
    """The junk guard (2026-09-10, 'Plumbus rick and morty' → Mahindra Thar).
    Bing's no-JS path answers only subjects it knows well; for anything else it
    hands back a random cached page for some OTHER query, sometimes keyed on a
    single word ('great horned owl' → Great Value bread). A real answer carries
    the query's words in its titles/pages/URLs; filler doesn't. Accept when at
    least half the query's content words appear anywhere in the results."""
    words = _content_words(query)
    if not words or not results:
        return True
    hay = ' '.join(f"{r.get('title', '')} {r.get('page', '')} {r.get('full', '')}" for r in results).lower()
    hits = sum(1 for w in words if w in hay)
    return hits * 2 >= len(words)


def search_bing_images(query: str, count: int, page: int = 1, safesearch: str = 'off') -> list:
    """Bing's image results page, server side, through core.net. (DDG's own
    image endpoint is walled — 403 for every non-browser client, verified
    2026-09-09 — and DDG's images ARE Bing's.) The user's browser never
    touches Bing: tiles are our stashed thumbs; the lightbox rides DDG's image
    proxy (core.images.proxied). page = offset of count; safesearch = Bing's
    adlt (the WEB_IMAGES_SAFESEARCH setting, default off)."""
    from core import net
    first = (max(1, int(page or 1)) - 1) * count + 1
    adlt = safesearch if safesearch in _SAFESEARCH else 'off'
    url = (f"{_BING_IMAGES}?q={urllib.parse.quote_plus(query)}&form=HDRSC2"
           f"&first={first}&adlt={adlt}")
    logger.info(f"[WEB] Bing image search requested (page {page}, adlt={adlt})")
    resp = net.wan_session().get(url, timeout=15)
    if resp.status_code != 200 or not resp.text:
        logger.warning(f"[WEB] Bing images bad status: {resp.status_code}")
        return []
    results = _parse_bing_images(resp.text, count)
    if not results:
        preview = resp.text[:200].replace('\n', ' ')
        logger.warning(f"[WEB] Bing images: 0 parsed (markup drift?): {preview}")
    logger.info(f"[WEB] Bing images: {len(results)} results")
    return results


def _fetch_any(*urls):
    """First URL that resolves to image bytes, else None (logged, never raised)."""
    from core import images as ci
    for u in urls:
        try:
            return ci.resolve(u).data
        except ci.ImageError as e:
            logger.warning(f"[WEB] image fetch skipped: {e}")
    return None


def image_search_result(query: str, results: list, view: bool = True, page: int = 1):
    """Her numbered list + the user's row (core.images.gallery). Every hit's
    thumbnail is fetched through the facade and STASHED as an img: handle —
    ours: vault-aware, offline, saveable even when the host refuses hotlinks —
    and the same bytes make her contact sheet, so her #3 is the user's #3.
    view=true adds the pixels (the full image for one result, else the sheet).
    Returns str, or the images contract dict."""
    from core import images as ci
    entries = [{'raw': _fetch_any(r['thumb']), 'thumb': ci.proxied(r['thumb']), 'full': ci.proxied(r['full']),
                'title': r['title'], 'page': r['page']} for r in results]
    if view and len(results) == 1:
        entries[0]['show'] = _fetch_any(results[0]['full'])
    where = f" (page {page})" if page and page > 1 else ''
    images, tail = ci.gallery(f"{query}{where}", entries, view=view)
    lines = []
    for i, (r, e) in enumerate(zip(results, entries), 1):
        host = urllib.parse.urlsplit(r['page'] or r['full']).hostname or ''
        dims = f" — {r['dims']}" if r.get('dims') else ''
        tag = f" — {e['handle']}" if e['handle'] else " — (thumbnail unavailable)"
        lines.append(f"{i}. {r['title'] or '(untitled)'} — {host}{dims}{tag}\n   full: {r['full']}")
    text = (f"Top {len(results)} images for '{query}'{where} — the user sees them as numbered tiles. "
            f"img: = the thumbnail kept in this chat (view or save it); full = the original on its host.\n"
            + "\n".join(lines) + "\n" + tail)
    return ci.result(text, images) if images else text


def view_image_url(url: str):
    """web_view_images(url=): one image from the web, for both of you."""
    from core import images as ci
    try:
        r = ci.resolve(url)
        w, h = r.size
        shaped = ci.for_chat(r.data)
    except ci.ImageError as e:
        return str(e), False
    return ci.result(f"{r.label} — {w}x{h} — from the web. You're looking at it now.", [shaped]), True


def _safesearch() -> str:
    """The WEB_IMAGES_SAFESEARCH setting (Settings › Images), read per call —
    `config` inside execute() is the call's settings arg, not the module."""
    from core.settings_manager import settings
    return str(settings.get('WEB_IMAGES_SAFESEARCH', 'off') or 'off').lower()


def _clamp(v, default, lo, hi):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(n, hi)) if n else default


def extract_site_links(html: str, base_url: str, strip_nav: bool = True) -> list:
    """Extract internal text links from HTML."""
    soup = BeautifulSoup(html, 'html.parser')

    if strip_nav:
        for tag in soup(['header', 'footer', 'nav', 'aside']):
            tag.decompose()

    parsed_base = urllib.parse.urlparse(base_url)
    base_domain = parsed_base.netloc.lower().lstrip('www.')

    seen = set()
    links = []

    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if not href or href.startswith('#') or href.startswith('javascript:') or href.startswith('mailto:'):
            continue

        # Text anchors only - skip image-only links
        text = a.get_text(strip=True)
        if not text:
            continue

        full_url = urllib.parse.urljoin(base_url, href)

        # Internal links only
        parsed = urllib.parse.urlparse(full_url)
        link_domain = parsed.netloc.lower().lstrip('www.')
        if link_domain != base_domain:
            continue

        if full_url in seen:
            continue
        seen.add(full_url)

        links.append({'text': text, 'url': full_url})

    logger.info(f"[WEB] Extracted {len(links)} internal links from {base_url}")
    return links[:50]


def fetch_single_site(url: str, max_chars: int = 10000) -> dict:
    logger.info(f"[WEB] Fetching site: {url}")
    try:
        resp = get_session().get(url, timeout=12)
        logger.info(f"[WEB] Site response {url}: {resp.status_code}")
        if resp.status_code != 200:
            return {'url': url, 'content': None, 'error': f'HTTP {resp.status_code}'}
        
        content = extract_content(resp.text)
        if not content:
            logger.warning(f"[WEB] No content extracted from {url}")
            return {'url': url, 'content': None, 'error': 'No content extracted'}
        
        if len(content) > max_chars:
            logger.info(f"[WEB] Truncating {url} content from {len(content)} to {max_chars}")
            content = content[:max_chars]
        
        logger.info(f"[WEB] Successfully fetched {url}: {len(content)} chars")
        return {'url': url, 'content': content, 'error': None}
    except Exception as e:
        logger.error(f"[WEB] Fetch failed for {url}: {type(e).__name__}: {e}")
        return {'url': url, 'content': None, 'error': str(e)}

def execute(function_name, arguments, config):
    logger.info(f"[WEB] Executing {function_name}")
    try:
        if function_name == "web_search":
            if not (query := arguments.get('query')):
                logger.warning("[WEB] web_search: No query provided")
                return "I need a search query.", False

            try:
                results = search_ddg_html(query, WORK_SEARCH_MAX_RESULTS)
            except SocksAuthError as e:
                logger.error(f"[WEB] web_search: SOCKS auth failed: {e}")
                return f"Search failed: SOCKS proxy authentication error. Tell the user to check their SOCKS credentials in Settings.", False
            except ValueError as e:
                if "SOCKS5 is enabled" in str(e):
                    logger.error("[WEB] web_search: SOCKS misconfiguration")
                    return "Search failed: SOCKS5 is enabled but credentials are not configured. Tell the user to set them in Settings → SOCKS.", False
                raise
            except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError) as e:
                logger.error(f"[WEB] web_search: Network/proxy error: {e}")
                return "Search failed: Could not connect through the network/proxy. Tell the user to check their SOCKS proxy and internet connection.", False

            if not results:
                logger.warning(f"[WEB] web_search: No results for '{query}'")
                return f"Search returned no results for '{query}'. This may be a temporary issue with the search provider. Try rephrasing or try again.", True
            
            logger.info(f"[WEB] web_search: Returning {len(results)} results")
            # Title + URL only - no snippets to prevent lazy AI
            out = "\n".join(f"{r['title']}: {r['href']}" for r in results)
            return f"Found {len(results)} results:\n\n{out}\n\nUse get_website on URLs to read their content.", True

        elif function_name == "get_website":
            if not (url := arguments.get('url')):
                logger.warning("[WEB] get_website: No URL provided")
                return "I need a URL to fetch.", False
            
            logger.info(f"[WEB] get_website: Fetching {url}")
            try:
                resp = get_session().get(url, timeout=12)
                logger.info(f"[WEB] get_website: Response {resp.status_code}")
                if resp.status_code != 200:
                    logger.warning(f"[WEB] get_website: Non-200 status {resp.status_code}")
                    return f"Couldn't access website. HTTP {resp.status_code}", False
                
                mode = str(arguments.get('show_image_urls') or 'false').lower()
                imgs = extract_image_urls(resp.text, url) if mode in ('true', 'only') else []
                if mode == 'only':
                    return (f"Images on {url} ({len(imgs)}) — web_view_images(url=...) shows one:\n"
                            f"{image_urls_text(imgs)}"), True

                content = extract_content(resp.text)
                if not content:
                    logger.warning(f"[WEB] get_website: No content extracted from {url}")
                    return "Could not extract content from that website.", False
                
                if len(content) > WORK_WEBSITE_MAX_CONTENT:
                    logger.info(f"[WEB] get_website: Truncating from {len(content)} to {WORK_WEBSITE_MAX_CONTENT}")
                    content = content[:WORK_WEBSITE_MAX_CONTENT] + f"\n\n[Truncated to {WORK_WEBSITE_MAX_CONTENT} chars]"
                if mode == 'true':
                    content += f"\n\nImages on the page ({len(imgs)}):\n{image_urls_text(imgs)}"
                
                logger.info(f"[WEB] get_website: Success, {len(content)} chars")
                return content, True
            except ValueError as e:
                if "SOCKS5 is enabled" in str(e):
                    logger.error(f"[WEB] get_website: SOCKS misconfiguration")
                    return "Web access failed: SOCKS5 credentials not configured.", False
                raise
            except requests.exceptions.ProxyError as e:
                logger.error(f"[WEB] get_website: SOCKS proxy error: {e}")
                return "Web access failed: SOCKS proxy error.", False
            except requests.exceptions.ConnectionError as e:
                logger.error(f"[WEB] get_website: Connection error: {e}")
                return "Web access failed: Connection error.", False
            except Exception as e:
                logger.error(f"[WEB] get_website: {type(e).__name__}: {e}")
                return f"Error fetching website: {str(e)}", False

        elif function_name == "get_wikipedia":
            if not (topic := arguments.get('topic')):
                logger.warning("[WEB] get_wikipedia: No topic provided")
                return "I need a topic to search Wikipedia.", False
            
            logger.info(f"[WEB] get_wikipedia: Searching for '{topic}'")
            try:
                # Use search API for better results than opensearch
                search_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(topic)}&srlimit=5&format=json"
                resp = get_session().get(search_url, timeout=12)
                logger.info(f"[WEB] get_wikipedia: Search response {resp.status_code}")
                
                if resp.status_code != 200:
                    logger.warning(f"[WEB] get_wikipedia: Non-200 search status {resp.status_code}")
                    return "Wikipedia search failed.", False
                
                data = json.loads(resp.text)
                search_results = data.get('query', {}).get('search', [])
                
                if not search_results:
                    logger.warning(f"[WEB] get_wikipedia: No results for '{topic}'")
                    return f"No Wikipedia article found for '{topic}'.", False
                
                # Filter out disambiguation and list pages
                skip_patterns = ['disambiguation', '(disambiguation)', 'list of', 'index of']
                title = None
                for result in search_results:
                    result_title = result.get('title', '').lower()
                    if not any(pattern in result_title for pattern in skip_patterns):
                        title = result.get('title')
                        break
                
                # Fallback to first result if all are filtered
                if not title:
                    title = search_results[0].get('title')
                
                logger.info(f"[WEB] get_wikipedia: Selected article '{title}'")
                
                # Fetch the summary
                api_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title)}"
                resp = get_session().get(api_url, timeout=12)
                logger.info(f"[WEB] get_wikipedia: Article fetch response {resp.status_code}")
                
                if resp.status_code != 200:
                    logger.warning(f"[WEB] get_wikipedia: Non-200 article status {resp.status_code}")
                    return f"Failed to fetch Wikipedia article for '{title}'.", False
                
                article = json.loads(resp.text)
                
                # Check if we got a disambiguation page anyway (type field)
                if article.get('type') == 'disambiguation':
                    logger.info(f"[WEB] get_wikipedia: '{title}' is disambiguation, fetching links")
                    
                    # Get the actual page content to find real article links
                    links_url = f"https://en.wikipedia.org/w/api.php?action=query&titles={urllib.parse.quote(title)}&prop=links&pllimit=20&format=json"
                    links_resp = get_session().get(links_url, timeout=12)
                    
                    if links_resp.status_code == 200:
                        links_data = json.loads(links_resp.text)
                        pages = links_data.get('query', {}).get('pages', {})
                        
                        for page_id, page_data in pages.items():
                            links = page_data.get('links', [])
                            # Find first non-meta link
                            for link in links:
                                link_title = link.get('title', '')
                                if link_title and not any(x in link_title.lower() for x in ['wikipedia:', 'help:', 'category:', 'template:', 'disambiguation']):
                                    # Fetch this article instead
                                    alt_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(link_title)}"
                                    alt_resp = get_session().get(alt_url, timeout=12)
                                    if alt_resp.status_code == 200:
                                        article = json.loads(alt_resp.text)
                                        if article.get('type') != 'disambiguation':
                                            logger.info(f"[WEB] get_wikipedia: Resolved to '{link_title}'")
                                            break
                            break
                
                logger.info(f"[WEB] get_wikipedia: Success, returning article for '{article.get('title')}'")
                return f"**{article.get('title')}**\n\n{article.get('extract')}\n\nFull article: {article.get('content_urls', {}).get('desktop', {}).get('page', '')}", True
                
            except ValueError as e:
                if "SOCKS5 is enabled" in str(e):
                    logger.error("[WEB] get_wikipedia: SOCKS misconfiguration")
                    return "Wikipedia access failed: SOCKS5 is enabled in config but credentials are not configured. Set SAPPHIRE_SOCKS_USERNAME and SAPPHIRE_SOCKS_PASSWORD environment variables, or create user/.socks_config file.", False
                raise
            except requests.exceptions.ProxyError:
                logger.error("[WEB] get_wikipedia: SOCKS proxy error")
                return "Wikipedia access failed: SOCKS proxy connection error. The secure proxy is unreachable or credentials are invalid.", False
            except requests.exceptions.ConnectionError:
                logger.error("[WEB] get_wikipedia: Connection error")
                return "Wikipedia access failed: Network connection error. Unable to establish connection.", False
            except Exception as e:
                logger.error(f"[WEB] get_wikipedia: {type(e).__name__}: {e}")
                return f"Wikipedia error: {str(e)}", False

        elif function_name == "research_topic":
            if not (query := arguments.get('query')):
                logger.warning("[WEB] research_topic: No query provided")
                return "I need a topic or question to research.", False
            
            logger.info(f"[WEB] research_topic: Researching")
            try:
                results = search_ddg_html(query, max_results=15)
            except SocksAuthError as e:
                logger.error(f"[WEB] research_topic: SOCKS auth failed: {e}")
                return f"Research failed: SOCKS proxy authentication error. Tell the user to check their SOCKS credentials in Settings.", False
            except (ValueError, requests.exceptions.ProxyError, requests.exceptions.ConnectionError) as e:
                logger.error(f"[WEB] research_topic: Network error: {e}")
                return "Research failed: Network/proxy connection error. Tell the user to check their connection.", False
            if not results:
                logger.warning(f"[WEB] research_topic: No search results for '{query}'")
                return f"Search returned no results for '{query}'. Try rephrasing the query.", True
            
            logger.info(f"[WEB] research_topic: Found {len(results)} search results")
            
            skip_patterns = ['.gov', '.ru', 'api.', '/api/', '.pdf']
            safe_urls = [r for r in results if not any(p in r['href'].lower() for p in skip_patterns)][:3]
            
            if not safe_urls:
                logger.warning("[WEB] research_topic: No safe URLs after filtering")
                return "Found search results but no safe websites to fetch.", True
            
            logger.info(f"[WEB] research_topic: Fetching {len(safe_urls)} safe URLs")
            
            fetched = []
            errors = []
            
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = {executor.submit(fetch_single_site, r['href'], 10000): r for r in safe_urls}
                
                try:
                    for future in as_completed(futures, timeout=15):
                        try:
                            result = future.result(timeout=0.5)
                            if result['content']:
                                title = futures[future]['title']
                                fetched.append(f"=== SOURCE: {title} ===\nURL: {result['url']}\n\n{result['content']}")
                                logger.info(f"[WEB] research_topic: Successfully fetched {result['url']}")
                            else:
                                error_msg = f"{result['url']}: {result['error']}"
                                errors.append(error_msg)
                                logger.warning(f"[WEB] research_topic: {error_msg}")
                        except Exception as e:
                            errors.append(f"Fetch error: {str(e)}")
                            logger.warning(f"[WEB] research_topic: Future failed: {type(e).__name__}: {e}")
                except Exception as e:
                    logger.error(f"[WEB] research_topic: Batch error: {type(e).__name__}: {e}")
                    errors.append(f"Batch timeout: {str(e)}")
            
            if not fetched:
                error_summary = "; ".join(errors[:3]) if errors else "Unknown error"
                logger.warning(f"[WEB] research_topic: No content fetched. Errors: {error_summary}")
                return f"I found URLs but couldn't fetch content. Errors: {error_summary}", True
            
            logger.info(f"[WEB] research_topic: Success, fetched {len(fetched)} of {len(safe_urls)} sites")
            final = "\n\n" + "="*80 + "\n\n".join(fetched)
            return f"I researched '{query}' and successfully fetched {len(fetched)} of {len(safe_urls)} website(s). Here's what I found:\n{final}", True

        elif function_name == "get_site_links":
            if not (url := arguments.get('url')):
                logger.warning("[WEB] get_site_links: No URL provided")
                return "I need a URL to browse.", False

            strip_nav = arguments.get('strip_nav', True)
            logger.info(f"[WEB] get_site_links: Fetching {url} (strip_nav={strip_nav})")
            try:
                resp = get_session().get(url, timeout=12)
                if resp.status_code != 200:
                    return f"Couldn't access website. HTTP {resp.status_code}", False

                links = extract_site_links(resp.text, url, strip_nav)
                if not links:
                    return "No internal text links found on that page.", True

                out = "\n".join(f"{l['text']}: {l['url']}" for l in links)
                return f"Found {len(links)} links on {url}:\n\n{out}", True
            except ValueError as e:
                if "SOCKS5 is enabled" in str(e):
                    return "Web access failed: SOCKS5 credentials not configured.", False
                raise
            except requests.exceptions.ProxyError as e:
                logger.error(f"[WEB] get_site_links: SOCKS proxy error: {e}")
                return "Web access failed: SOCKS proxy error.", False
            except requests.exceptions.ConnectionError as e:
                logger.error(f"[WEB] get_site_links: Connection error: {e}")
                return "Web access failed: Connection error.", False
            except Exception as e:
                logger.error(f"[WEB] get_site_links: {type(e).__name__}: {e}")
                return f"Error browsing website: {str(e)}", False

        elif function_name == "web_view_images":
            query = (arguments.get('query') or '').strip()
            url = (arguments.get('url') or '').strip()
            if url and not query:
                return view_image_url(url)
            if not query:
                return "Give me a search phrase (query) or an image URL (url).", False
            count = _clamp(arguments.get('count'), 6, 1, 12)
            page = _clamp(arguments.get('page'), 1, 1, 50)
            view = arguments.get('view', True) not in (False, 'false', 0)
            safesearch = _safesearch()
            try:
                results = search_bing_images(query, count, page, safesearch)
            except SocksAuthError as e:
                logger.error(f"[WEB] web_view_images: SOCKS auth failed: {e}")
                return "Image search failed: SOCKS proxy authentication error. Tell the user to check their SOCKS credentials in Settings.", False
            except ValueError as e:
                if "SOCKS5 is enabled" in str(e):
                    return "Image search failed: SOCKS5 is enabled but credentials are not configured. Tell the user to set them in Settings → SOCKS.", False
                raise
            except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError) as e:
                logger.error(f"[WEB] web_view_images: Network/proxy error: {e}")
                return "Image search failed: Could not connect through the network/proxy. Tell the user to check their SOCKS proxy and internet connection.", False
            except requests.exceptions.Timeout:
                return "Image search timed out.", False
            if not results:
                where = f" on page {page}" if page > 1 else ''
                return f"No images found for '{query}'{where}.", True
            if not looks_answered(query, results):
                logger.info(f"[WEB] web_view_images: Bing filler rejected (no query words in the results)")
                return (f"Bing didn't answer '{query}' — from here it only answers well-known subjects and "
                        f"hands back an unrelated page for the rest, so I dropped those results rather than "
                        f"show them. Try leading with the famous part of the subject, or a broader phrase."), True
            return image_search_result(query, results, view, page), True

        logger.warning(f"[WEB] Unknown function: {function_name}")
        return f"Unknown function: {function_name}", False

    except SocksAuthError as e:
        logger.error(f"[WEB] {function_name} SOCKS auth failed: {e}")
        return f"Web access blocked: {e}", False
    except Exception as e:
        logger.error(f"[WEB] {function_name} unhandled error: {type(e).__name__}: {e}")
        return f"Error executing {function_name}: {str(e)}", False