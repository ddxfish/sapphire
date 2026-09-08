// shared/checklist-modal.js — the ONE checklist primitive for "pick rows,
// then act" modals: prompt delete, bulk vault move, and the cleanup tool's
// orphan / trash lists. Before 2026-09-08 each hand-rolled its own rows,
// its own All/None/Main wiring (twice — a flat copy and a sectioned copy),
// and its own scroll box. Rides showModal for the shell (ESC, overlay,
// footer, promise-aware Save); the cleanup router keeps its own shell and
// uses listHTML + wireList directly.
//
//   Row:     { type, key, label?(html), checked, disabled, flag, badges?: [text],
//              title?, stock?, pack?, data?: {extra data-* attrs: kind, store} }
//            flag = vault-referenced: never joins bulk gestures (filters,
//            section toggles), stays individually clickable.
//   Section: { id, name?(plain), label?(html), open, flat, rows }
//            flat = no header, rows inline (a one-list modal).
//   Filters: names from FILTERS; a predicate reads the row's dataset.
import { showModal } from './modal.js';

export const GENERIC_TYPES = ['extras', 'emotions'];

// Pluggable quick-selects. Adding one = one line here; every modal that
// lists it gets it (Krem's "custom" ask, 2026-09-08).
export const FILTERS = {
    all:    { label: 'All',    title: '', pick: () => true },
    none:   { label: 'None',   title: '', pick: () => false },
    main:   { label: 'Main',   title: 'Everything except extras and emotions',
              pick: d => !GENERIC_TYPES.includes(d.type) },
    custom: { label: 'Custom', title: 'Only what you made — not shipped with Sapphire, not plugin-owned',
              pick: d => !d.stock && !d.pack },
};

export function esc(s) {
    if (s == null) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

const ROW_STYLE = 'display:flex;gap:8px;align-items:center;padding:3px 0;font-size:var(--font-xs)';
const BADGE_STYLE = 'opacity:0.75;margin-left:auto;white-space:nowrap';
const LINK_STYLE = 'color:var(--accent);cursor:pointer;text-decoration:underline';
const HEAD_STYLE = 'display:flex;gap:8px;align-items:center;cursor:pointer;padding:4px 0;font-size:var(--font-xs)';

export function rowHTML(r) {
    const extra = Object.entries(r.data || {})
        .map(([k, v]) => ` data-${esc(k)}="${esc(v)}"`).join('');
    return `
        <label style="${ROW_STYLE}" title="${esc(r.title || '')}">
            <input type="checkbox" class="pc-row" data-type="${esc(r.type)}" data-key="${esc(r.key)}"${
                r.flag ? ' data-flag="1"' : ''}${r.stock ? ' data-stock="1"' : ''}${r.pack ? ' data-pack="1"' : ''}${extra}${
                r.disabled ? ' disabled' : ''}${r.checked ? ' checked' : ''}>
            <span>${r.label || esc(`${r.type}/${r.key}`)}</span>
            <span style="${BADGE_STYLE}">${(r.badges || []).filter(Boolean).join(' · ')}</span>
        </label>`;
}

export function filtersHTML(names) {
    const chips = (names || []).filter(n => FILTERS[n]).map(n =>
        `<span class="pc-preset" data-preset="${n}" style="${LINK_STYLE}" title="${esc(FILTERS[n].title)}">${FILTERS[n].label}</span>`
    ).join('');
    if (!chips) return '';
    return `<div style="display:flex;gap:12px;align-items:center;font-size:var(--font-xs);margin:4px 0">
        <span>Select:</span>${chips}</div>`;
}

export function listHTML({ sections, filters = ['all', 'none', 'main'], maxHeight = '45vh' }) {
    const body = (sections || []).map(sec => {
        if (sec.flat) return sec.rows.map(rowHTML).join('');
        const name = sec.name || sec.id;
        return `
            <div class="pc-sec" data-type="${esc(sec.id)}">
                <div class="pc-sec-head" style="${HEAD_STYLE}">
                    <input type="checkbox" class="pc-sec-check" title="Check/uncheck everything in ${esc(name)}">
                    ${sec.label || `<b>${esc(name)}</b>`}<span style="opacity:0.7">(${sec.rows.length})</span>
                    <span class="pc-sec-arrow" style="margin-left:auto">${sec.open ? '▾' : '▸'}</span>
                </div>
                <div class="pc-sec-body" style="${sec.open ? '' : 'display:none;'}padding-left:20px">
                    ${sec.rows.map(rowHTML).join('')}
                </div>
            </div>`;
    }).join('');
    return `${filtersHTML(filters)}
        <div class="pc-list" style="max-height:${maxHeight};overflow-y:auto;border:1px solid var(--border);border-radius:6px;padding:6px 10px">
            ${body}
        </div>`;
}

// Section header checkboxes mirror their rows: checked = every selectable
// row on, indeterminate = some. Flagged / disabled rows don't count.
export function syncSectionChecks(scope) {
    scope.querySelectorAll('.pc-sec').forEach(sec => {
        const rows = [...sec.querySelectorAll('.pc-row:not([data-flag]):not(:disabled)')];
        const head = sec.querySelector('.pc-sec-check');
        if (!head) return;
        const on = rows.filter(r => r.checked).length;
        head.checked = rows.length > 0 && on === rows.length;
        head.indeterminate = on > 0 && on < rows.length;
    });
}

// One filter implementation for flat AND sectioned lists.
export function applyFilter(scope, mode) {
    const f = FILTERS[mode];
    if (!f) return;
    scope.querySelectorAll('.pc-row:not([data-flag]):not(:disabled)')
        .forEach(cb => { cb.checked = f.pick(cb.dataset); });
    syncSectionChecks(scope);
}

// Same logical row shown in two sections (a piece shared by two prompts)
// stays in sync.
const mirror = (scope, r) => {
    const sel = `.pc-row[data-type="${CSS.escape(r.dataset.type)}"][data-key="${CSS.escape(r.dataset.key)}"]`;
    scope.querySelectorAll(sel).forEach(o => { if (o !== r && !o.disabled) o.checked = r.checked; });
};

export function wireList(scope, { mirrorDuplicates = false } = {}) {
    scope.querySelectorAll('.pc-sec-head').forEach(head => {
        head.addEventListener('click', e => {
            if (e.target.classList.contains('pc-sec-check')) return;
            const bd = head.parentElement.querySelector('.pc-sec-body');
            const open = bd.style.display !== 'none';
            bd.style.display = open ? 'none' : '';
            head.querySelector('.pc-sec-arrow').textContent = open ? '▸' : '▾';
        });
    });
    scope.querySelectorAll('.pc-sec-check').forEach(cb => {
        cb.addEventListener('click', e => e.stopPropagation());
        cb.addEventListener('change', () => {
            cb.closest('.pc-sec').querySelectorAll('.pc-row:not([data-flag]):not(:disabled)')
                .forEach(r => { r.checked = cb.checked; if (mirrorDuplicates) mirror(scope, r); });
            syncSectionChecks(scope);
        });
    });
    scope.querySelectorAll('.pc-row').forEach(r =>
        r.addEventListener('change', () => {
            if (mirrorDuplicates) mirror(scope, r);
            syncSectionChecks(scope);
        }));
    scope.querySelectorAll('.pc-preset').forEach(p =>
        p.addEventListener('click', () => applyFilter(scope, p.dataset.preset)));
    syncSectionChecks(scope);
}

// Checked rows as {type, key, store, kind?} — the shape the piece routes
// (trash/restore) and the vault batch consume. Duplicates (mirrored rows)
// collapse to one.
export function checkedRows(scope) {
    const seen = new Set();
    const out = [];
    scope.querySelectorAll('.pc-row:checked').forEach(cb => {
        const d = cb.dataset;
        const id = `${d.kind || ''}|${d.type}|${d.key}`;
        if (seen.has(id)) return;
        seen.add(id);
        const row = { type: d.type, key: d.key, store: d.store || 'plain' };
        if (d.kind) row.kind = d.kind;
        out.push(row);
    });
    return out;
}

/**
 * The modal. onSave(chosenRows, modal) — return a promise to keep the modal
 * open ("Working…") until the work settles.
 */
export function openChecklist({ title, intro = '', banner = '', sections = [],
                                filters, mirrorDuplicates = false, maxHeight,
                                wide = true, saveLabel = 'OK', onSave }) {
    const html = `
        ${intro ? `<p style="font-size:var(--font-xs)">${intro}</p>` : ''}
        ${banner || ''}
        ${sections.length ? listHTML({ sections, filters, maxHeight }) : ''}`;
    const modal = showModal(title, [{ type: 'html', value: html }],
                            () => onSave?.(checkedRows(modal.element), modal),
                            { wide, saveLabel });
    wireList(modal.element, { mirrorDuplicates });
    return modal;
}
