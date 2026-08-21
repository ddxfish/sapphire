// shared/trigger-common.js - Shared renderers, helpers and actions for the
// Triggers group views (Heartbeat / Scheduled / Daemons / Webhooks).
//
// Factored verbatim out of the old monolithic views/schedule.js so the four
// sibling views stay thin and render identically (reusing the existing sched-*
// CSS). View-specific glue (what data to load, surgical poll updates) stays in
// each view; everything reusable lives here.
import { createTask, updateTask, deleteTask, runTask } from './continuity-api.js';
import { openTriggerEditor } from './trigger-editor/editor.js';
import { describeCron } from './trigger-editor/trigger-cron.js';
import { showExportDialog, showImportDialog } from './import-export.js';
import { getInitData } from './init-data.js';
import { fetchScopeData } from './scope-dropdowns.js';
import * as ui from '../ui.js';

// Tab config for the Triggers section (consumed by section-tabs/section-header).
export const TRIGGER_TABS = [
    { id: 'heartbeat', label: 'Heartbeat', icon: '💓' },
    { id: 'scheduled', label: 'Scheduled', icon: '📅' },
    { id: 'daemons', label: 'Daemons', icon: '📡' },
    { id: 'realtime', label: 'Realtime', icon: '⚡' },
    { id: 'webhooks', label: 'Webhooks', icon: '🔗' },
];

// ── Formatters ───────────────────────────────────────────────────────

export function esc(str) {
    // Quote-escaping included — used in attribute interpolations; same
    // class as the modal.js fix (2026-07-19).
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export function chatChip(target) {
    // '__locked__' is the server's sealed-vault mask for a private chat's
    // name (never the real value; the scheduler treats it as keep-existing
    // on write). Render it honestly instead of leaking the sentinel.
    if (!target) return '';
    if (target === '__locked__') return '💬 🔒 <em>locked vault</em>';
    return `💬 ${esc(target)}`;
}

export function formatHourRange(start, end) {
    const fmt = h => {
        if (h === 0) return '12AM';
        if (h < 12) return `${h}AM`;
        if (h === 12) return '12PM';
        return `${h - 12}PM`;
    };
    return `${fmt(start)}–${fmt(end)}`;
}

export function timeAgo(isoString) {
    if (!isoString) return '';
    try {
        const diff = Date.now() - new Date(isoString).getTime();
        if (diff < 60000) return 'just now';
        if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
        if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
        return `${Math.floor(diff / 86400000)}d ago`;
    } catch { return ''; }
}

export function timeUntil(isoString) {
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

export function formatTime(isoString) {
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

// ── Heartbeat helpers (timeline is the merged-timeline object) ────────

export function getHeartbeatState(hb, timeline) {
    if (!hb.enabled) return { label: 'Flatlined', cls: 'flatlined' };
    if (hb.running) return { label: 'Ba-bump', cls: 'babump' };
    if (!hb.last_run) return { label: 'Warming up', cls: 'warmup' };
    const recent = (timeline.past || []).filter(a => a.task_id === hb.id);
    if (recent.length > 0 && recent[0].status === 'error') return { label: 'Irregular', cls: 'irregular' };
    return { label: 'Beating', cls: 'beating' };
}

export function getBeatsForTask(timeline, taskId, count) {
    const all = (timeline.past || []).filter(a => a.task_id === taskId);
    return all.slice(0, count).reverse().map(a => a.status || 'complete');
}

export function getNextIn(timeline, taskId) {
    const next = (timeline.future || []).find(f => f.task_id === taskId);
    if (!next?.scheduled_for) return null;
    return timeUntil(next.scheduled_for);
}

export function renderHeatmap(beats) {
    const MAX = 20;
    const empty = MAX - beats.length;
    const blocks = [];
    for (let i = 0; i < empty; i++) blocks.push('<span class="hb-block empty"></span>');
    for (const s of beats) blocks.push(`<span class="hb-block ${s}"></span>`);
    return `<div class="hb-heatmap">${blocks.join('')}</div>`;
}

// ── Status row (row 2, under the tab strip) ───────────────────────────

export function statusRow({ enabled, total, running, desc }) {
    const dot = `<span class="sched-status-dot ${running ? 'running' : 'stopped'} ${running ? 'pulse' : ''}"></span>`;
    return `<div class="trigger-status">
        <span class="trigger-status-count">${enabled}/${total} enabled</span>
        ${desc ? `<span class="trigger-status-desc">${esc(desc)}</span>` : ''}
        <span class="trigger-status-run">${dot}${running ? 'Running' : 'Stopped'}</span>
    </div>`;
}

// ── Card renderers ────────────────────────────────────────────────────

export function renderTaskCard(t) {
    const sched = describeCron(t.schedule);
    const lastRun = t.last_run ? formatTime(t.last_run) : 'Never';
    const isPlugin = (t.source || '').startsWith('plugin:');
    const pluginName = isPlugin ? t.source.replace('plugin:', '') : '';
    let statusText = '';
    if (t.running) statusText = `<span class="sched-progress">Running...</span>`;
    const meta = [
        isPlugin ? `<span class="sched-plugin-badge" title="Managed by ${esc(pluginName)} plugin">${esc(pluginName)}</span>` : '',
        t.chance < 100 ? `${t.chance}%` : '',
        t.active_hours_start != null ? `🕓 ${formatHourRange(t.active_hours_start, t.active_hours_end)}` : '',
        statusText,
        chatChip(t.chat_target),
        `Last: ${lastRun}`
    ].filter(Boolean).join(' · ');

    const actions = isPlugin
        ? `<button class="btn-icon" data-action="run" data-id="${t.id}" title="Run now">▶</button>`
        : `<button class="btn-icon" data-action="run" data-id="${t.id}" title="Run now">▶</button>
           <button class="btn-icon" data-action="export" data-id="${t.id}" title="Export">⇩</button>
           <button class="btn-icon" data-action="edit" data-id="${t.id}" title="Edit">✏️</button>
           <button class="btn-icon danger" data-action="delete" data-id="${t.id}" title="Delete">✕</button>`;

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
}

export function renderTaskList(tasks, emptyMsg = 'No tasks yet. Create one to get started.') {
    if (!tasks.length) {
        return `<div class="view-placeholder" style="padding:40px;text-align:center">
            <p style="color:var(--text-muted)">${esc(emptyMsg)}</p>
        </div>`;
    }
    const sorted = [...tasks].sort((a, b) => (a.name || '').localeCompare(b.name || ''));
    return sorted.map(renderTaskCard).join('');
}

export function renderHeartbeatCard(hb, timeline) {
    const state = getHeartbeatState(hb, timeline);
    const emoji = hb.emoji || '❤️';
    const lastResp = hb.last_response || '';
    const TRUNC = 120;
    const needsExpand = lastResp.length > TRUNC;
    const truncResp = needsExpand ? lastResp.slice(0, TRUNC) + '…' : lastResp;
    const beats = getBeatsForTask(timeline, hb.id, 20);

    const lastAgo = hb.last_run ? timeAgo(hb.last_run) : null;
    const nextIn = getNextIn(timeline, hb.id);
    const timeParts = [
        state.label,
        hb.active_hours_start != null ? formatHourRange(hb.active_hours_start, hb.active_hours_end) : null,
        lastAgo ? `ran ${lastAgo}` : null,
        nextIn ? `next in ${nextIn}` : null
    ].filter(Boolean).join(' · ');

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
                <button class="btn-icon" data-action="run" data-id="${hb.id}" title="Run now">▶</button>
                <button class="btn-icon" data-action="export" data-id="${hb.id}" title="Export">⇩</button>
                <button class="btn-icon" data-action="edit" data-id="${hb.id}" title="Edit">✏️</button>
                <button class="btn-icon danger" data-action="delete" data-id="${hb.id}" title="Delete">✕</button>
            </div>
        </div>`;
}

export function renderMission(heartbeats, timeline) {
    if (!heartbeats.length) {
        return '<div class="text-muted" style="padding:20px;text-align:center;font-size:var(--font-sm)">Create a heartbeat to monitor vitals here</div>';
    }
    return `<div class="sched-vitals-grid">
        ${heartbeats.map(hb => renderHeartbeatCard(hb, timeline)).join('')}
    </div>`;
}

export function renderDaemonList(daemons) {
    if (!daemons.length) {
        return `<div class="text-muted" style="padding:20px;text-align:center;font-size:var(--font-sm)">
            No daemons configured yet. Install a daemon plugin (Discord, Telegram, etc.) to get started.
        </div>`;
    }
    return daemons.map(d => {
        const tc = d.trigger_config || {};
        const source = tc.source || 'unknown';
        const hasFilter = tc.filter && Object.keys(tc.filter).length > 0;
        const lastRun = d.last_run ? formatTime(d.last_run) : 'Never';
        const emoji = d.emoji || '📡';
        const meta = [
            source,
            hasFilter ? 'filtered' : '',
            chatChip(d.chat_target),
            `Last: ${lastRun}`
        ].filter(Boolean).join(' · ');

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
                    <button class="btn-icon" data-action="export" data-id="${d.id}" title="Export">⇩</button>
                    <button class="btn-icon" data-action="edit" data-id="${d.id}" title="Edit">✏️</button>
                    <button class="btn-icon danger" data-action="delete" data-id="${d.id}" title="Delete">✕</button>
                </div>
            </div>`;
    }).join('');
}

export function renderWebhookList(webhooks) {
    if (!webhooks.length) {
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
            chatChip(w.chat_target),
            `Last: ${lastRun}`
        ].filter(Boolean).join(' · ');

        return `
            <div class="sched-task-card${w.running ? ' running' : ''}">
                <label class="sched-toggle" title="${w.enabled ? 'Disable' : 'Enable'}">
                    <input type="checkbox" ${w.enabled ? 'checked' : ''} data-action="toggle" data-id="${w.id}">
                    <span class="toggle-slider"></span>
                </label>
                <div class="sched-task-info">
                    <div class="sched-task-name">🔗 ${esc(w.name)}</div>
                    <div class="sched-task-meta">${meta}</div>
                </div>
                <div class="sched-task-actions">
                    <button class="btn-icon" data-action="export" data-id="${w.id}" title="Export">⇩</button>
                    <button class="btn-icon" data-action="edit" data-id="${w.id}" title="Edit">✏️</button>
                    <button class="btn-icon danger" data-action="delete" data-id="${w.id}" title="Delete">✕</button>
                </div>
            </div>`;
    }).join('');
}

// ── Horizontal timeline strip (scoped to the items shown in this view) ─

export function renderTimeline(timeline, liveItems) {
    const { now, past, future } = timeline;
    const liveIds = new Set(liveItems.map(t => t.id));
    const allItems = [...(past || []), ...(future || [])].filter(item => liveIds.has(item.task_id));
    if (!allItems.length) return '';

    const nowMs = now ? new Date(now).getTime() : Date.now();
    const windowMs = 2 * 60 * 60 * 1000;
    const minMs = nowMs - windowMs;
    const maxMs = nowMs + windowMs;

    const rowMap = new Map();
    for (const t of liveItems) rowMap.set(t.id, rowMap.size);
    const pipData = [];
    for (const item of allItems) {
        const ts = item.timestamp || item.scheduled_for;
        if (!ts) continue;
        const ms = new Date(ts).getTime();
        if (ms < minMs || ms > maxMs) continue;
        const tid = item.task_id || item.task_name;
        if (!rowMap.has(tid)) rowMap.set(tid, rowMap.size);
        const pct = ((ms - minMs) / (maxMs - minMs)) * 100;
        const icon = item.heartbeat ? (item.emoji || '❤️') : '⚡';
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
        return `<span class="hstrip-pip${p.isPast ? ' past' : ''}" style="left:${p.pct}%;top:${topPx}px" title="${esc(p.name)} — ${p.timeStr}">${p.icon}</span>`;
    }).join('');

    const nowTimeStr = new Date(nowMs).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const fmt = ms => new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const markers = [
        { pct: 0, label: fmt(minMs) },
        { pct: 25, label: fmt(nowMs - windowMs / 2) },
        { pct: 75, label: fmt(nowMs + windowMs / 2) },
        { pct: 100, label: fmt(maxMs) }
    ].map(m => `<span class="hstrip-marker" style="left:${m.pct}%">${m.label}</span>`).join('');

    return `
        <div class="sched-hstrip">
            <div class="hstrip-markers">${markers}</div>
            <div class="hstrip-ruler" style="height:${rulerH}px">
                ${pips}
                <span class="hstrip-now"><span class="hstrip-now-label">${nowTimeStr}</span></span>
            </div>
        </div>`;
}

// ── Editor / actions (refresh = view's reload+rerender callback) ───────

export function openEditor(task, type, refresh) {
    openTriggerEditor(task, type, {
        onSave: async (id, data) => {
            try {
                if (id) await updateTask(id, data);
                else await createTask(data);
                ui.showToast(id ? 'Saved' : 'Created', 'success');
                await refresh();
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
                await refresh();
            } catch { ui.showToast('Delete failed', 'error'); }
        },
    });
}

const EXPORT_STRIP_KEYS = ['id', 'last_run', 'last_response', 'created', 'running', 'source', 'handler', 'plugin_dir'];

function buildTaskExport(task) {
    const clean = { ...task };
    EXPORT_STRIP_KEYS.forEach(k => delete clean[k]);
    clean.enabled = false; // always import disabled
    // Vault hunt U6: while sealed, a private target serves as the
    // '__locked__' mask — a serve-time disguise, not data. Baking it into
    // a persistence file meant import (even unlocked) silently stripped it
    // and the task arrived targetless with no notice. Drop it here instead;
    // exportTask warns the user the target was withheld.
    if (clean.chat_target === '__locked__') delete clean.chat_target;
    return {
        sapphire_export: true,
        type: clean.type || 'task',
        version: 1,
        name: clean.name,
        task: clean,
    };
}

export function exportTask(task) {
    const type = task.type || 'task';
    if (task.chat_target === '__locked__') {
        ui.showToast('This task\'s chat is in the locked vault — the export '
            + 'carries no target; re-pick one after importing (or export '
            + 'while unlocked)', 'warning');
    }
    showExportDialog({
        type: type.charAt(0).toUpperCase() + type.slice(1),
        name: task.name,
        filename: `${task.name.replace(/\s+/g, '_')}.${type}.json`,
        data: buildTaskExport(task),
    });
}

export async function importTask(type, allTasks, refresh) {
    // Fetch available scopes for import validation — driven by scope_declarations
    // so plugin scopes participate automatically.
    const initData = await getInitData().catch(() => null);
    const scopeDeclarations = initData?.scope_declarations || [];
    const scopeFetched = await fetchScopeData(scopeDeclarations);
    const scopeSets = {};
    for (const decl of scopeDeclarations) {
        const items = scopeFetched[decl.key] || [];
        const valueField = decl.value_field || 'name';
        scopeSets[`${decl.key}_scope`] = new Set([
            'default', 'none',
            ...items.map(s => (typeof s === 'string' ? s : s[valueField] || s.name || ''))
                   .filter(Boolean)
        ]);
    }

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

            const skippedScopes = [];
            for (const key of Object.keys(scopeSets)) {
                const val = task[key];
                if (val && !scopeSets[key].has(val)) {
                    skippedScopes.push(`${key}: "${val}"`);
                    task[key] = 'default';
                }
            }

            await createTask(task);

            if (skippedScopes.length) {
                ui.showToast(`Imported (${skippedScopes.length} scopes reset to default: ${skippedScopes.join(', ')})`, 'warning');
            }
        },
        onDone: async () => { await refresh(); },
    });
}

// ── Delegated action binder ───────────────────────────────────────────
// Bind ONCE per render on a stable parent that survives poll updates.
// getAll() returns the flat array of items in this view (for lookup);
// refresh() reloads data and re-renders the view body.

export function bindActions(rootEl, getAll, refresh) {
    rootEl.addEventListener('click', async e => {
        const btn = e.target.closest('[data-action]');
        if (!btn || btn.tagName === 'INPUT') return;
        const { action, id } = btn.dataset;
        const item = getAll().find(t => t.id === id);
        if (action === 'export') {
            if (item) exportTask(item);
        } else if (action === 'edit') {
            if (item) openEditor(item, item.type || (item.heartbeat ? 'heartbeat' : 'task'), refresh);
        } else if (action === 'run') {
            if (!item || !confirm(`Run "${item.name}" now?`)) return;
            try { await runTask(id); ui.showToast(`Running: ${item.name}`, 'success'); await refresh(); }
            catch { ui.showToast('Run failed', 'error'); }
        } else if (action === 'delete') {
            if (!item || !confirm(`Delete "${item.name}"?`)) return;
            try { await deleteTask(id); ui.showToast('Deleted', 'success'); await refresh(); }
            catch { ui.showToast('Delete failed', 'error'); }
        }
    });
    rootEl.addEventListener('change', async e => {
        const { action, id } = e.target.dataset || {};
        if (action === 'toggle' || action === 'hb-toggle') {
            const item = getAll().find(t => t.id === id);
            if (!item) return;
            try { await updateTask(id, { enabled: !item.enabled }); await refresh(); }
            catch { ui.showToast('Toggle failed', 'error'); }
        }
    });
}
