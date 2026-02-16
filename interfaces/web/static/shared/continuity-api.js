// continuity-api.js - API wrapper for continuity endpoints

const API_BASE = '/api/continuity';

export async function fetchTasks() {
  const res = await fetch(`${API_BASE}/tasks`);
  if (!res.ok) throw new Error('Failed to fetch tasks');
  const data = await res.json();
  return data.tasks || [];
}

export async function createTask(taskData) {
  const res = await fetch(`${API_BASE}/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(taskData)
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Failed to create task');
  }
  return res.json();
}

export async function getTask(taskId) {
  const res = await fetch(`${API_BASE}/tasks/${taskId}`);
  if (!res.ok) throw new Error('Task not found');
  return res.json();
}

export async function updateTask(taskId, taskData) {
  const res = await fetch(`${API_BASE}/tasks/${taskId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(taskData)
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Failed to update task');
  }
  return res.json();
}

export async function deleteTask(taskId) {
  const res = await fetch(`${API_BASE}/tasks/${taskId}`, {
    method: 'DELETE'
  });
  if (!res.ok) throw new Error('Failed to delete task');
  return res.json();
}

export async function runTask(taskId) {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/run`, {
    method: 'POST'
  });
  if (!res.ok) throw new Error('Failed to run task');
  return res.json();
}

export async function fetchStatus() {
  const res = await fetch(`${API_BASE}/status`);
  if (!res.ok) throw new Error('Failed to fetch status');
  return res.json();
}

export async function fetchActivity(limit = 50) {
  const res = await fetch(`${API_BASE}/activity?limit=${limit}`);
  if (!res.ok) throw new Error('Failed to fetch activity');
  const data = await res.json();
  return data.activity || [];
}

export async function fetchTimeline(hours = 24) {
  const res = await fetch(`${API_BASE}/timeline?hours=${hours}`);
  if (!res.ok) throw new Error('Failed to fetch timeline');
  const data = await res.json();
  return data.timeline || [];
}

// Fetch prompts for dropdown (live)
export async function fetchPrompts() {
  const res = await fetch('/api/prompts');
  if (!res.ok) return [];
  const data = await res.json();
  return data.prompts || [];
}

// Fetch toolsets for dropdown (live)
export async function fetchToolsets() {
  const res = await fetch('/api/toolsets');
  if (!res.ok) return [];
  const data = await res.json();
  return data.toolsets || [];
}

// Fetch LLM providers with metadata
export async function fetchLLMProviders() {
  const res = await fetch('/api/llm/providers');
  if (!res.ok) return { providers: [], metadata: {} };
  const data = await res.json();
  return {
    providers: data.providers || [],
    metadata: data.metadata || {}
  };
}

// Fetch memory scopes
export async function fetchMemoryScopes() {
  const res = await fetch('/api/memory/scopes');
  if (!res.ok) return [];
  const data = await res.json();
  return data.scopes || [];
}

// Fetch knowledge scopes
export async function fetchKnowledgeScopes() {
  const res = await fetch('/api/knowledge/scopes');
  if (!res.ok) return [];
  const data = await res.json();
  return data.scopes || [];
}

// Fetch people scopes
export async function fetchPeopleScopes() {
  const res = await fetch('/api/knowledge/people/scopes');
  if (!res.ok) return [];
  const data = await res.json();
  return data.scopes || [];
}

// Fetch goal scopes
export async function fetchGoalScopes() {
  const res = await fetch('/api/goals/scopes');
  if (!res.ok) return [];
  const data = await res.json();
  return data.scopes || [];
}

// Fetch tasks filtered by heartbeat
export async function fetchHeartbeats() {
  const res = await fetch(`${API_BASE}/tasks?heartbeat=true`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.tasks || [];
}

export async function fetchNonHeartbeatTasks() {
  const res = await fetch(`${API_BASE}/tasks?heartbeat=false`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.tasks || [];
}

// Fetch personas (list with summary)
export async function fetchPersonas() {
  const res = await fetch('/api/personas');
  if (!res.ok) return [];
  const data = await res.json();
  return data.personas || [];
}

// Fetch single persona with full settings
export async function fetchPersona(name) {
  const res = await fetch(`/api/personas/${encodeURIComponent(name)}`);
  if (!res.ok) return null;
  return res.json();
}

// Fetch merged timeline
export async function fetchMergedTimeline(hoursBack = 12, hoursAhead = 12) {
  const res = await fetch(`${API_BASE}/merged-timeline?hours_back=${hoursBack}&hours_ahead=${hoursAhead}`);
  if (!res.ok) return { now: null, past: [], future: [] };
  return res.json();
}