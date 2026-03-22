// features/agent-status.js — Agent pill bar + workspace runner
import * as eventBus from '../core/event-bus.js';
import { fetchWithTimeout } from '../shared/fetch.js';

let bar = null;
let pollTimer = null;
let initialized = false;
let agents = new Map(); // id -> {name, status, mission, chat_name}
let pendingAgentReport = null;
let pendingAgentChat = null;
let workspaces = new Map(); // project -> {type, url, running}

const STATUS_COLORS = {
    running: '#f0ad4e',
    pending: '#f0ad4e',
    done: '#5cb85c',
    failed: '#d9534f',
    cancelled: '#888',
};

function getActiveChat() {
    const sel = document.getElementById('chat-select');
    return sel?.value || '';
}

function ensureBar() {
    if (bar) return bar;
    const form = document.getElementById('chat-form');
    if (!form) return null;

    bar = document.createElement('div');
    bar.id = 'agent-bar';
    form.parentNode.insertBefore(bar, form);
    return bar;
}

function renderPills() {
    if (!bar) return;
    const chat = getActiveChat();

    const visible = new Map();
    for (const [id, agent] of agents) {
        if (agent.chat_name === chat) visible.set(id, agent);
    }

    // Check if we have anything to show (agents or workspaces)
    if (visible.size === 0 && workspaces.size === 0) {
        bar.style.display = 'none';
        const anyRunning = [...agents.values()].some(a => a.status === 'running');
        if (!anyRunning) stopPolling();
        return;
    }
    bar.style.display = 'flex';
    if (visible.size > 0) startPolling();

    // --- Agent pills ---
    const existing = new Set();
    for (const [id, agent] of visible) {
        existing.add(id);
        let pill = bar.querySelector(`[data-agent-id="${id}"]`);
        if (!pill) {
            pill = document.createElement('span');
            pill.className = 'agent-pill';
            pill.dataset.agentId = id;
            pill.dataset.status = agent.status;
            pill.innerHTML = `<span class="agent-name">${esc(agent.name)}</span><span class="agent-x" title="Dismiss">\u00d7</span>`;
            pill.title = `${agent.name}: ${agent.mission || ''}`;

            pill.querySelector('.agent-x').addEventListener('click', async (e) => {
                e.stopPropagation();
                try {
                    await fetchWithTimeout(`/api/agents/${id}/dismiss`, { method: 'POST' });
                } catch (err) {
                    console.warn('[Agents] dismiss failed:', err);
                }
                agents.delete(id);
                pill.remove();
                renderPills();
            });

            bar.appendChild(pill);
        }

        pill.dataset.status = agent.status;
        pill.style.borderColor = STATUS_COLORS[agent.status] || '#888';
        pill.title = `${agent.name}: ${agent.mission || ''}\nStatus: ${agent.status}`;
    }

    for (const pill of bar.querySelectorAll('.agent-pill')) {
        if (!existing.has(pill.dataset.agentId)) {
            pill.remove();
        }
    }

    // --- Workspace run pills ---
    renderWorkspacePills();
}

function renderWorkspacePills() {
    if (!bar) return;

    const existingProjects = new Set();
    for (const [project, ws] of workspaces) {
        existingProjects.add(project);
        let pill = bar.querySelector(`[data-workspace="${project}"]`);

        if (!pill) {
            pill = document.createElement('span');
            pill.className = 'agent-pill workspace-pill';
            pill.dataset.workspace = project;
            pill.style.borderColor = '#5cb85c';
            bar.appendChild(pill);
        }

        if (ws.type === 'html') {
            pill.innerHTML = `<span class="agent-name">\u2197 ${esc(project)}</span><span class="agent-x" title="Dismiss">\u00d7</span>`;
            pill.title = `Open ${project} in new tab`;
            pill.onclick = (e) => {
                if (e.target.classList.contains('agent-x')) return;
                window.open(ws.url, '_blank');
            };
        } else if (ws.running) {
            pill.innerHTML = `<span class="agent-name">\u25a0 ${esc(project)}</span><span class="agent-x" title="Dismiss">\u00d7</span>`;
            pill.title = `${project} is running — click to stop`;
            pill.style.borderColor = '#f0ad4e';
            pill.style.animation = 'agent-pulse 2s ease-in-out infinite';
            pill.onclick = async (e) => {
                if (e.target.classList.contains('agent-x')) return;
                try {
                    await fetchWithTimeout('/api/workspace/stop', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ project }),
                    });
                    ws.running = false;
                    renderWorkspacePills();
                } catch (err) {
                    console.warn('[Workspace] stop failed:', err);
                }
            };
        } else {
            pill.innerHTML = `<span class="agent-name">\u25b6 ${esc(project)}</span><span class="agent-x" title="Dismiss">\u00d7</span>`;
            pill.title = `Run ${project}`;
            pill.style.borderColor = '#5cb85c';
            pill.style.animation = '';
            pill.onclick = async (e) => {
                if (e.target.classList.contains('agent-x')) return;
                try {
                    const res = await fetchWithTimeout('/api/workspace/run', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ project }),
                    });
                    if (res.status === 'started' || res.status === 'already_running') {
                        ws.running = true;
                        renderWorkspacePills();
                    }
                } catch (err) {
                    console.warn('[Workspace] run failed:', err);
                }
            };
        }

        // Dismiss X — same handler for all types
        const xBtn = pill.querySelector('.agent-x');
        xBtn.onclick = (e) => {
            e.stopPropagation();
            // Stop if running before dismissing
            if (ws.running) {
                fetchWithTimeout('/api/workspace/stop', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ project }),
                }).catch(() => {});
            }
            workspaces.delete(project);
            pill.remove();
            renderPills();
        };
    }

    // Remove stale workspace pills
    for (const pill of bar.querySelectorAll('.workspace-pill')) {
        if (!existingProjects.has(pill.dataset.workspace)) {
            pill.remove();
        }
    }
}

async function poll() {
    try {
        const chat = getActiveChat();
        const data = await fetchWithTimeout(`/api/agents/status?chat=${encodeURIComponent(chat)}`, {}, 5000);
        if (!data?.agents) return;

        const remoteIds = new Set();
        for (const a of data.agents) {
            remoteIds.add(a.id);
            agents.set(a.id, a);
        }
        for (const [id, agent] of agents) {
            if (agent.chat_name === chat && !remoteIds.has(id)) agents.delete(id);
        }
        renderPills();
    } catch (err) {
        // Silent
    }
}

function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(poll, 3000);
}

function stopPolling() {
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
}

async function drainAgentReport() {
    if (!pendingAgentReport) return;
    try {
        const activeChat = getActiveChat();
        if (pendingAgentChat && pendingAgentChat !== activeChat) {
            console.log('[Agents] Wrong chat — report is for', pendingAgentChat, 'but active is', activeChat, '— holding');
            return;
        }
        const { getIsProc } = await import('../core/state.js');
        if (getIsProc()) {
            console.log('[Agents] Still processing, will retry on ai_typing_end');
            return;
        }
        const report = pendingAgentReport;
        pendingAgentReport = null;
        pendingAgentChat = null;
        console.log('[Agents] Sending auto-return report to chat');

        // Preserve user's in-progress typing
        const { getElements } = await import('../core/state.js');
        const { input } = getElements();
        const savedText = input?.value || '';

        const { triggerSendWithText } = await import('../handlers/send-handlers.js');
        await triggerSendWithText(report);

        // Restore what the user was typing
        if (savedText && input) {
            input.value = savedText;
            input.dispatchEvent(new Event('input'));
        }
    } catch (err) {
        console.error('[Agents] Auto-return failed:', err);
        pendingAgentReport = null;
        pendingAgentChat = null;
    }
}

function esc(s) {
    return s.replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export function initAgentStatus() {
    if (initialized) return;
    initialized = true;

    ensureBar();

    eventBus.on('agent_spawned', (data) => {
        agents.set(data.id, {
            id: data.id,
            name: data.name,
            status: 'running',
            mission: data.mission || '',
            chat_name: data.chat_name || '',
        });
        ensureBar();
        renderPills();
    });

    eventBus.on('agent_completed', (data) => {
        const agent = agents.get(data.id);
        if (agent) {
            agent.status = data.status || 'done';
            renderPills();
        }
    });

    eventBus.on('agent_dismissed', (data) => {
        agents.delete(data.id);
        renderPills();
    });

    eventBus.on('agent_batch_complete', (data) => {
        console.log('[Agents] Batch complete event received:', data.chat_name, 'agents:', data.agent_count);
        pendingAgentReport = data.report;
        pendingAgentChat = data.chat_name;
        setTimeout(() => drainAgentReport(), 1500);
    });

    // Workspace ready — show run/open button
    eventBus.on('workspace_ready', (data) => {
        console.log('[Agents] Workspace ready:', data.project, data.type);
        ensureBar();
        workspaces.set(data.project, {
            type: data.type,
            url: data.url,
            running: false,
        });
        renderPills();
    });

    eventBus.on('ai_typing_end', () => {
        if (!pendingAgentReport) return;
        console.log('[Agents] ai_typing_end — draining queued report');
        setTimeout(() => drainAgentReport(), 800);
    });

    eventBus.on(eventBus.Events.CHAT_SWITCHED, () => {
        renderPills();
        if (!pendingAgentReport) return;
        console.log('[Agents] Chat switched — checking if report can drain');
        setTimeout(() => drainAgentReport(), 500);
    });

    poll();
}
