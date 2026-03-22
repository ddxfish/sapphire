// continuity-api.js - API wrapper for continuity endpoints
import { fetchWithTimeout } from './fetch.js';

const API_BASE = '/api/continuity';

export async function fetchTasks() {
  const data = await fetchWithTimeout(`${API_BASE}/tasks`);
  return data.tasks || [];
}

export async function createTask(taskData) {
  return fetchWithTimeout(`${API_BASE}/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(taskData)
  });
}

export async function getTask(taskId) {
  return fetchWithTimeout(`${API_BASE}/tasks/${taskId}`);
}

export async function updateTask(taskId, taskData) {
  return fetchWithTimeout(`${API_BASE}/tasks/${taskId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(taskData)
  });
}

export async function deleteTask(taskId) {
  return fetchWithTimeout(`${API_BASE}/tasks/${taskId}`, { method: 'DELETE' });
}

export async function runTask(taskId) {
  return fetchWithTimeout(`${API_BASE}/tasks/${taskId}/run`, { method: 'POST' });
}

export async function fetchStatus() {
  return fetchWithTimeout(`${API_BASE}/status`);
}

export async function fetchActivity(limit = 50) {
  const data = await fetchWithTimeout(`${API_BASE}/activity?limit=${limit}`);
  return data.activity || [];
}

export async function fetchTimeline(hours = 24) {
  const data = await fetchWithTimeout(`${API_BASE}/timeline?hours=${hours}`);
  return data.timeline || [];
}

// Fetch prompts for dropdown (live)
export async function fetchPrompts() {
  try { const data = await fetchWithTimeout('/api/prompts'); return data.prompts || []; }
  catch { return []; }
}

// Fetch toolsets for dropdown (live)
export async function fetchToolsets() {
  try { const data = await fetchWithTimeout('/api/toolsets'); return data.toolsets || []; }
  catch { return []; }
}

// Fetch LLM providers with metadata
export async function fetchLLMProviders() {
  try {
    const data = await fetchWithTimeout('/api/llm/providers');
    return { providers: data.providers || [], metadata: data.metadata || {} };
  } catch { return { providers: [], metadata: {} }; }
}

// Fetch memory scopes
export async function fetchMemoryScopes() {
  try { const data = await fetchWithTimeout('/api/memory/scopes'); return data.scopes || []; }
  catch { return []; }
}

// Fetch knowledge scopes
export async function fetchKnowledgeScopes() {
  try { const data = await fetchWithTimeout('/api/knowledge/scopes'); return data.scopes || []; }
  catch { return []; }
}

// Fetch people scopes
export async function fetchPeopleScopes() {
  try { const data = await fetchWithTimeout('/api/knowledge/people/scopes'); return data.scopes || []; }
  catch { return []; }
}

// Fetch goal scopes
export async function fetchGoalScopes() {
  try { const data = await fetchWithTimeout('/api/goals/scopes'); return data.scopes || []; }
  catch { return []; }
}

// Fetch email accounts
export async function fetchEmailAccounts() {
  try { const data = await fetchWithTimeout('/api/email/accounts'); return data.accounts || []; }
  catch { return []; }
}

// Fetch tasks filtered by heartbeat
export async function fetchHeartbeats() {
  try { const data = await fetchWithTimeout(`${API_BASE}/tasks?heartbeat=true`); return data.tasks || []; }
  catch { return []; }
}

export async function fetchNonHeartbeatTasks() {
  try { const data = await fetchWithTimeout(`${API_BASE}/tasks?heartbeat=false`); return data.tasks || []; }
  catch { return []; }
}

// Fetch tasks by type
export async function fetchTasksByType(type) {
  try { const data = await fetchWithTimeout(`${API_BASE}/tasks?type=${type}`); return data.tasks || []; }
  catch { return []; }
}

// Fetch daemon event sources from loaded plugins
export async function fetchEventSources() {
  try { const data = await fetchWithTimeout('/api/events/sources'); return data.sources || []; }
  catch { return []; }
}

// Fetch personas (list with summary)
export async function fetchPersonas() {
  try { const data = await fetchWithTimeout('/api/personas'); return data.personas || []; }
  catch { return []; }
}

// Fetch single persona with full settings
export async function fetchPersona(name) {
  try { return await fetchWithTimeout(`/api/personas/${encodeURIComponent(name)}`); }
  catch { return null; }
}

// Fetch merged timeline
export async function fetchMergedTimeline(hoursBack = 12, hoursAhead = 12) {
  try { return await fetchWithTimeout(`${API_BASE}/merged-timeline?hours_back=${hoursBack}&hours_ahead=${hoursAhead}`); }
  catch { return { now: null, past: [], future: [] }; }
}
