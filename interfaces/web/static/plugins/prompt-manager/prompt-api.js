// API functions for Prompt Manager plugin

export async function getComponents() {
  const res = await fetch('/api/prompts/components');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  return data.components || {};
}

export async function listPrompts() {
  const res = await fetch('/api/prompts');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  return data.prompts || [];
}

export async function getPrompt(name) {
  const res = await fetch(`/api/prompts/${encodeURIComponent(name)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

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