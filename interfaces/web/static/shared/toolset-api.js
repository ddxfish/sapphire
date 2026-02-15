// API functions for Toolset Manager
import { getInitData } from './init-data.js';
import { fetchWithTimeout } from './fetch.js';

let _initialLoad = true;

// Use init data for first load, then fetch fresh from API
export async function getToolsets() {
  if (_initialLoad) {
    const init = await getInitData();
    return init.toolsets?.list || [];
  }
  const data = await fetchWithTimeout('/api/toolsets');
  return data.toolsets || [];
}

export async function getCurrentToolset() {
  if (_initialLoad) {
    const init = await getInitData();
    return init.toolsets?.current;
  }
  return fetchWithTimeout('/api/toolsets/current');
}

export async function getFunctions() {
  if (_initialLoad) {
    const init = await getInitData();
    _initialLoad = false;  // After first full load, always fetch fresh
    return init.functions;
  }
  return fetchWithTimeout('/api/functions');
}

export async function activateToolset(name) {
  const res = await fetch(`/api/toolsets/${encodeURIComponent(name)}/activate`, {
    method: 'POST'
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function enableFunctions(functionList) {
  const res = await fetch('/api/functions/enable', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ functions: functionList })
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function saveCustomToolset(name, functionList) {
  const res = await fetch('/api/toolsets/custom', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, functions: functionList })
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function deleteToolset(name) {
  const res = await fetch(`/api/toolsets/${encodeURIComponent(name)}`, {
    method: 'DELETE'
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function setToolsetEmoji(name, emoji) {
  const res = await fetch(`/api/toolsets/${encodeURIComponent(name)}/emoji`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ emoji })
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}
