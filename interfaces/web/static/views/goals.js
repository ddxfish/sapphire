// views/goals.js - Mind › Goals. Tracked objectives & tasks. Own scope domain
// (goal_scope). Extracted from the mind.js monolith.
import { renderSectionHeader, bindSectionHeader } from '../shared/section-header.js';
import { helpPills } from '../features/video-link.js';
import { renderScopeSidebar, bindScopeSidebar } from '../shared/scope-sidebar.js';
import { listScopes } from '../shared/scope-api.js';
import { MIND_TABS, csrfHeaders, escHtml, escAttr, timeAgo, scopeForChatTab, subscribeMindDomain } from '../shared/mind-common.js';
import { setupModalClose } from '../shared/modal.js';
import * as ui from '../ui.js';
import { snapScroll } from '../shared/dom-guard.js';

const SCOPE_KEY = 'goal_scope';
const DOMAIN = 'goal';
const SCOPE_ENDPOINT = '/api/goals/scopes';

let container = null;
let scope = 'default';
let scopes = [];
let goalStatusFilter = 'active';
let unsub = null;

export default {
    init(el) { container = el; },
    async show() {
        if (!unsub) unsub = subscribeMindDomain(DOMAIN, () => scope, () => container?.offsetParent !== null, renderGoals);
        if (window._mindScope) { scope = window._mindScope; delete window._mindScope; }
        else { const s = await scopeForChatTab(SCOPE_KEY); if (s) scope = s; }
        delete window._mindTab;
        scopes = await listScopes(SCOPE_ENDPOINT);
        render();
    },
    hide() { if (unsub) { unsub(); unsub = null; } }
};

function content() { return container?.querySelector('#goal-content'); }

function render() {
    if (!container) return;
    container.innerHTML = `
        ${renderSectionHeader({ tabs: MIND_TABS, active: 'goals', help: helpPills('Goals', { video: 'I3g3tzukpV0', doc: 'GOALS.md', inline: true }), status: 'Objectives & tasks Sapphire tracks — she manages these via tools; you can too.' })}
        <div class="two-panel">
            ${renderScopeSidebar(scopes, scope)}
            <div class="panel-right">
                <div class="view-body view-scroll" id="goal-content"></div>
            </div>
        </div>`;
    bindSectionHeader(container);
    bindScopeSidebar(container, {
        onScopeChange: (s) => { scope = s; render(); },
        onChanged: async (s) => { scope = s || 'default'; scopes = await listScopes(SCOPE_ENDPOINT); render(); },
    });
    renderGoals();
}

async function renderGoals() {
    const el = content();
    if (!el) return;
    const resp = await fetch(`/api/goals?scope=${encodeURIComponent(scope)}&status=${goalStatusFilter}`);
    if (!resp.ok) { el.innerHTML = '<div class="mind-empty">Failed to load goals</div>'; return; }
    const data = await resp.json();
    const goals = data.goals || [];

    const filterHtml = `
        <div class="mind-toolbar">
            <button class="mind-btn" id="mind-new-goal">+ New Goal</button>
            <div class="goal-status-filter">
                ${['active', 'completed', 'abandoned', 'all'].map(s =>
                    `<button class="mind-btn-sm goal-filter-btn${goalStatusFilter === s ? ' active' : ''}" data-status="${s}">${s[0].toUpperCase() + s.slice(1)}</button>`
                ).join('')}
            </div>
        </div>
    `;

    if (!goals.length) {
        el.innerHTML = filterHtml + `<div class="mind-empty">No ${goalStatusFilter === 'all' ? '' : goalStatusFilter + ' '}goals in this scope</div>`;
        bindGoalToolbar(el);
        return;
    }

    const restoreScroll = snapScroll(el);   // DOM-refresh hunt 2026-09-08
    el.innerHTML = filterHtml + '<div class="mind-list">' + goals.map(g => {
        const priClass = `goal-pri-${g.priority}`;
        const statusIcon = g.status === 'completed' ? '&#x2705;' : g.status === 'abandoned' ? '&#x274C;' : '&#x1F7E2;';
        const ago = timeAgo(g.updated_at);
        const subtasksDone = g.subtasks.filter(s => s.status === 'completed').length;
        const subtasksTotal = g.subtasks.length;

        return `
            <details class="mind-accordion">
                <summary class="mind-accordion-header">
                    <span class="goal-status-dot" title="${escHtml(g.status)}">${statusIcon}</span>
                    <span class="mind-accordion-title">${escHtml(g.title)}</span>
                    <span class="goal-pri-badge ${priClass}">${g.priority}</span>
                    ${g.permanent ? '<span class="goal-perm-badge" title="Permanent — AI cannot complete or delete">PERM</span>' : ''}
                    ${subtasksTotal ? `<span class="goal-subtask-count">${subtasksDone}/${subtasksTotal}</span>` : ''}
                    <span class="mind-accordion-count">${ago}</span>
                </summary>
                <div class="mind-accordion-body">
                    <div class="mind-accordion-inner">
                        ${g.description ? `<div class="goal-desc">${escHtml(g.description)}</div>` : ''}

                        ${subtasksTotal ? `
                            <div class="goal-subtasks">
                                <div class="goal-section-label">Subtasks</div>
                                ${g.subtasks.map(s => `
                                    <div class="goal-subtask" data-id="${s.id}">
                                        <button class="goal-subtask-check${s.status === 'completed' ? ' done' : ''}" data-id="${s.id}" data-status="${s.status}" title="Toggle complete">${s.status === 'completed' ? '&#x2611;' : '&#x2610;'}</button>
                                        <span class="goal-subtask-title${s.status === 'completed' ? ' done' : ''}">${escHtml(s.title)}</span>
                                        <button class="mind-btn-sm goal-del-subtask" data-id="${s.id}" title="Delete">&#x2715;</button>
                                    </div>
                                `).join('')}
                            </div>
                        ` : ''}

                        ${g.progress.length ? `
                            <div class="goal-progress">
                                <div class="goal-section-label">Progress Journal</div>
                                ${g.progress.map(p => `
                                    <div class="goal-progress-entry">
                                        <span class="goal-progress-time">${timeAgo(p.created_at)}</span>
                                        <span class="goal-progress-note">${escHtml(p.note)}</span>
                                    </div>
                                `).join('')}
                            </div>
                        ` : ''}

                        <div class="goal-actions">
                            ${g.status === 'active' ? `
                                <button class="mind-btn-sm goal-complete-btn" data-id="${g.id}" title="Mark complete">&#x2705; Complete</button>
                                <button class="mind-btn-sm goal-abandon-btn" data-id="${g.id}" title="Abandon">&#x274C; Abandon</button>
                            ` : `
                                <button class="mind-btn-sm goal-reactivate-btn" data-id="${g.id}" title="Reactivate">&#x1F504; Reactivate</button>
                            `}
                            <button class="mind-btn-sm goal-add-subtask" data-id="${g.id}" title="Add subtask">+ Subtask</button>
                            <button class="mind-btn-sm goal-add-note" data-id="${g.id}" title="Add progress note">+ Note</button>
                            <button class="mind-btn-sm goal-edit-btn" data-id="${g.id}" title="Edit">&#x270E;</button>
                            <button class="mind-btn-sm goal-del-btn" data-id="${g.id}" title="Delete">&#x1F5D1;</button>
                        </div>
                    </div>
                </div>
            </details>
        `;
    }).join('') + '</div>';
    restoreScroll();

    bindGoalToolbar(el);
    bindGoalActions(el);
}

function bindGoalToolbar(el) {
    el.querySelector('#mind-new-goal')?.addEventListener('click', () => showGoalModal());
    el.querySelectorAll('.goal-filter-btn').forEach(btn => {
        btn.addEventListener('click', () => { goalStatusFilter = btn.dataset.status; renderGoals(); });
    });
}

function bindGoalActions(el) {
    el.querySelectorAll('.goal-complete-btn').forEach(btn => btn.addEventListener('click', () => updateGoalStatus(btn.dataset.id, 'completed')));
    el.querySelectorAll('.goal-abandon-btn').forEach(btn => btn.addEventListener('click', () => updateGoalStatus(btn.dataset.id, 'abandoned')));
    el.querySelectorAll('.goal-reactivate-btn').forEach(btn => btn.addEventListener('click', () => updateGoalStatus(btn.dataset.id, 'active')));

    el.querySelectorAll('.goal-subtask-check').forEach(btn => {
        btn.addEventListener('click', () => {
            const newStatus = btn.dataset.status === 'completed' ? 'active' : 'completed';
            updateGoalStatus(btn.dataset.id, newStatus);
        });
    });

    el.querySelectorAll('.goal-del-subtask').forEach(btn => {
        btn.addEventListener('click', async () => {
            if (!confirm('Delete this subtask?')) return;
            try {
                const resp = await fetch(`/api/goals/${btn.dataset.id}`, { method: 'DELETE', headers: csrfHeaders() });
                if (resp.ok) { ui.showToast('Deleted', 'success'); renderGoals(); }
            } catch (e) { ui.showToast('Failed', 'error'); }
        });
    });

    el.querySelectorAll('.goal-add-subtask').forEach(btn => {
        btn.addEventListener('click', async () => {
            const title = prompt('Subtask title:');
            if (!title?.trim()) return;
            try {
                const resp = await fetch('/api/goals', {
                    method: 'POST',
                    headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ title: title.trim(), parent_id: parseInt(btn.dataset.id), scope })
                });
                if (resp.ok) { ui.showToast('Subtask added', 'success'); renderGoals(); }
                else { const err = await resp.json(); ui.showToast(err.detail || 'Failed', 'error'); }
            } catch (e) { ui.showToast('Failed', 'error'); }
        });
    });

    el.querySelectorAll('.goal-add-note').forEach(btn => {
        btn.addEventListener('click', async () => {
            const note = prompt('Progress note:');
            if (!note?.trim()) return;
            try {
                const resp = await fetch(`/api/goals/${btn.dataset.id}/progress`, {
                    method: 'POST',
                    headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ note: note.trim() })
                });
                if (resp.ok) { ui.showToast('Note added', 'success'); renderGoals(); }
                else { const err = await resp.json(); ui.showToast(err.detail || 'Failed', 'error'); }
            } catch (e) { ui.showToast('Failed', 'error'); }
        });
    });

    el.querySelectorAll('.goal-edit-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            try {
                const resp = await fetch(`/api/goals/${btn.dataset.id}`);
                if (resp.ok) { showGoalModal(await resp.json()); }
            } catch (e) { ui.showToast('Failed to load goal', 'error'); }
        });
    });

    el.querySelectorAll('.goal-del-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const isPerm = btn.closest('.mind-accordion')?.querySelector('.goal-perm-badge');
            const msg = isPerm
                ? 'This is a PERMANENT goal. Are you sure you want to delete it?'
                : 'Delete this goal and all subtasks/progress?';
            if (!confirm(msg)) return;
            try {
                const resp = await fetch(`/api/goals/${btn.dataset.id}`, { method: 'DELETE', headers: csrfHeaders() });
                if (resp.ok) { ui.showToast('Deleted', 'success'); renderGoals(); }
            } catch (e) { ui.showToast('Failed', 'error'); }
        });
    });
}

async function updateGoalStatus(goalId, status) {
    try {
        const resp = await fetch(`/api/goals/${goalId}`, {
            method: 'PUT',
            headers: csrfHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ status })
        });
        if (resp.ok) { renderGoals(); }
        else { const err = await resp.json(); ui.showToast(err.detail || 'Failed', 'error'); }
    } catch (e) { ui.showToast('Failed', 'error'); }
}

function showGoalModal(goal = null) {
    document.querySelector('.mind-modal-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal">
            <div class="pr-modal-header">
                <h3>${goal ? 'Edit' : 'New'} Goal</h3>
                <button class="mind-btn-sm mind-modal-close">&#x2715;</button>
            </div>
            <div class="pr-modal-body">
                <div class="mind-form">
                    <input type="text" id="mg-title" placeholder="Title *" value="${escAttr(goal?.title || '')}">
                    <textarea id="mg-desc" placeholder="Description (optional)" rows="3">${escHtml(goal?.description || '')}</textarea>
                    <div style="display:flex;gap:8px;align-items:center">
                        <label style="color:var(--text-muted);font-size:var(--font-sm)">Priority:</label>
                        <select id="mg-priority" style="padding:4px 8px;background:var(--input-bg);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-size:var(--font-sm)">
                            ${['high', 'medium', 'low'].map(p =>
                                `<option value="${p}"${(goal?.priority || 'medium') === p ? ' selected' : ''}>${p[0].toUpperCase() + p.slice(1)}</option>`
                            ).join('')}
                        </select>
                    </div>
                    <label style="display:flex;gap:6px;align-items:center;color:var(--text-muted);font-size:var(--font-sm);cursor:pointer">
                        <input type="checkbox" id="mg-permanent" ${goal?.permanent ? 'checked' : ''}>
                        Permanent <span style="opacity:0.6">(AI cannot complete or delete)</span>
                    </label>
                    <div style="display:flex;justify-content:flex-end;gap:8px">
                        <button class="mind-btn mind-modal-cancel">Cancel</button>
                        <button class="mind-btn" id="mg-save" style="border-color:var(--trim,var(--accent-blue))">${goal ? 'Update' : 'Create'}</button>
                    </div>
                </div>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    const close = () => overlay.remove();
    overlay.querySelector('.mind-modal-close').addEventListener('click', close);
    overlay.querySelector('.mind-modal-cancel').addEventListener('click', close);
    setupModalClose(overlay, close);
    overlay.querySelector('#mg-title').focus();

    overlay.querySelector('#mg-save').addEventListener('click', async () => {
        const title = overlay.querySelector('#mg-title').value.trim();
        if (!title) { ui.showToast('Title is required', 'error'); return; }
        const body = {
            title,
            description: overlay.querySelector('#mg-desc').value.trim() || null,
            priority: overlay.querySelector('#mg-priority').value,
            permanent: overlay.querySelector('#mg-permanent').checked,
        };
        try {
            let resp;
            if (goal) {
                resp = await fetch(`/api/goals/${goal.id}`, {
                    method: 'PUT',
                    headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify(body)
                });
            } else {
                body.scope = scope;
                resp = await fetch('/api/goals', {
                    method: 'POST',
                    headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify(body)
                });
            }
            if (resp.ok) { close(); ui.showToast(goal ? 'Updated' : 'Created', 'success'); renderGoals(); }
            else { const err = await resp.json(); ui.showToast(err.detail || 'Failed', 'error'); }
        } catch (e) { ui.showToast('Failed', 'error'); }
    });
}
