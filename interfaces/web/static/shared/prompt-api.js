// API functions for Prompt Manager plugin
import { fetchWithTimeout } from './fetch.js';
import { refreshInitData } from './init-data.js';

// Fetch fresh components from server (init cache goes stale when AI tools modify components)
export async function getComponents() {
  const data = await fetchWithTimeout('/api/prompts/components');
  return data.components || {};
}

// Components + plugin-pack sources ({type: {key: pluginName}}) in one fetch
export async function getComponentsWithSources() {
  const data = await fetchWithTimeout('/api/prompts/components');
  return { components: data.components || {}, sources: data.sources || {} };
}

export async function listPrompts() {
  // Always fetch fresh list - cache may be stale after create/delete
  const data = await fetchWithTimeout('/api/prompts');
  const prompts = data.prompts || [];
  const current = data.current;
  if (current) prompts.forEach(p => p.active = (p.name === current));
  return prompts;
}

// Always fetch fresh prompt data (init cache goes stale when AI tools modify prompts)
export async function getPrompt(name) {
  const response = await fetchWithTimeout(`/api/prompts/${encodeURIComponent(name)}`);
  return response.data;
}

// Mutations - use fetchWithTimeout for session ID header
export async function savePrompt(name, data) {
  const res = await fetchWithTimeout(`/api/prompts/${encodeURIComponent(name)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  refreshInitData();  // bust cached prompt list so persona/chat dropdowns see the new/renamed prompt
  return res;
}

export async function deletePrompt(name) {
  const res = await fetchWithTimeout(`/api/prompts/${encodeURIComponent(name)}`, {
    method: 'DELETE'
  });
  refreshInitData();  // bust cached prompt list so persona/chat dropdowns drop the deleted prompt
  return res;
}

export async function saveComponent(type, key, value, reason) {
  return await fetchWithTimeout(`/api/prompts/components/${encodeURIComponent(type)}/${encodeURIComponent(key)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(reason ? { value, reason } : { value })
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
    throw new Error(data.error || `Failed to load prompt '${name}'`);
  }

  return data;
}
