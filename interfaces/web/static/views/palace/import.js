// views/palace/import.js - the Import tab (2026-10-30). What the classic
// Memory (legacy) databases would bring into the palace — per store, per
// scope, against what the palace already holds — BEFORE anything runs;
// one button; progress from the background run. Read-only toward the old
// databases (the importer opens them mode=ro); the copy is additive and
// idempotent, so the numbers here are the whole story.
import { renderSectionHeader, bindSectionHeader } from '../../shared/section-header.js';
import { helpPills } from '../../features/video-link.js';
import { escHtml } from '../../shared/mind-common.js';
import * as ui from '../../ui.js';
import { PALACE_TABS, refreshPalaceTabs, palaceGet, palaceSend } from './common.js';

const STORES = [
    ['memories', '\u{1F9E0} Memories', 'memory.db', 'Memories tab (events layer)'],
    ['people', '\u{1F465} People', 'knowledge.db', 'Entities tab (person cards)'],
    ['knowledge', '\u{1F4DA} Knowledge', 'knowledge.db', 'Knowledge tab; Admin › Copy knowledge into the Library makes documents of them'],
    ['goals', '\u{1F3AF} Goals', 'goals.db', 'Goals tab (subtasks and journal notes kept)'],
];
const FILES = ['memory.db', 'knowledge.db', 'goals.db'];

let container = null;
let _gen = 0;
let _timer = null;

export default {
    init(el) { container = el; },
    async show() {
        await refreshPalaceTabs();
        render();
    },
    hide() { _gen++; clearTimeout(_timer); _timer = null; },
};

function content() { return container?.querySelector('#pal-import-content'); }

function render() {
    if (!container) return;
    container.innerHTML = `
        ${renderSectionHeader({ tabs: PALACE_TABS, active: 'import', help: helpPills('Import', { doc: 'MIND-PALACE.md', inline: true }), status: '\u{1F4E6} Mind Palace — bring Memory (legacy) across. The old databases are opened read-only and never changed; the import is additive and safe to re-run.' })}
        <div class="view-body view-scroll" id="pal-import-content"><div class="ui-empty">Counting…</div></div>`;
    bindSectionHeader(container);
    load();
}

async function load() {
    const gen = ++_gen;
    let p;
    try {
        p = await palaceGet('import/preview');
    } catch (e) {
        const el = content();
        if (el) el.innerHTML = `<div class="ui-empty">Failed to load: ${escHtml(e.message)}</div>`;
        return;
    }
    if (gen !== _gen) return;
    paint(p);
    if (p.running) poll(gen);
}

function fmtBytes(n) {
    if (!n && n !== 0) return '';
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

const num = n => `<td style="text-align:right;padding:4px 10px">${n}</td>`;

function paint(p) {
    const el = content();
    if (!el) return;
    const files = p.files || {};
    const anyFile = FILES.some(f => files[f]?.present);
    const fileRows = FILES.map(f => {
        const info = files[f] || {};
        return `<div class="ui-row" style="gap:10px">
            <span class="ui-dot ${info.present ? 'ui-dot-ok' : ''}"></span>
            <code>user/${escHtml(f)}</code>
            <span class="ui-meta-text">${info.present ? fmtBytes(info.bytes) : 'not found'}</span>
        </div>`;
    }).join('');

    // One line per scope across the stores: the scope is what you import.
    const byScope = {};
    let total = 0, imported = 0, anyStore = false;
    for (const [key, label] of STORES) {
        const s = p.stores?.[key];
        if (!s || s.absent) continue;
        anyStore = true;
        total += s.total; imported += s.imported;
        for (const [scope, v] of Object.entries(s.scopes || {})) {
            const row = byScope[scope] ||= { total: 0, imported: 0, pending: 0, removed: 0, tombstoned: 0, stores: [] };
            for (const k of ['total', 'imported', 'pending', 'removed', 'tombstoned']) row[k] += v[k] || 0;
            row.stores.push(`${label} ${v.total}`);
        }
    }
    const scopeRows = Object.entries(byScope).sort(([a], [b]) => a.localeCompare(b)).map(([scope, v]) => `<tr>
            <td style="padding:4px 10px"><b>${escHtml(scope)}</b><div class="ui-meta-text">${escHtml(v.stores.join(' · '))}</div></td>
            ${num(v.total)}${num(v.imported)}
            ${num(v.pending ? `<b>${v.pending}</b>` : `<span class="ui-meta-text">0</span>`)}
            ${num(v.removed ? `<span style="color:var(--warning,#e0a030)">${v.removed}</span>` : `<span class="ui-meta-text">0</span>`)}
            <td style="padding:4px 10px">${v.pending ? `<button class="mind-btn-sm pal-import-scope" data-scope="${escHtml(scope)}" ${p.running ? 'disabled' : ''}>Import ${v.pending}</button>` : ''}</td>
        </tr>`);
    const pending = p.pending || 0, removed = p.removed || 0;
    const table = scopeRows.length ? `
        <table style="border-collapse:collapse;width:100%;max-width:820px">
            <thead><tr class="ui-meta-text">
                <th style="text-align:left;padding:4px 10px">Scope</th>
                <th style="text-align:right;padding:4px 10px">In Memory (legacy)</th>
                <th style="text-align:right;padding:4px 10px">In the palace</th>
                <th style="text-align:right;padding:4px 10px">New</th>
                <th style="text-align:right;padding:4px 10px">Removed before</th>
                <th></th>
            </tr></thead>
            <tbody>${scopeRows.join('')}
            <tr style="border-top:1px solid var(--border)"><td style="padding:6px 10px"><b>Total</b></td>${num(total)}${num(imported)}${num(pending ? `<b>${pending}</b>` : '0')}${num(removed || '0')}<td></td></tr>
            </tbody>
        </table>` : `<div class="ui-empty">${anyStore ? 'The classic databases are empty.' : 'No Memory (legacy) databases found — nothing to import. A fresh install starts in the palace.'}</div>`;

    const notes = [];
    notes.push('<b>New</b> = never imported. <b>Removed before</b> = imported once and since deleted from the palace; those stay out unless you bring them back below. Deleting a palace memory now keeps its key as a tombstone, so it never comes back.');
    if (p.rag_rows) notes.push(`${p.rag_rows} row${p.rag_rows === 1 ? '' : 's'} in per-chat attachment tabs stay with their chats — core keeps those under either engine.`);
    notes.push('Knowledge entries also become Library documents in a second step: Admin › Migration › Copy knowledge into the Library.');
    notes.push('Re-running copies zero duplicates; switching back to Memory (legacy) is the same plugin toggle in reverse.');

    const removedBox = removed ? `
        <div class="ui-box" style="border-color:var(--warning-border,var(--border))">
            <div class="ui-box-head">
                <span class="ui-box-title">\u{1F5D1} Removed before</span>
                <span class="ui-box-desc">${removed} row${removed === 1 ? '' : 's'} you deleted from the palace after an earlier import — still in the old database</span>
            </div>
            <div class="ui-box-body">
                <div class="ui-row" style="gap:10px;flex-wrap:wrap">
                    <select class="palace-search" id="pal-import-rm-scope" style="max-width:220px">
                        <option value="">every scope</option>
                        ${Object.entries(byScope).filter(([, v]) => v.removed).map(([s, v]) => `<option value="${escHtml(s)}">${escHtml(s)} (${v.removed})</option>`).join('')}
                    </select>
                    <button class="mind-btn-sm" id="pal-import-rm-show">Show them</button>
                    <button class="mind-btn-sm" id="pal-import-rm-back" ${p.running ? 'disabled' : ''}>Bring back…</button>
                    <button class="mind-btn-sm" id="pal-import-rm-scrub" style="color:var(--error,#f44)" ${p.running ? 'disabled' : ''}>Scrub from the old database…</button>
                </div>
                <div class="ui-meta-text" style="margin-top:6px">Bring back re-imports them into the palace. Scrub deletes them from Memory (legacy) for good and tombstones them — the only time the palace ever writes to the old database, and only on your typed confirm.</div>
                <div id="pal-import-rm-list" style="margin-top:8px"></div>
            </div>
        </div>` : '';

    const running = !!p.running;
    const last = p.last ? `<pre class="ui-meta-text" style="white-space:pre-wrap;margin:8px 0 0" id="pal-import-result">${escHtml(p.last)}</pre>` : '<pre class="ui-meta-text" style="white-space:pre-wrap;margin:8px 0 0" id="pal-import-result" hidden></pre>';
    el.innerHTML = `
        <div class="ui-box">
            <div class="ui-box-head">
                <span class="ui-box-title">\u{1F5C4} Memory (legacy) databases</span>
                <span class="ui-box-desc">opened read-only, never written</span>
            </div>
            <div class="ui-box-body">${fileRows}</div>
        </div>
        <div class="ui-box">
            <div class="ui-box-head">
                <span class="ui-box-title">\u{1F4CB} What will come across</span>
                <span class="ui-box-desc">every scope, as named in the old system</span>
            </div>
            <div class="ui-box-body">
                ${table}
                <ul class="ui-meta-text" style="margin:10px 0 0;padding-left:18px">${notes.map(n => `<li>${n}</li>`).join('')}</ul>
            </div>
        </div>
        <div class="ui-box">
            <div class="ui-box-head">
                <span class="ui-box-title">\u{1F4E6} Import</span>
                <span class="ui-box-desc">${running ? 'running…' : (pending ? `${pending} new` : 'everything new is in the palace')}</span>
            </div>
            <div class="ui-box-body">
                <div class="ui-row" style="gap:12px">
                    <button class="mind-btn" id="pal-import-run" ${(!pending || running) ? 'disabled' : ''}>${running ? '⏳ Importing…' : (pending ? `Import ${pending} new row${pending === 1 ? '' : 's'} (all scopes)` : 'Nothing new to import')}</button>
                    <span class="ui-meta-text" id="pal-import-stage">${running ? escHtml(stageText(p)) : ''}</span>
                </div>
                <div id="pal-import-bar" style="height:6px;border-radius:3px;background:var(--border);margin-top:10px;max-width:720px;${running ? '' : 'display:none'}">
                    <div id="pal-import-fill" style="height:100%;border-radius:3px;background:var(--accent,#5ac8fa);width:0%;transition:width .3s"></div>
                </div>
                ${last}
            </div>
        </div>
        ${removedBox}`;
    el.querySelector('#pal-import-run')?.addEventListener('click', () => runImport({}));
    el.querySelectorAll('.pal-import-scope').forEach(btn =>
        btn.addEventListener('click', () => runImport({ scope: btn.dataset.scope })));
    const rmScope = () => el.querySelector('#pal-import-rm-scope')?.value || '';
    el.querySelector('#pal-import-rm-show')?.addEventListener('click', async () => {
        const box = el.querySelector('#pal-import-rm-list');
        try {
            const r = await palaceGet(`import/removed?scope=${encodeURIComponent(rmScope())}`);
            const rows = r.rows || [];
            box.innerHTML = rows.length ? `<div style="max-height:320px;overflow:auto">${rows.map(x => `
                <div class="palace-ledger-row" title="${escHtml(x.key)}">
                    <span class="palace-ledger-ts">${escHtml((x.created || '').slice(0, 10))}</span>
                    <span class="ui-meta-text" style="min-width:90px">${escHtml(x.scope)} · ${escHtml(x.store)}</span>
                    <span class="palace-ledger-sum">${escHtml(x.preview)}</span>
                </div>`).join('')}</div>` : '<div class="ui-empty">Nothing removed before in that scope.</div>';
        } catch (e) { ui.showToast(e.message, 'error'); }
    });
    el.querySelector('#pal-import-rm-back')?.addEventListener('click', () => {
        const s = rmScope();
        const n = s ? byScope[s]?.removed : removed;
        if (!confirm(`Bring back ${n} removed row${n === 1 ? '' : 's'} ${s ? `in scope '${s}'` : 'in every scope'}?\n\nThey were deleted from the palace after an earlier import. This re-imports them; you can delete them again afterwards.`)) return;
        runImport({ scope: s || undefined, include_removed: true, confirm: s || 'all' });
    });
    el.querySelector('#pal-import-rm-scrub')?.addEventListener('click', async () => {
        const s = rmScope();
        const n = s ? byScope[s]?.removed : removed;
        const typed = prompt(`⚠ PERMANENTLY DELETE ${n} row${n === 1 ? '' : 's'} from Memory (legacy) ${s ? `in scope '${s}'` : 'in every scope'}.\n\nThese are rows you already removed from the palace. They will be gone from the old database too, and tombstoned so they never return. Nothing else in the old database changes.\n\nType ${s ? `the scope name ('${s}')` : `'all'`} to confirm:`);
        if (typed === null) return;
        if (typed !== (s || 'all')) { ui.showToast('Did not match — nothing scrubbed', 'warning'); return; }
        try {
            const r = await palaceSend('import/scrub', 'POST', { scope: s || undefined, confirm: typed });
            ui.showToast(`Scrubbed ${r.scrubbed} row${r.scrubbed === 1 ? '' : 's'} from the old database`, 'success');
            load();
        } catch (e) { ui.showToast(e.message, 'error'); }
    });
}

function stageText(st) {
    if (!st) return '';
    const stage = st.stage ? `${st.stage}` : '';
    return st.total ? `${stage}: ${st.done}/${st.total}` : stage;
}

async function runImport(opts) {
    const el = content();
    el?.querySelectorAll('#pal-import-run, .pal-import-scope').forEach(b => { b.disabled = true; });
    try {
        const r = await palaceSend('import/run', 'POST', { what: 'all', ...opts });
        ui.showToast(r.message || 'Import started', 'success');
    } catch (e) {
        ui.showToast(e.message, 'error');
        load();
        return;
    }
    load();
}

async function poll(gen) {
    clearTimeout(_timer);
    let st;
    try { st = await palaceGet('import/status'); }
    catch { return; }
    if (gen !== _gen) return;
    const el = content();
    if (!el) return;
    if (st.running) {
        const pct = st.total ? Math.round(100 * st.done / st.total) : 0;
        const fill = el.querySelector('#pal-import-fill');
        if (fill) fill.style.width = `${pct}%`;
        const stage = el.querySelector('#pal-import-stage');
        if (stage) stage.textContent = stageText(st);
        _timer = setTimeout(() => poll(gen), 1000);
        return;
    }
    ui.showToast(st.error ? `Import failed: ${st.error}` : 'Import finished', st.error ? 'error' : 'success');
    load();
}
