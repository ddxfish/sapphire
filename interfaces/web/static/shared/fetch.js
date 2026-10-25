// /static/shared/fetch.js - Shared fetch utility with timeout and auth handling

// Session ID for SSE origin tracking - generated once per browser tab
const getSessionId = () => {
  let sid = sessionStorage.getItem('sapphire_session_id');
  if (!sid) {
    sid = 'ses_' + Math.random().toString(36).slice(2) + Date.now().toString(36);
    sessionStorage.setItem('sapphire_session_id', sid);
  }
  return sid;
};

// Export for SSE filtering
export const sessionId = getSessionId();

// A thrown error as words: the browser's own ("Failed to fetch", Safari's
// "Load failed", "Unexpected token <" from a non-JSON reply) never reach a person.
const NETWORK = /failed to fetch|load failed|networkerror|network request failed/i;
export function errWords(e, fallback = "That didn't work. Try again.") {
  const m = e?.message || '';
  if (e instanceof TypeError && NETWORK.test(m)) return "Can't reach Sapphire. Check that it's running, then reload the page.";
  if (e instanceof SyntaxError) return 'Sapphire sent back something unexpected. Reload the page and try again.';
  return m || fallback;
}

// Plain words for a status when the reply carries none of its own.
export function statusWords(status) {
  if (status === 401) return 'You were signed out. Reload the page and sign in.';
  if (status === 403) return "That isn't allowed. Reload the page and try again.";
  if (status === 404) return "Sapphire couldn't find that. Reload the page and try again.";
  if (status === 409) return 'Something changed it first. Reload the page and try again.';
  if (status === 413) return 'That file is too big.';
  if (status === 429) return 'Too many tries. Wait a minute, then try again.';
  if (status >= 502 && status <= 504) return "Can't reach Sapphire right now (it may be restarting). Wait a moment, then try again.";
  if (status >= 500) return 'Something went wrong inside Sapphire. Try again. If it keeps happening, restart Sapphire.';
  return `Sapphire turned that down (${status}). Reload the page and try again.`;
}

// THE words of a failed response's (already parsed) body: FastAPI's `detail`
// (a string, or a 422's list), a route's own `error`/`message`, else statusWords.
export function wordsOf(body, status) {
  let d = body?.detail ?? body?.error ?? body?.message;
  if (Array.isArray(d)) d = d.map(x => x?.msg || String(x)).join('; ');
  else if (d && typeof d === 'object') d = d.message || d.msg || '';
  // the server's bare reason phrase ("Not Found" from a route that isn't there) says nothing
  return d && !BARE_PHRASES.has(String(d)) ? String(d) : statusWords(status);
}
const BARE_PHRASES = new Set(['Not Found', 'Method Not Allowed', 'Internal Server Error', 'Bad Request',
                              'Forbidden', 'Unauthorized', 'Unprocessable Entity', 'Service Unavailable']);

// Same, reading the body (once) off a !res.ok response.
export async function errorText(res) {
  return wordsOf(await res.json().catch(() => ({})), res.status);
}

// A !res.ok response as an Error carrying its words and status.
export async function responseError(res) {
  const e = new Error(await errorText(res));
  e.status = res.status;
  return e;
}

export const fetchWithTimeout = async (url, opts = {}, timeout = 60000) => {
  const ctrl = new AbortController();
  const id = setTimeout(() => ctrl.abort(), timeout);
  // The caller's signal (a Stop) AND our clock: a caller's signal used to
  // replace ctrl.signal outright, so a passed signal meant no timeout at all.
  const outer = opts.signal;
  if (outer) {
    if (outer.aborted) ctrl.abort();
    else outer.addEventListener('abort', () => ctrl.abort(), { once: true });
  }

  // Add session ID and CSRF token headers to all requests
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const headers = { ...opts.headers, 'X-Session-ID': sessionId, 'X-CSRF-Token': csrfToken };

  try {
    const res = await fetch(url, { ...opts, headers, signal: ctrl.signal });
    clearTimeout(id);
    
    if (!res.ok) {
      if (res.status === 401) {
        window.location.href = '/login';
        return;
      }
      throw await responseError(res);
    }

    // 204: nothing to return, on purpose (speech the server chose not to
    // voice here). null, never a raw Response the caller would read as a
    // bad content-type. 2026-10-17.
    if (res.status === 204) return null;

    const ct = res.headers.get('content-type');
    if (ct?.includes('application/json')) return await res.json();
    if (ct?.includes('audio/')) {
      const blob = await res.blob();
      if (blob.size === 0) throw new Error('Empty audio');
      return blob;
    }
    // Neither JSON nor audio — return the raw Response. Callers expecting a
    // blob (TTS) must guard against this. A stripped/rewritten content-type
    // (Brave Shields, an extension, a proxy) lands here and is a known
    // silent-failure vector, so leave a breadcrumb. 2026-05-28.
    if (ct && !ct.includes('text/html')) {
      console.warn('[FETCH] non-json/non-audio content-type, returning raw Response:', url, 'ct=', ct, 'status=', res.status);
    }
    return res;
  } catch (e) {
    clearTimeout(id);
    if (e.name === 'AbortError') {
      if (opts.signal?.aborted) throw new Error('Cancelled');
      throw new Error('Sapphire took too long to answer. Try again.');
    }
    if (e instanceof TypeError && NETWORK.test(e.message))
      throw new Error(errWords(e));
    throw e;
  }
};