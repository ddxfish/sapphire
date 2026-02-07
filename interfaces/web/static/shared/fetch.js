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

  // Add session ID header to all requests
  const headers = { ...opts.headers, 'X-Session-ID': sessionId };

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
    
    const ct = res.headers.get('content-type');
    if (ct?.includes('application/json')) return await res.json();
    if (ct?.includes('audio/')) {
      const blob = await res.blob();
      if (blob.size === 0) throw new Error('Empty audio');
      return blob;
    }
    return res;
  } catch (e) {
    clearTimeout(id);
    if (e.name === 'AbortError') throw new Error(opts.signal?.aborted ? 'Cancelled' : 'Timeout');
    if (e.message.includes('fetch')) throw new Error('Network failed');
    throw e;
  }
};