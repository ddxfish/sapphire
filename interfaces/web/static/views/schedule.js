// views/schedule.js - Continuity task scheduler view
import { fetchTasks, fetchStatus, fetchTimeline, fetchActivity, createTask, updateTask, deleteTask, runTask, fetchPrompts, fetchToolsets, fetchLLMProviders, fetchMemoryScopes } from '../shared/continuity-api.js';
import * as ui from '../ui.js';

let container = null;
let activeTab = 'tasks';
let tasks = [];
let status = {};
let timeline = [];
let activity = [];
let pollTimer = null;

export default {
    init(el) {
        container = el;
    },

    async show() {
        await loadData();
        render();
        startPolling();
    },

    hide() {
        stopPolling();
    }
};

function startPolling() {
    stopPolling();
    pollTimer = setInterval(async () => {
        await loadData();
        renderContent();
    }, 5000);
}

function stopPolling() {
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
}

async function loadData() {
    try {
        const [t, s, tl, a] = await Promise.all([
            fetchTasks(),
            fetchStatus(),
            fetchTimeline(24),
            fetchActivity(20)
        ]);
        tasks = t;
        status = s;
        timeline = tl;
        activity = a;
    } catch (e) {
        console.warn('Schedule load failed:', e);
    }
}

function render() {
    if (!container) return;

    container.innerHTML = `
        <div class="sched-view">
            <div class="view-header">
                <div class="view-header-left">
                    <h2>Schedule</h2>
                    <span class="view-subtitle">${status.enabled_tasks || 0}/${status.total_tasks || 0} tasks enabled
                        <span class="sched-status-dot ${status.running ? 'running' : 'stopped'}"></span>
                        ${status.running ? 'Running' : 'Stopped'}
                    </span>
                </div>
                <div class="view-header-actions">
                    <button class="btn-primary" id="sched-new">+ New Task</button>
                </div>
            </div>
            <div class="sched-tabs">
                <button class="sched-tab${activeTab === 'tasks' ? ' active' : ''}" data-tab="tasks">Tasks</button>
                <button class="sched-tab${activeTab === 'timeline' ? ' active' : ''}" data-tab="timeline">Timeline</button>
                <button class="sched-tab${activeTab === 'activity' ? ' active' : ''}" data-tab="activity">Activity</button>
            </div>
            <div class="view-body view-scroll" id="sched-content"></div>
        </div>
    `;

    renderContent();
    bindEvents();
}

function renderContent() {
    const content = container?.querySelector('#sched-content');
    if (!content) return;

    // Update status dot
    const dot = container.querySelector('.sched-status-dot');
    if (dot) {
        dot.className = `sched-status-dot ${status.running ? 'running' : 'stopped'}`;
    }

    switch (activeTab) {
        case 'tasks': content.innerHTML = renderTasks(); break;
        case 'timeline': content.innerHTML = renderTimeline(); break;
        case 'activity': content.innerHTML = renderActivity(); break;
    }

    // Re-bind content events
    bindContentEvents();
}

function renderTasks() {
    if (tasks.length === 0) {
        return `<div class="view-placeholder" style="padding:40px;text-align:center">
            <p style="color:var(--text-muted)">No tasks yet. Create one to get started.</p>
        </div>`;
    }

    return `<div class="sched-task-list">
        ${tasks.map(t => {
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
                t.memory_scope && t.memory_scope !== 'none' ? `\u{1F4BE} ${t.memory_scope}` : '',
                `Last: ${lastRun}`
            ].filter(Boolean).join(' \u2022 ');

            return `
                <div class="sched-task-card${t.running ? ' running' : ''}">
                    <label class="sched-toggle" title="${t.enabled ? 'Disable' : 'Enable'}">
                        <input type="checkbox" ${t.enabled ? 'checked' : ''} data-action="toggle" data-id="${t.id}">
                        <span class="toggle-slider"></span>
                    </label>
                    <div class="sched-task-info">
                        <div class="sched-task-name">${escapeHtml(t.name)}</div>
                        <div class="sched-task-cron">${escapeHtml(t.schedule)}</div>
                        <div class="sched-task-meta">${meta}</div>
                    </div>
                    <div class="sched-task-actions">
                        <button class="btn-icon" data-action="run" data-id="${t.id}" title="Run now">\u25B6</button>
                        <button class="btn-icon" data-action="edit" data-id="${t.id}" title="Edit">\u270F\uFE0F</button>
                        <button class="btn-icon danger" data-action="delete" data-id="${t.id}" title="Delete">\u2715</button>
                    </div>
                </div>
            `;
        }).join('')}
    </div>`;
}

function renderTimeline() {
    if (timeline.length === 0) {
        return `<div class="view-placeholder" style="padding:40px;text-align:center">
            <p style="color:var(--text-muted)">No tasks scheduled in the next 24 hours</p>
        </div>`;
    }

    return `<div class="sched-timeline">
        ${timeline.map(t => `
            <div class="sched-tl-item">
                <div class="sched-tl-dot"></div>
                <div class="sched-tl-time">${formatTime(t.scheduled_for)}</div>
                <div class="sched-tl-name">${escapeHtml(t.task_name)}</div>
                ${t.chance < 100 ? `<div class="sched-tl-chance">${t.chance}%</div>` : ''}
            </div>
        `).join('')}
    </div>`;
}

function renderActivity() {
    if (activity.length === 0) {
        return `<div class="view-placeholder" style="padding:40px;text-align:center">
            <p style="color:var(--text-muted)">No activity yet</p>
        </div>`;
    }

    return `<div class="sched-activity">
        ${activity.slice().reverse().map(a => `
            <div class="sched-act-item">
                <div class="sched-act-dot ${a.status}"></div>
                <div class="sched-act-time">${formatTime(a.timestamp)}</div>
                <div class="sched-act-name">${escapeHtml(a.task_name)}</div>
                <div class="sched-act-detail">${a.status}${a.details?.reason ? ` (${a.details.reason})` : ''}</div>
            </div>
        `).join('')}
    </div>`;
}

function bindEvents() {
    // Tab switching
    container.querySelector('.sched-tabs')?.addEventListener('click', e => {
        const tab = e.target.dataset.tab;
        if (!tab) return;
        activeTab = tab;
        container.querySelectorAll('.sched-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
        renderContent();
    });

    // New task
    container.querySelector('#sched-new')?.addEventListener('click', () => openEditor(null));
}

function bindContentEvents() {
    const content = container?.querySelector('#sched-content');
    if (!content) return;

    content.addEventListener('click', async e => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;

        const action = btn.dataset.action;
        const id = btn.dataset.id;

        if (action === 'edit') {
            const task = tasks.find(t => t.id === id);
            if (task) openEditor(task);
        } else if (action === 'run') {
            const task = tasks.find(t => t.id === id);
            if (!task || !confirm(`Run "${task.name}" now?`)) return;
            try {
                await runTask(id);
                ui.showToast(`Running: ${task.name}`, 'success');
                await loadData();
                renderContent();
            } catch (e) { ui.showToast('Run failed', 'error'); }
        } else if (action === 'delete') {
            const task = tasks.find(t => t.id === id);
            if (!task || !confirm(`Delete "${task.name}"?`)) return;
            try {
                await deleteTask(id);
                ui.showToast('Deleted', 'success');
                await loadData();
                renderContent();
            } catch (e) { ui.showToast('Delete failed', 'error'); }
        }
    });

    content.addEventListener('change', async e => {
        if (e.target.dataset.action === 'toggle') {
            const id = e.target.dataset.id;
            const task = tasks.find(t => t.id === id);
            if (!task) return;
            try {
                await updateTask(id, { enabled: !task.enabled });
                await loadData();
                renderContent();
            } catch (err) { ui.showToast('Toggle failed', 'error'); }
        }
    });
}

// ── Task Editor Modal ──

async function openEditor(task) {
    // Fetch dropdown data
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
    } catch (e) {
        console.warn('Editor: failed to fetch options', e);
    }

    const isEdit = !!task;
    const t = task || {};

    const providerOpts = llmProviders
        .filter(p => p.enabled)
        .map(p => `<option value="${p.key}" ${t.provider === p.key ? 'selected' : ''}>${p.display_name}${p.is_local ? ' \u{1F3E0}' : ' \u2601\uFE0F'}</option>`)
        .join('');

    const scopeOpts = memoryScopes
        .map(s => `<option value="${s.name}" ${t.memory_scope === s.name ? 'selected' : ''}>${s.name} (${s.count})</option>`)
        .join('');

    const modal = document.createElement('div');
    modal.className = 'sched-editor-overlay';
    modal.innerHTML = `
        <div class="sched-editor">
            <div class="sched-editor-header">
                <h3>${isEdit ? 'Edit Task' : 'New Task'}</h3>
                <button class="btn-icon" data-action="close">&times;</button>
            </div>
            <div class="sched-editor-body">
                <div class="sched-field">
                    <label>Task Name *</label>
                    <input type="text" id="ed-name" value="${escapeHtml(t.name || '')}" placeholder="Morning Greeting">
                </div>
                <div class="sched-field">
                    <label>Schedule (Cron) *</label>
                    <input type="text" id="ed-schedule" value="${t.schedule || '0 9 * * *'}" placeholder="0 9 * * *">
                    <span class="sched-hint">minute hour day month weekday — e.g. "0 9 * * *" = 9 AM daily</span>
                </div>
                <div class="sched-field-row">
                    <div class="sched-field">
                        <label>Chance (%)</label>
                        <input type="number" id="ed-chance" value="${t.chance ?? 100}" min="1" max="100">
                    </div>
                    <div class="sched-field">
                        <label>Cooldown (min)</label>
                        <input type="number" id="ed-cooldown" value="${t.cooldown_minutes ?? 1}" min="0">
                    </div>
                    <div class="sched-field">
                        <label>Iterations</label>
                        <input type="number" id="ed-iterations" value="${t.iterations ?? 1}" min="1" max="10">
                    </div>
                </div>
                <div class="sched-field">
                    <label>Initial Message</label>
                    <textarea id="ed-message" rows="2" placeholder="What should the AI receive?">${escapeHtml(t.initial_message || '')}</textarea>
                </div>
                <div class="sched-field">
                    <label>Chat Name</label>
                    <input type="text" id="ed-chat" value="${escapeHtml(t.chat_target || '')}" placeholder="Leave blank for ephemeral">
                    <span class="sched-hint">Blank = ephemeral (background). Named = persists to that chat.</span>
                </div>
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
                        <label>LLM Provider</label>
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
                    <label>Memory Scope</label>
                    <div style="display:flex;gap:8px">
                        <select id="ed-memory" style="flex:1">
                            <option value="none" ${t.memory_scope === 'none' ? 'selected' : ''}>None (disabled)</option>
                            <option value="default" ${!t.memory_scope || t.memory_scope === 'default' ? 'selected' : ''}>default</option>
                            ${scopeOpts}
                        </select>
                        <button class="btn-sm" id="ed-add-scope" title="New scope">+</button>
                    </div>
                </div>
                <div class="sched-checkbox">
                    <label><input type="checkbox" id="ed-tts" ${t.tts_enabled !== false ? 'checked' : ''}> Enable TTS (speak responses)</label>
                </div>
                <div class="sched-checkbox">
                    <label><input type="checkbox" id="ed-datetime" ${t.inject_datetime ? 'checked' : ''}> Inject date/time in system prompt</label>
                </div>
            </div>
            <div class="sched-editor-footer">
                <button class="btn-sm" data-action="close">Cancel</button>
                <button class="btn-primary" id="ed-save">Save Task</button>
            </div>
        </div>
    `;

    document.body.appendChild(modal);

    // Close handlers
    const close = () => modal.remove();
    modal.addEventListener('click', e => { if (e.target === modal) close(); });
    modal.querySelectorAll('[data-action="close"]').forEach(b => b.addEventListener('click', close));

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

    // Add scope
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
            schedule: modal.querySelector('#ed-schedule').value.trim(),
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
            inject_datetime: modal.querySelector('#ed-datetime').checked
        };

        if (!data.name) { alert('Task name is required'); return; }
        if (!data.schedule) { alert('Schedule is required'); return; }

        try {
            if (isEdit) {
                await updateTask(task.id, data);
            } else {
                await createTask(data);
            }
            ui.showToast(isEdit ? 'Task updated' : 'Task created', 'success');
            close();
            await loadData();
            renderContent();
        } catch (e) {
            ui.showToast(e.message || 'Save failed', 'error');
        }
    });
}

// ── Helpers ──

function formatTime(isoString) {
    if (!isoString) return 'Unknown';
    try {
        const d = new Date(isoString);
        const now = new Date();
        if (d.toDateString() === now.toDateString()) {
            return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        }
        const yesterday = new Date(now);
        yesterday.setDate(yesterday.getDate() - 1);
        if (d.toDateString() === yesterday.toDateString()) {
            return 'Yesterday ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        }
        if (now - d < 7 * 24 * 60 * 60 * 1000) {
            return d.toLocaleDateString([], { weekday: 'short' }) + ' ' +
                   d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        }
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
