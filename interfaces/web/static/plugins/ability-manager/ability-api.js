// API functions for Ability Manager plugin
import { getInitData } from '../../shared/init-data.js';

// Use init data for initial load (avoids 3 separate API calls)
export async function getAbilities() {
  const init = await getInitData();
  return { abilities: init.abilities.list, count: init.abilities.list.length };
}

export async function getCurrentAbility() {
  const init = await getInitData();
  return init.abilities.current;
}

export async function getFunctions() {
  const init = await getInitData();
  return init.functions;
}

// These still need direct API calls (mutations)
export async function activateAbility(name) {
  const res = await fetch(`/api/abilities/${encodeURIComponent(name)}/activate`, {
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
  const res = await fetch('/api/abilities/custom', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, functions: functionList })
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function deleteAbility(name) {
  const res = await fetch(`/api/abilities/${encodeURIComponent(name)}`, {
    method: 'DELETE'
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}
