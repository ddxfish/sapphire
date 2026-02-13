// API functions for Ability Manager plugin
import { getInitData } from './init-data.js';
import { fetchWithTimeout } from './fetch.js';

let _initialLoad = true;

// Use init data for first load, then fetch fresh from API
export async function getAbilities() {
  if (_initialLoad) {
    const init = await getInitData();
    return { abilities: init.toolsets.list, count: init.toolsets.list.length };
  }
  return fetchWithTimeout('/api/toolsets');
}

export async function getCurrentAbility() {
  if (_initialLoad) {
    const init = await getInitData();
    return init.toolsets.current;
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

// These still need direct API calls (mutations)
export async function activateAbility(name) {
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

export async function saveCustomAbility(name, functionList) {
  const res = await fetch('/api/toolsets/custom', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, functions: functionList })
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function deleteAbility(name) {
  const res = await fetch(`/api/toolsets/${encodeURIComponent(name)}`, {
    method: 'DELETE'
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}
