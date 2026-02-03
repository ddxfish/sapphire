// API functions for Prompt Manager plugin
import { getInitData, getInitDataSync } from '../../shared/init-data.js';

// Use init data for initial load (avoids 3 separate API calls)
export async function getComponents() {
  const init = await getInitData();
  return init.prompts.components || {};
}

export async function listPrompts() {
  const init = await getInitData();
  return init.prompts.list || [];
}

// For current prompt, try init data first (avoids extra call at startup)
export async function getPrompt(name) {
  const cached = getInitDataSync();
  if (cached && cached.prompts.current_name === name && cached.prompts.current) {
    return cached.prompts.current;
  }
  // Fall back to direct API for other prompts
  const res = await fetch(`/api/prompts/${encodeURIComponent(name)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

// These still need direct API calls (mutations)
export async function savePrompt(name, data) {
  const res = await fetch(`/api/prompts/${encodeURIComponent(name)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function deletePrompt(name) {
  const res = await fetch(`/api/prompts/${encodeURIComponent(name)}`, {
    method: 'DELETE'
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function saveComponent(type, key, value) {
  const res = await fetch(`/api/prompts/components/${encodeURIComponent(type)}/${encodeURIComponent(key)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value })
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function deleteComponent(type, key) {
  const res = await fetch(`/api/prompts/components/${encodeURIComponent(type)}/${encodeURIComponent(key)}`, {
    method: 'DELETE'
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function loadPrompt(name) {
  const res = await fetch(`/api/prompts/${encodeURIComponent(name)}/load`, {
    method: 'POST'
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}
