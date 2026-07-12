// views/palace/goals.js - Mind › Goals, palace edition (L4, 2026-07-11).
// Goals are chunks in the graph: search finds them, mentions weave them to
// people, permanent goals sit in the never-fades band. Cards show subtasks +
// the progress journal; the UI has full control (permanent toggle, force
// delete) — the AI-facing guards live in the tools.
import { renderSectionHeader, bindSectionHeader } from '../../shared/section-header.js';
import { helpPills } from '../../features/video-link.js';
import { renderScopeSidebar, bindScopeSidebar } from '../../shared/scope-sidebar.js';
import { listScopes } from '../../shared/scope-api.js';
import { escHtml, escAttr, timeAgo, scopeForChatTab, subscribeMindDomain } from '../../shared/mind-common.js';
import { setupModalClose } from '../../shared/modal.js';
import * as ui from '../../ui.js';
import { PALACE_TABS, refreshPalaceTabs, SCOPE_ENDPOINT, palaceGet, palaceSend, describeScopeForDelete, transferButtons, bindTransfer } from './common.js';

const SCOPE_KEY = 'memory_scope';
const DOMAIN = 'goals';

let container = null;
let scope = 'default';
let scopes = [];
let unsub = null;
let _status = 'active';

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

const PRIO = { high: '\u{1F53A} high', medium: '● medium', low: '○ low' };

function goalCard(g) {
    const done = g.status === 'completed';
    return `
        <div class="mind-mem-card palace-goal-card ${done ? 'palace-goal-done' : ''}" data-id="${g.id}">
            <div class="mind-mem-header">
                ${g.permanent ? '<span class="palace-goal-perm" title="Permanent — never fades, refuses deletion">\u{1F512}</span>' : ''}
                <span class="palace-goal-title">${escHtml(g.title)}</span>
                <span class="palace-goal-prio">${PRIO[g.priority] || g.priority}</span>
                ${g.status !== 'active' ? `<span class="palace-pruned-pill">${escHtml(g.status)}</span>` : ''}
                <span class="mind-mem-time">${escHtml(timeAgo(g.updated))}</span>
                <span class="mind-mem-id">[${g.id}]</span>
            </div>
            ${g.description ? `<div class="palace-goal-desc">${escHtml(g.description)}</div>` : ''}
            ${g.subtasks.length ? `<div class="palace-goal-subs">
                ${g.subtasks.map(s => `
                    <label class="palace-goal-sub">
                        <input type="checkbox" data-sub="${s.id}" ${s.status === 'completed' ? 'checked' : ''}>
                        <span class="${s.status === 'completed' ? 'palace-sub-done' : ''}">${escHtml(s.title)}</span>
                    </label>`).join('')}
            </div>` : ''}
            ${g.progress.length ? `<details class="palace-goal-journal">
                <summary>journal (${g.progress.length})</summary>
                ${g.progress.map(p => `<div class="palace-goal-note">${escHtml(timeAgo(p.created))} — ${escHtml(p.note)}</div>`).join('')}
            </details>` : ''}
            <div class="mind-mem-actions">
                <input type="text" class="palace-goal-noteinput" placeholder="+ progress note" maxlength="1024">
                ${g.status === 'active'
                    ? `<button class="mind-btn-sm palace-goal-complete" title="Mark completed">✓ done</button>`
                    : `<button class="mind-btn-sm palace-goal-reopen" title="Reopen">↩ reopen</button>`}
                <button class="mind-btn-sm palace-goal-del" title="Delete (permanent goals ask twice)">✕</button>
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
    const goals = data.goals || [];
    const pill = (val, label) =>
        `<button class="palace-kpill ${_status === val ? 'active' : ''}" data-status="${val}">${label}</button>`;
    el.innerHTML = `
        <div class="mind-toolbar palace-kind-pills">
            ${pill('active', 'Active')}${pill('completed', 'Completed')}${pill('abandoned', 'Abandoned')}${pill('all', 'All')}
            <button class="mind-btn" id="pal-goal-add">+ Add goal</button>
            ${transferButtons()}
            <span class="palace-count">${goals.length} goals</span>
        </div>
        ${goals.length
            ? `<div class="palace-chunk-list">${goals.map(goalCard).join('')}</div>`
            : `<div class="mind-empty">No ${_status === 'all' ? '' : _status + ' '}goals in this scope — she plans with create_goal, or use + Add goal.</div>`}
    `;
    el.querySelectorAll('.palace-kpill').forEach(btn => {
        btn.addEventListener('click', () => { _status = btn.dataset.status; renderList(); });
    });
    el.querySelector('#pal-goal-add')?.addEventListener('click', showAddModal);
    bindTransfer(el, 'goals', () => scope, ui, renderList);
    bindGoalCards(el);
}

function bindGoalCards(el) {
    el.querySelectorAll('.palace-goal-card').forEach(card => {
        const gid = parseInt(card.dataset.id);
        const put = async (body, okMsg) => {
            try {
                await palaceSend(`goals/${gid}`, 'PUT', { ...body, scope });
                if (okMsg) ui.showToast(okMsg, 'success');
                renderList();
            } catch (e) { ui.showToast(e.message, 'error'); }
        };
        card.querySelector('.palace-goal-complete')?.addEventListener('click', () => put({ status: 'completed' }, 'Completed 🎯'));
        card.querySelector('.palace-goal-reopen')?.addEventListener('click', () => put({ status: 'active' }, 'Reopened'));
        card.querySelectorAll('[data-sub]').forEach(cb => {
            cb.addEventListener('change', async () => {
                try {
                    await palaceSend(`goals/${cb.dataset.sub}`, 'PUT',
                        { status: cb.checked ? 'completed' : 'active', scope });
                    renderList();
                } catch (e) { ui.showToast(e.message, 'error'); }
            });
        });
        const noteInput = card.querySelector('.palace-goal-noteinput');
        noteInput?.addEventListener('keydown', e => {
            if (e.key === 'Enter' && noteInput.value.trim()) {
                put({ progress_note: noteInput.value.trim() }, 'Noted');
            }
        });
        card.querySelector('.palace-goal-del')?.addEventListener('click', async () => {
            if (!confirm('Delete this goal, its subtasks and journal? (abandon keeps history instead)')) return;
            try {
                await palaceSend(`goals/${gid}?scope=${encodeURIComponent(scope)}`, 'DELETE');
                renderList();
            } catch (e) {
                if (/permanent/i.test(e.message)) {
                    if (confirm('This goal is PERMANENT — a standing duty. Really delete it?')) {
                        try {
                            await palaceSend(`goals/${gid}?scope=${encodeURIComponent(scope)}&force=1`, 'DELETE');
                            renderList();
                        } catch (e2) { ui.showToast(e2.message, 'error'); }
                    }
                } else ui.showToast(e.message, 'error');
            }
        });
    });
}

function showAddModal() {
    document.querySelector('.mind-modal-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal">
            <div class="pr-modal-header">
                <h3>Add goal</h3>
                <button class="mind-btn-sm mind-modal-close">✕</button>
            </div>
            <div class="pr-modal-body">
                <div class="mind-form">
                    <input type="text" id="pal-goal-title" placeholder="Title *" maxlength="200">
                    <textarea id="pal-goal-desc" rows="3" maxlength="500" placeholder="Context / success criteria (optional)"></textarea>
                    <select id="pal-goal-prio" class="palace-select">
                        <option value="medium" selected>● medium priority</option>
                        <option value="high">🔺 high priority</option>
                        <option value="low">○ low priority</option>
                    </select>
                    <label style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--text-muted);cursor:pointer">
                        <input type="checkbox" id="pal-goal-perm"> 🔒 Permanent (standing duty — never fades)
                    </label>
                    <button class="mind-btn" id="pal-goal-save">Create</button>
                </div>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector('.mind-modal-close').addEventListener('click', () => overlay.remove());
    setupModalClose(overlay, () => overlay.remove());
    overlay.querySelector('#pal-goal-save').addEventListener('click', async () => {
        const title = overlay.querySelector('#pal-goal-title').value.trim();
        if (!title) { ui.showToast('Title is required', 'error'); return; }
        try {
            await palaceSend('goals', 'POST', {
                scope, title,
                description: overlay.querySelector('#pal-goal-desc').value.trim() || null,
                priority: overlay.querySelector('#pal-goal-prio').value,
                permanent: overlay.querySelector('#pal-goal-perm').checked,
            });
            overlay.remove();
            ui.showToast('Goal created', 'success');
            renderList();
        } catch (e) { ui.showToast(`Create failed: ${e.message}`, 'error'); }
    });
    overlay.querySelector('#pal-goal-title').focus();
}
