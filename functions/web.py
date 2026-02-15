# functions/web.py

import json
import logging
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
    'get_images',
]

TOOLS = [
    {
        "type": "function",
        "network": True,
        "is_local": False,
        "function": {
            "name": "web_search",
            "description": "Search the web to find relevant URLs. Returns titles and URLs only - use get_website to read content.",
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
            "description": "Fetch and read the full content of a webpage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"}
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
            "description": "Get Wikipedia article summary for any topic",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic to search Wikipedia"}
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
            "description": "Use this for advanced research if you want. It returns multiple pages of data on your topic.",
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
            "description": "Get internal text links from a website for browsing its structure. Returns anchor text and URLs for categories, navigation, and content links. Use this to explore a site's structure before extracting images with get_images.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to browse"},
                    "strip_nav": {"type": "boolean", "description": "Strip header/footer/nav links to focus on content area links (default true). Set false to include full site navigation."}
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
            "name": "get_images",
            "description": "Extract image URLs from a webpage's content area. Returns image URLs with descriptions. To display images to the user, use markdown: ![description](url). Set show_to_user to auto-display all images in a gallery.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to extract images from"},
                    "show_to_user": {"type": "boolean", "description": "Auto-display all images in chat gallery (default false)"}
                },
                "required": ["url"]
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
        clear_session_cache()
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

def _best_srcset_url(srcset: str) -> str:
    """Pick best URL from srcset, preferring ~1920w."""
    if not srcset:
        return None
    candidates = []
    for part in srcset.split(','):
        part = part.strip()
        if not part:
            continue
        pieces = part.split()
        if len(pieces) >= 2:
            url, descriptor = pieces[0], pieces[1]
            if descriptor.endswith('w'):
                try:
                    candidates.append((url, int(descriptor[:-1])))
                except ValueError:
                    candidates.append((url, 0))
            elif descriptor.endswith('x'):
                try:
                    candidates.append((url, int(float(descriptor[:-1]) * 1000)))
                except ValueError:
                    candidates.append((url, 0))
        elif len(pieces) == 1:
            candidates.append((pieces[0], 0))
    if not candidates:
        return None
    # Prefer closest to 1920w, fall back to largest
    best = min(candidates, key=lambda c: abs(c[1] - 1920) if c[1] > 0 else float('inf'))
    return best[0]


_JUNK_PATTERNS = ['logo', 'icon', 'sprite', 'pixel', 'badge', 'emoji', 'favicon',
                  'avatar', '1x1', 'spacer', 'blank', 'tracking', 'spinner', 'loader']


def extract_images(html: str, base_url: str) -> list:
    """Extract content images from HTML, stripping nav/header/footer."""
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(WORK_WEBSITE_STRIP_ELEMENTS + ['form']):
        tag.decompose()

    images = []
    seen_urls = set()

    for img in soup.find_all('img'):
        src = (img.get('data-src') or img.get('data-lazy-src') or
               _best_srcset_url(img.get('srcset')) or img.get('src'))
        if not src:
            continue
        if src.startswith('data:') or src.endswith('.svg'):
            continue

        full_src = urllib.parse.urljoin(base_url, src)

        if full_src in seen_urls:
            continue
        seen_urls.add(full_src)

        src_lower = full_src.lower()
        if any(p in src_lower for p in _JUNK_PATTERNS):
            continue

        # Skip tiny images
        width, height = img.get('width', ''), img.get('height', '')
        try:
            if width and height and (int(width) < 80 or int(height) < 80):
                continue
        except (ValueError, TypeError):
            pass

        alt = img.get('alt', '').strip()
        title = img.get('title', '').strip()

        # Check for figcaption
        caption = ''
        figure = img.find_parent('figure')
        if figure:
            figcaption = figure.find('figcaption')
            if figcaption:
                caption = figcaption.get_text(strip=True)

        # Parent link
        parent_link = ''
        parent_a = img.find_parent('a')
        if parent_a and parent_a.get('href'):
            parent_link = urllib.parse.urljoin(base_url, parent_a['href'])

        images.append({
            'url': full_src,
            'name': caption or alt or title or '',
            'link': parent_link
        })

    logger.info(f"[WEB] Extracted {len(images)} images from {base_url}")
    return images[:30]


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
                
                content = extract_content(resp.text)
                if not content:
                    logger.warning(f"[WEB] get_website: No content extracted from {url}")
                    return "Could not extract content from that website.", False
                
                if len(content) > WORK_WEBSITE_MAX_CONTENT:
                    logger.info(f"[WEB] get_website: Truncating from {len(content)} to {WORK_WEBSITE_MAX_CONTENT}")
                    content = content[:WORK_WEBSITE_MAX_CONTENT] + f"\n\n[Truncated to {WORK_WEBSITE_MAX_CONTENT} chars]"
                
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

        elif function_name == "get_images":
            if not (url := arguments.get('url')):
                logger.warning("[WEB] get_images: No URL provided")
                return "I need a URL to get images from.", False

            logger.info(f"[WEB] get_images: Fetching {url}")
            try:
                resp = get_session().get(url, timeout=12)
                if resp.status_code != 200:
                    return f"Couldn't access website. HTTP {resp.status_code}", False

                images = extract_images(resp.text, url)
                if not images:
                    return "No images found in the content area of that page.", True

                lines = []
                for i, img in enumerate(images, 1):
                    name = img['name'] or '(no description)'
                    line = f"{i}. [{name}] {img['url']}"
                    if img['link']:
                        line += f"\n   → links to: {img['link']}"
                    lines.append(line)

                out = "\n\n".join(lines)

                if arguments.get('show_to_user', False):
                    urls = [img['url'] for img in images]
                    return (f"Found {len(images)} images on {url}. Images are displayed in chat.\n\n{out}"
                            f"\n<!--GALLERY:{json.dumps(urls)}-->"), True

                return (f"Found {len(images)} images on {url}:\n\n{out}\n\n"
                        "To display images, use: ![description](image_url)"), True
            except ValueError as e:
                if "SOCKS5 is enabled" in str(e):
                    return "Web access failed: SOCKS5 credentials not configured.", False
                raise
            except requests.exceptions.ProxyError as e:
                logger.error(f"[WEB] get_images: SOCKS proxy error: {e}")
                return "Web access failed: SOCKS proxy error.", False
            except requests.exceptions.ConnectionError as e:
                logger.error(f"[WEB] get_images: Connection error: {e}")
                return "Web access failed: Connection error.", False
            except Exception as e:
                logger.error(f"[WEB] get_images: {type(e).__name__}: {e}")
                return f"Error getting images: {str(e)}", False

        logger.warning(f"[WEB] Unknown function: {function_name}")
        return f"Unknown function: {function_name}", False

    except SocksAuthError as e:
        logger.error(f"[WEB] {function_name} SOCKS auth failed: {e}")
        return f"Web access blocked: {e}", False
    except Exception as e:
        logger.error(f"[WEB] {function_name} unhandled error: {type(e).__name__}: {e}")
        return f"Error executing {function_name}: {str(e)}", False