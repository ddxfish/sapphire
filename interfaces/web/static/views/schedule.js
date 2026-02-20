// views/schedule.js - Scheduler view (Tasks + Heartbeats)
import { fetchNonHeartbeatTasks, fetchHeartbeats, fetchStatus, fetchMergedTimeline,
         createTask, updateTask, deleteTask, runTask,
         fetchPrompts, fetchToolsets, fetchLLMProviders,
         fetchMemoryScopes, fetchKnowledgeScopes, fetchPeopleScopes, fetchGoalScopes, fetchEmailAccounts,
         fetchPersonas, fetchPersona } from '../shared/continuity-api.js';
import * as ui from '../ui.js';

let container = null;
let tasks = [];         // non-heartbeat only
let heartbeats = [];    // heartbeats only
let status = {};
let mergedTimeline = { now: null, past: [], future: [] };
let pollTimer = null;
let _docClickBound = false;

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
        const [t, hb, s, mt] = await Promise.all([
            fetchNonHeartbeatTasks(), fetchHeartbeats(), fetchStatus(), fetchMergedTimeline(12, 12)
        ]);
        tasks = t; heartbeats = hb; status = s; mergedTimeline = mt;
    } catch (e) { console.warn('Schedule load failed:', e); }
}

// ── Main Layout ──

function render() {
    if (!container) return;
    container.innerHTML = `
        <div class="sched-view">
            <div class="view-header sched-header-centered">
                <h2>Schedule</h2>
                <span class="view-subtitle" id="sched-subtitle"></span>
                <div class="sched-create-menu">
                    <button class="btn-primary" id="sched-new-btn">+ New</button>
                    <div class="sched-create-dropdown" id="sched-create-dropdown">
                        <button class="sched-create-opt" data-create="task">\u26A1 New Task</button>
                        <button class="sched-create-opt" data-create="heartbeat">\uD83D\uDC93 New Heartbeat</button>
                    </div>
                </div>
            </div>
            <div class="view-body view-scroll">
                <div id="sched-hstrip-wrap"></div>
                <div class="sched-layout">
                    <div id="sched-tasks"></div>
                    <div class="sched-mission" id="sched-mission"></div>
                </div>
            </div>
        </div>
    `;
    updateContent();
    bindEvents();
}

function updateContent() {
    const tasksEl = container?.querySelector('#sched-tasks');
    const missionEl = container?.querySelector('#sched-mission');
    const subEl = container?.querySelector('#sched-subtitle');
    const hstripEl = container?.querySelector('#sched-hstrip-wrap');

    // Preserve open accordion state + scroll position before re-render
    const openCards = new Set();
    if (missionEl) {
        for (const d of missionEl.querySelectorAll('details.hb-response-wrap[open]')) {
            const card = d.closest('.hb-card');
            if (card) openCards.add(card.id);
        }
    }
    const scrollEl = container?.querySelector('.view-scroll');
    const scrollTop = scrollEl?.scrollTop || 0;

    if (tasksEl) tasksEl.innerHTML = renderTaskList();
    if (missionEl) missionEl.innerHTML = renderMission();
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
        let statusText = '';
        if (t.running) {
            statusText = `<span class="sched-progress">Running...</span>`;
        }
        const meta = [
            t.chance < 100 ? `${t.chance}%` : '',
            t.active_hours_start != null ? `\uD83D\uDD53 ${formatHourRange(t.active_hours_start, t.active_hours_end)}` : '',
            statusText,
            t.chat_target ? `\uD83D\uDCAC ${esc(t.chat_target)}` : '',
            `Last: ${lastRun}`
        ].filter(Boolean).join(' \u00B7 ');

        return `
            <div class="sched-task-card${t.running ? ' running' : ''}">
                <label class="sched-toggle" title="${t.enabled ? 'Disable' : 'Enable'}">
                    <input type="checkbox" ${t.enabled ? 'checked' : ''} data-action="toggle" data-id="${t.id}">
                    <span class="toggle-slider"></span>
                </label>
                <div class="sched-task-info">
                    <div class="sched-task-name">${esc(t.name)}</div>
                    <div class="sched-task-schedule">${esc(sched)}</div>
                    <div class="sched-task-meta">${meta}</div>
                </div>
                <div class="sched-task-actions">
                    <button class="btn-icon" data-action="run" data-id="${t.id}" title="Run now">\u25B6</button>
                    <button class="btn-icon" data-action="edit" data-id="${t.id}" title="Edit">\u270F\uFE0F</button>
                    <button class="btn-icon danger" data-action="delete" data-id="${t.id}" title="Delete">\u2715</button>
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

    // Time context: "Beating · ran 3m ago · next in 12m"
    const lastAgo = hb.last_run ? timeAgo(hb.last_run) : null;
    const nextIn = getNextIn(hb.id);
    const timeParts = [
        state.label,
        hb.active_hours_start != null ? formatHourRange(hb.active_hours_start, hb.active_hours_end) : null,
        lastAgo ? `ran ${lastAgo}` : null,
        nextIn ? `next in ${nextIn}` : null
    ].filter(Boolean).join(' \u00B7 ');

    // Response with expand accordion
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
                <button class="btn-icon" data-action="edit" data-id="${hb.id}" title="Edit">\u270F\uFE0F</button>
                <button class="btn-icon danger" data-action="delete" data-id="${hb.id}" title="Delete">\u2715</button>
            </div>
        </div>`;
}

// ── Heatmap Blocks ──

function renderHeatmap(beats) {
    // Fixed 20-slot scale, right-aligned. Empty slots = border-colored.
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
    // Filter to only tasks that still exist
    const liveIds = new Set([...tasks, ...heartbeats].map(t => t.id));
    const allItems = [...past, ...future].filter(item => liveIds.has(item.task_id));
    if (!allItems.length) return '';

    const nowMs = now ? new Date(now).getTime() : Date.now();
    const windowMs = 2 * 60 * 60 * 1000; // 2h each side = 4h window
    const minMs = nowMs - windowMs;
    const maxMs = nowMs + windowMs;

    // Group by task_id — each task gets its own row, matching card order
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
    // Compact rows: remap to remove gaps from unused pre-seeded rows
    const rowRemap = new Map();
    [...usedRows].sort((a, b) => a - b).forEach((r, i) => rowRemap.set(r, i));
    for (const p of pipData) p.row = rowRemap.get(p.row);
    const rulerH = numRows * rowH + 8;

    const pips = pipData.map(p => {
        const topPx = 4 + p.row * rowH; // 4px top pad + row offset
        return `<span class="hstrip-pip${p.isPast ? ' past' : ''}" style="left:${p.pct}%;top:${topPx}px" title="${esc(p.name)} \u2014 ${p.timeStr}">${p.icon}</span>`;
    }).join('');

    const nowTimeStr = new Date(nowMs).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    // Time markers at -2h, -1h, NOW, +1h, +2h
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

    // Check recent activity for errors
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
    // Create menu dropdown
    const newBtn = container.querySelector('#sched-new-btn');
    const dropdown = container.querySelector('#sched-create-dropdown');
    newBtn?.addEventListener('click', () => dropdown?.classList.toggle('show'));

    // Close dropdown on outside click (bind once — survives innerHTML replacement)
    if (!_docClickBound) {
        document.addEventListener('click', e => {
            if (!e.target.closest('.sched-create-menu')) {
                container?.querySelector('#sched-create-dropdown')?.classList.remove('show');
            }
        });
        _docClickBound = true;
    }

    container.querySelector('.sched-create-dropdown')?.addEventListener('click', e => {
        const opt = e.target.closest('.sched-create-opt');
        if (!opt) return;
        dropdown?.classList.remove('show');
        const type = opt.dataset.create;
        openEditor(null, type === 'heartbeat');
    });

    const layout = container.querySelector('.sched-layout');
    if (!layout) return;

    // Task + vital card actions (delegated)
    layout.addEventListener('click', async e => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        const { action, id } = btn.dataset;
        const allTasks = [...tasks, ...heartbeats];

        if (action === 'edit') {
            const task = allTasks.find(t => t.id === id);
            if (task) openEditor(task, task.heartbeat);
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

    // Toggle (checkbox change) — tasks + heartbeats
    layout.addEventListener('change', async e => {
        const { action, id } = e.target.dataset;
        if (action === 'toggle') {
            const task = tasks.find(t => t.id === id);
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

// ── Task Editor Modal ──

const EMOJI_PICKS = [
    // hearts
    '\u2764\uFE0F', '\uD83E\uDE77', '\uD83E\uDDE1', '\uD83D\uDC9B', '\uD83D\uDC9A', '\uD83D\uDC99', '\uD83D\uDC9C',
    '\uD83D\uDDA4', '\uD83E\uDD0D', '\uD83E\uDE76', '\uD83D\uDC96', '\uD83D\uDC9D', '\uD83D\uDC93', '\uD83D\uDC97', '\uD83D\uDC95',
    // cosmic & nature
    '\uD83D\uDD25', '\u26A1', '\uD83C\uDF19', '\u2728', '\uD83D\uDCAB', '\uD83C\uDF1F', '\u2600\uFE0F', '\uD83C\uDF08',
    '\uD83C\uDF0A', '\uD83C\uDF3F', '\uD83C\uDF38', '\uD83C\uDF40', '\uD83E\uDD8B', '\uD83D\uDD4A\uFE0F', '\uD83E\uDEA9',
    // tech & tools
    '\uD83E\uDDE0', '\uD83D\uDC41\uFE0F', '\uD83D\uDEE1\uFE0F', '\uD83D\uDD2E', '\uD83C\uDF00', '\uD83D\uDCA1',
    '\uD83D\uDD27', '\u2699\uFE0F', '\uD83D\uDCE1', '\uD83D\uDEF8',
    // objects & symbols
    '\uD83D\uDC8E', '\uD83C\uDFAF', '\uD83D\uDD14', '\uD83C\uDFB5', '\uD83D\uDD11', '\uD83D\uDEE1\uFE0F',
    '\uD83D\uDDDD\uFE0F', '\uD83C\uDFF9', '\uD83E\uDEAC', '\uD83D\uDCBF'
];

const TTS_VOICES = {
    'American Female': ['af_bella', 'af_nicole', 'af_heart', 'af_jessica', 'af_sarah', 'af_river', 'af_sky'],
    'American Male': ['am_adam', 'am_eric', 'am_liam', 'am_michael'],
    'British Female': ['bf_emma', 'bf_isabella', 'bf_alice', 'bf_lily'],
    'British Male': ['bm_george', 'bm_daniel', 'bm_lewis']
};

async function openEditor(task, isHeartbeat = false) {
    document.querySelector('.sched-editor-overlay')?.remove();

    let prompts = [], toolsetsList = [], llmProviders = [], llmMetadata = {};
    let memoryScopes = [], knowledgeScopes = [], peopleScopes = [], goalScopes = [], emailAccounts = [];
    let personas = [];
    try {
        const [p, ts, llm, ms, ks, ps, gs, ea, per] = await Promise.all([
            fetchPrompts(), fetchToolsets(), fetchLLMProviders(),
            fetchMemoryScopes(), fetchKnowledgeScopes(), fetchPeopleScopes(), fetchGoalScopes(),
            fetchEmailAccounts(), fetchPersonas()
        ]);
        prompts = p || []; toolsetsList = ts || [];
        llmProviders = llm.providers || []; llmMetadata = llm.metadata || {};
        memoryScopes = ms || []; knowledgeScopes = ks || [];
        peopleScopes = ps || []; goalScopes = gs || [];
        emailAccounts = ea || [];
        personas = per || [];
    } catch (e) { console.warn('Editor: failed to fetch options', e); }

    const isEdit = !!task;
    const t = task || {};
    if (!isEdit && isHeartbeat) {
        t.heartbeat = true;
        t.tts_enabled = false;
        t.emoji = t.emoji || '\u2764\uFE0F';
    }
    const parsed = t.schedule ? parseCron(t.schedule) : (isHeartbeat ? { mode: 'interval', value: 15, unit: 'minutes' } : { mode: 'daily', time: '09:00' });
    // Determine initial tab: simple if mode matches type, advanced otherwise
    const simpleOk = isHeartbeat ? parsed.mode === 'interval' : (parsed.mode === 'daily' || parsed.mode === 'weekly');
    const initTab = simpleOk || !isEdit ? 'simple' : 'advanced';
    // For new heartbeats, force interval defaults
    if (!isEdit && isHeartbeat && parsed.mode !== 'interval') {
        parsed.mode = 'interval';
        parsed.value = 15;
        parsed.unit = 'minutes';
    }

    const providerOpts = llmProviders
        .filter(p => p.enabled)
        .map(p => `<option value="${p.key}" ${t.provider === p.key ? 'selected' : ''}>${p.display_name}${p.is_local ? ' \uD83C\uDFE0' : ' \u2601\uFE0F'}</option>`)
        .join('');

    const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    const selectedDays = parsed.mode === 'weekly' ? parsed.days : [1, 2, 3, 4, 5];
    const dayChecks = dayNames.map((name, i) =>
        `<label class="sched-day-label"><input type="checkbox" value="${i}" ${selectedDays.includes(i) ? 'checked' : ''}> ${name}</label>`
    ).join('');

    const currentTime = parsed.time || '09:00';
    const intervalValue = parsed.mode === 'interval' ? parsed.value : 15;
    const intervalUnit = parsed.mode === 'interval' ? parsed.unit : 'minutes';
    const cronRaw = t.schedule || '0 9 * * *';

    // Voice dropdown options
    const voiceOpts = Object.entries(TTS_VOICES).map(([group, voices]) =>
        `<optgroup label="${group}">${voices.map(v =>
            `<option value="${v}" ${t.voice === v ? 'selected' : ''}>${v}</option>`
        ).join('')}</optgroup>`
    ).join('');

    const modal = document.createElement('div');
    modal.className = 'sched-editor-overlay';
    modal.innerHTML = `
        <div class="sched-editor">
            <div class="sched-editor-header">
                ${isHeartbeat ? `
                <div class="sched-hb-emoji-wrap" id="sched-hb-emoji-wrap">
                    <span class="sched-hb-emoji-btn" id="sched-hb-emoji-btn" title="Pick emoji">${t.emoji || '\u2764\uFE0F'}</span>
                </div>` : ''}
                <h3>${isEdit ? (isHeartbeat ? 'Edit Heartbeat' : 'Edit Task') : (isHeartbeat ? 'New Heartbeat' : 'New Task')}</h3>
                <div style="flex:1"></div>
                ${isEdit ? `<button class="btn-sm danger" id="ed-delete" style="margin-right:8px">Delete</button>` : ''}
                <button class="btn-icon" data-action="close">&times;</button>
            </div>
            <div class="sched-editor-body">
                <p class="sched-editor-blurb">${isHeartbeat
                    ? 'Heartbeats wake Sapphire up on a rhythm to check on things. She remembers what she found last time.'
                    : 'Tasks run at a scheduled time — an alarm that triggers Sapphire to do something specific.'}</p>

                <div class="sched-field">
                    <label>${isHeartbeat ? 'Heartbeat Name' : 'Task Name'}</label>
                    <input type="text" id="ed-name" value="${esc(t.name || '')}" placeholder="${isHeartbeat ? 'System Health Check' : 'Morning Greeting'}">
                </div>
                <div class="sched-field">
                    <label>${isHeartbeat ? 'What should Sapphire check?' : 'What should Sapphire do?'} <span class="help-tip" data-tip="What the AI receives when this fires. Be specific — the AI only knows what you tell it here.">?</span></label>
                    <textarea id="ed-message" rows="2" placeholder="${isHeartbeat
                        ? 'Check my emails and calendar. If anything needs attention, tell me. Otherwise just say all clear.'
                        : 'Write a brief daily summary of what happened today.'}">${esc(t.initial_message || '')}</textarea>
                </div>

                <div class="sched-section-title" style="margin-top:16px">\uD83D\uDCC5 When</div>
                <div class="sched-picker">
                    <div class="sched-picker-tabs">
                        <button type="button" class="sched-picker-tab${initTab === 'simple' ? ' active' : ''}" data-tab="simple">Simple</button>
                        <button type="button" class="sched-picker-tab${initTab === 'advanced' ? ' active' : ''}" data-tab="advanced">Advanced</button>
                    </div>
                    <div class="sched-pick-panel" data-tab-panel="simple" ${initTab !== 'simple' ? 'style="display:none"' : ''}>
                        ${isHeartbeat ? `
                        <div class="sched-sentence">
                            Beats every
                            <input type="number" id="ed-interval-val" value="${intervalValue}" min="1" style="width:60px">
                            <select id="ed-interval-unit">
                                <option value="minutes" ${intervalUnit === 'minutes' ? 'selected' : ''}>minutes</option>
                                <option value="hours" ${intervalUnit === 'hours' ? 'selected' : ''}>hours</option>
                            </select>
                        </div>
                        ` : `
                        <div class="sched-sentence">
                            Runs at <input type="time" id="ed-time" value="${currentTime}"> every
                            <select id="ed-frequency">
                                <option value="day" ${parsed.mode === 'daily' ? 'selected' : ''}>Day</option>
                                <option value="days" ${parsed.mode === 'weekly' ? 'selected' : ''}>On these days</option>
                            </select>
                        </div>
                        <div class="sched-days" id="ed-days-row" ${parsed.mode !== 'weekly' ? 'style="display:none"' : ''}>${dayChecks}</div>
                        `}
                    </div>
                    <div class="sched-pick-panel" data-tab-panel="advanced" ${initTab !== 'advanced' ? 'style="display:none"' : ''}>
                        <div class="sched-field" style="margin-bottom:4px">
                            <label>Cron expression</label>
                            <input type="text" id="ed-cron-raw" value="${esc(cronRaw)}" placeholder="0 9 * * *">
                        </div>
                    </div>
                    <div class="sched-preview-line" id="sched-preview-line"></div>
                </div>

                <div class="sched-modifiers">
                    <label class="sched-modifier">
                        <input type="checkbox" id="ed-active-hours-on" ${t.active_hours_start != null ? 'checked' : ''}>
                        Active hours
                        <span class="help-tip" data-tip="Restrict to a time window. Outside these hours, cron matches are skipped. Supports overnight (e.g. 8PM-4AM).">?</span>
                        <span class="sched-modifier-inputs" id="ed-active-hours-row" ${t.active_hours_start == null ? 'style="display:none"' : ''}>
                            <select id="ed-active-start">${hourOptions(t.active_hours_start ?? 20)}</select>
                            to
                            <select id="ed-active-end">${hourOptions(t.active_hours_end ?? 4)}</select>
                        </span>
                    </label>
                    <label class="sched-modifier">
                        <input type="checkbox" id="ed-chance-on" ${(t.chance ?? 100) < 100 ? 'checked' : ''}>
                        Chance
                        <span class="help-tip" data-tip="Roll the dice each time this fires. 50% = runs half the time. 100% = always.">?</span>
                        <span class="sched-modifier-inputs" id="ed-chance-row" ${(t.chance ?? 100) >= 100 ? 'style="display:none"' : ''}>
                            <input type="number" id="ed-chance" value="${t.chance ?? 100}" min="1" max="100" style="width:60px">%
                        </span>
                    </label>
                </div>

                <div class="sched-field" style="margin-top:16px">
                    <label>\uD83D\uDC64 Persona <span class="help-tip" data-tip="Auto-fills prompt, voice, toolset, model, scopes, and more from a persona profile. You can still override individual settings in the accordions below.">?</span></label>
                    <select id="ed-persona">
                        <option value="">None (manual settings)</option>
                        ${personas.map(p => `<option value="${p.name}" ${t.persona === p.name ? 'selected' : ''}>${p.name}${p.tagline ? ' — ' + p.tagline : ''}</option>`).join('')}
                    </select>
                </div>

                <hr class="sched-divider">

                <details class="sched-accordion">
                    <summary class="sched-acc-header">AI <span class="sched-preview" id="ed-ai-preview">${t.prompt && t.prompt !== 'default' ? t.prompt : ''}</span></summary>
                    <div class="sched-acc-body"><div class="sched-acc-inner">
                        <div class="sched-field-row">
                            <div class="sched-field">
                                <label>Prompt</label>
                                <select id="ed-prompt">
                                    <option value="default">default</option>
                                    ${prompts.map(p => `<option value="${p.name}" ${t.prompt === p.name ? 'selected' : ''}>${p.name}</option>`).join('')}
                                </select>
                            </div>
                            <div class="sched-field">
                                <label>Toolset</label>
                                <select id="ed-toolset">
                                    <option value="none" ${t.toolset === 'none' ? 'selected' : ''}>none</option>
                                    <option value="default" ${t.toolset === 'default' ? 'selected' : ''}>default</option>
                                    ${toolsetsList.map(ts => `<option value="${ts.name}" ${t.toolset === ts.name ? 'selected' : ''}>${ts.name}</option>`).join('')}
                                </select>
                            </div>
                        </div>
                        <div class="sched-field-row">
                            <div class="sched-field">
                                <label>Provider</label>
                                <select id="ed-provider">
                                    <option value="auto" ${t.provider === 'auto' || !t.provider ? 'selected' : ''}>Auto (default)</option>
                                    ${providerOpts}
                                </select>
                            </div>
                            <div class="sched-field" id="ed-model-field" style="display:none">
                                <label>Model</label>
                                <select id="ed-model"><option value="">Provider default</option></select>
                            </div>
                            <div class="sched-field" id="ed-model-custom-field" style="display:none">
                                <label>Model</label>
                                <input type="text" id="ed-model-custom" value="${esc(t.model || '')}" placeholder="Model name">
                            </div>
                        </div>
                    </div></div>
                </details>

                <details class="sched-accordion">
                    <summary class="sched-acc-header">Chat <span class="sched-preview" id="ed-chat-preview">${t.chat_target ? esc(t.chat_target) : 'No history'}</span></summary>
                    <div class="sched-acc-body"><div class="sched-acc-inner">
                        <div class="sched-field">
                            <label>Chat Name <span class="help-tip" data-tip="Run in a named chat (conversation saved). Leave blank for ephemeral background execution.">?</span></label>
                            <input type="text" id="ed-chat" value="${esc(t.chat_target || '')}" placeholder="Leave blank for ephemeral">
                        </div>
                        <div class="sched-checkbox">
                            <label><input type="checkbox" id="ed-datetime" ${t.inject_datetime ? 'checked' : ''}> Inject date/time</label>
                        </div>
                    </div></div>
                </details>

                <details class="sched-accordion">
                    <summary class="sched-acc-header">Voice <span class="sched-preview" id="ed-voice-preview">${(isHeartbeat ? !t.tts_enabled : t.tts_enabled === false) ? 'TTS disabled' : (t.voice || '')}</span></summary>
                    <div class="sched-acc-body"><div class="sched-acc-inner">
                        <div class="sched-checkbox">
                            <label><input type="checkbox" id="ed-tts" ${t.tts_enabled !== false && !isHeartbeat ? 'checked' : ''}${isHeartbeat && t.tts_enabled ? ' checked' : ''}> Speak response</label>
                        </div>
                        <div class="sched-field">
                            <label>Voice <span class="help-tip" data-tip="TTS voice to use. Leave on default to use whatever voice is currently active.">?</span></label>
                            <select id="ed-voice">
                                <option value="">Default (current voice)</option>
                                ${voiceOpts}
                            </select>
                        </div>
                        <div class="sched-field-row">
                            <div class="sched-field">
                                <label>Pitch</label>
                                <input type="number" id="ed-pitch" value="${t.pitch ?? ''}" min="0.5" max="2.0" step="0.05" placeholder="default" style="width:80px">
                            </div>
                            <div class="sched-field">
                                <label>Speed</label>
                                <input type="number" id="ed-speed" value="${t.speed ?? ''}" min="0.5" max="2.0" step="0.05" placeholder="default" style="width:80px">
                            </div>
                        </div>
                    </div></div>
                </details>

                <details class="sched-accordion">
                    <summary class="sched-acc-header">Mind</summary>
                    <div class="sched-acc-body"><div class="sched-acc-inner">
                        ${renderScopeField('Memory', 'ed-memory', t.memory_scope, memoryScopes, '/api/memory/scopes')}
                        ${renderScopeField('Knowledge', 'ed-knowledge', t.knowledge_scope, knowledgeScopes, '/api/knowledge/scopes')}
                        ${renderScopeField('People', 'ed-people', t.people_scope, peopleScopes, '/api/knowledge/people/scopes')}
                        ${renderScopeField('Goals', 'ed-goals', t.goal_scope, goalScopes, '/api/goals/scopes')}
                        ${renderScopeField('Email', 'ed-email', t.email_scope, emailAccounts.map(a => ({name: a.scope, count: null})), null)}
                    </div></div>
                </details>

                <details class="sched-accordion">
                    <summary class="sched-acc-header">Execution Limits</summary>
                    <div class="sched-acc-body"><div class="sched-acc-inner">
                        <p class="text-muted" style="font-size:var(--font-xs);margin:0 0 10px">Override app defaults for this task. 0 = use global setting.</p>
                        <div class="sched-field-row">
                            <div class="sched-field">
                                <label>Context window <span class="help-tip" data-tip="Token limit for conversation history. 0 = app default. Set higher for long tasks needing more context.">?</span></label>
                                <div style="display:flex;align-items:center;gap:4px">
                                    <input type="number" id="ed-context-limit" value="${t.context_limit || 0}" min="0" style="width:90px">
                                    <span class="text-muted">tokens</span>
                                </div>
                            </div>
                        </div>
                        <div class="sched-field-row">
                            <div class="sched-field">
                                <label>Max parallel tools <span class="help-tip" data-tip="Tools AI can call at once per response. 0 = app default.">?</span></label>
                                <input type="number" id="ed-max-parallel" value="${t.max_parallel_tools || 0}" min="0" style="width:60px">
                            </div>
                            <div class="sched-field">
                                <label>Max tool rounds <span class="help-tip" data-tip="Tool-result loops before forcing a final reply. 0 = app default.">?</span></label>
                                <input type="number" id="ed-max-rounds" value="${t.max_tool_rounds || 0}" min="0" style="width:60px">
                            </div>
                        </div>
                    </div></div>
                </details>
            </div>
            <div class="sched-editor-footer">
                <button class="btn-sm" data-action="close">Cancel</button>
                <button class="btn-primary" id="ed-save">${isEdit ? 'Save' : (isHeartbeat ? 'Create Heartbeat' : 'Create Task')}</button>
            </div>
        </div>
    `;

    document.body.appendChild(modal);

    // Tooltips
    const tipEl = document.createElement('div');
    tipEl.className = 'help-tip-popup';
    document.body.appendChild(tipEl);
    modal.addEventListener('mouseover', e => {
        const tip = e.target.closest('.help-tip');
        if (!tip?.dataset.tip) return;
        tipEl.textContent = tip.dataset.tip;
        tipEl.style.display = 'block';
        const r = tip.getBoundingClientRect();
        tipEl.style.left = (r.left + r.width / 2) + 'px';
        tipEl.style.top = (r.top - 6) + 'px';
    });
    modal.addEventListener('mouseout', e => {
        if (e.target.closest('.help-tip') && !e.target.closest('.help-tip').contains(e.relatedTarget))
            tipEl.style.display = 'none';
    });

    // Close
    const close = () => { modal.remove(); tipEl.remove(); };
    modal.addEventListener('click', e => { if (e.target === modal) close(); });
    modal.querySelectorAll('[data-action="close"]').forEach(b => b.addEventListener('click', close));

    // Delete from editor header
    modal.querySelector('#ed-delete')?.addEventListener('click', async () => {
        if (!confirm(`Delete "${t.name}"?`)) return;
        try {
            await deleteTask(t.id);
            close();
            ui.showToast('Deleted', 'success');
            await loadData(); updateContent();
        } catch { ui.showToast('Delete failed', 'error'); }
    });

    // Emoji picker (header dropdown, heartbeat only)
    const emojiBtn = modal.querySelector('#sched-hb-emoji-btn');
    emojiBtn?.addEventListener('click', e => {
        e.stopPropagation();
        const wrap = modal.querySelector('#sched-hb-emoji-wrap');
        if (!wrap) return;
        wrap.querySelector('.sched-hb-emoji-picker')?.remove();
        const picker = document.createElement('div');
        picker.className = 'sched-hb-emoji-picker';
        picker.innerHTML = `<div class="sched-hb-emoji-grid">${EMOJI_PICKS.map(em =>
            `<button class="sched-hb-emoji-opt" data-emoji="${em}">${em}</button>`
        ).join('')}</div>`;
        wrap.appendChild(picker);
        picker.addEventListener('click', ev => {
            const opt = ev.target.closest('.sched-hb-emoji-opt');
            if (!opt) return;
            emojiBtn.textContent = opt.dataset.emoji;
            picker.remove();
        });
        const closePicker = ev => {
            if (!picker.contains(ev.target) && ev.target !== emojiBtn) {
                picker.remove();
                document.removeEventListener('click', closePicker);
            }
        };
        setTimeout(() => document.addEventListener('click', closePicker), 0);
    });

    // Persona auto-fill
    const personaSel = modal.querySelector('#ed-persona');
    personaSel?.addEventListener('change', async () => {
        const name = personaSel.value;
        if (!name) {
            const set = (id, val) => { const el = modal.querySelector(id); if (el) el.value = val; };
            set('#ed-memory', 'none');
            set('#ed-knowledge', 'none');
            set('#ed-people', 'none');
            set('#ed-goals', 'none');
            return;
        }
        try {
            const persona = await fetchPersona(name);
            if (!persona?.settings) return;
            const s = persona.settings;
            const set = (id, val) => { const el = modal.querySelector(id); if (el && val != null) el.value = val; };
            set('#ed-prompt', s.prompt || 'default');
            set('#ed-toolset', s.toolset || 'none');
            set('#ed-voice', s.voice || '');
            set('#ed-pitch', s.pitch ?? '');
            set('#ed-speed', s.speed ?? '');
            set('#ed-memory', s.memory_scope || 'none');
            set('#ed-knowledge', s.knowledge_scope || 'none');
            set('#ed-people', s.people_scope || 'none');
            set('#ed-goals', s.goal_scope || 'none');
            if (s.inject_datetime != null) modal.querySelector('#ed-datetime').checked = !!s.inject_datetime;
            // Provider + model
            if (s.llm_primary) {
                set('#ed-provider', s.llm_primary);
                updateModels();
                if (s.llm_model) setTimeout(() => set('#ed-model', s.llm_model), 50);
            }
            // Update preview chips
            const aiPrev = modal.querySelector('#ed-ai-preview');
            if (aiPrev) aiPrev.textContent = s.prompt && s.prompt !== 'default' ? s.prompt : '';
            const voicePrev = modal.querySelector('#ed-voice-preview');
            if (voicePrev) voicePrev.textContent = s.voice || '';
        } catch (e) { console.warn('Failed to load persona:', e); }
    });

    // Schedule picker tab switching (Simple/Advanced)
    let currentTab = initTab;
    modal.querySelector('.sched-picker-tabs')?.addEventListener('click', e => {
        const tab = e.target.closest('.sched-picker-tab');
        if (!tab) return;
        currentTab = tab.dataset.tab;
        modal.querySelectorAll('.sched-picker-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === currentTab));
        modal.querySelectorAll('[data-tab-panel]').forEach(p => p.style.display = p.dataset.tabPanel === currentTab ? '' : 'none');
        updatePreview();
    });

    // Frequency dropdown (tasks only, simple mode): show/hide day chips
    modal.querySelector('#ed-frequency')?.addEventListener('change', () => {
        const daysRow = modal.querySelector('#ed-days-row');
        if (daysRow) daysRow.style.display = modal.querySelector('#ed-frequency').value === 'days' ? '' : 'none';
        updatePreview();
    });

    const getCurrentCron = () => {
        if (currentTab === 'advanced') {
            return modal.querySelector('#ed-cron-raw')?.value || '0 9 * * *';
        }
        // Simple mode
        if (isHeartbeat) {
            return buildCron('interval', {
                value: parseInt(modal.querySelector('#ed-interval-val')?.value) || 15,
                unit: modal.querySelector('#ed-interval-unit')?.value || 'minutes'
            });
        }
        // Task: day or specific days
        const time = modal.querySelector('#ed-time')?.value || '09:00';
        const freq = modal.querySelector('#ed-frequency')?.value || 'day';
        if (freq === 'days') {
            const days = [...modal.querySelectorAll('.sched-days input:checked')].map(c => parseInt(c.value));
            if (days.length === 0) return buildCron('daily', { time });
            return buildCron('weekly', { time, days });
        }
        return buildCron('daily', { time });
    };

    const updatePreview = () => {
        const cronText = describeCron(getCurrentCron());
        const chanceOn = modal.querySelector('#ed-chance-on')?.checked;
        const chance = chanceOn ? (parseInt(modal.querySelector('#ed-chance')?.value) || 100) : 100;
        const text = chance < 100 ? `${chance}% chance to run ${cronText.toLowerCase()}` : cronText;
        const el = modal.querySelector('#sched-preview-line');
        if (el) el.textContent = text;
        modal.querySelectorAll('.sched-preview:not(#ed-ai-preview):not(#ed-voice-preview):not(#ed-chat-preview)').forEach(el => el.textContent = text);
    };
    modal.querySelector('#ed-chance')?.addEventListener('input', updatePreview);
    updatePreview();

    // Active hours toggle
    modal.querySelector('#ed-active-hours-on')?.addEventListener('change', () => {
        const row = modal.querySelector('#ed-active-hours-row');
        if (row) row.style.display = modal.querySelector('#ed-active-hours-on').checked ? '' : 'none';
    });

    // Chance toggle
    modal.querySelector('#ed-chance-on')?.addEventListener('change', () => {
        const row = modal.querySelector('#ed-chance-row');
        if (row) row.style.display = modal.querySelector('#ed-chance-on').checked ? '' : 'none';
        updatePreview();
    });

    // Chat name label
    modal.querySelector('#ed-chat')?.addEventListener('input', () => {
        const el = modal.querySelector('#ed-chat-preview');
        if (el) el.textContent = modal.querySelector('#ed-chat').value.trim() || 'No history';
    });

    // Live preview updates
    modal.querySelectorAll('#ed-time, #ed-interval-val, #ed-interval-unit, #ed-cron-raw')
        .forEach(el => el.addEventListener('input', updatePreview));
    modal.querySelectorAll('.sched-days input')
        .forEach(el => el.addEventListener('change', updatePreview));

    // Provider -> model logic
    const providerSel = modal.querySelector('#ed-provider');
    const updateModels = () => {
        const key = providerSel.value;
        const modelField = modal.querySelector('#ed-model-field');
        const modelCustomField = modal.querySelector('#ed-model-custom-field');
        const modelSel = modal.querySelector('#ed-model');
        modelField.style.display = 'none';
        modelCustomField.style.display = 'none';
        if (key === 'auto' || !key) return;
        const meta = llmMetadata[key];
        const pConfig = llmProviders.find(p => p.key === key);
        if (meta?.model_options && Object.keys(meta.model_options).length > 0) {
            const defaultModel = pConfig?.model || '';
            const defaultLabel = defaultModel ? `Provider default (${meta.model_options[defaultModel] || defaultModel})` : 'Provider default';
            modelSel.innerHTML = `<option value="">${defaultLabel}</option>` +
                Object.entries(meta.model_options)
                    .map(([k, v]) => `<option value="${k}"${k === (t.model || '') ? ' selected' : ''}>${v}</option>`)
                    .join('');
            if (t.model && !meta.model_options[t.model]) {
                modelSel.innerHTML += `<option value="${t.model}" selected>${t.model}</option>`;
            }
            modelField.style.display = '';
        } else if (key === 'other' || key === 'lmstudio') {
            modelCustomField.style.display = '';
        }
    };
    providerSel.addEventListener('change', updateModels);
    updateModels();

    // AI preview chip update on prompt change
    modal.querySelector('#ed-prompt')?.addEventListener('change', () => {
        const v = modal.querySelector('#ed-prompt').value;
        const el = modal.querySelector('#ed-ai-preview');
        if (el) el.textContent = v && v !== 'default' ? v : '';
    });
    // Voice preview chip update
    const updateVoicePreview = () => {
        const el = modal.querySelector('#ed-voice-preview');
        if (!el) return;
        const ttsOn = modal.querySelector('#ed-tts')?.checked;
        el.textContent = ttsOn ? (modal.querySelector('#ed-voice')?.value || '') : 'TTS disabled';
    };
    modal.querySelector('#ed-voice')?.addEventListener('change', updateVoicePreview);
    modal.querySelector('#ed-tts')?.addEventListener('change', updateVoicePreview);

    // Scope "+" buttons
    modal.querySelectorAll('.sched-add-scope').forEach(btn => {
        btn.addEventListener('click', async () => {
            const name = prompt('New scope name (lowercase, no spaces):');
            if (!name) return;
            const clean = name.trim().toLowerCase().replace(/[^a-z0-9_]/g, '');
            if (!clean || clean.length > 32) { alert('Invalid name'); return; }
            try {
                const res = await fetch(btn.dataset.api, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: clean })
                });
                if (res.ok) {
                    const sel = btn.previousElementSibling;
                    const opt = document.createElement('option');
                    opt.value = clean;
                    opt.textContent = clean;
                    sel.appendChild(opt);
                    sel.value = clean;
                } else {
                    const err = await res.json().catch(() => ({}));
                    alert(err.error || err.detail || 'Failed');
                }
            } catch { alert('Failed to create scope'); }
        });
    });

    // Save
    modal.querySelector('#ed-save')?.addEventListener('click', async () => {
        const modelField = modal.querySelector('#ed-model-field');
        const modelSel = modal.querySelector('#ed-model');
        const modelCustom = modal.querySelector('#ed-model-custom');
        let modelValue = '';
        if (modelField?.style.display !== 'none') modelValue = modelSel?.value || '';
        else if (modal.querySelector('#ed-model-custom-field')?.style.display !== 'none') modelValue = modelCustom?.value?.trim() || '';

        const selectedEmoji = modal.querySelector('#sched-hb-emoji-btn')?.textContent?.trim();
        const pitchVal = modal.querySelector('#ed-pitch')?.value;
        const speedVal = modal.querySelector('#ed-speed')?.value;

        const chanceOn = modal.querySelector('#ed-chance-on')?.checked;
        const data = {
            name: modal.querySelector('#ed-name').value.trim(),
            schedule: getCurrentCron(),
            chance: chanceOn ? (parseInt(modal.querySelector('#ed-chance').value) || 100) : 100,
            initial_message: modal.querySelector('#ed-message').value.trim() || 'Hello.',
            chat_target: modal.querySelector('#ed-chat').value.trim(),
            persona: modal.querySelector('#ed-persona').value,
            prompt: modal.querySelector('#ed-prompt').value,
            toolset: modal.querySelector('#ed-toolset').value,
            provider: modal.querySelector('#ed-provider').value,
            model: modelValue,
            voice: modal.querySelector('#ed-voice')?.value || '',
            pitch: pitchVal ? parseFloat(pitchVal) : null,
            speed: speedVal ? parseFloat(speedVal) : null,
            memory_scope: modal.querySelector('#ed-memory').value,
            knowledge_scope: modal.querySelector('#ed-knowledge')?.value || 'none',
            people_scope: modal.querySelector('#ed-people')?.value || 'none',
            goal_scope: modal.querySelector('#ed-goals')?.value || 'none',
            email_scope: modal.querySelector('#ed-email')?.value || 'default',
            tts_enabled: modal.querySelector('#ed-tts').checked,
            inject_datetime: modal.querySelector('#ed-datetime').checked,
            heartbeat: isHeartbeat,
            emoji: selectedEmoji || t.emoji || '',
            context_limit: parseInt(modal.querySelector('#ed-context-limit')?.value) || 0,
            max_parallel_tools: parseInt(modal.querySelector('#ed-max-parallel')?.value) || 0,
            max_tool_rounds: parseInt(modal.querySelector('#ed-max-rounds')?.value) || 0,
            active_hours_start: modal.querySelector('#ed-active-hours-on')?.checked ? parseInt(modal.querySelector('#ed-active-start')?.value) : null,
            active_hours_end: modal.querySelector('#ed-active-hours-on')?.checked ? parseInt(modal.querySelector('#ed-active-end')?.value) : null
        };

        if (!data.name) { alert('Name is required'); return; }
        if (!data.schedule) { alert('Schedule is required'); return; }

        try {
            if (isEdit) await updateTask(task.id, data);
            else await createTask(data);
            ui.showToast(isEdit ? 'Saved' : 'Created', 'success');
            close();
            await loadData();
            updateContent();
        } catch (e) {
            ui.showToast(e.message || 'Save failed', 'error');
        }
    });
}

function renderScopeField(label, id, currentValue, scopes, apiUrl) {
    const opts = scopes.map(s => {
        const name = typeof s === 'string' ? s : s.name;
        const count = typeof s === 'object' && s.count != null ? ` (${s.count})` : '';
        return `<option value="${name}" ${currentValue === name ? 'selected' : ''}>${name}${count}</option>`;
    }).join('');
    const addBtn = apiUrl ? `<button type="button" class="btn-sm sched-add-scope" data-api="${apiUrl}" title="New scope">+</button>` : '';
    return `
        <div class="sched-field">
            <label>${label}</label>
            <div style="display:flex;gap:8px">
                <select id="${id}" style="flex:1">
                    <option value="none" ${!currentValue || currentValue === 'none' ? 'selected' : ''}>None</option>
                    <option value="default" ${currentValue === 'default' ? 'selected' : ''}>default</option>
                    ${opts}
                </select>
                ${addBtn}
            </div>
        </div>`;
}

// ── Cron Helpers ──

function parseCron(cron) {
    if (!cron) return { mode: 'daily', time: '09:00' };
    const parts = cron.trim().split(/\s+/);
    if (parts.length !== 5) return { mode: 'cron', raw: cron };
    const [min, hour, dom, mon, dow] = parts;

    if (min.startsWith('*/') && hour === '*' && dom === '*' && mon === '*' && dow === '*')
        return { mode: 'interval', value: parseInt(min.slice(2)), unit: 'minutes' };
    if (min === '0' && hour.startsWith('*/') && dom === '*' && mon === '*' && dow === '*')
        return { mode: 'interval', value: parseInt(hour.slice(2)), unit: 'hours' };
    if (/^\d+$/.test(min) && /^\d+$/.test(hour) && dom === '*' && mon === '*') {
        const time = `${hour.padStart(2, '0')}:${min.padStart(2, '0')}`;
        if (dow === '*') return { mode: 'daily', time };
        const days = dow.split(',').map(Number).filter(d => !isNaN(d));
        if (days.length > 0) return { mode: 'weekly', time, days };
    }
    return { mode: 'cron', raw: cron };
}

function buildCron(mode, config) {
    switch (mode) {
        case 'daily': {
            const [h, m] = (config.time || '09:00').split(':');
            return `${parseInt(m)} ${parseInt(h)} * * *`;
        }
        case 'weekly': {
            const [h, m] = (config.time || '09:00').split(':');
            return `${parseInt(m)} ${parseInt(h)} * * ${config.days.sort((a,b) => a-b).join(',')}`;
        }
        case 'interval':
            if (config.unit === 'minutes') return `*/${config.value} * * * *`;
            return `0 */${config.value} * * *`;
        case 'cron': return config.raw || '0 9 * * *';
    }
}

function describeCron(cron) {
    if (!cron) return '';
    const parsed = parseCron(cron);
    const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    switch (parsed.mode) {
        case 'daily': return `Daily at ${formatTime12(parsed.time)}`;
        case 'weekly': return `${parsed.days.map(d => dayNames[d]).join(', ')} at ${formatTime12(parsed.time)}`;
        case 'interval': return `Every ${parsed.value} ${parsed.unit}`;
        default: return cron;
    }
}

function formatTime12(time24) {
    if (!time24) return '';
    const [h, m] = time24.split(':').map(Number);
    const ampm = h >= 12 ? 'PM' : 'AM';
    return `${h % 12 || 12}:${m.toString().padStart(2, '0')} ${ampm}`;
}

function hourOptions(selected) {
    return Array.from({ length: 24 }, (_, i) => {
        const label = i === 0 ? '12 AM' : i < 12 ? `${i} AM` : i === 12 ? '12 PM' : `${i - 12} PM`;
        return `<option value="${i}" ${i === selected ? 'selected' : ''}>${label}</option>`;
    }).join('');
}

function formatHourRange(start, end) {
    const fmt = h => {
        if (h === 0) return '12AM';
        if (h < 12) return `${h}AM`;
        if (h === 12) return '12PM';
        return `${h - 12}PM`;
    };
    return `${fmt(start)}\u2013${fmt(end)}`;
}

// ── General Helpers ──

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
