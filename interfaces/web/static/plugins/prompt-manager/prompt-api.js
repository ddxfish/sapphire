// API functions for Prompt Manager plugin
import { getInitData, getInitDataSync } from '../../shared/init-data.js';
import { fetchWithTimeout } from '../../shared/fetch.js';

// Use init data for initial load (avoids 3 separate API calls)
export async function getComponents() {
  const init = await getInitData();
  return init.prompts.components || {};
}

export async function listPrompts() {
  // Always fetch fresh list - cache may be stale after create/delete
  const data = await fetchWithTimeout('/api/prompts');
  return data.prompts || [];
}

// For current prompt, try init data first (avoids extra call at startup)
export async function getPrompt(name) {
  const cached = getInitDataSync();
  if (cached && cached.prompts.current_name === name && cached.prompts.current) {
    return cached.prompts.current;
  }
  // Fall back to direct API for other prompts
  return await fetchWithTimeout(`/api/prompts/${encodeURIComponent(name)}`);
}

// Mutations - use fetchWithTimeout for session ID header
export async function savePrompt(name, data) {
  return await fetchWithTimeout(`/api/prompts/${encodeURIComponent(name)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
}

export async function deletePrompt(name) {
  return await fetchWithTimeout(`/api/prompts/${encodeURIComponent(name)}`, {
    method: 'DELETE'
  });
}

export async function saveComponent(type, key, value) {
  return await fetchWithTimeout(`/api/prompts/components/${encodeURIComponent(type)}/${encodeURIComponent(key)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value })
  });
}

export async function deleteComponent(type, key) {
  return await fetchWithTimeout(`/api/prompts/components/${encodeURIComponent(type)}/${encodeURIComponent(key)}`, {
    method: 'DELETE'
  });
}

export async function loadPrompt(name) {
  const resp = await fetch(`/api/prompts/${encodeURIComponent(name)}/load`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' }
  });

  const data = await resp.json();

  if (!resp.ok) {
    // Handle privacy requirement case with specific error
    if (data.privacy_required) {
      const err = new Error(data.error || `Prompt '${name}' requires Privacy Mode`);
      err.privacyRequired = true;
      throw err;
    }
    throw new Error(data.error || `Failed to load prompt '${name}'`);
  }

  return data;
}
