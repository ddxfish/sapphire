// API functions for Ability Manager plugin

export async function getAbilities() {
  const res = await fetch('/api/abilities');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function getCurrentAbility() {
  const res = await fetch('/api/abilities/current');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function activateAbility(name) {
  const res = await fetch(`/api/abilities/${encodeURIComponent(name)}/activate`, {
    method: 'POST'
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function getFunctions() {
  const res = await fetch('/api/functions');
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