// spice-api.js - Backend communication for Spice Manager
import { fetchWithTimeout } from './fetch.js';
import { getInitData } from './init-data.js';

let _initialLoad = true;

// Use init data for first load, then fetch fresh from API
export const getSpices = async () => {
  if (_initialLoad) {
    _initialLoad = false;
    const init = await getInitData();
    return init.spices;
  }
  return fetchWithTimeout('/api/spices');
};

export const addSpice = (category, text) =>
  fetchWithTimeout('/api/spices', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ category, text })
  });

export const updateSpice = (category, index, text) =>
  fetchWithTimeout(`/api/spices/${encodeURIComponent(category)}/${index}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text })
  });

export const deleteSpice = (category, index) =>
  fetchWithTimeout(`/api/spices/${encodeURIComponent(category)}/${index}`, {
    method: 'DELETE'
  });

export const addCategory = (name) =>
  fetchWithTimeout('/api/spices/category', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name })
  });

export const renameCategory = (oldName, newName) =>
  fetchWithTimeout(`/api/spices/category/${encodeURIComponent(oldName)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ new_name: newName })
  });

export const deleteCategory = (name) =>
  fetchWithTimeout(`/api/spices/category/${encodeURIComponent(name)}`, {
    method: 'DELETE'
  });

export const reloadSpices = () =>
  fetchWithTimeout('/api/spices/reload', { method: 'POST' });

export const toggleCategory = (name) =>
  fetchWithTimeout(`/api/spices/category/${encodeURIComponent(name)}/toggle`, {
    method: 'POST'
  });

// Spice set API
let _setsInitialLoad = true;

export async function getSpiceSets() {
  if (_setsInitialLoad) {
    const init = await getInitData();
    return init.spice_sets?.list || [];
  }
  const data = await fetchWithTimeout('/api/spice-sets');
  return data.spice_sets || [];
}

export async function getCurrentSpiceSet() {
  if (_setsInitialLoad) {
    const init = await getInitData();
    _setsInitialLoad = false;
    return init.spice_sets?.current;
  }
  const data = await fetchWithTimeout('/api/spice-sets/current');
  return data.name;
}

export async function activateSpiceSet(name) {
  const res = await fetch(`/api/spice-sets/${encodeURIComponent(name)}/activate`, { method: 'POST' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function saveCustomSpiceSet(name, categories) {
  const res = await fetch('/api/spice-sets/custom', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, categories })
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function deleteSpiceSet(name) {
  const res = await fetch(`/api/spice-sets/${encodeURIComponent(name)}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function setSpiceSetEmoji(name, emoji) {
  const res = await fetch(`/api/spice-sets/${encodeURIComponent(name)}/emoji`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ emoji })
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}
