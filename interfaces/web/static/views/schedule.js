// views/schedule.js - Scheduler view
import { fetchTasks, fetchStatus, fetchTimeline, fetchActivity, createTask, updateTask, deleteTask, runTask, fetchPrompts, fetchToolsets, fetchLLMProviders, fetchMemoryScopes } from '../shared/continuity-api.js';
import * as ui from '../ui.js';

let container = null;
let tasks = [];
let status = {};
let timeline = [];
let activity = [];
let pollTimer = null;

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
        const [t, s, tl, a] = await Promise.all([
            fetchTasks(), fetchStatus(), fetchTimeline(24), fetchActivity(20)
        ]);
        tasks = t; status = s; timeline = tl; activity = a;
    } catch (e) { console.warn('Schedule load failed:', e); }
}

// ── Main Layout ──

function render() {
    if (!container) return;
    container.innerHTML = `
        <div class="sched-view">
            <div class="view-header">
                <div class="view-header-left">
                    <h2>Schedule</h2>
                    <span class="view-subtitle" id="sched-subtitle"></span>
                </div>
                <div class="view-header-actions">
                    <button class="btn-primary" id="sched-new">+ New Task</button>
                </div>
            </div>
            <div class="view-body view-scroll">
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
    if (tasksEl) tasksEl.innerHTML = renderTaskList();
    if (missionEl) missionEl.innerHTML = renderMission();
    if (subEl) subEl.innerHTML = `${status.enabled_tasks || 0}/${status.total_tasks || 0} tasks
        <span class="sched-status-dot ${status.running ? 'running' : 'stopped'} ${status.running ? 'pulse' : ''}"></span>
        ${status.running ? 'Running' : 'Stopped'}`;
}

function renderTaskList() {
    if (tasks.length === 0) {
        return `<div class="view-placeholder" style="padding:40px;text-align:center">
            <p style="color:var(--text-muted)">No tasks yet. Create one to get started.</p>
        </div>`;
    }
    const sorted = [...tasks].sort((a, b) => {
        if (a.heartbeat && !b.heartbeat) return -1;
        if (!a.heartbeat && b.heartbeat) return 1;
        return (a.name || '').localeCompare(b.name || '');
    });
    return sorted.map(t => {
        const sched = describeCron(t.schedule);
        const lastRun = t.last_run ? formatTime(t.last_run) : 'Never';
        let iterText = '';
        if (t.progress) {
            iterText = `<span class="sched-progress">${t.progress.iteration}/${t.progress.total} iters</span>`;
        } else if (t.running) {
            iterText = `<span class="sched-progress">Running...</span>`;
        } else if (t.iterations > 1) {
            iterText = `${t.iterations} iters`;
        }
        const meta = [
            t.chance < 100 ? `${t.chance}%` : '',
            iterText,
            t.chat_target ? `\u{1F4AC} ${escapeHtml(t.chat_target)}` : '',
            `Last: ${lastRun}`
        ].filter(Boolean).join(' \u00B7 ');

        return `
            <div class="sched-task-card${t.running ? ' running' : ''}">
                <label class="sched-toggle" title="${t.enabled ? 'Disable' : 'Enable'}">
                    <input type="checkbox" ${t.enabled ? 'checked' : ''} data-action="toggle" data-id="${t.id}">
                    <span class="toggle-slider"></span>
                </label>
                <div class="sched-task-info">
                    <div class="sched-task-name">${escapeHtml(t.name)}</div>
                    <div class="sched-task-schedule">${escapeHtml(sched)}</div>
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

// ── Mission Control (right panel) ──

function renderMission() {
    const hearts = ['\u2764\uFE0F', '\uD83E\uDE77', '\uD83E\uDDE1', '\uD83D\uDC9B', '\uD83D\uDC9A', '\uD83D\uDC99'];
    const hbTasks = tasks.filter(t => t.heartbeat);
    const nextHb = timeline.find(t => hbTasks.some(h => h.id === t.task_id));

    const today = new Date().toDateString();
    const todayActs = activity.filter(a => {
        try { return new Date(a.timestamp).toDateString() === today; } catch { return false; }
    });
    const ran = todayActs.filter(a => a.status === 'complete').length;
    const skipped = todayActs.filter(a => a.status === 'skipped').length;
    let todayStr = `${ran} ran`;
    if (skipped) todayStr += `, ${skipped} skipped`;

    return `
        <div class="sched-panel">
            <div class="sched-section-title">\u2764 Heartbeats</div>
            ${hbTasks.length === 0 ?
                '<div class="text-muted" style="font-size:var(--font-sm);padding:4px 0">No heartbeats yet — toggle \u2764\uFE0F in any task</div>' :
                hbTasks.slice(0, 6).map((t, i) => `
                    <div class="sched-hb-entry" data-action="edit" data-id="${t.id}">
                        <span class="sched-hb-heart">${hearts[i] || hearts[hearts.length - 1]}</span>
                        <span class="sched-hb-name">${escapeHtml(t.name)}</span>
                        <span class="sched-hb-status ${t.enabled ? 'active' : 'paused'}">${t.enabled ? describeCron(t.schedule) : 'Paused'}</span>
                    </div>
                `).join('')}
        </div>
        <div class="sched-panel">
            <div class="sched-section-title">Coming Up</div>
            ${timeline.length === 0 ? '<div class="text-muted" style="font-size:var(--font-sm)">Nothing in the next 24h</div>' :
            timeline.slice(0, 8).map(t => `
                <div class="sched-up-item">
                    <span class="sched-up-time">${formatTime(t.scheduled_for)}</span>
                    <span class="sched-up-name">${escapeHtml(t.task_name)}</span>
                    ${t.chance < 100 ? `<span class="sched-up-chance">${t.chance}%</span>` : ''}
                </div>
            `).join('')}
        </div>
        <div class="sched-panel">
            <div class="sched-section-title">Recent</div>
            ${activity.length === 0 ? '<div class="text-muted" style="font-size:var(--font-sm)">No activity yet</div>' :
            activity.slice(-8).reverse().map(a => `
                <div class="sched-rec-item">
                    <span class="sched-act-dot ${a.status}"></span>
                    <span class="sched-rec-time">${formatTime(a.timestamp)}</span>
                    <span class="sched-rec-name">${escapeHtml(a.task_name)}</span>
                    ${a.details?.reason ? `<span class="sched-rec-detail">(${escapeHtml(a.details.reason)})</span>` : ''}
                </div>
            `).join('')}
        </div>
    `;
}

// ── Events (bound once — fixes overlay stacking bug) ──

function bindEvents() {
    container.querySelector('#sched-new')?.addEventListener('click', () => openEditor(null));

    const layout = container.querySelector('.sched-layout');
    if (!layout) return;

    layout.addEventListener('click', async e => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        const { action, id } = btn.dataset;

        if (action === 'edit') {
            const task = tasks.find(t => t.id === id);
            if (task) openEditor(task);
        } else if (action === 'run') {
            const task = tasks.find(t => t.id === id);
            if (!task || !confirm(`Run "${task.name}" now?`)) return;
            try {
                await runTask(id);
                ui.showToast(`Running: ${task.name}`, 'success');
                await loadData(); updateContent();
            } catch { ui.showToast('Run failed', 'error'); }
        } else if (action === 'delete') {
            const task = tasks.find(t => t.id === id);
            if (!task || !confirm(`Delete "${task.name}"?`)) return;
            try {
                await deleteTask(id);
                ui.showToast('Deleted', 'success');
                await loadData(); updateContent();
            } catch { ui.showToast('Delete failed', 'error'); }
        }
    });

    layout.addEventListener('change', async e => {
        if (e.target.dataset.action === 'toggle') {
            const id = e.target.dataset.id;
            const task = tasks.find(t => t.id === id);
            if (!task) return;
            try {
                await updateTask(id, { enabled: !task.enabled });
                await loadData(); updateContent();
            } catch { ui.showToast('Toggle failed', 'error'); }
        }
    });
}

// ── Task Editor Modal ──

async function openEditor(task) {
    // Kill any existing editor (prevents stacking)
    document.querySelector('.sched-editor-overlay')?.remove();

    let prompts = [], toolsetsList = [], llmProviders = [], llmMetadata = {}, memoryScopes = [];
    try {
        const [p, ts, llm, scopes] = await Promise.all([
            fetchPrompts(), fetchToolsets(), fetchLLMProviders(), fetchMemoryScopes()
        ]);
        prompts = p || [];
        toolsetsList = ts || [];
        llmProviders = llm.providers || [];
        llmMetadata = llm.metadata || {};
        memoryScopes = scopes || [];
    } catch (e) { console.warn('Editor: failed to fetch options', e); }

    const isEdit = !!task;
    const t = task || {};
    const parsed = t.schedule ? parseCron(t.schedule) : { mode: 'daily', time: '09:00' };

    const providerOpts = llmProviders
        .filter(p => p.enabled)
        .map(p => `<option value="${p.key}" ${t.provider === p.key ? 'selected' : ''}>${p.display_name}${p.is_local ? ' \u{1F3E0}' : ' \u2601\uFE0F'}</option>`)
        .join('');

    const scopeOpts = memoryScopes
        .map(s => `<option value="${s.name}" ${t.memory_scope === s.name ? 'selected' : ''}>${s.name} (${s.count})</option>`)
        .join('');

    const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    const selectedDays = parsed.mode === 'weekly' ? parsed.days : [1, 2, 3, 4, 5];
    const dayChecks = dayNames.map((name, i) =>
        `<label class="sched-day-label"><input type="checkbox" value="${i}" ${selectedDays.includes(i) ? 'checked' : ''}> ${name}</label>`
    ).join('');

    const currentTime = parsed.time || '09:00';
    const intervalValue = parsed.mode === 'interval' ? parsed.value : 4;
    const intervalUnit = parsed.mode === 'interval' ? parsed.unit : 'hours';
    const cronRaw = t.schedule || '0 9 * * *';

    const modal = document.createElement('div');
    modal.className = 'sched-editor-overlay';
    modal.innerHTML = `
        <div class="sched-editor">
            <div class="sched-editor-header">
                <h3>${isEdit ? 'Edit Task' : 'New Task'}</h3>
                <span class="sched-header-divider"></span>
                <label class="sched-hb-toggle help-tip" data-tip="Track this task in the Heartbeats panel on your dashboard. Heartbeats are your AI's vital signs — see at a glance what's alive and running.">
                    <input type="checkbox" id="ed-heartbeat" ${t.heartbeat ? 'checked' : ''}>
                    <span>\u2764\uFE0F</span>
                </label>
                <div style="flex:1"></div>
                <button class="btn-icon" data-action="close">&times;</button>
            </div>
            <div class="sched-editor-body">
                <div class="sched-field">
                    <label>Task Name <span class="help-tip" data-tip="Give this task a name so you can find it later. Shows in the task list, activity log, and heartbeat panel.">?</span></label>
                    <input type="text" id="ed-name" value="${escapeHtml(t.name || '')}" placeholder="Morning Greeting">
                </div>
                <div class="sched-field">
                    <label>Initial Message <span class="help-tip" data-tip="This is what the AI receives when the task runs. Be elaborate — give full context, instructions, and expectations. The AI only knows what you put here, so more detail means better results.">?</span></label>
                    <textarea id="ed-message" rows="2" placeholder="What should the AI receive?">${escapeHtml(t.initial_message || '')}</textarea>
                </div>

                <details class="sched-accordion" ${!isEdit ? 'open' : ''}>
                    <summary class="sched-acc-header">When <span class="sched-preview">${describeCron(t.schedule || '0 9 * * *')}</span></summary>
                    <div class="sched-acc-body"><div class="sched-acc-inner">
                        <div class="sched-picker">
                            <div class="sched-picker-tabs">
                                <button type="button" class="sched-picker-tab${parsed.mode === 'daily' ? ' active' : ''}" data-mode="daily">Daily</button>
                                <button type="button" class="sched-picker-tab${parsed.mode === 'weekly' ? ' active' : ''}" data-mode="weekly">Weekly</button>
                                <button type="button" class="sched-picker-tab${parsed.mode === 'interval' ? ' active' : ''}" data-mode="interval">Interval</button>
                                <button type="button" class="sched-picker-tab${parsed.mode === 'cron' ? ' active' : ''}" data-mode="cron">Cron</button>
                            </div>
                            <div class="sched-pick-panel" data-panel="weekly" ${parsed.mode !== 'weekly' ? 'style="display:none"' : ''}>
                                <div class="sched-days">${dayChecks}</div>
                            </div>
                            <div class="sched-when-row">
                                <div class="sched-pick-panel" data-panel="daily" ${parsed.mode !== 'daily' ? 'style="display:none"' : ''}>
                                    <label>Time</label>
                                    <input type="time" id="ed-time-daily" value="${currentTime}">
                                </div>
                                <div class="sched-pick-panel" data-panel="weekly" ${parsed.mode !== 'weekly' ? 'style="display:none"' : ''}>
                                    <label>Time</label>
                                    <input type="time" id="ed-time-weekly" value="${currentTime}">
                                </div>
                                <div class="sched-pick-panel" data-panel="interval" ${parsed.mode !== 'interval' ? 'style="display:none"' : ''}>
                                    <label>Every</label>
                                    <div style="display:flex;gap:6px;align-items:center">
                                        <input type="number" id="ed-interval-val" value="${intervalValue}" min="1" style="width:60px">
                                        <select id="ed-interval-unit">
                                            <option value="minutes" ${intervalUnit === 'minutes' ? 'selected' : ''}>min</option>
                                            <option value="hours" ${intervalUnit === 'hours' ? 'selected' : ''}>hrs</option>
                                        </select>
                                    </div>
                                </div>
                                <div class="sched-pick-panel" data-panel="cron" ${parsed.mode !== 'cron' ? 'style="display:none"' : ''}>
                                    <label>Cron</label>
                                    <input type="text" id="ed-cron-raw" value="${escapeHtml(cronRaw)}" placeholder="0 9 * * *" style="width:120px">
                                </div>
                            </div>
                            <div class="sched-when-row" style="margin-top:10px">
                                <div class="sched-field">
                                    <label>Chance <span class="help-tip" data-tip="Roll the dice each time this fires. At 50%, the task only runs half the time — great for variety so the AI doesn't feel robotic. 100% = always runs.">?</span></label>
                                    <div style="display:flex;align-items:center;gap:4px">
                                        <input type="number" id="ed-chance" value="${t.chance ?? 100}" min="1" max="100" style="width:60px">
                                        <span class="text-muted">%</span>
                                    </div>
                                </div>
                                <div class="sched-field">
                                    <label>Cooldown <span class="help-tip" data-tip="Prevents the task from running again too soon. If the schedule fires every minute but cooldown is 30, it waits at least 30 minutes between runs.">?</span></label>
                                    <div style="display:flex;align-items:center;gap:4px">
                                        <input type="number" id="ed-cooldown" value="${t.cooldown_minutes ?? 1}" min="0" style="width:60px">
                                        <span class="text-muted">min</span>
                                    </div>
                                </div>
                                <div class="sched-field">
                                    <label>Iterations <span class="help-tip" data-tip="How many back-and-forth turns the AI gets per run. 1 = single response. Higher values let the AI use tools and follow up on its own work.">?</span></label>
                                    <input type="number" id="ed-iterations" value="${t.iterations ?? 1}" min="1" max="10" style="width:60px">
                                </div>
                            </div>
                        </div>
                    </div></div>
                </details>

                <details class="sched-accordion">
                    <summary class="sched-acc-header">Who</summary>
                    <div class="sched-acc-body"><div class="sched-acc-inner">
                        <div class="sched-field-row">
                            <div class="sched-field">
                                <label>Prompt <span class="help-tip" data-tip="Choose which personality or system prompt the AI uses. 'default' uses your main prompt. Pick a custom one to give the AI a different persona for this task.">?</span></label>
                                <select id="ed-prompt">
                                    <option value="default">default</option>
                                    ${prompts.map(p => `<option value="${p.name}" ${t.prompt === p.name ? 'selected' : ''}>${p.name}</option>`).join('')}
                                </select>
                            </div>
                            <div class="sched-field">
                                <label>Toolset <span class="help-tip" data-tip="Which set of tools the AI can call — web search, file access, smart home, etc. 'none' means no tools, just conversation. 'default' uses your main toolset.">?</span></label>
                                <select id="ed-toolset">
                                    <option value="none" ${t.toolset === 'none' ? 'selected' : ''}>none</option>
                                    <option value="default" ${t.toolset === 'default' ? 'selected' : ''}>default</option>
                                    ${toolsetsList.map(ts => `<option value="${ts.name}" ${t.toolset === ts.name ? 'selected' : ''}>${ts.name}</option>`).join('')}
                                </select>
                            </div>
                        </div>
                        <div class="sched-field-row">
                            <div class="sched-field">
                                <label>Provider <span class="help-tip" data-tip="Pick which AI provider runs this task — OpenAI, Claude, local models, etc. 'Auto' uses your default. Choose a specific one to control cost or capability per task.">?</span></label>
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
                                <input type="text" id="ed-model-custom" value="${escapeHtml(t.model || '')}" placeholder="Model name">
                            </div>
                        </div>
                        <div class="sched-field">
                            <label>Memory Scope <span class="help-tip" data-tip="Memory lets the AI remember things between runs. 'default' shares memory with your main chat. Pick a separate scope to give this task its own memory. 'None' = no memory at all.">?</span></label>
                            <div style="display:flex;gap:8px">
                                <select id="ed-memory" style="flex:1">
                                    <option value="none" ${t.memory_scope === 'none' ? 'selected' : ''}>None (disabled)</option>
                                    <option value="default" ${t.memory_scope === 'default' ? 'selected' : ''}>default</option>
                                    ${scopeOpts}
                                </select>
                                <button class="btn-sm" id="ed-add-scope" title="New scope">+</button>
                            </div>
                        </div>
                    </div></div>
                </details>

                <details class="sched-accordion">
                    <summary class="sched-acc-header">Chat</summary>
                    <div class="sched-acc-body"><div class="sched-acc-inner">
                        <div class="sched-field">
                            <label>Chat Name <span class="help-tip" data-tip="Enter a name to run this task in a specific chat — the conversation is saved and visible in the UI. Leave blank to run as a one-off background task with no history.">?</span></label>
                            <input type="text" id="ed-chat" value="${escapeHtml(t.chat_target || '')}" placeholder="Leave blank for ephemeral">
                        </div>
                        <div class="sched-field-row">
                            <div class="sched-checkbox">
                                <label><input type="checkbox" id="ed-tts" ${t.tts_enabled !== false ? 'checked' : ''}> Enable TTS <span class="help-tip" data-tip="When enabled, the AI speaks its response out loud through your speakers. Turn off for silent background tasks.">?</span></label>
                            </div>
                            <div class="sched-checkbox">
                                <label><input type="checkbox" id="ed-datetime" ${t.inject_datetime ? 'checked' : ''}> Inject date/time <span class="help-tip" data-tip="Tells the AI what day and time it is right now. Useful for tasks like 'good morning' greetings or anything time-aware.">?</span></label>
                            </div>
                        </div>
                    </div></div>
                </details>
            </div>
            <div class="sched-editor-footer">
                <button class="btn-sm" data-action="close">Cancel</button>
                <button class="btn-primary" id="ed-save">Save Task</button>
            </div>
        </div>
    `;

    document.body.appendChild(modal);

    // Tooltips (JS-based to escape overflow containers)
    const tipEl = document.createElement('div');
    tipEl.className = 'help-tip-popup';
    document.body.appendChild(tipEl);
    modal.addEventListener('mouseover', e => {
        const tip = e.target.closest('.help-tip');
        if (!tip) return;
        const text = tip.dataset.tip;
        if (!text) return;
        tipEl.textContent = text;
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

    // Schedule picker mode switching
    let currentMode = parsed.mode;
    modal.querySelector('.sched-picker-tabs')?.addEventListener('click', e => {
        const tab = e.target.closest('.sched-picker-tab');
        if (!tab) return;
        currentMode = tab.dataset.mode;
        modal.querySelectorAll('.sched-picker-tab').forEach(t => t.classList.toggle('active', t.dataset.mode === currentMode));
        modal.querySelectorAll('.sched-pick-panel').forEach(p => p.style.display = p.dataset.panel === currentMode ? '' : 'none');
        updatePreview();
    });

    const getCurrentCron = () => {
        switch (currentMode) {
            case 'daily': return buildCron('daily', { time: modal.querySelector('#ed-time-daily')?.value || '09:00' });
            case 'weekly': {
                const days = [...modal.querySelectorAll('.sched-days input:checked')].map(c => parseInt(c.value));
                if (days.length === 0) return buildCron('daily', { time: modal.querySelector('#ed-time-weekly')?.value || '09:00' });
                return buildCron('weekly', { time: modal.querySelector('#ed-time-weekly')?.value || '09:00', days });
            }
            case 'interval': return buildCron('interval', {
                value: parseInt(modal.querySelector('#ed-interval-val')?.value) || 4,
                unit: modal.querySelector('#ed-interval-unit')?.value || 'hours'
            });
            case 'cron': return modal.querySelector('#ed-cron-raw')?.value || '0 9 * * *';
        }
    };

    const updatePreview = () => {
        const text = describeCron(getCurrentCron());
        modal.querySelectorAll('.sched-preview').forEach(el => el.textContent = text);
    };

    // Live preview updates
    modal.querySelectorAll('#ed-time-daily, #ed-time-weekly, #ed-interval-val, #ed-interval-unit, #ed-cron-raw')
        .forEach(el => el.addEventListener('input', updatePreview));
    modal.querySelectorAll('.sched-days input')
        .forEach(el => el.addEventListener('change', updatePreview));

    // Provider → model logic
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

    // Add memory scope
    modal.querySelector('#ed-add-scope')?.addEventListener('click', async () => {
        const name = prompt('New memory slot name (lowercase, no spaces):');
        if (!name) return;
        const clean = name.trim().toLowerCase().replace(/[^a-z0-9_]/g, '');
        if (!clean || clean.length > 32) { alert('Invalid name'); return; }
        try {
            const res = await fetch('/api/memory/scopes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: clean })
            });
            if (res.ok) {
                const sel = modal.querySelector('#ed-memory');
                const opt = document.createElement('option');
                opt.value = clean;
                opt.textContent = `${clean} (0)`;
                sel.appendChild(opt);
                sel.value = clean;
            } else {
                const err = await res.json();
                alert(err.error || 'Failed');
            }
        } catch { alert('Failed to create scope'); }
    });

    // Save
    modal.querySelector('#ed-save')?.addEventListener('click', async () => {
        const modelField = modal.querySelector('#ed-model-field');
        const modelSel = modal.querySelector('#ed-model');
        const modelCustom = modal.querySelector('#ed-model-custom');
        let modelValue = '';
        if (modelField?.style.display !== 'none') modelValue = modelSel?.value || '';
        else if (modal.querySelector('#ed-model-custom-field')?.style.display !== 'none') modelValue = modelCustom?.value?.trim() || '';

        const data = {
            name: modal.querySelector('#ed-name').value.trim(),
            schedule: getCurrentCron(),
            chance: parseInt(modal.querySelector('#ed-chance').value) || 100,
            cooldown_minutes: parseInt(modal.querySelector('#ed-cooldown').value) || 1,
            iterations: parseInt(modal.querySelector('#ed-iterations').value) || 1,
            initial_message: modal.querySelector('#ed-message').value.trim() || 'Hello.',
            chat_target: modal.querySelector('#ed-chat').value.trim(),
            prompt: modal.querySelector('#ed-prompt').value,
            toolset: modal.querySelector('#ed-toolset').value,
            provider: modal.querySelector('#ed-provider').value,
            model: modelValue,
            memory_scope: modal.querySelector('#ed-memory').value,
            tts_enabled: modal.querySelector('#ed-tts').checked,
            inject_datetime: modal.querySelector('#ed-datetime').checked,
            heartbeat: modal.querySelector('#ed-heartbeat').checked
        };

        if (!data.name) { alert('Task name is required'); return; }
        if (!data.schedule) { alert('Schedule is required'); return; }

        try {
            if (isEdit) await updateTask(task.id, data);
            else await createTask(data);
            ui.showToast(isEdit ? 'Task updated' : 'Task created', 'success');
            close();
            await loadData();
            updateContent();
        } catch (e) {
            ui.showToast(e.message || 'Save failed', 'error');
        }
    });
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

// ── General Helpers ──

function timeAgo(isoString) {
    if (!isoString) return 'Unknown';
    try {
        const diff = Date.now() - new Date(isoString).getTime();
        if (diff < 60000) return 'Just now';
        if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
        if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
        return `${Math.floor(diff / 86400000)}d ago`;
    } catch { return 'Unknown'; }
}

function formatTime(isoString) {
    if (!isoString) return 'Unknown';
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

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
