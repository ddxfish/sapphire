// features/notify-sidebar.js - the chat sidebar's notification row, and the
// count on the sidebar's expand tab (tmp/notification-center-plan.md).
//
// Folded: one line. The bell, the top row's title (active work first, then
// the newest unread warning or error), a red count (errors + critical), a
// yellow one (warnings), a chevron. Hidden outright when nothing is unread and
// nothing is active; success and info never land here, they never count.
//
// Clicking the line opens the pane, and opening reads what it shows (Krem,
// 2026-10-31): those rows are marked read but stay listed until the pane folds,
// or they would vanish under the click. A row that arrives while open stays
// unread and keeps its count, since an open pane is no proof anyone saw it.
// Each row has its words, Open (if it points somewhere) and a remove button.
//
// A progress tick moves the bars in place: rebuilding the row ten times a
// second would drop any click whose press and release straddled a rebuild.
//
// The expand tab, the handle that shows when the sidebar is collapsed (every
// phone), wears the same count as a circle: red for errors, yellow for
// warnings, a pulsing dot while something is in progress. That is the mobile
// answer (Krem, 2026-10-20).
import * as notify from '../shared/notify.js';

const LEVELS_HERE = new Set(['warning', 'error', 'critical']);
let open = false;
let shown = new Set();       // ids that opening the pane read: listed until it folds
let painted = null;          // the shape last painted; a tick that keeps it moves bars only
let wired = false;
let el = null;
let tab = null;

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export function initNotifySidebar() {
    if (wired) return;       // main.js calls again when boot throws: a second listener would double-toggle
    wired = true;
    el = document.getElementById('sb-notify');
    tab = document.getElementById('chat-sidebar-expand');
    if (tab && !tab.querySelector('.sb-expand-count')) {
        const b = document.createElement('span');
        b.className = 'sb-expand-count hidden';
        tab.appendChild(b);
    }
    el?.addEventListener('click', onClick);
    notify.onChange(render);
    render();
}

function pick() {
    return notify.rows().filter(r => r.state === 'active' || shown.has(r.id) || (!r.read && LEVELS_HERE.has(r.level)));
}

const indeterminate = r => !r.progress || r.progress.indeterminate || r.progress.percent == null;

function bar(r) {
    return `<div class="nc-bar-track"><div class="nc-bar-fill${indeterminate(r) ? ' indeterminate' : ''}" data-bar="${esc(r.id)}" style="width:${indeterminate(r) ? 100 : r.progress.percent}%"></div></div>`;
}

function rowHtml(r) {
    const active = r.state === 'active';
    return `
        <div class="sb-notify-row sb-${active ? 'active' : r.level}">
            <span class="sb-notify-dot"></span>
            <div class="sb-notify-main">
                <div class="sb-notify-title">${esc(r.title)}</div>
                ${r.body ? `<div class="nc-body" data-body="${esc(r.id)}">${esc(r.body)}</div>` : ''}
                ${active ? bar(r) : ''}
                <div class="sb-notify-meta">
                    <span class="nc-meta">${esc(r.source)} · ${active ? 'in progress' : notify.ago(r.updated)}</span>
                    ${notify.repairButtonHtml(r, 'nc-repair')}
                    ${r.go ? `<button type="button" class="sb-notify-open" data-go="${esc(r.id)}">Open →</button>` : ''}
                </div>
            </div>
            ${active ? '' : `<button type="button" class="nc-x" data-x="${esc(r.id)}" title="Remove">×</button>`}
        </div>`;
}

// Everything a repaint would change except what a tick moves in place.
function shape(rows, red, yellow) {
    return JSON.stringify([open, red, yellow, rows.map(r => {
        const active = r.state === 'active';
        return [r.id, r.state, r.level, r.title, r.source, !!r.go, active ? !!r.body : r.body, active ? 0 : r.updated,
                notify.repairButtonHtml(r)];
    })]);
}

function moveBars(rows) {
    for (const r of rows) {
        if (r.state !== 'active') continue;
        const id = CSS.escape(r.id);
        el.querySelectorAll(`[data-bar="${id}"]`).forEach(b => {
            b.classList.toggle('indeterminate', indeterminate(r));
            b.style.width = `${indeterminate(r) ? 100 : r.progress.percent}%`;
        });
        const words = el.querySelector(`[data-body="${id}"]`);
        if (words) words.textContent = r.body;
        const head = el.querySelector(`.sb-notify-head[data-head="${id}"]`);
        if (head) head.title = r.body || r.title;
    }
}

function render() {
    const { red, yellow, active } = notify.counts();
    if (tab) {
        const b = tab.querySelector('.sb-expand-count');
        if (b) {
            const n = red || yellow;
            b.textContent = n ? String(n) : '';
            b.className = `sb-expand-count ${red ? 'red' : yellow ? 'yellow' : active ? 'busy' : 'hidden'}`;
        }
    }
    if (!el) return;
    const rows = pick();
    if (!rows.length) {
        el.classList.add('hidden');
        el.innerHTML = '';
        open = false;
        shown.clear();
        painted = null;
        return;
    }
    const next = shape(rows, red, yellow);
    if (next === painted) { moveBars(rows); return; }
    painted = next;
    el.classList.remove('hidden');
    const top = el.querySelector('.sb-notify-list')?.scrollTop || 0;
    const head = rows[0];
    const counts = `${red ? `<span class="nc-count red">${red}</span>` : ''}${yellow ? `<span class="nc-count yellow">${yellow}</span>` : ''}`;
    el.innerHTML = open ? `
        <button type="button" class="sb-notify-head" aria-expanded="true" title="Fold">
            <span class="sb-notify-bell">\u{1F514}</span>
            <span class="sb-notify-title">Notifications</span>
            ${counts}
            <span class="sb-notify-chev">▴</span>
        </button>
        <div class="sb-notify-list">${rows.map(rowHtml).join('')}</div>
        <button type="button" class="sb-notify-more" data-more="1">All notifications</button>` : `
        <button type="button" class="sb-notify-head sb-${head.state === 'active' ? 'active' : head.level}" data-head="${esc(head.id)}" aria-expanded="false" title="${esc(head.body || head.title)}">
            <span class="sb-notify-bell">\u{1F514}</span>
            <span class="sb-notify-title">${esc(head.title)}</span>
            ${counts}
            <span class="sb-notify-chev">▾</span>
        </button>
        ${head.state === 'active' ? bar(head) : ''}`;
    const list = el.querySelector('.sb-notify-list');
    if (list) list.scrollTop = top;
}

function toggle() {
    open = !open;
    if (open) {
        const rows = pick();
        shown = new Set(rows.map(r => r.id));    // before the marks: each one repaints
        for (const r of rows) if (r.state !== 'active') notify.markRead(r.id);
    } else {
        shown.clear();
    }
    render();
    el.querySelector('.sb-notify-head')?.focus();
}

async function onClick(e) {
    if (e.target.closest('.sb-notify-head')) { toggle(); return; }
    if (e.target.closest('[data-more]')) {
        const router = await import('../core/router.js');
        router.switchView('notifications');
        return;
    }
    if (notify.handleRepairClick(e)) return;
    const x = e.target.closest('[data-x]');
    if (x) { notify.dismiss(x.dataset.x); return; }
    const go = e.target.closest('[data-go]');
    if (!go) return;
    const r = notify.rows().find(q => q.id === go.dataset.go);
    if (!r) return;
    if (r.state !== 'active') notify.markRead(r.id);
    notify.goTo(r.go);
}
