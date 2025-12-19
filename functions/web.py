# functions/web.py

import json
import logging
import urllib.parse
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.socks_proxy import get_session, SocksAuthError
import config

logger = logging.getLogger(__name__)

ENABLED = True

WORK_SEARCH_MAX_RESULTS = 8
WORK_WEBSITE_MAX_CONTENT = 12000
WORK_WEBSITE_STRIP_ELEMENTS = ["script", "style", "nav", "footer", "header", "aside", "iframe"]

AVAILABLE_FUNCTIONS = [
    'web_search',
    'get_website',
    'get_wikipedia',
    'research_topic',
]

TOOLS = [
    {
        "type": "function",
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
    }
]

def search_ddg_html(query: str, max_results: int = 15) -> list:
    logger.info(f"[WEB] DDG search query: '{query}'")
    encoded = urllib.parse.quote_plus(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded}&kp=-1&kl=us-en"
    
    try:
        logger.info(f"[WEB] Fetching DDG: {url}")
        resp = get_session().get(url, timeout=12)
        logger.info(f"[WEB] DDG response: {resp.status_code}")
        if resp.status_code not in [200, 202]:
            logger.warning(f"[WEB] DDG bad status: {resp.status_code}")
            return []
        if not resp.text or len(resp.text) < 100:
            logger.warning(f"[WEB] DDG returned empty/minimal response ({len(resp.text)} chars)")
            return []
    except ValueError as e:
        # SOCKS misconfiguration - let execute() handle it
        if "SOCKS5 is enabled" in str(e):
            logger.error(f"[WEB] SOCKS misconfiguration: {e}")
            raise
        raise
    except requests.exceptions.ProxyError as e:
        logger.error(f"[WEB] SOCKS proxy failed: {e}")
        raise
    except Exception as e:
        logger.error(f"[WEB] DDG request failed: {type(e).__name__}: {e}")
        return []
    
    logger.info(f"[WEB] Parsing DDG HTML ({len(resp.text)} chars)")
    soup = BeautifulSoup(resp.text, 'html.parser')
    
    # Diagnostic: see what divs we actually have
    all_divs = soup.find_all('div', class_=True)
    div_classes = set()
    for div in all_divs[:20]:  # Sample first 20 divs
        div_classes.update(div.get('class', []))
    logger.info(f"[WEB] Sample div classes found: {sorted(list(div_classes))[:10]}")
    
    # Find result divs - DDG uses multiple classes including 'result' and 'web-result'
    result_divs = soup.find_all('div', class_='result')
    logger.info(f"[WEB] Found {len(result_divs)} result divs in HTML")
    
    results = []
    
    for div in result_divs[:max_results]:
        # Skip ads
        if div.find('div', class_='badge--ad__tooltip-wrap'):
            continue
        
        # New structure uses double-underscore classes
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
                except Exception as e:
                    logger.warning(f"[WEB] URL decode failed: {e}")
                    continue
            
            results.append({
                'title': title_link.get_text(strip=True),
                'href': href,
                'body': snippet_link.get_text(strip=True)[:50] if snippet_link else ''
            })
    
    logger.info(f"[WEB] DDG found {len(results)} results")
    if not results and resp.text:
        # Show first 500 chars to diagnose what page we got
        preview = resp.text[:500].replace('\n', ' ')
        logger.warning(f"[WEB] No results, HTML preview: {preview}")
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
    logger.info(f"[WEB] Executing {function_name} with args: {arguments}")
    try:
        if function_name == "web_search":
            if not (query := arguments.get('query')):
                logger.warning("[WEB] web_search: No query provided")
                return "I need a search query.", False

            try:
                results = search_ddg_html(query, WORK_SEARCH_MAX_RESULTS)
            except ValueError as e:
                if "SOCKS5 is enabled" in str(e):
                    logger.error("[WEB] web_search: SOCKS misconfiguration")
                    return "Search failed: SOCKS5 is enabled in config but credentials are not configured.", False
                raise
            except requests.exceptions.ProxyError:
                logger.error("[WEB] web_search: SOCKS proxy error")
                return "Search failed: SOCKS proxy connection error.", False
            except requests.exceptions.ConnectionError:
                logger.error("[WEB] web_search: Connection error")
                return "Search failed: Network connection error.", False
            
            if not results:
                logger.warning(f"[WEB] web_search: No results for '{query}'")
                return "No search results found.", True
            
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
            
            logger.info(f"[WEB] research_topic: Researching '{query}'")
            results = search_ddg_html(query, max_results=15)
            if not results:
                logger.warning(f"[WEB] research_topic: No search results for '{query}'")
                return "I couldn't find any search results to research that topic.", True
            
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

        logger.warning(f"[WEB] Unknown function: {function_name}")
        return f"Unknown function: {function_name}", False

    except SocksAuthError as e:
        logger.error(f"[WEB] {function_name} SOCKS auth failed: {e}")
        return f"Web access blocked: {e}", False
    except Exception as e:
        logger.error(f"[WEB] {function_name} unhandled error: {type(e).__name__}: {e}")
        return f"Error executing {function_name}: {str(e)}", False