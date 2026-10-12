// shared/plugin-settings-renderer.js - Auto-render plugin settings from manifest schema
// Renders forms using existing .setting-row/.setting-toggle CSS — no new styles needed.

import { showDangerConfirm } from './danger-confirm.js';

function escapeHtml(s) {
    // Must escape quotes too — output lands in value="..." attributes; the
    // list widget stores JSON (`["a"]`) whose quotes truncated the attribute
    // and the next save persisted [] — silent list-setting wipe (scout find,
    // 2026-07-19).
    const d = document.createElement('div');
    d.textContent = s ?? '';
    return d.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Manifest keys may contain dots (voice.join_targets) — a raw `#ps-{key}`
// selector parses the dot as a class boundary and matches nothing, so the
// field renders fine but reads back as its default (silent settings wipe).
// Every id lookup must go through this.
const psSel = key => '#' + CSS.escape('ps-' + key);

/**
 * Render a settings form from a manifest schema array.
 * @param {HTMLElement} container - Where to render
 * @param {Array} schema - [{key, type, label, default, help?, widget?, options?, placeholder?, confirm?, tab?}]
 *   type "found": a live pick list. found_url answers {found: [{id, name, kind, note?, actions?}]};
 *     a thing's actions ([{do, label}]) are buttons that POST {id, do} to found_do_url,
 *   the value is {all, only: [{id, name}]}. many:false makes it "pick one".
 * @param {Object} values - Current setting values (merged with defaults by backend)
 * @param {Object} [opts] - {onChange: (key, value) => void, managed, slots}
 *   slots: [{tab, mount(el)}] — caller-rendered widget sections (dynamic
 *   pickers, dependent dropdowns, anything the schema can't express) placed
 *   inside the named tab's pane after that tab's fields. A slot naming a tab
 *   no field uses creates the tab. Opt-in: without slots, output is unchanged.
 */
export function renderSettingsForm(container, schema, values = {}, { onChange, managed, slots = [] } = {}) {
    // hidden:true fields never render here — they belong to a dedicated UI
    // (e.g. Mind → Admin) and are skipped by readSettingsForm too, so a
    // Settings-page save can't clobber them with defaults.
    schema = (schema || []).filter(f => !f.hidden);
    if (!schema?.length && !slots.length) {
        container.innerHTML = '<p style="color:var(--text-muted)">No settings available.</p>';
        return;
    }

    const rowHTML = field => {
        const val = values[field.key] ?? field.default ?? '';
        return `
            <div class="setting-row" data-key="${escapeHtml(field.key)}"${showIfAttr(field)}>
                <div class="setting-label">
                    <label>${escapeHtml(field.label)}</label>
                    ${field.help ? `<div class="setting-help">${escapeHtml(field.help)}</div>` : ''}
                </div>
                <div class="setting-input">${renderWidget(field, val)}</div>
            </div>
        `;
    };

    // Optional per-field "tab" groups fields under a tab strip. Untagged
    // fields go to "General" (always first); tagged tabs follow in
    // first-seen schema order. With <2 groups the output is identical to
    // the untabbed form, so existing plugins render unchanged.
    const groups = new Map();
    for (const field of schema) {
        const tab = String(field.tab || 'General').trim() || 'General';
        if (!groups.has(tab)) groups.set(tab, []);
        groups.get(tab).push(field);
    }

    // Slot sections render as full-width children after their tab's fields.
    // The wrapper div is renderer-owned; the caller only ever touches the
    // element handed to mount() — no reaching into pane markup from outside.
    const slotsByTab = new Map();
    slots.forEach((slot, i) => {
        const tab = String(slot.tab || 'General').trim() || 'General';
        if (!slotsByTab.has(tab)) slotsByTab.set(tab, []);
        slotsByTab.get(tab).push(i);
    });
    const slotHTML = i => `<div class="ps-slot" data-ps-slot="${i}" style="grid-column:1/-1"></div>`;
    const paneHTML = n => (groups.get(n) || []).map(rowHTML).join('')
        + (slotsByTab.get(n) || []).map(slotHTML).join('');

    const names = [...groups.keys()];
    for (const tab of slotsByTab.keys()) {
        if (!names.includes(tab)) names.push(tab);
    }

    if (names.length < 2) {
        container.innerHTML = `<div class="settings-grid">${paneHTML(names[0])}</div>`;
    } else {
        if (names.includes('General')) {
            names.splice(names.indexOf('General'), 1);
            names.unshift('General');
        }
        const strip = `<div class="ps-tabs">${names.map((n, i) =>
            `<button type="button" class="ps-tab${i === 0 ? ' active' : ''}" data-ps-tab="${escapeHtml(n)}">${escapeHtml(n)}</button>`
        ).join('')}</div>`;
        // Every pane stays in the DOM — save (readSettingsForm) and the
        // widget wiring below scan the whole container by #ps-{key}, so
        // inactive panes are CSS-hidden, never removed.
        const panes = names.map((n, i) =>
            `<div class="settings-grid" data-ps-pane="${escapeHtml(n)}"${i === 0 ? '' : ' hidden'}>${paneHTML(n)}</div>`
        ).join('');
        container.innerHTML = strip + panes;
        container.querySelector('.ps-tabs').addEventListener('click', e => {
            const btn = e.target.closest('.ps-tab');
            if (!btn) return;
            container.querySelectorAll('.ps-tab').forEach(b => b.classList.toggle('active', b === btn));
            container.querySelectorAll('[data-ps-pane]').forEach(p =>
                p.toggleAttribute('hidden', p.dataset.psPane !== btn.dataset.psTab));
        });
    }

    // Mount slots synchronously so callers can wire events right after this
    // function returns. One broken widget must not take down the whole form.
    container.querySelectorAll('[data-ps-slot]').forEach(el => {
        try {
            slots[Number(el.dataset.psSlot)].mount(el);
        } catch (err) {
            console.error('[plugin-settings] slot mount failed:', err);
        }
    });

    // Attach confirm gates and onChange handlers
    for (const field of schema) {
        if (field.confirm) attachConfirmGate(container, field, managed);
    }

    // Wire up "clear" links for password fields
    container.querySelectorAll('.ps-clear-key').forEach(link => {
        link.addEventListener('click', e => {
            e.preventDefault();
            const input = container.querySelector('#' + CSS.escape(link.dataset.field));
            if (input) {
                input.value = '__CLEAR__';
                input.placeholder = 'Key cleared — save to apply';
                link.closest('.setting-input').querySelector('small')?.remove();
                link.remove();
            }
        });
    });

    // Wire up action buttons
    for (const field of schema) {
        if ((field.widget || inferWidget(field)) !== 'button') continue;
        const btn = container.querySelector(psSel(field.key));
        if (!btn) continue;

        // Check status on render if status URL provided
        if (field.status) {
            fetch(field.status).then(r => r.json()).then(data => {
                if (data.connected) {
                    btn.textContent = field.button_label_connected || 'Connected ✓';
                    btn.dataset.connected = 'true';
                    btn.classList.add('btn-connected');
                    // Add disconnect button if disconnect URL provided
                    if (field.disconnect) {
                        let discBtn = btn.parentElement.querySelector('.btn-disconnect');
                        if (!discBtn) {
                            discBtn = document.createElement('button');
                            discBtn.type = 'button';
                            discBtn.className = 'btn-action btn-disconnect';
                            discBtn.textContent = 'Disconnect';
                            discBtn.style.marginLeft = '8px';
                            discBtn.addEventListener('click', async () => {
                                const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                                try {
                                    const res = await fetch(field.disconnect, {
                                        method: 'POST',
                                        headers: { 'X-CSRF-Token': csrf }
                                    });
                                    if (!res.ok) { console.error('Disconnect failed:', res.status); return; }
                                } catch (e) { console.error('Disconnect failed:', e); return; }
                                btn.textContent = field.button_label || 'Connect';
                                btn.dataset.connected = 'false';
                                btn.classList.remove('btn-connected');
                                discBtn.remove();
                            });
                            btn.parentElement.appendChild(discBtn);
                        }
                    }
                }
            }).catch(() => {});
        }

        btn.addEventListener('click', async () => {
            const url = btn.dataset.actionUrl;
            if (!url) return;
            // Default behavior is browser navigation — required for OAuth flows
            // that expect the server to 302-redirect to an external auth page.
            // Plugins that return JSON (e.g. an API key to display) MUST set
            // `action_mode: "display"` in the manifest — otherwise the raw JSON
            // body lands in the URL bar / browser history and leaks the secret.
            // Scout finding #9 — 2026-04-20.
            const mode = field.action_mode || 'navigate';
            if (mode === 'display') {
                try {
                    const res = await fetch(url);
                    const data = await res.json().catch(() => ({}));
                    _showActionResult(field, data, res.ok);
                } catch (e) {
                    _showActionResult(field, { error: String(e) }, false);
                }
                return;
            }
            window.location.href = url;
        });
    }

    // Wire up list fields (chips + add/remove)
    for (const field of schema) {
        if ((field.widget || inferWidget(field)) !== 'list') continue;
        wireListField(container, field);
    }

    // Wire up rows fields (a list of objects)
    for (const field of schema) {
        if ((field.widget || inferWidget(field)) !== 'rows') continue;
        wireRowsField(container, field);
    }

    // Wire up found fields (a live pick list)
    for (const field of schema) {
        if ((field.widget || inferWidget(field)) !== 'found') continue;
        wireFoundField(container, field);
    }

    // show_if: a row is visible only while its controlling field has the
    // named value. Hidden rows stay in the DOM and still save.
    if (schema.some(f => f.show_if)) {
        const apply = () => container.querySelectorAll('[data-show-if]').forEach(row => {
            let cond = {};
            try { cond = JSON.parse(row.dataset.showIf); } catch { /* shown */ }
            const ok = Object.entries(cond).every(([k, want]) => String(
                getFieldValue(container, k, schema.find(f => f.key === k))) === String(want));
            row.toggleAttribute('hidden', !ok);
        });
        container.addEventListener('change', apply);
        apply();
    }

    if (onChange) {
        container.addEventListener('change', e => {
            const key = e.target.closest('[data-key]')?.dataset.key;
            if (key) onChange(key, getFieldValue(container, key, schema.find(f => f.key === key)));
        });
    }
}

function wireListField(container, field) {
    const wrap = container.querySelector(`.ps-list[data-list-key="${field.key}"]`);
    if (!wrap) return;
    const hidden = wrap.querySelector(psSel(field.key));
    const chipsEl = wrap.querySelector('.ps-list-chips');
    const srcEl = wrap.querySelector('.ps-list-src');

    const read = () => { try { const a = JSON.parse(hidden.value); return Array.isArray(a) ? a : []; } catch { return []; } };
    const write = (items) => {
        hidden.value = JSON.stringify(items);
        render();
        hidden.dispatchEvent(new Event('change', { bubbles: true }));
    };
    const render = () => {
        const items = read();
        chipsEl.innerHTML = items.map(v => `
            <span class="ps-list-chip" data-val="${escapeHtml(v)}" style="display:inline-flex;align-items:center;gap:6px;padding:3px 10px;background:var(--bg-secondary,#1a1b2e);border:1px solid var(--border,#333);border-radius:12px;font-size:var(--font-sm,13px)">
                ${escapeHtml(v)}<a href="#" class="ps-list-del" style="color:var(--text-muted);text-decoration:none">✕</a>
            </span>`).join('') || '<span style="color:var(--text-muted);font-size:var(--font-sm,13px)">None added</span>';
        if (srcEl.tagName === 'SELECT') refreshOptions(items);
    };
    const refreshOptions = (items) => {
        const current = srcEl.value;
        const opts = (srcEl._options || []).filter(o => !items.includes(o));
        srcEl.innerHTML = opts.map(o => `<option value="${escapeHtml(o)}">${escapeHtml(o)}</option>`).join('');
        if (opts.includes(current)) srcEl.value = current;
    };

    chipsEl.addEventListener('click', e => {
        const del = e.target.closest('.ps-list-del');
        if (!del) return;
        e.preventDefault();
        const val = del.closest('.ps-list-chip')?.dataset.val;
        write(read().filter(v => v !== val));
    });
    wrap.querySelector('.ps-list-add')?.addEventListener('click', () => {
        const val = (srcEl.value || '').trim();
        if (!val) return;
        const items = read();
        if (!items.includes(val)) write([...items, val]);
        if (srcEl.tagName === 'INPUT') srcEl.value = '';
    });

    if (field.options_endpoint && srcEl.tagName === 'SELECT') {
        fetch(field.options_endpoint).then(r => r.json()).then(data => {
            const rows = field.data_key ? (data[field.data_key] || []) : (Array.isArray(data) ? data : []);
            srcEl._options = rows.map(r => typeof r === 'string' ? r : String(r[field.value_field || 'name'] ?? ''))
                                 .filter(Boolean);
            refreshOptions(read());
        }).catch(() => {});
    }
    render();
}

function showIfAttr(field) {
    if (!field.show_if || typeof field.show_if !== 'object') return '';
    return ` data-show-if="${escapeHtml(JSON.stringify(field.show_if))}"`;
}

// rows: a list of objects, one text input per column, "+ Add" and a delete
// per row. The value lives as JSON in a hidden input (the list widget's
// pattern) so readSettingsForm/getFieldValue work unchanged.
function rowsColumns(field) {
    return (field.columns || []).map(c => typeof c === 'string'
        ? { key: c, label: c } : { key: String(c.key || ''), label: c.label || c.key, placeholder: c.placeholder })
        .filter(c => c.key);
}

function wireRowsField(container, field) {
    const wrap = container.querySelector(`.ps-rows[data-rows-key="${CSS.escape(field.key)}"]`);
    if (!wrap) return;
    const hidden = wrap.querySelector(psSel(field.key));
    const body = wrap.querySelector('.ps-rows-body');
    const cols = rowsColumns(field);

    const read = () => { try { const a = JSON.parse(hidden.value); return Array.isArray(a) ? a : []; } catch { return []; } };
    const write = (items, redraw) => {
        hidden.value = JSON.stringify(items);
        if (redraw) render();
        hidden.dispatchEvent(new Event('change', { bubbles: true }));
    };
    const render = () => {
        const items = read();
        body.innerHTML = items.map((row, i) => `
            <div class="ps-rows-row" data-row="${i}" style="display:flex;gap:6px;margin-bottom:6px;align-items:center">
                ${cols.map((c, n) => `<input type="text" class="ps-rows-cell" data-col="${escapeHtml(c.key)}"
                    value="${escapeHtml(String(row?.[c.key] ?? ''))}" placeholder="${escapeHtml(c.placeholder || c.label)}"
                    style="flex:${n === 0 ? 1 : 2};min-width:0">`).join('')}
                <a href="#" class="ps-rows-del" title="Remove" style="color:var(--text-muted);text-decoration:none">\u2715</a>
            </div>`).join('') || '<div style="color:var(--text-muted);font-size:var(--font-sm,13px);margin-bottom:6px">None added</div>';
    };

    // Cell edits write through without a redraw, so typing keeps its focus.
    // stopPropagation: the cell's own change event carries no data-key value.
    body.addEventListener('input', e => {
        const cell = e.target.closest('.ps-rows-cell');
        if (!cell) return;
        const items = read();
        const i = Number(cell.closest('.ps-rows-row').dataset.row);
        items[i] = { ...(items[i] || {}), [cell.dataset.col]: cell.value };
        write(items, false);
    });
    body.addEventListener('change', e => { if (e.target.closest('.ps-rows-cell')) e.stopPropagation(); });
    body.addEventListener('click', e => {
        const del = e.target.closest('.ps-rows-del');
        if (!del) return;
        e.preventDefault();
        const i = Number(del.closest('.ps-rows-row').dataset.row);
        write(read().filter((_, n) => n !== i), true);
    });
    wrap.querySelector('.ps-rows-add')?.addEventListener('click', () => {
        write([...read(), Object.fromEntries(cols.map(c => [c.key, '']))], true);
        body.querySelector('.ps-rows-row:last-child .ps-rows-cell')?.focus();
    });
    render();
}

// found: a pick list of what is here right now (a keyboard, a board). The
// value lives as JSON in a hidden input, like rows. "all" counts everything
// that is found. The ticks are kept while "all" is on, so they are still
// there when the user switches back.
export function foundPicks(value) {
    let v = value;
    if (typeof v === 'string') { try { v = JSON.parse(v); } catch { v = null; } }
    const only = Array.isArray(v?.only)
        ? v.only.filter(p => p && p.id).map(p => ({ id: String(p.id), name: String(p.name || p.id) }))
        : [];
    return { all: v?.all !== false, only };
}

// What is here, then what was picked and is away.
export function foundRows(picks, found) {
    const here = (found || []).filter(t => t && t.id)
        .map(t => ({ id: String(t.id), name: String(t.name || t.id), kind: String(t.kind || ''),
                     note: String(t.note || ''), actions: Array.isArray(t.actions) ? t.actions : [] }));
    const away = picks.only.filter(p => !here.some(t => t.id === p.id))
        .map(p => ({ ...p, kind: '', note: '', actions: [], away: true }));
    return [...here, ...away];
}

// A thing may be set up before it is a device (a phone is paired): the words
// under it and its buttons come from the driver (`note`, `actions`).
function foundAbout(t) {
    const buttons = (t.actions || []).map(a => `<button type="button" class="btn-action ps-found-do"
        data-id="${escapeHtml(t.id)}" data-do="${escapeHtml(a.do)}" style="margin:4px 6px 0 0">${escapeHtml(a.label || a.do)}</button>`).join('');
    if (!t.note && !buttons) return '';
    return `<div class="ps-found-about" style="margin:4px 0 6px 0;font-size:var(--font-sm,13px)">
            ${t.note ? `<span style="color:var(--text-muted)">${escapeHtml(t.note)}</span>` : ''}${buttons ? `<div>${buttons}</div>` : ''}</div>`;
}

export function foundHTML(field, picks, found, problem = '', word = '') {
    const name = `ps-${field.key}-mode`;
    const every = field.all_label || 'Everything that is found';
    const rows = foundRows(picks, found);
    const note = t => t.away ? 'not here now' : t.kind;
    const looking = found === null;
    const again = `<button type="button" class="btn-action ps-found-look" style="margin-top:6px">Look again</button>`;
    const trouble = problem
        ? `<div style="color:var(--error,#e53935);font-size:var(--font-sm,13px)">${escapeHtml(problem)}</div>` : '';
    const said = word ? `<div class="ps-found-word" style="font-size:var(--font-sm,13px);margin-top:4px">${escapeHtml(word)}</div>` : '';
    if (field.many === false) {
        const chosen = picks.all ? '' : (picks.only[0]?.id || '');
        const about = rows.filter(t => !t.away && (t.note || t.actions.length)).map(t =>
            `<div style="margin-top:6px"><b style="font-size:var(--font-sm,13px)">${escapeHtml(t.name)}</b>${foundAbout(t)}</div>`).join('');
        return `<select class="ps-found-one">
                <option value="">${escapeHtml(field.all_label || 'Whichever is found')}</option>
                ${rows.map(t => `<option value="${escapeHtml(t.id)}" ${t.id === chosen ? 'selected' : ''}>${
                    escapeHtml(t.name)}${note(t) ? ` (${escapeHtml(note(t))})` : ''}</option>`).join('')}
            </select> ${again}${about}${said}${trouble}`;
    }
    const ticked = t => picks.all ? !t.away : picks.only.some(p => p.id === t.id);
    const list = looking ? '<span style="color:var(--text-muted)">Looking...</span>'
        : rows.map(t => `<label style="display:block;margin:2px 0">
                <input type="checkbox" class="ps-found-tick" data-id="${escapeHtml(t.id)}" data-name="${escapeHtml(t.name)}"
                    ${ticked(t) ? 'checked' : ''} ${picks.all ? 'disabled' : ''}>
                ${escapeHtml(t.name)} <span style="color:var(--text-muted);font-size:var(--font-sm,13px)">${escapeHtml(note(t))}</span>
            </label>${t.away ? '' : foundAbout(t)}`).join('') || '<span style="color:var(--text-muted)">Nothing is here right now.</span>';
    return `<label style="display:block"><input type="radio" name="${escapeHtml(name)}" value="all" ${picks.all ? 'checked' : ''}> ${escapeHtml(every)}</label>
            <label style="display:block"><input type="radio" name="${escapeHtml(name)}" value="only" ${picks.all ? '' : 'checked'}> ${escapeHtml(field.only_label || 'Only these')}</label>
            <div class="ps-found-list" style="margin:6px 0 0 22px">${list}</div>${again}${said}${trouble}`;
}

function wireFoundField(container, field) {
    const wrap = container.querySelector(`.ps-found[data-found-key="${CSS.escape(field.key)}"]`);
    if (!wrap) return;
    const hidden = wrap.querySelector(psSel(field.key));
    const body = wrap.querySelector('.ps-found-body');
    let found = null, problem = '', word = '';           // null = still looking; word = the last button's answer

    const read = () => foundPicks(hidden.value);
    const draw = () => { body.innerHTML = foundHTML(field, read(), found, problem, word); };
    const write = picks => {
        hidden.value = JSON.stringify(picks);
        draw();
        hidden.dispatchEvent(new Event('change', { bubbles: true }));
    };
    // quiet=true: a look on the clock, redrawn only when something changed
    const look = async (quiet = false) => {
        const before = JSON.stringify(found);
        if (!quiet) { found = null; problem = ''; draw(); }
        try {
            if (field.found_url) {
                const res = await fetch(field.found_url);
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || `The look failed (${res.status}).`);
                found = Array.isArray(data.found) ? data.found : [];
                problem = '';
            } else found = [];
        } catch (e) { if (!quiet) { found = []; problem = String(e.message || e); } }
        if (!quiet || JSON.stringify(found) !== before) draw();
    };
    // a thing with a button is a thing whose state changes on the other side
    // (a phone answering a pairing): keep looking while one is shown
    const clock = setInterval(() => {
        if (!document.body.contains(wrap)) return clearInterval(clock);
        if ((found || []).some(t => Array.isArray(t.actions) && t.actions.length)) look(true);
    }, 3000);
    const press = async btn => {
        if (!field.found_do_url) return;
        // acting on a thing means this thing: it is picked (a paired phone that
        // was never chosen in the dropdown refused to be added, 2026-10-12)
        const { id, do: action } = btn.dataset;
        const row = foundRows(read(), found).find(t => t.id === id);
        if (row) {
            const picks = read();
            hidden.value = JSON.stringify(field.many === false
                ? { all: false, only: [{ id: row.id, name: row.name }] }
                : { all: false, only: [...picks.only.filter(p => p.id !== row.id), { id: row.id, name: row.name }] });
            hidden.dispatchEvent(new Event('change', { bubbles: true }));
        }
        word = 'One moment...'; draw();
        try {
            const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
            const res = await fetch(field.found_do_url, { method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
                body: JSON.stringify({ id, do: action }) });
            const data = await res.json().catch(() => ({}));
            word = res.ok ? String(data.text || (data.ok ? 'Done.' : 'That did not work.'))
                          : String(data.detail || `That did not work (${res.status}).`);
        } catch (e) { word = String(e.message || e); }
        await look(true);
        draw();
    };
    // The inner inputs carry no value of their own: the hidden input speaks.
    body.addEventListener('change', e => {
        e.stopPropagation();
        const picks = read();
        if (e.target.matches('.ps-found-one')) {
            const row = foundRows(picks, found).find(t => t.id === e.target.value);
            return write(row ? { all: false, only: [{ id: row.id, name: row.name }] }
                             : { all: true, only: picks.only });
        }
        if (e.target.matches('input[type="radio"]')) return write({ ...picks, all: e.target.value === 'all' });
        if (e.target.matches('.ps-found-tick')) {
            const { id, name } = e.target.dataset;
            const only = picks.only.filter(p => p.id !== id);
            if (e.target.checked) only.push({ id, name });
            write({ all: false, only });
        }
    });
    body.addEventListener('click', e => {
        const btn = e.target.closest('.ps-found-do');
        if (btn) return press(btn);
        if (e.target.closest('.ps-found-look')) look();
    });
    look();
}

function renderWidget(field, value) {
    const id = `ps-${field.key}`;
    const widget = field.widget || inferWidget(field);

    switch (widget) {
        case 'rows': {
            const items = Array.isArray(value) ? value : [];
            return `<div class="ps-rows" data-rows-key="${escapeHtml(field.key)}">
                <input type="hidden" id="${id}" value="${escapeHtml(JSON.stringify(items))}">
                <div class="ps-rows-body"></div>
                <button type="button" class="btn-action ps-rows-add">${escapeHtml(field.add_label || '+ Add')}</button>
            </div>`;
        }

        case 'textarea':
            // secret textarea (a pasted multi-line key): never echoed. Shows
            // "Set" + clear, like the password widget, which cannot hold
            // line breaks.
            if (field.secret) {
                const isSet = value && String(value).trim();
                const mark = isSet
                    ? '<small style="color:var(--success,#4caf50);margin-left:6px">\u2713 Set</small> <a href="#" class="ps-clear-key" data-field="' + id + '" style="font-size:var(--font-xs);margin-left:4px;color:var(--text-muted)">clear</a>'
                    : '';
                return `<textarea id="${id}" rows="${field.rows || 4}" placeholder="${isSet ? 'Set. Paste a new one to replace it.' : escapeHtml(field.placeholder || 'Paste here')}" style="width:100%;background:var(--bg-secondary,#1a1b2e);color:var(--text,#e1e1e6);border:1px solid var(--border,#333);border-radius:6px;padding:8px;font-family:monospace;font-size:var(--font-sm,13px);resize:vertical"></textarea>${mark}`;
            }
            return `<textarea id="${id}" rows="${field.rows || 8}" placeholder="${escapeHtml(field.placeholder || '')}" style="width:100%;background:var(--bg-secondary,#1a1b2e);color:var(--text,#e1e1e6);border:1px solid var(--border,#333);border-radius:6px;padding:8px;font-family:monospace;font-size:var(--font-sm,13px);resize:vertical">${escapeHtml(String(value))}</textarea>`;

        case 'password': {
            const hasValue = value && String(value).trim();
            const indicator = hasValue
                ? '<small style="color:var(--success,#4caf50);margin-left:6px">\u2713 Set</small> <a href="#" class="ps-clear-key" data-field="' + id + '" style="font-size:var(--font-xs);margin-left:4px;color:var(--text-muted)">clear</a>'
                : '';
            return `<input type="password" id="${id}" value="" placeholder="${hasValue ? '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022' : 'Enter key'}">${indicator}`;
        }

        case 'select':
            return `<select id="${id}">${(field.options || []).map(o =>
                `<option value="${escapeHtml(o.value)}" ${String(value) === String(o.value) ? 'selected' : ''}>${escapeHtml(o.label)}</option>`
            ).join('')}</select>`;

        case 'radio':
            return (field.options || []).map(o =>
                `<label style="display:inline-flex;align-items:center;gap:4px;margin-right:12px">
                    <input type="radio" name="${id}" value="${escapeHtml(o.value)}" ${String(value) === String(o.value) ? 'checked' : ''}>
                    ${escapeHtml(o.label)}
                </label>`
            ).join('');

        case 'toggle':
            return `<label class="setting-toggle">
                <input type="checkbox" id="${id}" ${value ? 'checked' : ''}>
            </label>`;

        case 'number': {
            // min/max from the schema — a plugin author could not express a
            // range before (Discord hunt 2.13.0, row 81: hour 24 fell to the
            // manifest cron silently).
            const bounds = (field.min !== undefined ? ` min="${Number(field.min)}"` : '')
                + (field.max !== undefined ? ` max="${Number(field.max)}"` : '');
            return `<input type="number" id="${id}" value="${value}" step="any"${bounds} placeholder="${escapeHtml(field.placeholder || '')}">`;
        }

        case 'list': {
            // Chips + "+ Add" row. Value lives as JSON in a hidden input so
            // readSettingsForm/getFieldValue work unchanged. With
            // options_endpoint the source is a dropdown (populated after
            // render, added values filtered out); without, a free-text input.
            const items = Array.isArray(value) ? value : (value ? [String(value)] : []);
            const src = field.options_endpoint
                ? `<select class="ps-list-src" style="flex:1"></select>`
                : `<input type="text" class="ps-list-src" placeholder="${escapeHtml(field.placeholder || '')}" style="flex:1">`;
            return `<div class="ps-list" data-list-key="${escapeHtml(field.key)}">
                <input type="hidden" id="${id}" value="${escapeHtml(JSON.stringify(items))}">
                <div class="ps-list-chips" style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:6px"></div>
                <div style="display:flex;gap:6px">${src}
                    <button type="button" class="btn-action ps-list-add">+ Add</button>
                </div>
            </div>`;
        }

        case 'found':
            return `<div class="ps-found" data-found-key="${escapeHtml(field.key)}">
                <input type="hidden" id="${id}" value="${escapeHtml(JSON.stringify(foundPicks(value)))}">
                <div class="ps-found-body"></div>
            </div>`;

        case 'button':
            return `<button type="button" id="${id}" class="btn-action" data-action-url="${escapeHtml(field.action || '')}" data-status-url="${escapeHtml(field.status || '')}">${escapeHtml(field.button_label || field.label || 'Action')}</button>`;

        default: // text
            return `<input type="text" id="${id}" value="${escapeHtml(String(value))}" placeholder="${escapeHtml(field.placeholder || '')}">`;
    }
}

function inferWidget(field) {
    if (field.type === 'boolean') return 'toggle';
    if (field.type === 'number') return 'number';
    if (field.type === 'textarea') return 'textarea';
    if (field.type === 'list') return 'list';
    if (field.type === 'rows') return 'rows';
    if (field.type === 'found') return 'found';
    // type:"password" without an explicit widget fell through to 'text' and
    // rendered the stored secret in a plaintext value="..." (live: the
    // ElevenLabs API key on screen — HDF scout, 2026-07-19).
    if (field.type === 'password') return 'password';
    if (field.options) return 'select';
    return 'text';
}

/**
 * Read form values back into a dict with type coercion.
 */
export function readSettingsForm(container, schema) {
    const result = {};
    for (const field of schema) {
        // Skip action buttons — they're not settings
        if ((field.widget || inferWidget(field)) === 'button') continue;
        // Skip hidden fields — not in this form's DOM; including them would
        // send their DEFAULTS and overwrite values owned by another UI.
        if (field.hidden) continue;
        const val = getFieldValue(container, field.key, field);
        // Password field: skip if empty (preserve stored key), send empty if
        // sentinel. Keyed off the RESOLVED widget, not just type — a
        // widget:"password" field with a non-password type would otherwise
        // wipe its stored secret on any unrelated save (scout, 2026-07-20).
        const w = field.widget || inferWidget(field);
        if (field.type === 'password' || w === 'password' || (w === 'textarea' && field.secret)) {
            if (val === '__CLEAR__') { result[field.key] = ''; continue; }
            if (!val) continue;
        }
        result[field.key] = val;
    }
    return result;
}

function getFieldValue(container, key, field) {
    const id = `ps-${key}`;
    const widget = field?.widget || inferWidget(field || {});

    if (widget === 'toggle') {
        const el = container.querySelector(psSel(key));
        return el ? el.checked : false;
    }
    if (widget === 'radio') {
        const checked = container.querySelector(`input[name="${id}"]:checked`);
        return coerce(checked?.value ?? field?.default ?? '', field);
    }
    const el = container.querySelector(psSel(key));
    if (!el) return field?.default ?? '';
    return coerce(el.value, field);
}

function coerce(value, field) {
    if (!field) return value;
    if (field.type === 'number') {
        let n = Number(value) || 0;
        if (field.min !== undefined && n < Number(field.min)) n = Number(field.min);
        if (field.max !== undefined && n > Number(field.max)) n = Number(field.max);
        return n;
    }
    if (field.type === 'boolean') return Boolean(value);
    if (field.type === 'list') {
        try { const a = JSON.parse(value); return Array.isArray(a) ? a : []; } catch { return []; }
    }
    if (field.type === 'found' || field.widget === 'found') return foundPicks(value);
    if (field.type === 'rows' || field.widget === 'rows') {
        try {
            const a = JSON.parse(value);
            return Array.isArray(a) ? a.filter(r => r && typeof r === 'object') : [];
        } catch { return []; }
    }
    return value;
}

/**
 * Attach a danger confirm gate to a field.
 */
function attachConfirmGate(container, field, managed) {
    const id = `ps-${field.key}`;
    const widget = field.widget || inferWidget(field);
    const el = widget === 'radio'
        ? container.querySelectorAll(`input[name="${id}"]`)
        : container.querySelector(psSel(field.key));

    if (!el) return;
    const conf = field.confirm;
    let previousValue = getFieldValue(container, field.key, field);

    const handler = async (e) => {
        const newValue = widget === 'toggle' ? String(e.target.checked) : e.target.value;
        if (!conf.values?.includes(newValue)) {
            previousValue = newValue;
            return;
        }

        // Block confirm-gated values entirely in managed mode — unless the
        // manifest opts out (`allow_managed: true`, e.g. alpha-feature gates
        // that warn rather than protect the host).
        if (managed && !conf.allow_managed) {
            if (widget === 'select') e.target.value = previousValue;
            else if (widget === 'toggle') { e.target.checked = previousValue === 'true'; }
            else if (widget === 'radio') {
                const prev = container.querySelector(`input[name="${id}"][value="${previousValue}"]`);
                if (prev) prev.checked = true;
            }
            const { showToast } = await import('../ui.js');
            showToast(`${conf.title || 'This option'} is disabled in managed mode`, 'error');
            e.stopImmediatePropagation();
            return;
        }

        const ok = await showDangerConfirm({
            title: conf.title || 'Confirm',
            warnings: conf.warnings || [],
            buttonLabel: conf.buttonLabel || 'Confirm',
        });

        if (!ok) {
            // Revert
            if (widget === 'select') {
                e.target.value = previousValue;
            } else if (widget === 'toggle') {
                e.target.checked = previousValue === 'true';
            } else if (widget === 'radio') {
                const prev = container.querySelector(`input[name="${id}"][value="${previousValue}"]`);
                if (prev) prev.checked = true;
            }
            e.stopImmediatePropagation();
        } else {
            previousValue = newValue;
        }
    };

    if (el instanceof NodeList || el instanceof HTMLCollection) {
        el.forEach(r => r.addEventListener('change', handler));
    } else {
        el.addEventListener('change', handler);
    }
}

// Minimal modal for displaying action results (keys, tokens, etc.). Built in
// JS to avoid requiring plugin authors to ship CSS. The value is rendered in
// a selectable <input readonly> so users can copy it without it landing in
// browser history or URL bar.
function _showActionResult(field, data, ok) {
    const existing = document.getElementById('ps-action-modal');
    if (existing) existing.remove();
    const wrap = document.createElement('div');
    wrap.id = 'ps-action-modal';
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;z-index:10000';
    const box = document.createElement('div');
    box.style.cssText = 'background:var(--bg-primary,#0f1020);color:var(--text,#e1e1e6);padding:24px;border-radius:10px;min-width:360px;max-width:80vw;border:1px solid var(--border,#333)';
    const title = document.createElement('h3');
    title.textContent = field.label || field.key || 'Result';
    title.style.cssText = 'margin:0 0 12px 0';
    box.appendChild(title);
    if (!ok) {
        const err = document.createElement('div');
        err.style.cssText = 'color:var(--error,#f44);margin-bottom:12px';
        err.textContent = `Request failed: ${data?.error || 'Unknown error'}`;
        box.appendChild(err);
    } else if (data && typeof data === 'object') {
        // Find the most likely display value — a string field (key/token/etc.)
        const displayKey = Object.keys(data).find(k => typeof data[k] === 'string' && data[k].length > 0);
        if (displayKey) {
            const lbl = document.createElement('div');
            lbl.style.cssText = 'font-size:var(--font-sm,13px);color:var(--text-muted);margin-bottom:4px';
            lbl.textContent = displayKey;
            box.appendChild(lbl);
            const input = document.createElement('input');
            input.type = 'text';
            input.readOnly = true;
            input.value = data[displayKey];
            input.style.cssText = 'width:100%;font-family:monospace;padding:8px;background:var(--bg-secondary,#1a1b2e);color:var(--text,#e1e1e6);border:1px solid var(--border,#333);border-radius:6px;font-size:var(--font-sm,13px)';
            input.addEventListener('focus', () => input.select());
            box.appendChild(input);
        } else {
            const pre = document.createElement('pre');
            pre.style.cssText = 'background:var(--bg-secondary,#1a1b2e);padding:8px;border-radius:6px;overflow:auto;max-height:40vh';
            pre.textContent = JSON.stringify(data, null, 2);
            box.appendChild(pre);
        }
    }
    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;justify-content:flex-end;margin-top:16px';
    const close = document.createElement('button');
    close.textContent = 'Close';
    close.style.cssText = 'padding:8px 16px;background:var(--accent,#4a7);color:#fff;border:0;border-radius:6px;cursor:pointer';
    close.addEventListener('click', () => wrap.remove());
    btnRow.appendChild(close);
    box.appendChild(btnRow);
    wrap.appendChild(box);
    wrap.addEventListener('click', e => { if (e.target === wrap) wrap.remove(); });
    document.body.appendChild(wrap);
}
