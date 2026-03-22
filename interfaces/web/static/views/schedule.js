// views/schedule.js - Triggers view (Time tab + Events tab)
import { fetchNonHeartbeatTasks, fetchHeartbeats, fetchStatus, fetchMergedTimeline,
         fetchTasksByType, createTask, updateTask, deleteTask, runTask,
         fetchMemoryScopes, fetchKnowledgeScopes, fetchPeopleScopes, fetchGoalScopes } from '../shared/continuity-api.js';
import { openTriggerEditor } from '../shared/trigger-editor/editor.js';
import { describeCron } from '../shared/trigger-editor/trigger-cron.js';
import { showExportDialog, showImportDialog } from '../shared/import-export.js';
import * as ui from '../ui.js';

let container = null;
let tasks = [];         // non-heartbeat only
let heartbeats = [];    // heartbeats only
let daemons = [];       // daemon type
let webhooks = [];      // webhook type
let status = {};
let mergedTimeline = { now: null, past: [], future: [] };
let pollTimer = null;
let _docClickBound = false;
let activeTab = 'time'; // 'time' | 'events'

export default {
    init(el) { container = el; },
    async show() {
        await loadData();
        render();
        startPolling();
    },
    hide() { stopPolling(); }
};

function startPolling() {
    stopPolling();
    pollTimer = setInterval(async () => {
        await loadData();
        updateContent();
    }, 5000);
}

function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

async function loadData() {
    try {
        const [t, hb, s, mt, d, w] = await Promise.all([
            fetchNonHeartbeatTasks(), fetchHeartbeats(), fetchStatus(), fetchMergedTimeline(12, 12),
            fetchTasksByType('daemon'), fetchTasksByType('webhook'),
        ]);
        tasks = t; heartbeats = hb; status = s; mergedTimeline = mt;
        daemons = d; webhooks = w;
    } catch (e) { console.warn('Schedule load failed:', e); }
}

// ── Main Layout ──

function render() {
    if (!container) return;
    container.innerHTML = `
        <div class="sched-view">
            <div class="view-header sched-header-centered">
                <h2>Triggers</h2>
                <span class="view-subtitle" id="sched-subtitle"></span>
            </div>
            <div class="sched-tab-bar">
                <button class="sched-tab${activeTab === 'time' ? ' active' : ''}" data-tab="time">\u23F0 Time</button>
                <button class="sched-tab${activeTab === 'events' ? ' active' : ''}" data-tab="events">\u26A1 Events</button>
            </div>
            <div class="view-body view-scroll">
                <div id="sched-tab-content"></div>
            </div>
        </div>
    `;
    renderTabContent();
    bindEvents();
}

function renderTabContent() {
    const contentEl = container?.querySelector('#sched-tab-content');
    if (!contentEl) return;

    if (activeTab === 'time') {
        contentEl.innerHTML = `
            <div id="sched-hstrip-wrap"></div>
            <div class="sched-layout">
                <div id="sched-tasks">
                    <div class="sched-col-header">
                        <h3>Tasks</h3>
                        <button class="btn-sm" id="sched-import-task" title="Import task">\u2B07</button>
                        <button class="btn-sm btn-primary" id="sched-new-task">+ Task</button>
                    </div>
                </div>
                <div class="sched-mission" id="sched-mission">
                    <div class="sched-col-header">
                        <h3>Mission Control</h3>
                        <button class="btn-sm" id="sched-import-heartbeat" title="Import heartbeat">\u2B07</button>
                        <button class="btn-sm btn-primary" id="sched-new-heartbeat">+ Heartbeat</button>
                    </div>
                </div>
            </div>
        `;
    } else {
        contentEl.innerHTML = `
            <div class="sched-layout">
                <div id="sched-daemons">
                    <div class="sched-col-header">
                        <h3>Daemons</h3>
                        <button class="btn-sm" id="sched-import-daemon" title="Import daemon">\u2B07</button>
                        <button class="btn-sm btn-primary" id="sched-new-daemon">+ Daemon</button>
                    </div>
                </div>
                <div class="sched-mission" id="sched-webhooks">
                    <div class="sched-col-header">
                        <h3>Webhooks</h3>
                        <button class="btn-sm" id="sched-import-webhook" title="Import webhook">\u2B07</button>
                        <button class="btn-sm btn-primary" id="sched-new-webhook">+ Webhook</button>
                    </div>
                </div>
            </div>
        `;
        updateEventsContent();
    }
    updateContent();
    bindTabContent();
}

function updateContent() {
    if (activeTab === 'events') { updateEventsContent(); return; }
    if (activeTab !== 'time') return;

    const tasksEl = container?.querySelector('#sched-tasks');
    const missionEl = container?.querySelector('#sched-mission');
    const subEl = container?.querySelector('#sched-subtitle');
    const hstripEl = container?.querySelector('#sched-hstrip-wrap');

    // Preserve open accordion state + scroll position
    const openCards = new Set();
    if (missionEl) {
        for (const d of missionEl.querySelectorAll('details.hb-response-wrap[open]')) {
            const card = d.closest('.hb-card');
            if (card) openCards.add(card.id);
        }
    }
    const scrollEl = container?.querySelector('.view-scroll');
    const scrollTop = scrollEl?.scrollTop || 0;

    // Keep the col-header, replace content after it
    const taskHeader = tasksEl?.querySelector('.sched-col-header');
    const missionHeader = missionEl?.querySelector('.sched-col-header');
    if (tasksEl && taskHeader) {
        // Remove all children after header
        while (taskHeader.nextSibling) taskHeader.nextSibling.remove();
        tasksEl.insertAdjacentHTML('beforeend', renderTaskList());
    }
    if (missionEl && missionHeader) {
        while (missionHeader.nextSibling) missionHeader.nextSibling.remove();
        missionEl.insertAdjacentHTML('beforeend', renderMission());
    }
    if (hstripEl) hstripEl.innerHTML = renderHorizontalTimeline();

    // Restore open accordions + scroll position
    for (const id of openCards) {
        const details = missionEl?.querySelector(`#${id} details.hb-response-wrap`);
        if (details) details.open = true;
    }
    if (scrollEl) scrollEl.scrollTop = scrollTop;
    const total = tasks.length + heartbeats.length;
    const enabled = [...tasks, ...heartbeats].filter(t => t.enabled).length;
    if (subEl) subEl.innerHTML = `${enabled}/${total} active
        <span class="sched-status-dot ${status.running ? 'running' : 'stopped'} ${status.running ? 'pulse' : ''}"></span>
        ${status.running ? 'Running' : 'Stopped'}`;
}

// ── Left Column: Task List (no heartbeats) ──

function renderTaskList() {
    if (tasks.length === 0) {
        return `<div class="view-placeholder" style="padding:40px;text-align:center">
            <p style="color:var(--text-muted)">No tasks yet. Create one to get started.</p>
        </div>`;
    }
    const sorted = [...tasks].sort((a, b) => (a.name || '').localeCompare(b.name || ''));
    return sorted.map(t => {
        const sched = describeCron(t.schedule);
        const lastRun = t.last_run ? formatTime(t.last_run) : 'Never';
        const isPlugin = (t.source || '').startsWith('plugin:');
        const pluginName = isPlugin ? t.source.replace('plugin:', '') : '';
        let statusText = '';
        if (t.running) {
            statusText = `<span class="sched-progress">Running...</span>`;
        }
        const meta = [
            isPlugin ? `<span class="sched-plugin-badge" title="Managed by ${esc(pluginName)} plugin">plugin</span>` : '',
            t.chance < 100 ? `${t.chance}%` : '',
            t.active_hours_start != null ? `\uD83D\uDD53 ${formatHourRange(t.active_hours_start, t.active_hours_end)}` : '',
            statusText,
            t.chat_target ? `\uD83D\uDCAC ${esc(t.chat_target)}` : '',
            `Last: ${lastRun}`
        ].filter(Boolean).join(' \u00B7 ');

        const actions = isPlugin
            ? `<button class="btn-icon" data-action="run" data-id="${t.id}" title="Run now">\u25B6</button>`
            : `<button class="btn-icon" data-action="run" data-id="${t.id}" title="Run now">\u25B6</button>
               <button class="btn-icon" data-action="export" data-id="${t.id}" title="Export">\u21E9</button>
               <button class="btn-icon" data-action="edit" data-id="${t.id}" title="Edit">\u270F\uFE0F</button>
               <button class="btn-icon danger" data-action="delete" data-id="${t.id}" title="Delete">\u2715</button>`;

        const toggle = isPlugin ? '' : `
                <label class="sched-toggle" title="${t.enabled ? 'Disable' : 'Enable'}">
                    <input type="checkbox" ${t.enabled ? 'checked' : ''} data-action="toggle" data-id="${t.id}">
                    <span class="toggle-slider"></span>
                </label>`;

        return `
            <div class="sched-task-card${t.running ? ' running' : ''}${isPlugin ? ' plugin-task' : ''}">
                ${toggle}
                <div class="sched-task-info">
                    <div class="sched-task-name">${esc(t.name)}</div>
                    <div class="sched-task-schedule">${esc(sched)}</div>
                    <div class="sched-task-meta">${meta}</div>
                </div>
                <div class="sched-task-actions">
                    ${actions}
                </div>
            </div>`;
    }).join('');
}

// ── Right Column: Heartbeat Cards ──

function renderMission() {
    if (heartbeats.length === 0) {
        return '<div class="text-muted" style="padding:20px;text-align:center;font-size:var(--font-sm)">Create a heartbeat to monitor vitals here</div>';
    }
    return `<div class="sched-vitals-grid">
        ${heartbeats.map(hb => renderHeartbeatCard(hb)).join('')}
    </div>`;
}

function renderHeartbeatCard(hb) {
    const state = getHeartbeatState(hb);
    const emoji = hb.emoji || '\u2764\uFE0F';
    const lastResp = hb.last_response || '';
    const TRUNC = 120;
    const needsExpand = lastResp.length > TRUNC;
    const truncResp = needsExpand ? lastResp.slice(0, TRUNC) + '\u2026' : lastResp;
    const beats = getBeatsForTask(hb.id, 20);

    const lastAgo = hb.last_run ? timeAgo(hb.last_run) : null;
    const nextIn = getNextIn(hb.id);
    const timeParts = [
        state.label,
        hb.active_hours_start != null ? formatHourRange(hb.active_hours_start, hb.active_hours_end) : null,
        lastAgo ? `ran ${lastAgo}` : null,
        nextIn ? `next in ${nextIn}` : null
    ].filter(Boolean).join(' \u00B7 ');

    let responseHtml = '';
    if (lastResp) {
        if (needsExpand) {
            responseHtml = `<details class="hb-response-wrap">
                <summary class="hb-response-summary">${esc(truncResp)}</summary>
                <div class="hb-response-full">${esc(lastResp)}</div>
            </details>`;
        } else {
            responseHtml = `<div class="hb-response-summary">${esc(lastResp)}</div>`;
        }
    }

    return `
        <div class="hb-card ${state.cls}" id="vital-${hb.id}">
            <div class="hb-card-header">
                <span class="hb-emoji">${emoji}</span>
                <span class="hb-name" data-action="edit" data-id="${hb.id}">${esc(hb.name)}</span>
                <label class="sched-toggle hb-toggle" title="${hb.enabled ? 'Pause' : 'Resume'}">
                    <input type="checkbox" ${hb.enabled ? 'checked' : ''} data-action="hb-toggle" data-id="${hb.id}">
                    <span class="toggle-slider"></span>
                </label>
            </div>
            ${renderHeatmap(beats)}
            <div class="hb-time">${timeParts}</div>
            ${responseHtml}
            <div class="hb-actions">
                <button class="btn-icon" data-action="run" data-id="${hb.id}" title="Run now">\u25B6</button>
                <button class="btn-icon" data-action="export" data-id="${hb.id}" title="Export">\u21E9</button>
                <button class="btn-icon" data-action="edit" data-id="${hb.id}" title="Edit">\u270F\uFE0F</button>
                <button class="btn-icon danger" data-action="delete" data-id="${hb.id}" title="Delete">\u2715</button>
            </div>
        </div>`;
}

// ── Events Tab Content ──

function updateEventsContent() {
    const daemonsEl = container?.querySelector('#sched-daemons');
    const webhooksEl = container?.querySelector('#sched-webhooks');
    if (!daemonsEl || !webhooksEl) return;

    const dHeader = daemonsEl.querySelector('.sched-col-header');
    const wHeader = webhooksEl.querySelector('.sched-col-header');
    if (dHeader) {
        while (dHeader.nextSibling) dHeader.nextSibling.remove();
        daemonsEl.insertAdjacentHTML('beforeend', renderDaemonList());
    }
    if (wHeader) {
        while (wHeader.nextSibling) wHeader.nextSibling.remove();
        webhooksEl.insertAdjacentHTML('beforeend', renderWebhookList());
    }
}

function renderDaemonList() {
    if (daemons.length === 0) {
        return `<div class="text-muted" style="padding:20px;text-align:center;font-size:var(--font-sm)">
            No daemons configured yet. Install a daemon plugin (Discord, Telegram, etc.) to get started.
        </div>`;
    }
    return daemons.map(d => {
        const tc = d.trigger_config || {};
        const source = tc.source || 'unknown';
        const hasFilter = tc.filter && Object.keys(tc.filter).length > 0;
        const lastRun = d.last_run ? formatTime(d.last_run) : 'Never';
        const emoji = d.emoji || '\uD83D\uDCE1';
        const meta = [
            source,
            hasFilter ? 'filtered' : '',
            d.chat_target ? `\uD83D\uDCAC ${esc(d.chat_target)}` : '',
            `Last: ${lastRun}`
        ].filter(Boolean).join(' \u00B7 ');

        return `
            <div class="sched-task-card${d.running ? ' running' : ''}">
                <label class="sched-toggle" title="${d.enabled ? 'Disable' : 'Enable'}">
                    <input type="checkbox" ${d.enabled ? 'checked' : ''} data-action="toggle" data-id="${d.id}">
                    <span class="toggle-slider"></span>
                </label>
                <div class="sched-task-info">
                    <div class="sched-task-name">${emoji} ${esc(d.name)}</div>
                    <div class="sched-task-meta">${meta}</div>
                </div>
                <div class="sched-task-actions">
                    <button class="btn-icon" data-action="export" data-id="${d.id}" title="Export">\u21E9</button>
                    <button class="btn-icon" data-action="edit" data-id="${d.id}" title="Edit">\u270F\uFE0F</button>
                    <button class="btn-icon danger" data-action="delete" data-id="${d.id}" title="Delete">\u2715</button>
                </div>
            </div>`;
    }).join('');
}

function renderWebhookList() {
    if (webhooks.length === 0) {
        return `<div class="text-muted" style="padding:20px;text-align:center;font-size:var(--font-sm)">
            No webhooks configured yet. Create one to trigger Sapphire from external services.
        </div>`;
    }
    return webhooks.map(w => {
        const tc = w.trigger_config || {};
        const path = tc.path || '???';
        const method = tc.method || 'POST';
        const lastRun = w.last_run ? formatTime(w.last_run) : 'Never';
        const meta = [
            `${method} /api/events/webhook/${esc(path)}`,
            w.chat_target ? `\uD83D\uDCAC ${esc(w.chat_target)}` : '',
            `Last: ${lastRun}`
        ].filter(Boolean).join(' \u00B7 ');

        return `
            <div class="sched-task-card${w.running ? ' running' : ''}">
                <label class="sched-toggle" title="${w.enabled ? 'Disable' : 'Enable'}">
                    <input type="checkbox" ${w.enabled ? 'checked' : ''} data-action="toggle" data-id="${w.id}">
                    <span class="toggle-slider"></span>
                </label>
                <div class="sched-task-info">
                    <div class="sched-task-name">\uD83D\uDD17 ${esc(w.name)}</div>
                    <div class="sched-task-meta">${meta}</div>
                </div>
                <div class="sched-task-actions">
                    <button class="btn-icon" data-action="export" data-id="${w.id}" title="Export">\u21E9</button>
                    <button class="btn-icon" data-action="edit" data-id="${w.id}" title="Edit">\u270F\uFE0F</button>
                    <button class="btn-icon danger" data-action="delete" data-id="${w.id}" title="Delete">\u2715</button>
                </div>
            </div>`;
    }).join('');
}

// ── Heatmap Blocks ──

function renderHeatmap(beats) {
    const MAX = 20;
    const empty = MAX - beats.length;
    const blocks = [];
    for (let i = 0; i < empty; i++) blocks.push('<span class="hb-block empty"></span>');
    for (const s of beats) blocks.push(`<span class="hb-block ${s}"></span>`);
    return `<div class="hb-heatmap">${blocks.join('')}</div>`;
}

// ── Horizontal Timeline Strip ──

function renderHorizontalTimeline() {
    const { now, past, future } = mergedTimeline;
    const liveIds = new Set([...tasks, ...heartbeats].map(t => t.id));
    const allItems = [...past, ...future].filter(item => liveIds.has(item.task_id));
    if (!allItems.length) return '';

    const nowMs = now ? new Date(now).getTime() : Date.now();
    const windowMs = 2 * 60 * 60 * 1000;
    const minMs = nowMs - windowMs;
    const maxMs = nowMs + windowMs;

    const rowMap = new Map();
    for (const hb of heartbeats) rowMap.set(hb.id, rowMap.size);
    for (const t of tasks) rowMap.set(t.id, rowMap.size);
    const pipData = [];
    for (const item of allItems) {
        const ts = item.timestamp || item.scheduled_for;
        if (!ts) continue;
        const ms = new Date(ts).getTime();
        if (ms < minMs || ms > maxMs) continue;
        const tid = item.task_id || item.task_name;
        if (!rowMap.has(tid)) rowMap.set(tid, rowMap.size);
        const pct = ((ms - minMs) / (maxMs - minMs)) * 100;
        const icon = item.heartbeat ? (item.emoji || '\u2764\uFE0F') : '\u26A1';
        const isPast = ms <= nowMs;
        const timeStr = new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        pipData.push({ pct, icon, isPast, name: item.task_name, timeStr, row: rowMap.get(tid) });
    }

    const usedRows = new Set(pipData.map(p => p.row));
    const numRows = Math.max(1, usedRows.size);
    const rowH = 20;
    const rowRemap = new Map();
    [...usedRows].sort((a, b) => a - b).forEach((r, i) => rowRemap.set(r, i));
    for (const p of pipData) p.row = rowRemap.get(p.row);
    const rulerH = numRows * rowH + 8;

    const pips = pipData.map(p => {
        const topPx = 4 + p.row * rowH;
        return `<span class="hstrip-pip${p.isPast ? ' past' : ''}" style="left:${p.pct}%;top:${topPx}px" title="${esc(p.name)} \u2014 ${p.timeStr}">${p.icon}</span>`;
    }).join('');

    const nowTimeStr = new Date(nowMs).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    const fmt = ms => new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const markers = [
        { pct: 0, label: fmt(minMs) },
        { pct: 25, label: fmt(nowMs - windowMs / 2) },
        { pct: 75, label: fmt(nowMs + windowMs / 2) },
        { pct: 100, label: fmt(maxMs) }
    ].map(m =>
        `<span class="hstrip-marker" style="left:${m.pct}%">${m.label}</span>`
    ).join('');

    return `
        <div class="sched-hstrip">
            <div class="hstrip-markers">${markers}</div>
            <div class="hstrip-ruler" style="height:${rulerH}px">
                ${pips}
                <span class="hstrip-now"><span class="hstrip-now-label">${nowTimeStr}</span></span>
            </div>
        </div>`;
}

// ── Heartbeat Helpers ──

function getHeartbeatState(hb) {
    if (!hb.enabled) return { label: 'Flatlined', cls: 'flatlined' };
    if (hb.running) return { label: 'Ba-bump', cls: 'babump' };
    if (!hb.last_run) return { label: 'Warming up', cls: 'warmup' };
    const recent = (mergedTimeline.past || []).filter(a => a.task_id === hb.id);
    if (recent.length > 0 && recent[0].status === 'error') return { label: 'Irregular', cls: 'irregular' };
    return { label: 'Beating', cls: 'beating' };
}

function getBeatsForTask(taskId, count) {
    const all = (mergedTimeline.past || []).filter(a => a.task_id === taskId);
    return all.slice(0, count).reverse().map(a => a.status || 'complete');
}

function getNextIn(taskId) {
    const next = (mergedTimeline.future || []).find(f => f.task_id === taskId);
    if (!next?.scheduled_for) return null;
    return timeUntil(next.scheduled_for);
}

function timeUntil(isoString) {
    if (!isoString) return '';
    try {
        const diff = new Date(isoString).getTime() - Date.now();
        if (diff < 0) return null;
        if (diff < 60000) return '<1m';
        if (diff < 3600000) return `${Math.floor(diff / 60000)}m`;
        if (diff < 86400000) return `${Math.floor(diff / 3600000)}h`;
        return `${Math.floor(diff / 86400000)}d`;
    } catch { return ''; }
}

// ── Events ──

function bindEvents() {
    // Tab switching — reload data when changing tabs so events data is fresh
    container.querySelector('.sched-tab-bar')?.addEventListener('click', async e => {
        const tab = e.target.closest('.sched-tab');
        if (!tab || tab.dataset.tab === activeTab) return;
        activeTab = tab.dataset.tab;
        container.querySelectorAll('.sched-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === activeTab));
        await loadData();
        renderTabContent();
    });
}

function bindTabContent() {
    // New buttons
    container.querySelector('#sched-new-task')?.addEventListener('click', () => openEditor(null, 'task'));
    container.querySelector('#sched-new-heartbeat')?.addEventListener('click', () => openEditor(null, 'heartbeat'));
    container.querySelector('#sched-new-daemon')?.addEventListener('click', () => openEditor(null, 'daemon'));
    container.querySelector('#sched-new-webhook')?.addEventListener('click', () => openEditor(null, 'webhook'));

    // Import buttons
    container.querySelector('#sched-import-task')?.addEventListener('click', () => importTask('task', tasks));
    container.querySelector('#sched-import-heartbeat')?.addEventListener('click', () => importTask('heartbeat', heartbeats));
    container.querySelector('#sched-import-daemon')?.addEventListener('click', () => importTask('daemon', daemons));
    container.querySelector('#sched-import-webhook')?.addEventListener('click', () => importTask('webhook', webhooks));

    // Delegated actions for task/heartbeat cards
    const layout = container.querySelector('.sched-layout');
    if (!layout) return;

    layout.addEventListener('click', async e => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        const { action, id } = btn.dataset;
        const allTasks = [...tasks, ...heartbeats, ...daemons, ...webhooks];

        if (action === 'export') {
            const task = allTasks.find(t => t.id === id);
            if (task) exportTask(task);
        } else if (action === 'edit') {
            const task = allTasks.find(t => t.id === id);
            if (task) {
                const type = task.type || (task.heartbeat ? 'heartbeat' : 'task');
                openEditor(task, type);
            }
        } else if (action === 'run') {
            const task = allTasks.find(t => t.id === id);
            if (!task || !confirm(`Run "${task.name}" now?`)) return;
            try {
                await runTask(id);
                ui.showToast(`Running: ${task.name}`, 'success');
                await loadData(); updateContent();
            } catch { ui.showToast('Run failed', 'error'); }
        } else if (action === 'delete') {
            const task = allTasks.find(t => t.id === id);
            if (!task || !confirm(`Delete "${task.name}"?`)) return;
            try {
                await deleteTask(id);
                ui.showToast('Deleted', 'success');
                await loadData(); updateContent();
            } catch { ui.showToast('Delete failed', 'error'); }
        }
    });

    // Toggle (checkbox change)
    layout.addEventListener('change', async e => {
        const { action, id } = e.target.dataset;
        if (action === 'toggle') {
            const task = [...tasks, ...daemons, ...webhooks].find(t => t.id === id);
            if (!task) return;
            try {
                await updateTask(id, { enabled: !task.enabled });
                await loadData(); updateContent();
            } catch { ui.showToast('Toggle failed', 'error'); }
        } else if (action === 'hb-toggle') {
            const hb = heartbeats.find(h => h.id === id);
            if (!hb) return;
            try {
                await updateTask(id, { enabled: !hb.enabled });
                await loadData(); updateContent();
            } catch { ui.showToast('Toggle failed', 'error'); }
        }
    });
}

// ── Editor (uses shared trigger-editor) ──

function openEditor(task, type) {
    openTriggerEditor(task, type, {
        onSave: async (id, data) => {
            try {
                if (id) await updateTask(id, data);
                else await createTask(data);
                ui.showToast(id ? 'Saved' : 'Created', 'success');
                await loadData();
                updateContent();
                return true;
            } catch (e) {
                ui.showToast(e.message || 'Save failed', 'error');
                return false;
            }
        },
        onDelete: async (id) => {
            try {
                await deleteTask(id);
                ui.showToast('Deleted', 'success');
                await loadData();
                updateContent();
            } catch { ui.showToast('Delete failed', 'error'); }
        },
    });
}

// ── Import / Export ──

const EXPORT_STRIP_KEYS = ['id', 'last_run', 'last_response', 'created', 'running', 'source', 'handler', 'plugin_dir'];
const SCOPE_KEYS = ['memory_scope', 'knowledge_scope', 'people_scope', 'goal_scope'];

function buildTaskExport(task) {
    const clean = { ...task };
    EXPORT_STRIP_KEYS.forEach(k => delete clean[k]);
    clean.enabled = false; // always import disabled
    return {
        sapphire_export: true,
        type: clean.type || 'task',
        version: 1,
        name: clean.name,
        task: clean,
    };
}

function exportTask(task) {
    const type = task.type || 'task';
    showExportDialog({
        type: type.charAt(0).toUpperCase() + type.slice(1),
        name: task.name,
        filename: `${task.name.replace(/\s+/g, '_')}.${type}.json`,
        data: buildTaskExport(task),
    });
}

async function importTask(type, allTasks) {
    // Fetch available scopes for validation
    const [memScopes, knowScopes, pplScopes, goalScopes] = await Promise.all([
        fetchMemoryScopes(), fetchKnowledgeScopes(), fetchPeopleScopes(), fetchGoalScopes(),
    ]);
    const scopeSets = {
        memory_scope: new Set(['default', 'none', ...memScopes.map(s => s.name || s)]),
        knowledge_scope: new Set(['default', 'none', ...knowScopes.map(s => s.name || s)]),
        people_scope: new Set(['default', 'none', ...pplScopes.map(s => s.name || s)]),
        goal_scope: new Set(['default', 'none', ...goalScopes.map(s => s.name || s)]),
    };

    const typeLabel = type.charAt(0).toUpperCase() + type.slice(1);
    showImportDialog({
        type: typeLabel,
        existingNames: allTasks.map(t => t.name),
        validate: (d) => {
            if (d.sapphire_export && d.task) return null;
            if (d.name && (d.schedule || d.trigger_config || d.initial_message)) return null;
            return `Invalid ${type} format`;
        },
        getName: (d) => (d.task?.name || d.name || 'imported'),
        onImport: async (data, { name }) => {
            const task = data.task || data;
            task.name = name;
            task.type = task.type || type;
            task.enabled = false;
            delete task.id;
            delete task.last_run;
            delete task.last_response;
            delete task.created;

            // Validate scopes — reset to default if they don't exist on this instance
            const skippedScopes = [];
            for (const key of SCOPE_KEYS) {
                const val = task[key];
                if (val && scopeSets[key] && !scopeSets[key].has(val)) {
                    skippedScopes.push(`${key}: "${val}"`);
                    task[key] = 'default';
                }
            }

            await createTask(task);

            if (skippedScopes.length) {
                ui.showToast(`Imported (${skippedScopes.length} scopes reset to default: ${skippedScopes.join(', ')})`, 'warning');
            }
        },
        onDone: async () => {
            await loadData();
            activeTab === 'time' ? updateContent() : renderTabContent();
        },
    });
}

// ── General Helpers ──

function formatHourRange(start, end) {
    const fmt = h => {
        if (h === 0) return '12AM';
        if (h < 12) return `${h}AM`;
        if (h === 12) return '12PM';
        return `${h - 12}PM`;
    };
    return `${fmt(start)}\u2013${fmt(end)}`;
}

function timeAgo(isoString) {
    if (!isoString) return '';
    try {
        const diff = Date.now() - new Date(isoString).getTime();
        if (diff < 60000) return 'just now';
        if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
        if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
        return `${Math.floor(diff / 86400000)}d ago`;
    } catch { return ''; }
}

function formatTime(isoString) {
    if (!isoString) return '';
    try {
        const d = new Date(isoString);
        const now = new Date();
        if (d.toDateString() === now.toDateString())
            return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        const yesterday = new Date(now);
        yesterday.setDate(yesterday.getDate() - 1);
        if (d.toDateString() === yesterday.toDateString())
            return 'Yesterday ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        if (now - d < 7 * 24 * 60 * 60 * 1000)
            return d.toLocaleDateString([], { weekday: 'short' }) + ' ' +
                   d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' +
               d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch { return isoString; }
}

function esc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
