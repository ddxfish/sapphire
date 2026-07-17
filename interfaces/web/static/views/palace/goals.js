// views/palace/goals.js - Mind › Goals, palace edition v2 (2026-07-16).
// Goals are chunks in the graph: search finds them, mentions weave them to
// people, permanent goals sit in the never-fades band. v2: spacious cards,
// real modals (shared/modal.js — no browser popups), subtask add/edit with
// descriptions + instructions, priority sort, due dates, in-progress state.
// The UI has full control (permanent toggle, force delete) — the AI-facing
// guards live in the tools.
import { renderSectionHeader, bindSectionHeader } from '../../shared/section-header.js';
import { helpPills } from '../../features/video-link.js';
import { renderScopeSidebar, bindScopeSidebar } from '../../shared/scope-sidebar.js';
import { listScopes } from '../../shared/scope-api.js';
import { escHtml, timeAgo, scopeForChatTab, subscribeMindDomain } from '../../shared/mind-common.js';
import { showModal, showConfirm } from '../../shared/modal.js';
import * as ui from '../../ui.js';
import { PALACE_TABS, refreshPalaceTabs, SCOPE_ENDPOINT, palaceGet, palaceSend, describeScopeForDelete, transferButtons, bindTransfer } from './common.js';

const SCOPE_KEY = 'memory_scope';
const DOMAIN = 'goal';   // mind_events domain (singular — matches the publisher)

let container = null;
let scope = 'default';
let scopes = [];
let unsub = null;
let _status = 'active';
let _sort = 'priority';

export default {
    init(el) { container = el; },
    async show() {
        await refreshPalaceTabs();
        if (!unsub) unsub = subscribeMindDomain(DOMAIN, () => scope, () => container?.offsetParent !== null, renderList);
        if (window._mindScope) { scope = window._mindScope; delete window._mindScope; }
        else { const s = await scopeForChatTab(SCOPE_KEY); if (s) scope = s; }
        delete window._mindTab;
        scopes = await listScopes(SCOPE_ENDPOINT);
        render();
    },
    hide() { if (unsub) { unsub(); unsub = null; } }
};

function content() { return container?.querySelector('#pal-goal-content'); }

function render() {
    if (!container) return;
    container.innerHTML = `
        ${renderSectionHeader({ tabs: PALACE_TABS, active: 'goals', help: helpPills('Goals', { doc: 'MEMORY.md', inline: true }), status: '\u{1F3DB}️ Mind Palace — her goals (L4), woven into the graph. \u{1F512} permanent goals never fade and refuse deletion.' })}
        <div class="two-panel">
            ${renderScopeSidebar(scopes, scope)}
            <div class="panel-right">
                <div class="view-body view-scroll" id="pal-goal-content"></div>
            </div>
        </div>`;
    bindSectionHeader(container);
    bindScopeSidebar(container, {
        describeScope: describeScopeForDelete,
        onScopeChange: (s) => { scope = s; _status = 'active'; render(); },
        onChanged: async (s) => { scope = s || 'default'; _status = 'active'; scopes = await listScopes(SCOPE_ENDPOINT); render(); },
    });
    renderList();
}

const PRIO_BADGE = {
    high: '<span class="goal-pri-badge goal-pri-high">high</span>',
    medium: '<span class="goal-pri-badge goal-pri-medium">med</span>',
    low: '<span class="goal-pri-badge goal-pri-low">low</span>',
};
const PRIO_RANK = { high: 0, medium: 1, low: 2 };
const STATUSES = ['active', 'in_progress', 'completed', 'abandoned'];
const STATUS_LABELS = ['Active', 'In progress', 'Completed', 'Abandoned'];
const SORTS = {
    priority: (a, b) => (PRIO_RANK[a.priority] ?? 1) - (PRIO_RANK[b.priority] ?? 1) || (b.updated || '').localeCompare(a.updated || ''),
    updated: (a, b) => (b.updated || '').localeCompare(a.updated || ''),
    created: (a, b) => (b.created || '').localeCompare(a.created || ''),
    alpha: (a, b) => a.title.localeCompare(b.title),
    due: (a, b) => (a.due || '9999').localeCompare(b.due || '9999'),
};

function subRow(s) {
    const done = s.status === 'completed';
    const meta = [];
    if (s.priority && s.priority !== 'medium') meta.push(s.priority);
    if (s.status === 'in_progress') meta.push('in progress');
    if (s.due) meta.push(`due ${s.due}`);
    return `
        <div class="pgoal-sub ${done ? 'pgoal-sub-done' : ''}" data-sub="${s.id}">
            <input type="checkbox" data-subcheck="${s.id}" ${done ? 'checked' : ''} title="Mark ${done ? 'active' : 'completed'}">
            <div class="pgoal-sub-main">
                <span class="pgoal-sub-title">${escHtml(s.title)}</span>
                ${meta.length ? `<span class="pgoal-sub-meta">${escHtml(meta.join(' · '))}</span>` : ''}
                ${s.description ? `<div class="pgoal-sub-desc">${escHtml(s.description)}</div>` : ''}
                ${s.instructions ? `<details class="pgoal-instr"><summary>instructions</summary><div class="pgoal-instr-body">${escHtml(s.instructions)}</div></details>` : ''}
            </div>
            <button class="mind-btn-sm pgoal-sub-edit" data-subedit="${s.id}" title="Edit subtask">✎</button>
        </div>`;
}

function goalCard(g) {
    const done = g.status === 'completed';
    const subs = g.subtasks || [];
    const subsDone = subs.filter(s => s.status === 'completed').length;
    return `
        <div class="pgoal-card ${done || g.status === 'abandoned' ? 'pgoal-closed' : ''}" data-id="${g.id}">
            <div class="pgoal-head">
                ${g.permanent ? '<span class="pgoal-perm" title="Permanent — never fades, refuses deletion">\u{1F512}</span>' : ''}
                <span class="pgoal-title">${escHtml(g.title)}</span>
                ${PRIO_BADGE[g.priority] || ''}
                ${g.status !== 'active' ? `<span class="palace-pruned-pill">${escHtml(g.status.replace('_', ' '))}</span>` : ''}
                ${subs.length ? `<span class="pgoal-chip">${subsDone}/${subs.length} done</span>` : ''}
                ${g.due ? `<span class="pgoal-chip pgoal-chip-due">due ${escHtml(g.due)}</span>` : ''}
                <span class="pgoal-age" title="created ${escHtml(timeAgo(g.created))}">${escHtml(timeAgo(g.updated))}</span>
                <button class="mind-btn-sm pgoal-edit" title="Edit goal">✎</button>
                <button class="mind-btn-sm pgoal-del" title="Delete goal">✕</button>
            </div>
            ${g.description ? `<div class="pgoal-desc">${escHtml(g.description)}</div>` : ''}
            ${g.instructions ? `<details class="pgoal-instr"><summary>instructions</summary><div class="pgoal-instr-body">${escHtml(g.instructions)}</div></details>` : ''}
            <div class="pgoal-subs">
                ${subs.map(subRow).join('')}
                <button class="mind-btn-sm pgoal-addsub">+ subtask</button>
            </div>
            ${g.progress.length ? `<details class="pgoal-journal">
                <summary>journal (${g.progress.length})</summary>
                ${g.progress.map(p => `<div class="pgoal-note">${escHtml(timeAgo(p.created))} — ${escHtml(p.note)}</div>`).join('')}
            </details>` : ''}
            <div class="pgoal-foot">
                <input type="text" class="pgoal-noteinput" placeholder="+ progress note (Enter to log)" maxlength="1024">
                ${g.status === 'completed' || g.status === 'abandoned'
                    ? `<button class="mind-btn-sm pgoal-reopen" title="Reopen">↩ reopen</button>`
                    : `<button class="mind-btn-sm pgoal-complete" title="Mark completed">✓ done</button>`}
            </div>
        </div>`;
}

async function renderList() {
    const el = content();
    if (!el) return;
    let data;
    try {
        data = await palaceGet(`goals?scope=${encodeURIComponent(scope)}&status=${encodeURIComponent(_status)}`);
    } catch (e) {
        el.innerHTML = `<div class="mind-empty">Failed to load: ${escHtml(e.message)}</div>`;
        return;
    }
    const goals = (data.goals || []).slice().sort(SORTS[_sort] || SORTS.priority);
    const pill = (val, label) =>
        `<button class="palace-kpill ${_status === val ? 'active' : ''}" data-status="${val}">${label}</button>`;
    const sortOpt = (val, label) =>
        `<option value="${val}" ${_sort === val ? 'selected' : ''}>${label}</option>`;
    el.innerHTML = `
        <div class="mind-toolbar pgoal-actions">
            <button class="mind-btn" id="pal-goal-add">+ Add goal</button>
            ${transferButtons()}
            <span class="palace-count">${goals.length} goals</span>
        </div>
        <div class="pgoal-filters">
            ${pill('active', 'Active')}${pill('completed', 'Completed')}${pill('abandoned', 'Abandoned')}${pill('all', 'All')}
            <select class="palace-select pgoal-sort" title="Sort">
                ${sortOpt('priority', 'Sort: priority')}${sortOpt('updated', 'Sort: recent')}${sortOpt('created', 'Sort: created')}${sortOpt('alpha', 'Sort: A→Z')}${sortOpt('due', 'Sort: due date')}
            </select>
        </div>
        ${goals.length
            ? `<div class="pgoal-list">${goals.map(goalCard).join('')}</div>`
            : `<div class="mind-empty">No ${_status === 'all' ? '' : _status + ' '}goals in this scope — she plans with create_goal, or use + Add goal.</div>`}
    `;
    el.querySelectorAll('.palace-kpill').forEach(btn => {
        btn.addEventListener('click', () => { _status = btn.dataset.status; renderList(); });
    });
    el.querySelector('.pgoal-sort')?.addEventListener('change', e => { _sort = e.target.value; renderList(); });
    el.querySelector('#pal-goal-add')?.addEventListener('click', () => goalModal(null));
    bindTransfer(el, 'goals', () => scope, ui, renderList);
    bindGoalCards(el, data.goals || []);
}

function bindGoalCards(el, goals) {
    const byId = Object.fromEntries(goals.map(g => [g.id, g]));
    el.querySelectorAll('.pgoal-card').forEach(card => {
        const gid = parseInt(card.dataset.id);
        const g = byId[gid];
        const put = async (body, okMsg) => {
            try {
                await palaceSend(`goals/${gid}`, 'PUT', { ...body, scope });
                if (okMsg) ui.showToast(okMsg, 'success');
                renderList();
            } catch (e) { ui.showToast(e.message, 'error'); }
        };
        card.querySelector('.pgoal-complete')?.addEventListener('click', () => put({ status: 'completed' }, 'Completed 🎯'));
        card.querySelector('.pgoal-reopen')?.addEventListener('click', () => put({ status: 'active' }, 'Reopened'));
        card.querySelector('.pgoal-edit')?.addEventListener('click', () => goalModal(g));
        card.querySelector('.pgoal-addsub')?.addEventListener('click', () => subModal(gid, null));
        card.querySelectorAll('[data-subedit]').forEach(btn => {
            const s = (g.subtasks || []).find(x => x.id === parseInt(btn.dataset.subedit));
            if (s) btn.addEventListener('click', () => subModal(gid, s));
        });
        card.querySelectorAll('[data-subcheck]').forEach(cb => {
            cb.addEventListener('change', async () => {
                try {
                    await palaceSend(`goals/${cb.dataset.subcheck}`, 'PUT',
                        { status: cb.checked ? 'completed' : 'active', scope });
                    renderList();
                } catch (e) { ui.showToast(e.message, 'error'); }
            });
        });
        const noteInput = card.querySelector('.pgoal-noteinput');
        noteInput?.addEventListener('keydown', e => {
            if (e.key === 'Enter' && noteInput.value.trim()) {
                put({ progress_note: noteInput.value.trim() }, 'Noted');
            }
        });
        card.querySelector('.pgoal-del')?.addEventListener('click', () => deleteGoal(g));
    });
}

function deleteGoal(g) {
    const n = (g.subtasks || []).length + (g.progress || []).length;
    const extra = n ? ` Its ${n} subtask/journal rows go with it.` : '';
    showConfirm(`Delete "${g.title}"?${extra} (Abandon keeps history instead.)`, async () => {
        try {
            await palaceSend(`goals/${g.id}?scope=${encodeURIComponent(scope)}`, 'DELETE');
            renderList();
        } catch (e) {
            if (/permanent/i.test(e.message)) {
                showConfirm(`"${g.title}" is PERMANENT — a standing duty. Really delete it?`, async () => {
                    try {
                        await palaceSend(`goals/${g.id}?scope=${encodeURIComponent(scope)}&force=1`, 'DELETE');
                        renderList();
                    } catch (e2) { ui.showToast(e2.message, 'error'); }
                }, { title: 'Permanent goal', saveLabel: 'Delete anyway' });
            } else ui.showToast(e.message, 'error');
        }
    }, { title: 'Delete goal', saveLabel: 'Delete' });
}

// One field kit for both modals — goals get permanent + (on add) bulk
// subtask lines; subtasks skip permanent. Edit adds the status select.
function goalFields(g, { isSub = false, isEdit = false } = {}) {
    g = g || {};
    const f = [
        { id: 'title', label: 'Title *', type: 'text', value: g.title || '' },
        { id: 'description', label: 'Description — what & why', type: 'textarea', rows: 3, value: g.description || '' },
        { id: 'instructions', label: 'Instructions — how, step by step (shown on request)', type: 'textarea', rows: 4, value: g.instructions || '' },
        { id: 'priority', label: 'Priority', type: 'select', options: ['high', 'medium', 'low'], labels: ['🔺 High', '● Medium', '○ Low'], value: g.priority || 'medium' },
        { id: 'due', label: 'Due date (optional)', type: 'date', value: g.due || '' },
    ];
    if (isEdit) f.push({ id: 'status', label: 'Status', type: 'select', options: STATUSES, labels: STATUS_LABELS, value: g.status || 'active' });
    if (!isSub) f.push({ id: 'permanent', label: 'Flags', type: 'checkboxes', options: { on: '🔒 Permanent — standing duty, never fades, refuses deletion' }, selected: g.permanent ? ['on'] : [] });
    if (!isSub && !isEdit) f.push({ id: 'subtasks', label: 'Subtasks — one per line (optional)', type: 'textarea', rows: 4, value: '' });
    return f;
}

function goalModal(g) {
    const isEdit = !!g;
    showModal(isEdit ? `Edit goal [${g.id}]` : 'Add goal', goalFields(g, { isEdit }), async data => {
        if (!data.title?.trim()) { ui.showToast('Title is required', 'error'); return; }
        const body = {
            scope, title: data.title.trim(),
            description: data.description.trim(),
            instructions: data.instructions.trim(),
            priority: data.priority, due: data.due,
            permanent: (data.permanent || []).includes('on'),
        };
        try {
            if (isEdit) {
                await palaceSend(`goals/${g.id}`, 'PUT', { ...body, status: data.status });
                ui.showToast('Goal updated', 'success');
            } else {
                body.subtasks = (data.subtasks || '').split('\n').map(s => s.trim()).filter(Boolean);
                await palaceSend('goals', 'POST', body);
                ui.showToast('Goal created', 'success');
            }
            renderList();
        } catch (e) { ui.showToast(`Save failed: ${e.message}`, 'error'); }
    }, { saveLabel: isEdit ? 'Save' : 'Create' });
}

function subModal(gid, s) {
    const isEdit = !!s;
    showModal(isEdit ? `Edit subtask [${s.id}]` : 'Add subtask', goalFields(s, { isSub: true, isEdit }), async data => {
        if (!data.title?.trim()) { ui.showToast('Title is required', 'error'); return; }
        const body = {
            scope, title: data.title.trim(),
            description: data.description.trim(),
            instructions: data.instructions.trim(),
            priority: data.priority, due: data.due,
        };
        try {
            if (isEdit) {
                await palaceSend(`goals/${s.id}`, 'PUT', { ...body, status: data.status });
                ui.showToast('Subtask updated', 'success');
            } else {
                await palaceSend('goals', 'POST', { ...body, parent_id: gid });
                ui.showToast('Subtask added', 'success');
            }
            renderList();
        } catch (e) { ui.showToast(`Save failed: ${e.message}`, 'error'); }
    }, { saveLabel: isEdit ? 'Save' : 'Add' });
}
