// views/notifications.js - System > Notifications: the notification center's
// page (tmp/notification-center-plan.md, 2026-10-20). Every row the engine
// kept, active rows first, then newest first; filters by level and by source;
// opening the page marks everything read (Krem's rule), so the counts in the
// chat sidebar go to zero. Rows that were unread when the page opened stay
// bold for this visit, so what was new is still visible after it is read. A
// row with a target is a link: click = go where it points. The gear holds the
// two toast settings. The mirror in shared/notify.js is the data; this view
// only paints it.
import * as notify from '../shared/notify.js';
import { renderSectionHeader, bindSectionHeader } from '../shared/section-header.js';
import { updateSettingsBatch } from '../shared/settings-api.js';

let container = null;
let unsub = null;
let filter = 'all';          // all | active | critical | error | warning | success | info
let source = '';             // '' = every source
let fresh = new Set();       // ids unread when the page opened: bold this visit
let gearOpen = false;

const TABS = [{ id: 'notifications', label: 'Notifications', icon: '\u{1F514}' }];
const CHIPS = [['all', 'All'], ['active', 'Active'], ['critical', 'Critical'], ['error', 'Error'],
               ['warning', 'Warning'], ['success', 'Success'], ['info', 'Info']];
const ICON = { info: 'ℹ', success: '✓', warning: '⚠', error: '✖', critical: '‼', active: '↻' };
const TOAST_LEVELS = ['success', 'warning', 'error', 'critical'];

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export default {
    init(el) { container = el; },
    show() {
        fresh = new Set(notify.rows({ unread: true }).map(r => r.id));
        render();
        unsub = notify.onChange(update);
        notify.markAllRead();
    },
    hide() { if (unsub) { unsub(); unsub = null; } },
};

function ago(ts) {
    const s = Math.max(0, Date.now() / 1000 - (ts || 0));
    if (s < 45) return 'just now';
    if (s < 3600) return `${Math.round(s / 60)}m ago`;
    if (s < 86400) return `${Math.round(s / 3600)}h ago`;
    if (s < 172800) return 'yesterday';
    const d = new Date(ts * 1000);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function render() {
    if (!container) return;
    container.innerHTML = `
        ${renderSectionHeader({ tabs: TABS, active: 'notifications', status: '' })}
        <div class="view-body view-scroll nc-page">
            <div class="nc-bar">
                <div class="nc-chips">${CHIPS.map(([id, label]) =>
                    `<button type="button" class="nc-chip${filter === id ? ' active' : ''}" data-filter="${id}">${label}<span class="nc-chip-n" data-n="${id}"></span></button>`).join('')}</div>
                <div class="nc-tools">
                    <select class="nc-source" id="nc-source" title="Only rows from one source"></select>
                    <button type="button" class="btn-sm" id="nc-clear" title="Remove every row that is not active">Clear all</button>
                    <button type="button" class="btn-sm" id="nc-gear" title="Toast settings">⚙</button>
                </div>
            </div>
            <div class="nc-gear${gearOpen ? '' : ' hidden'}" id="nc-gear-panel"></div>
            <div class="nc-list" id="nc-list"></div>
        </div>`;
    bindSectionHeader(container);
    container.querySelector('.nc-chips')?.addEventListener('click', e => {
        const chip = e.target.closest('[data-filter]');
        if (!chip) return;
        filter = chip.dataset.filter;
        container.querySelectorAll('.nc-chip').forEach(c => c.classList.toggle('active', c.dataset.filter === filter));
        update();
    });
    container.querySelector('#nc-source')?.addEventListener('change', e => { source = e.target.value; update(); });
    container.querySelector('#nc-clear')?.addEventListener('click', () => { notify.clearAll(); });
    container.querySelector('#nc-gear')?.addEventListener('click', () => {
        gearOpen = !gearOpen;
        const panel = container.querySelector('#nc-gear-panel');
        panel.classList.toggle('hidden', !gearOpen);
        if (gearOpen) renderGear(panel);
    });
    container.querySelector('#nc-list')?.addEventListener('click', e => {
        const x = e.target.closest('[data-x]');
        if (x) { e.stopPropagation(); notify.dismiss(x.dataset.x); return; }
        const row = e.target.closest('.nc-row');
        if (!row) return;
        const r = notify.rows().find(q => q.id === row.dataset.id);
        if (r?.go) notify.goTo(r.go);
    });
    if (gearOpen) renderGear(container.querySelector('#nc-gear-panel'));
    update();
}

function renderGear(panel) {
    if (!panel) return;
    const s = notify.settings();
    panel.innerHTML = `
        <label>Toast seconds <input type="number" id="nc-seconds" min="1" max="60" step="1" value="${esc(s.seconds)}"></label>
        <label>Toast from level <select id="nc-min">${TOAST_LEVELS.map(l =>
            `<option value="${l}"${s.minLevel === l ? ' selected' : ''}>${l}</option>`).join('')}</select></label>
        <span class="nc-gear-help">Info never toasts: it is the log. Quick confirmations like Copied always show.</span>`;
    const save = async (settings) => {
        try {
            await updateSettingsBatch(settings);
            notify.applySettings(settings);
            notify.flash('Saved', 'success', 2);
        } catch (e) {
            notify.flash(`Could not save: ${e.message || e}`, 'error');
        }
    };
    panel.querySelector('#nc-seconds')?.addEventListener('change', e => {
        const n = Math.max(1, Math.min(60, Math.round(Number(e.target.value) || 4)));
        e.target.value = n;
        save({ NOTIFY_TOAST_SECONDS: n });
    });
    panel.querySelector('#nc-min')?.addEventListener('change', e => save({ NOTIFY_TOAST_MIN_LEVEL: e.target.value }));
}

function visible() {
    const all = notify.rows();
    let out = all;
    if (filter === 'active') out = out.filter(r => r.state === 'active');
    else if (filter !== 'all') out = out.filter(r => r.level === filter);
    if (source) out = out.filter(r => r.source === source);
    return { all, out };
}

function update() {
    if (!container) return;
    const { all, out } = visible();
    // rows that arrive while the page is open are read too (the person is looking)
    const arrived = all.filter(r => !r.read && !fresh.has(r.id));
    if (arrived.length) { for (const r of arrived) fresh.add(r.id); notify.markAllRead(); }

    const n = { all: all.length, active: 0, critical: 0, error: 0, warning: 0, success: 0, info: 0 };
    for (const r of all) { if (r.state === 'active') n.active++; n[r.level] = (n[r.level] || 0) + 1; }
    container.querySelectorAll('.nc-chip-n').forEach(el => { const v = n[el.dataset.n] || 0; el.textContent = v ? ` ${v}` : ''; });

    const sel = container.querySelector('#nc-source');
    if (sel) {
        const sources = [...new Set(all.map(r => r.source))].sort();
        const want = sources.includes(source) ? source : '';
        sel.innerHTML = `<option value="">All sources</option>${sources.map(s => `<option value="${esc(s)}"${s === want ? ' selected' : ''}>${esc(s)}</option>`).join('')}`;
        source = want;
    }

    const status = container.querySelector('.section-status');
    if (status) {
        const c = notify.counts();
        status.textContent = `${all.length} row${all.length === 1 ? '' : 's'} · ${c.active} active · ${fresh.size} new this visit`;
    }

    const list = container.querySelector('#nc-list');
    if (!list) return;
    const scrollEl = container.querySelector('.view-scroll');
    const top = scrollEl?.scrollTop || 0;
    list.innerHTML = out.length ? out.map(rowHtml).join('')
        : `<div class="nc-empty">${all.length ? 'Nothing matches this filter.' : 'Nothing yet. Downloads, plugin errors, failed tasks and backup alerts land here.'}</div>`;
    if (scrollEl) scrollEl.scrollTop = top;
}

function rowHtml(r) {
    const lvl = r.state === 'active' ? 'active' : r.level;
    const p = r.progress;
    const indeterminate = r.state === 'active' && (!p || p.indeterminate || p.percent == null);
    const bar = r.state === 'active'
        ? `<div class="nc-bar-track"><div class="nc-bar-fill${indeterminate ? ' indeterminate' : ''}" style="width:${indeterminate ? 100 : p.percent}%"></div></div>` : '';
    const pct = r.state === 'active' && !indeterminate ? `<span class="nc-pct">${p.percent}%</span>` : '';
    const tail = r.state === 'failed' ? ' · failed' : r.state === 'done' ? ' · done' : r.state === 'active' ? ' · in progress' : '';
    return `<div class="nc-row nc-${lvl}${fresh.has(r.id) ? ' nc-new' : ''}${r.go ? ' nc-link' : ''}" data-id="${esc(r.id)}"${r.go ? ' title="Open"' : ''}>
        <span class="nc-icon" aria-hidden="true">${ICON[lvl] || ''}</span>
        <div class="nc-main">
            <div class="nc-title">${esc(r.title)}${pct}</div>
            ${r.body ? `<div class="nc-body">${esc(r.body)}</div>` : ''}
            ${bar}
            <div class="nc-meta">${esc(r.source)} · ${ago(r.updated)}${tail}</div>
        </div>
        ${r.state !== 'active' ? `<button type="button" class="nc-x" data-x="${esc(r.id)}" title="Remove">×</button>` : ''}
    </div>`;
}
