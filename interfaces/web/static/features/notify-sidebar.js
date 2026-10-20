// features/notify-sidebar.js - the chat sidebar's notification row, and the
// count on the sidebar's expand tab (tmp/notification-center-plan.md).
//
// Folded: one line. The bell, the top row's title (active work first, then
// the newest unread warning or error), a red count (errors + critical), a
// yellow one (warnings), a chevron. Unfolded: the next few rows and "Show
// more", which opens System > Notifications. Hidden outright when nothing is
// unread and nothing is active; success and info never land here, they never
// count. Clicking a row marks it read and goes where it points.
//
// The expand tab, the handle that shows when the sidebar is collapsed (every
// phone), wears the same count as a circle: red for errors, yellow for
// warnings, a pulsing dot while something is in progress. That is the mobile
// answer (Krem, 2026-10-20).
import * as notify from '../shared/notify.js';

const SHOW_MAX = 5;
const LEVELS_HERE = new Set(['warning', 'error', 'critical']);
let open = false;
let el = null;
let tab = null;

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export function initNotifySidebar() {
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
    return notify.rows().filter(r => r.state === 'active' || (!r.read && LEVELS_HERE.has(r.level)));
}

function bar(r) {
    const p = r.progress;
    const indeterminate = !p || p.indeterminate || p.percent == null;
    return `<div class="nc-bar-track"><div class="nc-bar-fill${indeterminate ? ' indeterminate' : ''}" style="width:${indeterminate ? 100 : p.percent}%"></div></div>`;
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
        return;
    }
    el.classList.remove('hidden');
    const head = rows[0];
    const rest = rows.slice(1, 1 + SHOW_MAX);
    const more = rows.length > 1 || rest.length;
    el.innerHTML = `
        <div class="sb-notify-head sb-${head.state === 'active' ? 'active' : head.level}" data-id="${esc(head.id)}" role="button" tabindex="0" title="${esc(head.body || head.title)}">
            <span class="sb-notify-bell">\u{1F514}</span>
            <span class="sb-notify-title">${esc(head.title)}</span>
            ${red ? `<span class="nc-count red">${red}</span>` : ''}${yellow ? `<span class="nc-count yellow">${yellow}</span>` : ''}
            <span class="sb-notify-chev" data-toggle="1" title="${open ? 'Fold' : 'Show more'}">${open ? '▴' : '▾'}</span>
        </div>
        ${head.state === 'active' ? bar(head) : ''}
        <div class="sb-notify-list${open ? '' : ' hidden'}">
            ${rest.map(r => `
                <div class="sb-notify-row sb-${r.state === 'active' ? 'active' : r.level}" data-id="${esc(r.id)}" role="button" tabindex="0" title="${esc(r.body || r.title)}">
                    <span class="sb-notify-dot"></span><span class="sb-notify-title">${esc(r.title)}</span>
                </div>${r.state === 'active' ? bar(r) : ''}`).join('')}
            <button type="button" class="sb-notify-more" data-more="1">${more ? 'Show more' : 'Open notifications'}</button>
        </div>`;
}

async function onClick(e) {
    if (e.target.closest('[data-toggle]')) {
        open = !open;
        render();
        return;
    }
    if (e.target.closest('[data-more]')) {
        const router = await import('../core/router.js');
        router.switchView('notifications');
        return;
    }
    const hit = e.target.closest('[data-id]');
    if (!hit) return;
    const r = notify.rows().find(q => q.id === hit.dataset.id);
    if (!r) return;
    if (r.state !== 'active') notify.markRead(r.id);
    if (r.go) notify.goTo(r.go);
}
