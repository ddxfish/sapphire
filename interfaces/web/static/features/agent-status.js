// features/agent-status.js — Agent pill bar + workspace runner
//
// Agents v2 (2026-10-06): reports and questions reach Sapphire through the
// server-side inbox (core/chat/inbox.py) — the browser no longer drains them
// into the chat box. Pills show status; a waiting pill pulses.
import * as eventBus from '../core/event-bus.js';
import { fetchWithTimeout } from '../shared/fetch.js';
import { normalizeAsk, buildAskCards } from '../shared/ask-marker.js';

let bar = null;
let pollTimer = null;
let initialized = false;
let agents = new Map(); // id -> {name, status, mission, chat_name}
let workspaces = new Map(); // project -> {type, url, running}
let inboxDepth = new Map(); // chat_name -> items waiting (inbox_changed)
let openCard = null;       // the one popover open: a waiting agent's question, or the inbox list

const LIVE = new Set(['pending', 'running', 'waiting', 'idle']);
const PILL_LINGER_MS = 8000;   // a finished pill stays this long, then goes

const STATUS_COLORS = {
    running: '#f0ad4e',
    pending: '#f0ad4e',
    waiting: '#b07cff',    // asked the director something — pulses
    idle: '#5bc0de',       // a conversational agent between turns
    resting: '#7a8fa6',
    done: '#5cb85c',
    degraded: '#e6c229',   // amber — technically completed but output is a placeholder
    failed: '#d9534f',
    stopped: '#888',
    lost: '#a66',
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

    const queued = inboxDepth.get(chat) || 0;
    // Check if we have anything to show (agents, workspaces, or a queue)
    if (visible.size === 0 && workspaces.size === 0 && !queued) {
        bar.style.display = 'none';
        const anyLive = [...agents.values()].some(a => LIVE.has(a.status));
        if (!anyLive) stopPolling();
        return;
    }
    renderInboxChip(queued, chat);
    bar.style.display = 'flex';
    if ([...agents.values()].some(a => LIVE.has(a.status))) startPolling();

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
            // a waiting pill opens its question: lettered buttons, or your own words
            pill.addEventListener('click', (e) => {
                if (e.target.classList.contains('agent-x')) return;
                const a = agents.get(id);
                if (a && a.status === 'waiting') openQuestionCard(pill, a);
            });

            bar.appendChild(pill);
        }

        // If the agent carries a warning (tool-loop exhaustion, context overflow,
        // empty LLM), render it as 'degraded' — amber, not green. Prevents the
        // user from trusting a no-op run as success. Scout #15 — 2026-04-20.
        const effectiveStatus = (agent.status === 'done' && agent.warning)
            ? 'degraded' : agent.status;
        pill.dataset.status = effectiveStatus;
        pill.style.borderColor = STATUS_COLORS[effectiveStatus] || '#888';
        pill.style.animation = effectiveStatus === 'waiting' ? 'agent-pulse 1.6s ease-in-out infinite' : '';
        const warnTip = agent.warning ? `\nWarning: ${agent.warning}` : '';
        const askTip = effectiveStatus === 'waiting' ? '\nAsking Sapphire something — see the chat' : '';
        const progTip = agent.last_event ? `\n${agent.tool_count || 0} tool calls · last: ${agent.last_event}` : '';
        pill.title = `${agent.name} (${agent.kind || agent.agent_type || ''}): ${agent.mission || ''}\nStatus: ${effectiveStatus}${progTip}${askTip}${warnTip}`;
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

function closeCard() {
    if (openCard) { openCard.remove(); openCard = null; }
}

function csrfHeaders() {
    return { 'Content-Type': 'application/json', 'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]')?.content || '' };
}

function cardAt(anchor) {
    closeCard();
    const card = document.createElement('div');
    card.className = 'agent-card-pop';
    const r = anchor.getBoundingClientRect();
    card.style.left = `${Math.max(8, r.left)}px`;
    card.style.bottom = `${window.innerHeight - r.top + 6}px`;
    document.body.appendChild(card);
    openCard = card;
    setTimeout(() => document.addEventListener('click', function onDoc(ev) {
        if (!card.contains(ev.target)) { closeCard(); document.removeEventListener('click', onDoc); }
    }), 0);
    return card;
}

async function openQuestionCard(pill, agent) {
    const chat = agent.chat_name || getActiveChat();
    let q = null;
    try {
        const t = await fetchWithTimeout(`/api/agents/${encodeURIComponent(agent.id)}/transcript?chat=${encodeURIComponent(chat)}&last=1`, {}, 5000);
        q = t?.pending_question;
    } catch (err) { console.warn('[Agents] question fetch failed:', err); }
    const card = cardAt(pill);
    if (!q) { card.innerHTML = `<div class="acp-title">${esc(agent.name)} is not asking anything right now.</div>`; return; }
    const answer = async (value) => {
        if (!value?.trim()) return;
        try {
            await fetchWithTimeout(`/api/agents/${encodeURIComponent(agent.id)}/answer`, {
                method: 'POST', headers: csrfHeaders(), body: JSON.stringify({ chat, value }) }, 8000);
            agent.status = 'running';
            renderPills();
        } catch (err) { console.warn('[Agents] answer failed:', err); }
        closeCard();
    };
    card.innerHTML = `<div class="acp-title">${esc(agent.name)} asks</div>`;
    // AskUserQuestion's shape IS the question card's: the ONE renderer (shared/
    // ask-marker.js) draws it - tabs for several questions, one answer each
    // (the old lettered card sent one answer to every question), the answer
    // text is the card's own `Question → Answer` lines, which the engine maps
    // per question (core/agents/base.py _parse_per_question). 2026-10-07.
    const qs = q.questions
        ? normalizeAsk({ questions: q.questions.map(x => ({ ...x, multi_select: x.multiSelect ?? x.multi_select })) })
        : [];
    if (qs.length) {
        buildAskCards([qs], answer).forEach(el => card.appendChild(el));
        return;
    }
    // a free-text question (no options): one box
    card.innerHTML += `<div class="acp-q">${esc(q.text || '')}</div>`
      + `<div class="acp-row"><input placeholder="Your answer"><button data-ans-text>Answer</button></div>`;
    card.querySelector('[data-ans-text]').addEventListener('click', () => answer(card.querySelector('input').value));
    card.querySelector('input').addEventListener('keydown', (e) => { if (e.key === 'Enter') answer(e.target.value); });
}

function renderInboxChip(queued, chat) {
    let chip = bar.querySelector('.inbox-chip');
    if (!queued) { chip?.remove(); return; }
    if (!chip) {
        chip = document.createElement('span');
        chip.className = 'agent-pill inbox-chip';
        chip.style.borderColor = '#9aa7b8';
        chip.title = 'Waiting in this chat\'s inbox — click to see';
        bar.insertBefore(chip, bar.firstChild);
        // the chat is read at CLICK time: the chip outlives a chat switch, and a
        // listener bound to the chat it was made for opened the wrong queue
        chip.addEventListener('click', () => openInboxCard(chip, chip.dataset.chat || chat));
    }
    chip.dataset.chat = chat || '';
    chip.innerHTML = `<span class="agent-name">\u29d6 ${queued} queued</span>`;
}

async function openInboxCard(chip, chat) {
    let items = [];
    try { items = (await fetchWithTimeout(`/api/chat/queue?chat=${encodeURIComponent(chat)}`, {}, 5000))?.items || []; }
    catch (err) { console.warn('[Inbox] peek failed:', err); }
    const card = cardAt(chip);
    card.innerHTML = `<div class="acp-title">Waiting for a turn</div>` + (items.length ? items.map(i =>
        `<div class="acp-item"><span>${esc(i.source)} <em>${esc(i.lane)}</em> · ${Math.round(i.age)}s</span><button data-drop="${esc(i.ticket)}" title="Take it out">\u00d7</button></div>`).join('')
        : '<div class="acp-q">nothing</div>');
    card.querySelectorAll('[data-drop]').forEach(b => b.addEventListener('click', async () => {
        try {
            await fetchWithTimeout('/api/chat/queue/drop', { method: 'POST', headers: csrfHeaders(),
                body: JSON.stringify({ ticket: b.dataset.drop, chat }) }, 5000);
        } catch (err) { console.warn('[Inbox] drop failed:', err); }
        openInboxCard(chip, chat);
    }));
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

function esc(s) {
    return s.replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export function initAgentStatus() {
    if (initialized) return;
    initialized = true;

    ensureBar();

    // Same-client chat switch fires a DOM 'chat-activated' event on #chat-select.
    // The event bus's CHAT_SWITCHED SSE event drops self-originated messages
    // (intentional — prevents echo) so we'd miss our own chat switch without
    // this listener. Re-poll + re-render so Chat B's agent pills show up
    // immediately instead of waiting up to 3s for the next poll tick.
    const chatSelect = document.getElementById('chat-select');
    if (chatSelect) {
        chatSelect.addEventListener('chat-activated', () => {
            poll();
            renderPills();
        });
    }

    eventBus.on('agent_spawned', (data) => {
        // ids and names only ride the event (a mission may be private); the
        // next poll fills the rest in from /api/agents/status for this chat
        agents.set(data.id, {
            id: data.id,
            name: data.name,
            kind: data.agent_type || '',
            status: 'running',
            mission: '',
            chat_name: data.chat_name || '',
        });
        ensureBar();
        renderPills();
        poll();
    });

    eventBus.on('inbox_changed', (data) => {
        if (!data?.chat) return;
        inboxDepth.set(data.chat, data.depth || 0);
        ensureBar();
        renderPills();
    });

    eventBus.on('agent_waiting', (data) => {
        const agent = agents.get(data.id);
        if (agent) { agent.status = 'waiting'; renderPills(); }
    });

    eventBus.on('agent_event', (data) => {
        const agent = agents.get(data.id);
        if (agent && typeof data.tool_count === 'number') {
            agent.tool_count = data.tool_count;
            if (agent.status === 'waiting' && data.kind === 'answer') agent.status = 'running';
            renderPills();
        }
    });

    eventBus.on('agent_completed', (data) => {
        const agent = agents.get(data.id);
        if (agent) {
            agent.status = data.status || 'done';
            // Warning field = degradation reason (tool-loop exhaustion, overflow,
            // empty LLM). Stored so the pill can render amber instead of green.
            agent.warning = data.warning || null;
            renderPills();
            if (!LIVE.has(agent.status)) {
                // the report itself arrives in the chat through the inbox;
                // the pill lingers so the colour is seen, then goes
                setTimeout(() => {
                    if (agents.get(data.id) === agent && !LIVE.has(agent.status)) {
                        agents.delete(data.id);
                        renderPills();
                    }
                }, PILL_LINGER_MS);
            }
        }
    });

    eventBus.on('agent_dismissed', (data) => {
        agents.delete(data.id);
        renderPills();
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

    eventBus.on(eventBus.Events.CHAT_SWITCHED, () => {
        renderPills();
    });

    // Server restart — wipe stale pills, re-poll for actual state
    eventBus.on('server_restarted', () => {
        agents.clear();
        workspaces.clear();
        renderPills();
        poll();
    });

    poll();
}
