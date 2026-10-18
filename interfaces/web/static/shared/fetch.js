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

export const fetchWithTimeout = async (url, opts = {}, timeout = 60000) => {
  const ctrl = new AbortController();
  const id = setTimeout(() => ctrl.abort(), timeout);

  // Add session ID and CSRF token headers to all requests
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const headers = { ...opts.headers, 'X-Session-ID': sessionId, 'X-CSRF-Token': csrfToken };

  try {
    const res = await fetch(url, { ...opts, headers, signal: opts.signal || ctrl.signal });
    clearTimeout(id);
    
    if (!res.ok) {
      if (res.status === 401) {
        window.location.href = '/login';
        return;
      }
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || err.error || `HTTP ${res.status}`);
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
    if (e.name === 'AbortError') throw new Error(opts.signal?.aborted ? 'Cancelled' : 'Timeout');
    if (e.message.includes('fetch')) throw new Error('Network failed');
    throw e;
  }
};