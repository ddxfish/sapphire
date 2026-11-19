// The Calendar page: year, month (the default), week and day views over the
// calendar (routes/events.py). Events she adds with her tools show up on the
// next refresh: the page refetches when it regains focus and every 30 seconds
// while it is on screen.
//
// A calendar can have links (routes/links.py): a Google calendar (two-way) or
// an ICS address (read-only). Their events are copies kept by the server; the
// "Calendars" dialog is where links are made and removed.
//
// App contract: docs/plugin-author/APPS.md. All state lives inside render(),
// which returns its own teardown.
import { fetchWithTimeout } from '/static/shared/fetch.js';
import { setupModalClose, escapeHtml, showConfirm, showPrompt } from '/static/shared/modal.js';
import { showToast } from '/static/shared/notify.js';

const API = '/api/plugin/calendar';
const VIEWS = ['year', 'month', 'week', 'day'];
const VIEW_LABEL = { year: 'Year', month: 'Month', week: 'Week', day: 'Day' };
const HOUR_PX = 44;
const REFRESH_MS = 30000;
const SYNC_POLL_MS = 2500;                    // while the server is refreshing a link
const MAX_CHIPS = 3;
const NEW_CALENDAR = '+new';                  // '+' can never be in a calendar's name

let cssLink = null;
let cssUsers = 0;

// -- dates (all local wall time; 'YYYY-MM-DD' strings on the wire) ------------

const pad = n => String(n).padStart(2, '0');
const ymd = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const fromYmd = s => new Date(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10));
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const minutesOf = s => (s.length > 10 ? +s.slice(11, 13) * 60 + +s.slice(14, 16) : 0);
const pyWeekday = d => (d.getDay() + 6) % 7;                 // the server counts Monday as 0

const HOUR12 = (() => {
    try { return !!new Intl.DateTimeFormat(undefined, { hour: 'numeric' }).resolvedOptions().hour12; }
    catch { return true; }
})();

function shortTime(s) {                                      // '2026-11-18T09:30' -> '9:30a' or '09:30'
    const h = +s.slice(11, 13), m = s.slice(14, 16);
    if (!HOUR12) return `${pad(h)}:${m}`;
    return `${h % 12 || 12}${m === '00' ? '' : ':' + m}${h < 12 ? 'a' : 'p'}`;
}

function hourLabel(h) {
    if (!HOUR12) return `${pad(h)}:00`;
    return `${h % 12 || 12} ${h < 12 ? 'AM' : 'PM'}`;
}

const fmt = (d, opts) => d.toLocaleDateString(undefined, opts);

function whenWords(ev) {
    const a = fromYmd(ev.start), b = fromYmd(ev.end);
    const day = d => fmt(d, { weekday: 'short', month: 'short', day: 'numeric' });
    if (ev.all_day) return ev.start === ev.end ? day(a) : `${day(a)} to ${day(b)}`;
    if (ev.start.slice(0, 10) === ev.end.slice(0, 10)) return `${day(a)}, ${shortTime(ev.start)} to ${shortTime(ev.end)}`;
    return `${day(a)} ${shortTime(ev.start)} to ${day(b)} ${shortTime(ev.end)}`;
}

// The last day an occurrence shows on. A timed event that ends at midnight
// belongs to the day before.
function lastDayOf(ev) {
    const end = ev.end.slice(0, 10);
    if (!ev.all_day && end > ev.start.slice(0, 10) && ev.end.slice(11, 16) === '00:00') return ymd(addDays(fromYmd(end), -1));
    return end;
}

function agoWords(stamp) {
    if (!stamp) return 'not read yet';
    const mins = Math.max(0, Math.round((Date.now() / 1000 - stamp) / 60));
    if (mins < 1) return 'refreshed just now';
    if (mins < 60) return `refreshed ${mins} min ago`;
    if (mins < 1440) return `refreshed ${Math.round(mins / 60)} h ago`;
    return `refreshed ${Math.round(mins / 1440)} d ago`;
}

const safeLink = url => (/^https?:\/\//i.test(url || '') ? url : '');

function remember(key, value) {
    try {
        if (value === undefined) return localStorage.getItem('sapphire.calendar.' + key);
        localStorage.setItem('sapphire.calendar.' + key, value);
    } catch { /* storage blocked: the page still works */ }
    return null;
}

// -- the page -----------------------------------------------------------------

export function render(root) {
    if (!cssLink) {
        cssLink = document.createElement('link');
        cssLink.rel = 'stylesheet';
        const v = document.querySelector('meta[name="boot-version"]')?.content || '';
        cssLink.href = `/plugin-web/calendar/app/calendar.css?v=${v}`;
        document.head.appendChild(cssLink);
    }
    cssUsers++;

    const savedView = remember('view');
    const st = {
        view: VIEWS.includes(savedView) ? savedView : 'month',
        cursor: new Date(new Date().getFullYear(), new Date().getMonth(), new Date().getDate()),
        today: ymd(new Date()),
        events: [],
        calendars: [{ name: 'default', events: 0 }],
        links: [],
        refreshing: false,
        quick: null,
        filter: remember('filter') || '',                  // '' = every calendar
        weekStart: 0,                                      // 0 Sunday, 1 Monday
        key: '',
        seq: 0,
        loaded: false,
        dead: false,
        modals: new Set(),
    };

    root.innerHTML = `
        <div class="cal-root">
            <div class="cal-bar">
                <div class="cal-nav">
                    <button class="btn cal-prev" title="Earlier" aria-label="Earlier">&#8249;</button>
                    <button class="btn cal-today">Today</button>
                    <button class="btn cal-next" title="Later" aria-label="Later">&#8250;</button>
                </div>
                <h2 class="cal-title"></h2>
                <div class="cal-tools">
                    <div class="cal-views" role="tablist">
                        ${VIEWS.map(v => `<button class="cal-view-btn" data-view="${v}" role="tab">${VIEW_LABEL[v]}</button>`).join('')}
                    </div>
                    <select class="cal-filter" title="Which calendar to show" aria-label="Calendar"></select>
                    <button class="btn cal-drop hover-danger" title="Delete this calendar" hidden>Delete calendar</button>
                    <button class="btn cal-links" title="Linked calendars: Google, or a calendar address">Calendars</button>
                    <button class="btn btn-primary cal-add">+ Event</button>
                </div>
            </div>
            <div class="cal-body"><div class="cal-empty">Loading the calendar...</div></div>
        </div>`;
    const $ = sel => root.querySelector(sel);
    const body = $('.cal-body');

    // -- range and data --------------------------------------------------------

    const startOfWeek = d => addDays(d, -((d.getDay() - st.weekStart + 7) % 7));

    function range() {
        const c = st.cursor;
        if (st.view === 'day') return [c, c];
        if (st.view === 'week') { const a = startOfWeek(c); return [a, addDays(a, 6)]; }
        if (st.view === 'year') return [new Date(c.getFullYear(), 0, 1), new Date(c.getFullYear(), 11, 31)];
        const a = startOfWeek(new Date(c.getFullYear(), c.getMonth(), 1));
        return [a, addDays(a, 41)];
    }

    async function load(quiet = false) {
        if (st.dead) return;
        const mine = ++st.seq;
        const [a, b] = range();
        const q = new URLSearchParams({ start: ymd(a), end: ymd(b) });
        if (st.filter) q.set('calendar', st.filter);
        let data;
        try {
            data = await fetchWithTimeout(`${API}/events?${q}`, {}, 20000);
        } catch (e) {
            if (st.dead || mine !== st.seq) return;
            // A calendar that was deleted elsewhere: fall back to all of them once.
            if (st.filter && /no calendar named/i.test(e.message || '')) {
                st.filter = '';
                remember('filter', '');
                return load(quiet);
            }
            if (!quiet || !st.loaded) body.innerHTML = `<div class="cal-empty cal-error">${escapeHtml(e.message || "Couldn't load the calendar.")}</div>`;
            st.key = '';
            return;
        }
        if (st.dead || mine !== st.seq || !data) return;
        const weekStart = data.week_start === 'monday' ? 1 : 0;
        const key = JSON.stringify([st.view, ymd(a), ymd(b), st.filter, data.events, data.calendars, data.today, weekStart,
            (data.links || []).map(l => [l.id, l.name, l.color, l.error, l.target])]);
        st.links = data.links || [];
        st.refreshing = !!data.refreshing;
        // The server is reading a linked calendar: ask again soon, so its events appear when they land.
        clearTimeout(st.quick);
        if (st.refreshing) st.quick = setTimeout(() => { if (onScreen()) load(true); }, SYNC_POLL_MS);
        paintLinksButton();
        if (weekStart !== st.weekStart) {                 // the grid's first column moved: ask again for its range
            st.weekStart = weekStart;
            if (st.view === 'month' || st.view === 'week') return load(quiet);
        }
        st.loaded = true;
        if (key === st.key) return;                       // a quiet refresh that found nothing new
        st.key = key;
        st.events = data.events || [];
        st.calendars = data.calendars?.length ? data.calendars : st.calendars;
        st.today = data.today || st.today;
        paint();
    }

    // Occurrences by the days they show on, inside the loaded range.
    function byDay() {
        const [a, b] = range();
        const lo = ymd(a), hi = ymd(b);
        const map = new Map();
        for (const ev of st.events) {
            let day = ev.start.slice(0, 10) < lo ? lo : ev.start.slice(0, 10);
            const last = lastDayOf(ev) > hi ? hi : lastDayOf(ev);
            for (let guard = 0; day <= last && guard < 800; guard++) {
                if (!map.has(day)) map.set(day, []);
                map.get(day).push(ev);
                day = ymd(addDays(fromYmd(day), 1));
            }
        }
        return map;
    }

    const colorOf = ev => {
        if (ev.color != null) return `cal-c${ev.color % 6}`;          // a linked calendar has its own colour
        const i = st.calendars.findIndex(c => c.name === ev.calendar);
        return `cal-c${(i < 0 ? 0 : i) % 6}`;
    };

    function paintLinksButton() {
        const btn = $('.cal-links');
        const shown = st.links.filter(l => !st.filter || l.calendar === st.filter);
        const bad = shown.filter(l => l.error);
        btn.classList.toggle('warn', bad.length > 0);
        btn.classList.toggle('busy', st.refreshing);
        btn.title = bad.length ? `${bad[0].name}: ${bad[0].error}`
            : (st.refreshing ? 'Refreshing linked calendars...' : 'Linked calendars: Google, or a calendar address');
    }

    // -- toolbar ---------------------------------------------------------------

    function title() {
        const c = st.cursor;
        if (st.view === 'year') return String(c.getFullYear());
        if (st.view === 'month') return fmt(c, { month: 'long', year: 'numeric' });
        if (st.view === 'day') return fmt(c, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
        const [a, b] = range();
        const left = fmt(a, { month: 'short', day: 'numeric' });
        const right = a.getMonth() === b.getMonth() ? String(b.getDate()) : fmt(b, { month: 'short', day: 'numeric' });
        return `${left} to ${right}, ${b.getFullYear()}`;
    }

    function paintBar() {
        $('.cal-title').textContent = title();
        root.querySelectorAll('.cal-view-btn').forEach(b => {
            const on = b.dataset.view === st.view;
            b.classList.toggle('active', on);
            b.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        const sel = $('.cal-filter');
        const many = st.calendars.length > 1;
        sel.innerHTML = (many ? `<option value="">All calendars</option>` : '')
            + st.calendars.map(c => `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)}</option>`).join('')
            + `<option value="${NEW_CALENDAR}">+ New calendar...</option>`;
        sel.value = many ? st.filter : st.calendars[0].name;
        if (sel.selectedIndex < 0) sel.selectedIndex = 0;
        $('.cal-drop').hidden = !(st.filter && st.filter !== 'default');
    }

    function go(view, day) {
        if (view) { st.view = view; remember('view', view); }
        if (day) st.cursor = fromYmd(day);
        st.key = '';
        paintBar();
        load();
    }

    function step(n) {
        const c = st.cursor;
        if (st.view === 'day') st.cursor = addDays(c, n);
        else if (st.view === 'week') st.cursor = addDays(c, 7 * n);
        else if (st.view === 'year') st.cursor = new Date(c.getFullYear() + n, c.getMonth(), 1);
        else st.cursor = new Date(c.getFullYear(), c.getMonth() + n, 1);
        go();
    }

    // -- views -----------------------------------------------------------------

    function paint() {
        paintBar();
        const scroller = body.querySelector('.cal-tg-scroll');
        const keepScroll = scroller ? scroller.scrollTop : null;
        if (st.view === 'month') body.innerHTML = monthHtml();
        else if (st.view === 'year') body.innerHTML = yearHtml();
        else body.innerHTML = timeGridHtml();
        const now = body.querySelector('.cal-tg-scroll');
        if (now) now.scrollTop = keepScroll ?? Math.max(0, 7 * HOUR_PX - 8);       // first paint opens at 7
    }

    function weekdayHeads(short) {
        const out = [];
        for (let i = 0; i < 7; i++) {
            const d = new Date(2023, 0, 1 + st.weekStart + i);              // Jan 1 2023 was a Sunday
            out.push(fmt(d, { weekday: short ? 'narrow' : 'short' }));
        }
        return out;
    }

    function chipHtml(ev, day) {
        const cont = ev.start.slice(0, 10) < day;
        const time = (!ev.all_day && !cont) ? `<span class="cal-chip-time">${shortTime(ev.start)}</span>` : '';
        const cls = ['cal-chip', colorOf(ev), ev.all_day || cont || lastDayOf(ev) > day ? 'solid' : '', cont ? 'cont' : ''].join(' ');
        return `<button class="${cls}" data-ev="${escapeHtml(ev.id)}" data-day="${escapeHtml(ev.day)}"
                    title="${escapeHtml(ev.title + ' - ' + whenWords(ev))}">${time}<span class="cal-chip-title">${escapeHtml(ev.title)}</span></button>`;
    }

    function monthHtml() {
        const [a] = range();
        const days = byDay();
        const month = st.cursor.getMonth();
        let cells = '';
        for (let i = 0; i < 42; i++) {
            const d = addDays(a, i), key = ymd(d);
            const list = days.get(key) || [];
            const shown = list.slice(0, list.length > MAX_CHIPS ? MAX_CHIPS - 1 : MAX_CHIPS);
            const more = list.length - shown.length;
            cells += `<div class="cal-cell${d.getMonth() !== month ? ' other' : ''}${key === st.today ? ' today' : ''}" data-day="${key}">
                <div class="cal-cell-head"><button class="cal-daynum" data-goto="${key}" title="Open this day">${d.getDate()}</button></div>
                ${shown.map(ev => chipHtml(ev, key)).join('')}
                ${more > 0 ? `<button class="cal-more" data-goto="${key}">+${more} more</button>` : ''}
            </div>`;
        }
        return `<div class="cal-month">
            <div class="cal-dow">${weekdayHeads(false).map(w => `<div>${escapeHtml(w)}</div>`).join('')}</div>
            <div class="cal-grid">${cells}</div>
        </div>`;
    }

    function yearHtml() {
        const year = st.cursor.getFullYear();
        const days = byDay();
        const heads = weekdayHeads(true).map(w => `<span>${escapeHtml(w)}</span>`).join('');
        let out = '';
        for (let m = 0; m < 12; m++) {
            const first = new Date(year, m, 1);
            const lead = (first.getDay() - st.weekStart + 7) % 7;
            const count = new Date(year, m + 1, 0).getDate();
            let cells = '<span></span>'.repeat(lead);
            for (let n = 1; n <= count; n++) {
                const key = `${year}-${pad(m + 1)}-${pad(n)}`;
                const list = days.get(key) || [];
                const tip = list.length ? list.slice(0, 6).map(e => e.title).join(', ') + (list.length > 6 ? ', ...' : '') : '';
                cells += `<button class="cal-mini-day${key === st.today ? ' today' : ''}${list.length ? ' has has-' + Math.min(list.length, 3) : ''}"
                    data-goto="${key}"${tip ? ` title="${escapeHtml(tip)}"` : ''}>${n}</button>`;
            }
            out += `<div class="cal-mini">
                <button class="cal-mini-name" data-month="${year}-${pad(m + 1)}-01">${escapeHtml(fmt(first, { month: 'long' }))}</button>
                <div class="cal-mini-dow">${heads}</div>
                <div class="cal-mini-grid">${cells}</div>
            </div>`;
        }
        return `<div class="cal-year">${out}</div>`;
    }

    // Side-by-side columns for timed events that overlap on one day.
    function pack(blocks) {
        blocks.sort((x, y) => x.from - y.from || y.to - x.to);
        let cluster = [], clusterEnd = -1;
        const close = () => {
            const cols = cluster.reduce((n, b) => Math.max(n, b.col + 1), 0);
            cluster.forEach(b => { b.cols = cols; });
            cluster = [];
        };
        for (const b of blocks) {
            if (cluster.length && b.from >= clusterEnd) close();
            const taken = new Set(cluster.filter(o => o.to > b.from).map(o => o.col));
            let col = 0;
            while (taken.has(col)) col++;
            b.col = col;
            cluster.push(b);
            clusterEnd = Math.max(clusterEnd, b.to);
        }
        if (cluster.length) close();
        return blocks;
    }

    function timeGridHtml() {
        const [a, b] = range();
        const count = Math.round((b - a) / 86400000) + 1;
        const days = byDay();
        const now = new Date();
        let heads = '', allDay = '', cols = '';
        for (let i = 0; i < count; i++) {
            const d = addDays(a, i), key = ymd(d);
            const list = days.get(key) || [];
            const isToday = key === st.today;
            heads += `<button class="cal-tg-day${isToday ? ' today' : ''}" data-goto="${key}" title="Open this day">
                <span class="cal-tg-dow">${escapeHtml(fmt(d, { weekday: 'short' }))}</span><span class="cal-tg-num">${d.getDate()}</span></button>`;
            // All-day events, and timed ones that fill this whole day, sit in the top row.
            const top = [], blocks = [];
            for (const ev of list) {
                const from = ev.start.slice(0, 10) < key ? 0 : minutesOf(ev.start);
                const to = lastDayOf(ev) > key ? 1440 : (ev.end.slice(0, 10) > key ? 1440 : minutesOf(ev.end));
                if (ev.all_day || (from === 0 && to === 1440)) top.push(ev);
                else blocks.push({ ev, from, to: Math.max(to, from + 15) });
            }
            allDay += `<div class="cal-tg-allcell" data-day="${key}">${top.map(ev => chipHtml(ev, key)).join('')}</div>`;
            const evs = pack(blocks).map(bk => {
                const topPx = bk.from / 60 * HOUR_PX;
                const height = Math.max(18, (bk.to - bk.from) / 60 * HOUR_PX - 2);
                const ev = bk.ev;
                return `<button class="cal-block ${colorOf(ev)}" data-ev="${escapeHtml(ev.id)}" data-day="${escapeHtml(ev.day)}"
                    style="top:${topPx}px;height:${height}px;left:${bk.col / bk.cols * 100}%;width:${100 / bk.cols}%"
                    title="${escapeHtml(ev.title + ' - ' + whenWords(ev))}">
                    <span class="cal-block-title">${escapeHtml(ev.title)}</span>
                    <span class="cal-block-time">${shortTime(ev.start)} to ${shortTime(ev.end)}${ev.location ? ' - ' + escapeHtml(ev.location) : ''}</span>
                </button>`;
            }).join('');
            const line = (isToday && key === ymd(now))
                ? `<div class="cal-now" style="top:${(now.getHours() * 60 + now.getMinutes()) / 60 * HOUR_PX}px"></div>` : '';
            cols += `<div class="cal-tg-col${isToday ? ' today' : ''}" data-day="${key}">${evs}${line}</div>`;
        }
        let hours = '';
        for (let h = 0; h < 24; h++) hours += `<div class="cal-tg-hour" style="top:${h * HOUR_PX}px">${h ? hourLabel(h) : ''}</div>`;
        // One scroll box for everything, with the day names and the all-day row
        // stuck to its top: the columns can never drift apart by a scrollbar's width.
        return `<div class="cal-tg cal-tg-scroll${count === 1 ? ' one' : ''}" style="--cal-days:${count};--cal-hour:${HOUR_PX}px">
            <div class="cal-tg-wide">
                <div class="cal-tg-top">
                    <div class="cal-tg-head"><div class="cal-tg-gutter"></div>${heads}</div>
                    <div class="cal-tg-all"><div class="cal-tg-gutter">all day</div>${allDay}</div>
                </div>
                <div class="cal-tg-body" style="height:${24 * HOUR_PX}px">
                    <div class="cal-tg-gutter">${hours}</div>${cols}
                </div>
            </div>
        </div>`;
    }

    // -- the event form ----------------------------------------------------------

    function openModal(html) {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay cal-modal';
        overlay.innerHTML = `<div class="modal-base">${html}</div>`;
        document.body.appendChild(overlay);
        requestAnimationFrame(() => overlay.classList.add('active'));
        let unhook = null;
        const close = () => {
            if (overlay.dataset.busy) return;
            unhook?.();
            st.modals.delete(close);
            overlay.classList.remove('active');
            setTimeout(() => overlay.remove(), 200);
        };
        unhook = setupModalClose(overlay, close);
        overlay.querySelectorAll('.cal-x').forEach(b => b.addEventListener('click', close));
        st.modals.add(close);
        return { overlay, close };
    }

    function findEvent(id, day) {
        return st.events.find(e => e.id === id && e.day === day) || st.events.find(e => e.id === id);
    }

    // ev: an occurrence to edit, or null for a new event preset to `preset`.
    function openForm(ev, preset = {}) {
        const edit = !!ev;
        const rule = (edit && ev.rule) || null;
        const custom = rule?.repeat === 'custom';
        const start = edit ? ev.series_start : preset.start;
        const end = edit ? ev.series_end : preset.end;
        const allDay = edit ? ev.all_day : !!preset.all_day;
        const calNames = st.calendars.map(c => c.name);
        const cal = edit ? ev.calendar : (st.filter || 'default');
        const linked = edit && ev.source !== 'local';                // a copy of an event kept somewhere else
        const locked = edit && !ev.editable;                        // ...that cannot be changed from here
        const series = linked && ev.repeats;                        // a Google repeat: its rule is Google's
        const guests = (edit && ev.guests) || [];
        const openUrl = edit ? safeLink(ev.url) : '';
        const targets = name => st.links.filter(l => l.calendar === name && l.kind === 'google' && l.writable);
        const toOptions = name => {
            const list = targets(name);
            if (!list.length) return '';
            const chosen = list.find(l => l.target)?.id || 'local';
            return `<option value="local"${chosen === 'local' ? ' selected' : ''}>This machine only</option>`
                + list.map(l => `<option value="${escapeHtml(l.id)}"${l.id === chosen ? ' selected' : ''}>${escapeHtml(l.name)}</option>`).join('');
        };
        const dayNames = [];
        for (let i = 0; i < 7; i++) {
            const d = new Date(2023, 0, 1 + st.weekStart + i);
            dayNames.push({ py: pyWeekday(d), label: fmt(d, { weekday: 'short' }) });
        }
        const repeat = custom ? 'custom' : (rule?.repeat || 'none');
        const endsMode = rule?.times ? 'times' : (rule?.until ? 'until' : 'never');

        const { overlay, close } = openModal(`
            <div class="modal-header">
                <h3>${edit ? 'Edit event' : 'New event'}</h3>
                <button class="close-btn cal-x" aria-label="Close">&times;</button>
            </div>
            <div class="modal-body cal-form">
                ${locked ? `<div class="cal-note">From the linked calendar "${escapeHtml(ev.link_name)}", which is read-only. Change it where that calendar is kept.</div>` : ''}
                ${linked && !locked ? `<div class="cal-note">Kept in "${escapeHtml(ev.link_name)}". Changes here are saved there.</div>` : ''}
                ${edit && ev.repeats && !linked ? `<div class="cal-note">This event repeats (${escapeHtml(ev.repeat_text)}). Changes here apply to every occurrence.</div>` : ''}
                ${series && !locked ? `<div class="cal-note">This event repeats. Saving asks whether to change only this day or every occurrence.</div>` : ''}
                ${edit && ev.by === 'ai' ? `<div class="cal-note">Added by your AI.</div>` : ''}
                <div class="modal-field">
                    <label for="cal-f-title">Title</label>
                    <input type="text" id="cal-f-title" maxlength="200" value="${escapeHtml(edit ? ev.title : (preset.title || ''))}">
                </div>
                <label class="checkbox-wrap cal-allday"><input type="checkbox" id="cal-f-allday"${allDay ? ' checked' : ''}><span>All day</span></label>
                <div class="cal-row">
                    <div class="modal-field"><label for="cal-f-sd">Starts</label>
                        <div class="cal-when"><input type="date" id="cal-f-sd" value="${start.slice(0, 10)}"><input type="time" id="cal-f-st" value="${start.slice(11, 16) || '09:00'}"></div></div>
                    <div class="modal-field"><label for="cal-f-ed">Ends</label>
                        <div class="cal-when"><input type="date" id="cal-f-ed" value="${end.slice(0, 10)}"><input type="time" id="cal-f-et" value="${end.slice(11, 16) || '10:00'}"></div></div>
                </div>
                <div class="modal-field">
                    <label for="cal-f-repeat">Repeat</label>
                    ${series ? `<div class="cal-note">Its repeat rule is kept in ${escapeHtml(ev.link_name)}.</div>` : custom ? `<div class="cal-note">A repeat rule this page cannot edit (${escapeHtml(ev.repeat_text)}). It is kept as it is.</div>` : `
                    <select id="cal-f-repeat">
                        ${[['none', 'Does not repeat'], ['daily', 'Daily'], ['weekly', 'Weekly'], ['monthly', 'Monthly'], ['yearly', 'Yearly']]
                            .map(([v, l]) => `<option value="${v}"${v === repeat ? ' selected' : ''}>${l}</option>`).join('')}
                    </select>
                    <div class="cal-repeat" hidden>
                        <div class="cal-inline">Every <input type="number" id="cal-f-every" min="1" max="999" value="${rule?.every || 1}"> <span id="cal-f-unit"></span></div>
                        <div class="cal-days" hidden>${dayNames.map(d => `<label class="cal-daybox"><input type="checkbox" value="${d.py}"${rule?.days?.includes(d.py) ? ' checked' : ''}><span>${escapeHtml(d.label)}</span></label>`).join('')}</div>
                        <div class="cal-inline">Ends
                            <select id="cal-f-ends">
                                <option value="never"${endsMode === 'never' ? ' selected' : ''}>never</option>
                                <option value="until"${endsMode === 'until' ? ' selected' : ''}>on a date</option>
                                <option value="times"${endsMode === 'times' ? ' selected' : ''}>after a number of times</option>
                            </select>
                            <input type="date" id="cal-f-until" value="${rule?.until || ''}" hidden>
                            <input type="number" id="cal-f-times" min="1" max="1000" value="${rule?.times || 10}" hidden>
                        </div>
                    </div>`}
                </div>
                <div class="modal-field"><label for="cal-f-where">Where</label>
                    <input type="text" id="cal-f-where" maxlength="200" value="${escapeHtml(edit ? ev.location : '')}"></div>
                <div class="modal-field"><label for="cal-f-notes">Notes</label>
                    <textarea id="cal-f-notes" rows="3" maxlength="4000">${escapeHtml(edit ? ev.notes : '')}</textarea></div>
                ${guests.length ? `<div class="modal-field"><label>Guests</label>
                    <div class="cal-guests">${guests.map(g => `<span class="cal-guest">${escapeHtml(g.name || g.email || '?')} <em>${escapeHtml(g.rsvp || 'pending')}</em></span>`).join('')}</div></div>` : ''}
                ${calNames.length > 1 && !linked ? `<div class="modal-field"><label for="cal-f-cal">Calendar</label>
                    <select id="cal-f-cal">${calNames.map(n => `<option value="${escapeHtml(n)}"${n === cal ? ' selected' : ''}>${escapeHtml(n)}</option>`).join('')}</select></div>` : ''}
                ${!edit ? `<div class="modal-field cal-to-field"${toOptions(cal) ? '' : ' hidden'}><label for="cal-f-to">Save to</label>
                    <select id="cal-f-to">${toOptions(cal)}</select></div>` : ''}
                ${openUrl ? `<a class="cal-open" href="${escapeHtml(openUrl)}" target="_blank" rel="noopener noreferrer">Open in ${escapeHtml(ev.link_name || 'its calendar')}</a>` : ''}
                <div class="cal-form-error" role="alert" hidden></div>
            </div>
            <div class="modal-footer">
                ${edit && !locked ? `<button class="btn btn-danger cal-delete">Delete</button><span class="cal-spacer"></span>` : ''}
                <button class="btn cal-x">${locked ? 'Close' : 'Cancel'}</button>
                ${locked ? '' : `<button class="btn btn-primary cal-save">Save</button>`}
            </div>`);

        const q = id => overlay.querySelector('#cal-f-' + id);
        const errBox = overlay.querySelector('.cal-form-error');
        const fail = msg => { errBox.textContent = msg; errBox.hidden = false; };

        const syncAllDay = () => {
            const on = q('allday').checked;
            q('st').hidden = on;
            q('et').hidden = on;
        };
        const noRule = custom || series;                // no repeat controls in the form
        if (locked) overlay.querySelectorAll('.cal-form input, .cal-form select, .cal-form textarea').forEach(el => { el.disabled = true; });
        q('cal')?.addEventListener('change', () => {         // another calendar has its own "save to" choices
            const field = overlay.querySelector('.cal-to-field');
            if (!field) return;
            const html = toOptions(q('cal').value);
            q('to').innerHTML = html;
            field.hidden = !html;
        });
        const syncRepeat = () => {
            if (noRule) return;
            const mode = q('repeat').value;
            overlay.querySelector('.cal-repeat').hidden = mode === 'none';
            overlay.querySelector('.cal-days').hidden = mode !== 'weekly';
            const unit = { daily: 'day', weekly: 'week', monthly: 'month', yearly: 'year' }[mode] || '';
            q('unit').textContent = unit ? unit + '(s)' : '';
            if (mode === 'weekly' && !overlay.querySelector('.cal-days input:checked') && q('sd').value) {
                const box = overlay.querySelector(`.cal-days input[value="${pyWeekday(fromYmd(q('sd').value))}"]`);
                if (box) box.checked = true;                 // a weekly event starts on its own weekday
            }
            const ends = q('ends').value;
            q('until').hidden = ends !== 'until';
            q('times').hidden = ends !== 'times';
        };
        q('allday').addEventListener('change', syncAllDay);
        if (!noRule) {
            q('repeat').addEventListener('change', syncRepeat);
            q('ends').addEventListener('change', syncRepeat);
        }
        // Moving the start moves the end with it, so the length is kept.
        const stamp = () => new Date(`${q('sd').value || '1970-01-01'}T${q('st').value || '00:00'}`).getTime();
        let before = stamp();
        const shiftEnd = () => {
            const after = stamp();
            const endAt = new Date(`${q('ed').value}T${q('et').value || '00:00'}`).getTime();
            if (Number.isFinite(before) && Number.isFinite(after) && Number.isFinite(endAt)) {
                const moved = new Date(endAt + (after - before));
                q('ed').value = ymd(moved);
                q('et').value = `${pad(moved.getHours())}:${pad(moved.getMinutes())}`;
            }
            if (Number.isFinite(after)) before = after;
        };
        // A weekly event on just its own weekday follows the start to a new weekday.
        let dayBefore = q('sd').value;
        const followWeekday = () => {
            const day = q('sd').value;
            const picked = [...overlay.querySelectorAll('.cal-days input:checked')];
            if (day && dayBefore && picked.length === 1 && +picked[0].value === pyWeekday(fromYmd(dayBefore))) {
                picked[0].checked = false;
                const box = overlay.querySelector(`.cal-days input[value="${pyWeekday(fromYmd(day))}"]`);
                if (box) box.checked = true;
            }
            if (day) dayBefore = day;
        };
        q('sd').addEventListener('change', () => { shiftEnd(); followWeekday(); });
        q('st').addEventListener('change', shiftEnd);
        syncAllDay();
        syncRepeat();
        setTimeout(() => q('title').focus(), 50);

        const busy = on => {
            if (on) overlay.dataset.busy = '1'; else delete overlay.dataset.busy;
            overlay.querySelectorAll('.modal-footer button').forEach(b => { b.disabled = on; });
        };

        // A Google repeat: one day or all of them? Everything else saves straight away.
        function saveAsked() {
            if (!series) return save(null);
            const dayWords = fmt(fromYmd(ev.day), { weekday: 'short', month: 'short', day: 'numeric' });
            const ask = openModal(`
                <div class="modal-header"><h3>Change a repeating event</h3><button class="close-btn cal-x" aria-label="Close">&times;</button></div>
                <div class="modal-body"><p class="cal-ask">"${escapeHtml(ev.title)}" repeats. Change only ${escapeHtml(dayWords)}, or every occurrence?</p></div>
                <div class="modal-footer">
                    <button class="btn cal-x">Cancel</button>
                    <button class="btn btn-primary cal-one">Only ${escapeHtml(dayWords)}</button>
                    <button class="btn btn-primary cal-all">Every occurrence</button>
                </div>`);
            ask.overlay.querySelector('.cal-one').addEventListener('click', () => { ask.close(); save(ev.day); });
            ask.overlay.querySelector('.cal-all').addEventListener('click', () => { ask.close(); save(''); });
        }

        async function save(oneDay) {
            errBox.hidden = true;
            const all = q('allday').checked;
            if (!q('title').value.trim()) return fail('Give the event a title.');
            if (!q('sd').value || !q('ed').value) return fail('Pick a start and an end date.');
            const fields = {
                title: q('title').value.trim(),
                all_day: all,
                start: all ? q('sd').value : `${q('sd').value}T${q('st').value || '00:00'}`,
                end: all ? q('ed').value : `${q('ed').value}T${q('et').value || '00:00'}`,
                location: q('where').value.trim(),
                notes: q('notes').value,
            };
            if (q('cal')) fields.calendar = q('cal').value;
            else if (!edit) fields.calendar = cal;
            if (!edit && q('to')?.value) fields.to = q('to').value;
            if (oneDay) fields.day = oneDay;
            if (!noRule) {
                fields.repeat = q('repeat').value;
                if (fields.repeat !== 'none') {
                    fields.every = +q('every').value || 1;
                    fields.days = fields.repeat === 'weekly'
                        ? [...overlay.querySelectorAll('.cal-days input:checked')].map(b => +b.value) : [];
                    fields.until = q('ends').value === 'until' ? q('until').value : '';
                    fields.times = q('ends').value === 'times' ? (+q('times').value || '') : '';
                    if (q('ends').value === 'until' && !fields.until) return fail('Pick the date the repeat ends on.');
                }
            }
            busy(true);
            try {
                await fetchWithTimeout(edit ? `${API}/events/${encodeURIComponent(ev.id)}` : `${API}/events`, {
                    method: edit ? 'PUT' : 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(fields),
                }, 20000);
            } catch (e) {
                busy(false);
                return fail(e.message || "That didn't save. Try again.");
            }
            busy(false);
            close();
            st.key = '';
            load(true);
        }

        async function drop(day) {
            busy(true);
            try {
                await fetchWithTimeout(`${API}/events/${encodeURIComponent(ev.id)}`, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(day ? { day } : {}),
                }, 20000);
            } catch (e) {
                busy(false);
                return fail(e.message || "That didn't delete. Try again.");
            }
            busy(false);
            close();
            st.key = '';
            load(true);
        }

        overlay.querySelector('.cal-save')?.addEventListener('click', saveAsked);
        overlay.querySelector('#cal-f-title').addEventListener('keydown', e => { if (e.key === 'Enter' && !locked) saveAsked(); });
        overlay.querySelector('.cal-delete')?.addEventListener('click', () => {
            if (!ev.repeats) {
                showConfirm(`Delete "${ev.title}"? This cannot be undone.`, () => drop(null), { title: 'Delete event', saveLabel: 'Delete' });
                return;
            }
            const dayWords = fmt(fromYmd(ev.day), { weekday: 'short', month: 'short', day: 'numeric' });
            const ask = openModal(`
                <div class="modal-header"><h3>Delete a repeating event</h3><button class="close-btn cal-x" aria-label="Close">&times;</button></div>
                <div class="modal-body"><p class="cal-ask">"${escapeHtml(ev.title)}" repeats${ev.repeat_text && ev.repeat_text !== 'repeats' ? ` (${escapeHtml(ev.repeat_text)})` : ''}. Delete only ${escapeHtml(dayWords)}, or every occurrence? This cannot be undone.</p></div>
                <div class="modal-footer">
                    <button class="btn cal-x">Cancel</button>
                    <button class="btn btn-danger cal-one">Only ${escapeHtml(dayWords)}</button>
                    <button class="btn btn-danger cal-all">Every occurrence</button>
                </div>`);
            ask.overlay.querySelector('.cal-one').addEventListener('click', () => { ask.close(); drop(ev.day); });
            ask.overlay.querySelector('.cal-all').addEventListener('click', () => { ask.close(); drop(null); });
        });
    }

    // -- linked calendars --------------------------------------------------------

    function openLinks() {
        let calendar = st.filter || 'default';
        let state = null;
        const { overlay, close } = openModal(`
            <div class="modal-header">
                <h3>Calendars</h3>
                <button class="close-btn cal-x" aria-label="Close">&times;</button>
            </div>
            <div class="modal-body cal-linkbox"><div class="cal-empty">Loading...</div></div>
            <div class="modal-footer">
                <button class="btn cal-sync">Refresh now</button><span class="cal-spacer"></span>
                <button class="btn cal-x">Close</button>
            </div>`);
        const box = overlay.querySelector('.cal-linkbox');
        const say = (msg, kind = 'error') => showToast(msg, kind);
        const send = (path, method, body) => fetchWithTimeout(`${API}/${path}`, {
            method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined,
        }, 45000);

        function paintLinks() {
            const g = state.google || { connected: false, calendars: [] };
            const many = st.calendars.length > 1;
            const writable = state.links.filter(l => l.kind === 'google' && l.writable);
            const rows = state.links.map(l => `
                <div class="cal-link" data-link="${escapeHtml(l.id)}">
                    <span class="cal-dot cal-c${l.color % 6}"></span>
                    <div class="cal-link-main">
                        <div class="cal-link-name">${escapeHtml(l.name)}</div>
                        <div class="cal-link-sub">${l.kind === 'google' ? (l.writable ? 'Google, two-way' : 'Google, read-only') : `Calendar address, read-only${l.where ? ' - ' + escapeHtml(l.where) : ''}`} - ${escapeHtml(l.refreshing ? 'refreshing...' : agoWords(l.synced_at))}</div>
                        ${l.error ? `<div class="cal-link-error">${escapeHtml(l.error)}</div>` : ''}
                        ${l.note ? `<div class="cal-link-sub">${escapeHtml(l.note)}</div>` : ''}
                    </div>
                    <button class="btn hover-danger cal-unlink">Unlink</button>
                </div>`).join('');
            const offers = g.calendars.filter(c => !c.linked);
            box.innerHTML = `
                ${many ? `<div class="modal-field"><label for="cal-l-cal">Calendar</label>
                    <select id="cal-l-cal">${st.calendars.map(c => `<option value="${escapeHtml(c.name)}"${c.name === calendar ? ' selected' : ''}>${escapeHtml(c.name)}</option>`).join('')}</select></div>` : ''}
                <p class="cal-ask">Events always live on this machine. A link adds another calendar's events beside them: Google works both ways, a calendar address is read-only.</p>
                <h4 class="cal-h">Linked</h4>
                ${rows || `<div class="cal-link-sub">Nothing is linked to "${escapeHtml(calendar)}" yet.</div>`}
                ${writable.length ? `<div class="modal-field cal-gap"><label for="cal-l-target">New events go to</label>
                    <select id="cal-l-target"><option value="local"${state.target ? '' : ' selected'}>This machine only</option>
                        ${writable.map(l => `<option value="${escapeHtml(l.id)}"${l.id === state.target ? ' selected' : ''}>${escapeHtml(l.name)}</option>`).join('')}</select>
                    <div class="cal-link-sub">Also where your AI's new events go in chats that use this calendar.</div></div>` : ''}
                <h4 class="cal-h">Google</h4>
                ${g.connected ? `
                    <div class="cal-link-sub">A Google account is connected for "${escapeHtml(calendar)}".</div>
                    ${g.error ? `<div class="cal-link-error">${escapeHtml(g.error)}</div>` : ''}
                    ${offers.length ? `<div class="cal-inline cal-gap"><select id="cal-l-gpick">${offers.map(c => `<option value="${escapeHtml(c.id)}">${escapeHtml(c.name)}${c.writable ? '' : ' (read-only)'}</option>`).join('')}</select>
                        <button class="btn cal-glink">Link this one too</button></div>` : ''}
                    <div class="cal-inline cal-gap"><button class="btn cal-gconnect">Reconnect</button><button class="btn hover-danger cal-gdisconnect">Disconnect Google</button></div>`
                : `<div class="cal-link-sub">Connect a Google account to see and change its calendar here.</div>
                    <div class="cal-inline cal-gap"><button class="btn btn-primary cal-gconnect">Connect Google</button></div>`}
                <details class="cal-adv"><summary>Use your own Google client (advanced)</summary>
                    <div class="cal-link-sub">Leave this alone for Easy Connect. With your own OAuth client, add this redirect URI to it: <code>${escapeHtml(location.origin)}/api/plugin/calendar/callback</code></div>
                    <div class="modal-field"><label for="cal-l-cid">Client ID</label><input type="text" id="cal-l-cid" autocomplete="off" value="${escapeHtml(state.account?.client_id || '')}"></div>
                    <div class="modal-field"><label for="cal-l-secret">Client secret</label><input type="password" id="cal-l-secret" autocomplete="new-password" placeholder="Leave blank to keep the saved one"></div>
                    <button class="btn cal-gsave">Save client</button>
                </details>
                <h4 class="cal-h">Add a calendar address</h4>
                <div class="cal-link-sub">An .ics or webcal address: holidays, a team calendar, another app's "secret address in iCal format".</div>
                <div class="modal-field cal-gap"><label for="cal-l-url">Address</label><input type="text" inputmode="url" id="cal-l-url" placeholder="https://..." autocomplete="off" spellcheck="false"></div>
                <div class="modal-field"><label for="cal-l-name">Name (optional)</label><input type="text" id="cal-l-name" maxlength="40"></div>
                <button class="btn cal-addics">Add</button>`;
        }

        async function run(work, done) {
            overlay.dataset.busy = '1';
            box.classList.add('busy');
            try {
                const res = await work();
                if (res?.links) state = res.google ? res : { ...state, links: res.links };
                if (done) done(res);
                paintLinks();
                st.key = '';
                load(true);
            } catch (e) {
                say(e.message || "That didn't work.");
            } finally {
                delete overlay.dataset.busy;
                box.classList.remove('busy');
            }
        }

        const open = () => run(() => send(`links?calendar=${encodeURIComponent(calendar)}`, 'GET'));

        box.addEventListener('change', e => {
            if (e.target.id === 'cal-l-cal') { calendar = e.target.value; open(); }
            if (e.target.id === 'cal-l-target') run(() => send('target', 'POST', { calendar, to: e.target.value }));
        });
        box.addEventListener('click', e => {
            const row = e.target.closest('.cal-link');
            if (e.target.closest('.cal-unlink') && row) {
                const link = state.links.find(l => l.id === row.dataset.link);
                showConfirm(`Unlink "${link?.name || 'this calendar'}"? Its events leave this page. Nothing is deleted where that calendar is kept.`,
                    () => run(() => send(`links/${encodeURIComponent(row.dataset.link)}`, 'DELETE')), { title: 'Unlink calendar', saveLabel: 'Unlink' });
            } else if (e.target.closest('.cal-addics')) {
                const url = box.querySelector('#cal-l-url').value.trim();
                if (!url) return say('Paste the calendar address first.');
                run(() => send('links', 'POST', { calendar, kind: 'ics', url, name: box.querySelector('#cal-l-name').value.trim() }),
                    () => say('Calendar linked.', 'success'));
            } else if (e.target.closest('.cal-glink')) {
                run(() => send('links', 'POST', { calendar, kind: 'google', google_id: box.querySelector('#cal-l-gpick').value }));
            } else if (e.target.closest('.cal-gconnect')) {
                // Google's consent page takes over this tab and returns here.
                window.location.href = `${API}/auth?scope=${encodeURIComponent(calendar)}`;
            } else if (e.target.closest('.cal-gdisconnect')) {
                showConfirm(`Disconnect Google from "${calendar}"? Its Google links stay listed but stop refreshing until you connect again.`,
                    () => run(async () => { await send('disconnect', 'POST', { scope: calendar }); return send(`links?calendar=${encodeURIComponent(calendar)}`, 'GET'); }),
                    { title: 'Disconnect Google', saveLabel: 'Disconnect' });
            } else if (e.target.closest('.cal-gsave')) {
                const client_id = box.querySelector('#cal-l-cid').value.trim();
                const client_secret = box.querySelector('#cal-l-secret').value.trim();
                run(async () => {
                    await fetchWithTimeout(`/api/gcal/accounts/${encodeURIComponent(calendar)}`, {
                        method: 'PUT', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ client_id, client_secret, calendar_id: state.account?.calendar_id || 'primary', label: calendar }),
                    }, 20000);
                    return send(`links?calendar=${encodeURIComponent(calendar)}`, 'GET');
                }, () => say(client_id ? 'Client saved. Now connect Google.' : 'Back to Easy Connect.', 'success'));
            }
        });
        overlay.querySelector('.cal-sync').addEventListener('click', () => run(async () => {
            const res = await send('sync', 'POST', { calendar });
            if (res.failed?.length) say(`${res.failed[0].name}: ${res.failed[0].detail}`);
            return res;
        }));
        open();
        return close;
    }

    // Back from Google's consent page: #app-calendar/connected or /connect-error/<words>.
    function readConnectResult() {
        const m = /^#app-calendar\/(connected|connect-error)(?:\/(.*))?$/.exec(location.hash || '');
        if (!m) return;
        try { history.replaceState(null, '', '#app-calendar'); } catch { /* keep the hash */ }
        if (m[1] === 'connected') {
            showToast('Google is connected. Its calendar is linked and loading.', 'success');
        } else {
            let words = "Google didn't connect.";
            try { words = decodeURIComponent(m[2] || '') || words; } catch { /* keep the default */ }
            showToast(words, 'error');
        }
    }

    function newEventOn(day, minutes) {
        if (minutes == null) {
            // From a month cell or the + button: a timed event at 9, one click from all-day in the form.
            return openForm(null, { start: `${day}T09:00`, end: `${day}T10:00`, all_day: false });
        }
        const from = Math.min(Math.max(0, Math.round(minutes / 30) * 30), 23 * 60);
        const to = from + 60;
        const hm = m => `${pad(Math.floor(m / 60))}:${pad(m % 60)}`;
        const endDay = to >= 1440 ? ymd(addDays(fromYmd(day), 1)) : day;
        openForm(null, { start: `${day}T${hm(from)}`, end: `${endDay}T${hm(to % 1440)}`, all_day: false });
    }

    // -- clicks ------------------------------------------------------------------

    function onBodyClick(e) {
        const evEl = e.target.closest('[data-ev]');
        if (evEl) {
            const ev = findEvent(evEl.dataset.ev, evEl.dataset.day);
            if (ev) openForm(ev);
            return;
        }
        const month = e.target.closest('[data-month]');
        if (month) return go('month', month.dataset.month);
        const jump = e.target.closest('[data-goto]');
        if (jump) return go('day', jump.dataset.goto);
        const col = e.target.closest('.cal-tg-col');
        if (col) {
            const y = e.clientY - col.getBoundingClientRect().top;
            return newEventOn(col.dataset.day, y / HOUR_PX * 60 - 15);
        }
        const allCell = e.target.closest('.cal-tg-allcell');
        if (allCell) return openForm(null, { start: allCell.dataset.day, end: allCell.dataset.day, all_day: true });
        const cell = e.target.closest('.cal-cell');
        if (cell) return newEventOn(cell.dataset.day, null);
    }

    async function onFilter() {
        const sel = $('.cal-filter');
        if (sel.value !== NEW_CALENDAR) {
            st.filter = (st.calendars.length > 1) ? sel.value : '';
            remember('filter', st.filter);
            return go();
        }
        paintBar();                                          // put the select back while the prompt is open
        const name = (await showPrompt('New calendar', 'Name (letters, digits, - or _)') || '').trim();
        if (!name || st.dead) return;
        try {
            const res = await fetchWithTimeout(`${API}/calendars/${encodeURIComponent(name)}`, { method: 'PUT' }, 20000);
            st.calendars = res.calendars || st.calendars;
            st.filter = res.name;
            remember('filter', st.filter);
            showToast(`Calendar "${res.name}" added. Pick it for a chat in the chat's settings.`, 'success');
            go();
        } catch (e) {
            showToast(e.message || "Couldn't add that calendar.", 'error');
        }
    }

    function onDropCalendar() {
        const name = st.filter;
        const info = st.calendars.find(c => c.name === name);
        if (!name || name === 'default') return;
        const n = info?.events || 0;
        const linked = st.links.some(l => l.calendar === name);
        showConfirm(`Delete the calendar "${name}" and its ${n} event${n === 1 ? '' : 's'}? This cannot be undone.${linked ? ' Its links go too; nothing is deleted at Google or at a linked address.' : ''}`, async () => {
            try {
                const res = await fetchWithTimeout(`${API}/calendars/${encodeURIComponent(name)}`, { method: 'DELETE' }, 20000);
                st.calendars = res.calendars || st.calendars;
                st.filter = '';
                remember('filter', '');
                go();
            } catch (e) {
                showToast(e.message || "Couldn't delete that calendar.", 'error');
            }
        }, { title: 'Delete calendar', saveLabel: 'Delete' });
    }

    // -- wiring ------------------------------------------------------------------

    body.addEventListener('click', onBodyClick);
    $('.cal-prev').addEventListener('click', () => step(-1));
    $('.cal-next').addEventListener('click', () => step(1));
    $('.cal-today').addEventListener('click', () => go(null, st.today));
    $('.cal-add').addEventListener('click', () => {
        // Today when it is on screen, else the day the view is turned to.
        const [a, b] = range();
        const shown = st.view !== 'day' && st.today >= ymd(a) && st.today <= ymd(b);
        newEventOn(shown ? st.today : ymd(st.cursor), null);
    });
    $('.cal-filter').addEventListener('change', onFilter);
    $('.cal-drop').addEventListener('click', onDropCalendar);
    $('.cal-links').addEventListener('click', openLinks);
    root.querySelectorAll('.cal-view-btn').forEach(b => b.addEventListener('click', () => go(b.dataset.view)));

    const onScreen = () => !st.dead && root.isConnected && document.visibilityState === 'visible' && root.offsetParent !== null;
    const refresh = () => { if (onScreen()) load(true); };
    const timer = setInterval(refresh, REFRESH_MS);
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);

    paintBar();
    readConnectResult();
    load();

    return () => {
        st.dead = true;
        clearInterval(timer);
        clearTimeout(st.quick);
        window.removeEventListener('focus', refresh);
        document.removeEventListener('visibilitychange', refresh);
        [...st.modals].forEach(close => { try { close(); } catch { /* already gone */ } });
        if (--cssUsers <= 0 && cssLink) { cssLink.remove(); cssLink = null; cssUsers = 0; }
    };
}
