// /static/shared/fetch.js - Shared fetch utility with timeout and auth handling

export const fetchWithTimeout = async (url, opts = {}, timeout = 60000) => {
  const ctrl = new AbortController();
  const id = setTimeout(() => ctrl.abort(), timeout);
  
  try {
    const res = await fetch(url, { ...opts, signal: opts.signal || ctrl.signal });
    clearTimeout(id);
    
    if (!res.ok) {
      if (res.status === 401) {
        window.location.href = '/login';
        return;
      }
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${res.status}`);
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