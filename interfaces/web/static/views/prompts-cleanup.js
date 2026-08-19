// views/prompts-cleanup.js - proper delete modal + cleanup tools (orphans,
// trash) for the Prompts page. Everything rides two server primitives: the
// piece-usage index and the piece trash (soft delete). Vault semantics:
// while LOCKED, vault prompts are invisible to the index, so rows lean on
// the salted piece-refs hashes (vault_referenced) — flagged rows default
// unchecked; with NO sidecar data every row defaults unchecked and the
// banner says to unlock once. The unlock-time reconcile restores any
// trashed piece a vault prompt still references, as the last net.
import { showModal } from '../shared/modal.js';
import { getPrompt, deletePrompt, getPieceUsage,
         trashPieces, restorePieces, purgeTrash, listTrash } from '../shared/prompt-api.js';
import * as ui from '../ui.js';

function esc(s) {
    if (s == null) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

const ROW_STYLE = 'display:flex;gap:8px;align-items:center;padding:3px 0;font-size:var(--font-xs)';
const BADGE_STYLE = 'opacity:0.75;margin-left:auto;white-space:nowrap';
const LINK_STYLE = 'color:var(--accent);cursor:pointer;text-decoration:underline';
// Library-chip sections — the Main preset keeps these (same split as the
// prompt editor's Main: generic extras/emotions aren't junk when unused).
const GENERIC_TYPES = ['extras', 'emotions'];
// Canonical section order — MUST match the editor's accordion order
// (SINGLE_TYPES then MULTI_TYPES in prompts.js). Unknown types sort after.
const TYPE_ORDER = ['character', 'location', 'goals', 'relationship',
                    'format', 'scenario', 'extras', 'emotions'];
const typeRank = t => {
    const i = TYPE_ORDER.indexOf(t);
    return i === -1 ? TYPE_ORDER.length : i;
};
const byCanonical = (a, b) =>
    typeRank(a.type) - typeRank(b.type)
    || a.type.localeCompare(b.type) || a.key.localeCompare(b.key);

function presetsHTML() {
    return `
        <div style="display:flex;gap:12px;align-items:center;font-size:var(--font-xs);margin:4px 0">
            <span>Select:</span>
            <span class="pc-preset" data-preset="all" style="${LINK_STYLE}">All</span>
            <span class="pc-preset" data-preset="none" style="${LINK_STYLE}">None</span>
            <span class="pc-preset" data-preset="main" style="${LINK_STYLE}"
                  title="Everything except extras and emotions">Main</span>
        </div>`;
}

// Flat-list preset wiring (delete modal — no sections, rows carry data-type).
function wireFlatPresets(scope) {
    scope.querySelectorAll('.pc-preset').forEach(p =>
        p.addEventListener('click', () => {
            const mode = p.dataset.preset;
            scope.querySelectorAll('.pc-row:not([data-flag])').forEach(cb => {
                cb.checked = mode === 'all' ? true
                    : mode === 'none' ? false
                    : !GENERIC_TYPES.includes(cb.dataset.type);
            });
        }));
}

function pieceRow(r) {
    const badges = [];
    if (r.isVaultPiece) badges.push('\u{1F5DD} vault piece — trashed inside the vault');
    if (r.vaultFlag) badges.push('\u{1F5DD} used by a vault prompt');
    if (r.users?.length) badges.push(`in ${r.users.length} other prompt${r.users.length > 1 ? 's' : ''}`);
    else if (!r.vaultFlag) badges.push('only used here');
    return `
        <label style="${ROW_STYLE}" title="${esc((r.users || []).join(', '))}">
            <input type="checkbox" class="pc-row" data-type="${esc(r.type)}"
                   data-key="${esc(r.key)}"${r.vaultFlag ? ' data-flag="1"' : ''} ${r.checked ? 'checked' : ''}>
            <span>${esc(r.type)}/${esc(r.key)}</span>
            <span style="${BADGE_STYLE}">${badges.join(' · ')}</span>
        </label>`;
}

// Section header checkboxes mirror their rows: checked = every selectable
// row on, indeterminate = some. Vault-flagged rows never join bulk gestures
// (section toggles, presets) — they stay individually clickable only.
function syncSectionChecks(scope) {
    scope.querySelectorAll('.pc-sec').forEach(sec => {
        const rows = [...sec.querySelectorAll('.pc-row:not([data-flag])')];
        const head = sec.querySelector('.pc-sec-check');
        if (!head) return;
        const on = rows.filter(r => r.checked).length;
        head.checked = rows.length > 0 && on === rows.length;
        head.indeterminate = on > 0 && on < rows.length;
    });
}

function vaultBanner(vault) {
    if (!vault?.exists || vault.unlocked) return '';
    if (vault.piece_refs_available) {
        return `<p style="font-size:var(--font-xs);color:#f59e0b;margin:4px 0">
            \u{1F5DD} Vault locked — pieces referenced by vault prompts are
            flagged and left unchecked.</p>`;
    }
    return `<p style="font-size:var(--font-xs);color:#f59e0b;margin:4px 0">
        \u{1F5DD} Vault locked with no reference data — pieces used by vault
        prompts CANNOT be detected. Everything defaults unchecked; unlock the
        vault once to build the index.</p>`;
}

function checkedRows(element) {
    return [...element.querySelectorAll('.pc-row:checked')]
        .map(cb => ({ type: cb.dataset.type, key: cb.dataset.key,
                      store: cb.dataset.store || 'plain' }));
}

// Shared sectioned list: accordions per section (collapsed, with counts),
// a header checkbox driving every selectable row inside, and the
// All/None/Main presets (Main skips the generic extras/emotions).
function sectionedHTML(byType, rowFn) {
    const types = Object.keys(byType)
        .sort((a, b) => typeRank(a) - typeRank(b) || a.localeCompare(b));
    return `
        ${presetsHTML()}
        <div style="max-height:45vh;overflow-y:auto;border:1px solid var(--border);border-radius:6px;padding:6px 10px">
            ${types.map(t => `
            <div class="pc-sec" data-type="${esc(t)}">
                <div class="pc-sec-head" style="display:flex;gap:8px;align-items:center;cursor:pointer;padding:4px 0;font-size:var(--font-xs)">
                    <input type="checkbox" class="pc-sec-check" title="Check/uncheck every ${esc(t)} piece">
                    <b>${esc(t)}</b><span style="opacity:0.7">(${byType[t].length})</span>
                    <span class="pc-sec-arrow" style="margin-left:auto">▸</span>
                </div>
                <div class="pc-sec-body" style="display:none;padding-left:20px">
                    ${byType[t].map(rowFn).join('')}
                </div>
            </div>`).join('')}
        </div>`;
}

function wireSections(body) {
    body.querySelectorAll('.pc-sec-head').forEach(head => {
        head.addEventListener('click', e => {
            if (e.target.classList.contains('pc-sec-check')) return;
            const bd = head.parentElement.querySelector('.pc-sec-body');
            const open = bd.style.display !== 'none';
            bd.style.display = open ? 'none' : '';
            head.querySelector('.pc-sec-arrow').textContent = open ? '▸' : '▾';
        });
    });
    body.querySelectorAll('.pc-sec-check').forEach(cb => {
        cb.addEventListener('click', e => e.stopPropagation());
        cb.addEventListener('change', () => {
            cb.closest('.pc-sec').querySelectorAll('.pc-row:not([data-flag])')
                .forEach(r => { r.checked = cb.checked; });
            syncSectionChecks(body);
        });
    });
    body.querySelectorAll('.pc-row').forEach(r =>
        r.addEventListener('change', () => syncSectionChecks(body)));
    body.querySelectorAll('.pc-preset').forEach(p =>
        p.addEventListener('click', () => {
            const mode = p.dataset.preset;
            body.querySelectorAll('.pc-sec').forEach(sec => {
                const want = mode === 'all' ? true
                    : mode === 'none' ? false
                    : !GENERIC_TYPES.includes(sec.dataset.type);
                sec.querySelectorAll('.pc-row:not([data-flag])')
                    .forEach(r => { r.checked = want; });
            });
            syncSectionChecks(body);
        }));
    syncSectionChecks(body);
}

// ── proper delete (single prompt or roster bulk) ──

export async function openDeleteModal({ names, componentSources, vaultPieces, onDone }) {
    let usageResp;
    try { usageResp = await getPieceUsage(); }
    catch (e) { ui.showToast('Could not load the usage index', 'error'); return; }
    const usage = usageResp.usage || {};
    const vault = usageResp.vault || {};
    const vaultRef = usageResp.vault_referenced || {};
    const lockedNoData = vault.exists && !vault.unlocked && !vault.piece_refs_available;

    const pieces = new Map();
    for (const name of names) {
        let p = null;
        try { p = await getPrompt(name); } catch { /* deleted elsewhere */ }
        if (p?.type !== 'assembled' || !p.components) continue;
        for (const [type, val] of Object.entries(p.components)) {
            if (type.startsWith('_')) continue;
            const keys = Array.isArray(val) ? val : (val ? [val] : []);
            for (const k of keys) {
                if (!k || componentSources?.[type]?.[k]) continue;  // pack-owned
                pieces.set(`${type} ${k}`, { type, key: k });
            }
        }
    }
    const rows = [...pieces.values()].map(({ type, key }) => {
        const users = (usage[type]?.[key] || []).filter(n => !names.includes(n));
        const isVaultPiece = !!vaultPieces?.[type]?.has?.(key);
        const vaultFlag = (vaultRef[type] || []).includes(key);
        return { type, key, users, isVaultPiece, vaultFlag,
                 checked: !users.length && !vaultFlag && !lockedNoData };
    }).sort(byCanonical);

    const title = names.length > 1 ? `Delete ${names.length} prompts` : `Delete "${names[0]}"`;
    const html = `
        <p style="font-size:var(--font-xs)">The prompt record${names.length > 1 ? 's' : ''}
        (${names.map(esc).join(', ')}) will be deleted. Checked pieces below move to the
        piece <b>trash</b> (restorable); unchecked pieces stay.</p>
        ${vaultBanner(vault)}
        ${rows.length ? `${presetsHTML()}
        <div style="max-height:40vh;overflow-y:auto;border:1px solid var(--border);border-radius:6px;padding:6px 10px">
            ${rows.map(pieceRow).join('')}
        </div>` : '<p style="font-size:var(--font-xs);opacity:0.7">No deletable pieces (monolith or pack-owned pieces only).</p>'}
    `;
    const modal = showModal(title, [{ type: 'html', value: html }], async () => {
        const chosen = checkedRows(modal.element);
        let moved = 0;
        const failed = [];
        if (chosen.length) {
            // One funnel: the server trashes plaintext pieces to the sidecar
            // store and vault pieces to the vault's own encrypted trash.
            try {
                const res = await trashPieces(chosen);
                moved += (res.trashed || []).length;
                (res.skipped || []).forEach(s => failed.push(`${s.type}/${s.key}: ${s.why}`));
            } catch (e) { failed.push(`trash: ${e?.message || 'failed'}`); }
        }
        let gone = 0;
        for (const name of names) {
            try { await deletePrompt(name); gone++; }
            catch { failed.push(`${name}: prompt delete failed`); }
        }
        const msg = `Deleted ${gone} prompt${gone === 1 ? '' : 's'}`
            + (moved ? `, ${moved} piece${moved === 1 ? '' : 's'} removed` : '');
        if (failed.length) ui.showToast(`${msg} · ${failed.length} failed — ${failed[0]}`, 'error');
        else ui.showToast(msg, 'success');
        onDone?.();
    }, { wide: true, saveLabel: 'Delete' });
    wireFlatPresets(modal.element);
}

// ── cleanup modal (menu → tool views, one modal, no stacking) ──

export async function openCleanupModal({ components, componentSources, vaultPieces, onDone }) {
    let usageResp, trash;
    try {
        usageResp = await getPieceUsage();
        trash = await listTrash();
    } catch (e) { ui.showToast('Could not load cleanup data', 'error'); return; }
    const usage = usageResp.usage || {};
    const vault = usageResp.vault || {};
    const vaultRef = usageResp.vault_referenced || {};
    const lockedNoData = vault.exists && !vault.unlocked && !vault.piece_refs_available;

    // After trash/restore rounds the passed-in components snapshot is stale;
    // the trash list says which snapshot keys no longer live in the store.
    const stillLive = (type, key) =>
        !trash.some(it => it.type === type && it.key === key);

    // Orphans: plaintext, non-pack, non-vault pieces no visible prompt uses.
    const orphans = [];
    for (const [type, entries] of Object.entries(components || {})) {
        for (const key of Object.keys(entries)) {
            if (componentSources?.[type]?.[key]) continue;   // pack-owned
            if (vaultPieces?.[type]?.has?.(key)) continue;   // vault store
            if ((usage[type]?.[key] || []).length) continue; // in use
            const vaultFlag = (vaultRef[type] || []).includes(key);
            orphans.push({ type, key, users: [], vaultFlag,
                           checked: !vaultFlag && !lockedNoData });
        }
    }
    orphans.sort((a, b) => (a.type + a.key).localeCompare(b.type + b.key));

    const modal = showModal('Prompt cleanup', [{ type: 'html', value: '<div id="pc-body"></div>' }],
                            null, { wide: true });
    const body = modal.element.querySelector('#pc-body');
    let dirty = false;
    const done = () => { if (dirty) onDone?.(); };
    modal.element.querySelector('.modal-close')?.addEventListener('click', done);
    modal.element.querySelector('.modal-x')?.addEventListener('click', done);

    const CARD = 'border:1px solid var(--border);border-radius:8px;padding:10px 12px;margin:6px 0';
    const menu = () => {
        body.innerHTML = `
            ${vaultBanner(vault)}
            <div style="${CARD}">
                <b>Orphaned pieces (${orphans.length})</b>
                <p style="font-size:var(--font-xs);opacity:0.8;margin:4px 0">Pieces no prompt
                uses. Move them to the trash — restorable until you empty it.</p>
                <button class="btn-sm" id="pc-open-orphans" ${orphans.length ? '' : 'disabled'}>Review…</button>
            </div>
            <div style="${CARD}">
                <b>Piece trash (${trash.length})</b>
                <p style="font-size:var(--font-xs);opacity:0.8;margin:4px 0">Soft-deleted pieces.
                Restore, or empty the trash for good.</p>
                <button class="btn-sm" id="pc-open-trash" ${trash.length ? '' : 'disabled'}>Review…</button>
            </div>`;
        body.querySelector('#pc-open-orphans')?.addEventListener('click', orphanView);
        body.querySelector('#pc-open-trash')?.addEventListener('click', trashView);
    };

    const orphanView = () => {
        const byType = {};
        orphans.forEach(r => (byType[r.type] = byType[r.type] || []).push(r));
        body.innerHTML = `
            ${vaultBanner(vault)}
            ${sectionedHTML(byType, pieceRow)}
            <div style="display:flex;gap:8px;margin-top:8px">
                <button class="btn-sm" id="pc-orphan-go">Move checked to trash</button>
                <button class="btn-sm" id="pc-back">Back</button>
            </div>`;
        wireSections(body);
        body.querySelector('#pc-back').addEventListener('click', () => refresh());
        body.querySelector('#pc-orphan-go').addEventListener('click', async () => {
            const chosen = checkedRows(body);
            if (!chosen.length) { ui.showToast('Nothing checked', 'info'); return; }
            try {
                const res = await trashPieces(chosen);
                const n = (res.trashed || []).length;
                dirty = dirty || n > 0;
                ui.showToast(`Moved ${n} piece${n === 1 ? '' : 's'} to trash`
                    + ((res.skipped || []).length ? ` · ${res.skipped.length} skipped` : ''),
                    'success');
            } catch (e) { ui.showToast(e?.message || 'Trash failed', 'error'); }
            await refresh();
        });
    };

    const trashRow = it => `
        <label style="${ROW_STYLE}">
            <input type="checkbox" class="pc-row" data-type="${esc(it.type)}"
                   data-key="${esc(it.key)}" data-store="${esc(it.store || 'plain')}">
            <span>${esc(it.type)}/${esc(it.key)}${it.store === 'vault' ? ' \u{1F5DD}' : ''}</span>
            <span style="${BADGE_STYLE}">${it.deleted_at ? new Date(it.deleted_at * 1000).toLocaleString() : ''}</span>
        </label>`;

    const trashView = () => {
        const byType = {};
        trash.forEach(it => (byType[it.type] = byType[it.type] || []).push(it));
        body.innerHTML = `
            ${vault.exists && !vault.unlocked ? `<p style="font-size:var(--font-xs);color:#f59e0b;margin:4px 0">
                \u{1F5DD} Vault locked — vault-piece trash (if any) stays sealed and
                hidden until unlock; Empty trash can't touch it.</p>` : ''}
            ${sectionedHTML(byType, trashRow)}
            <div style="display:flex;gap:8px;margin-top:8px">
                <button class="btn-sm" id="pc-restore">Restore checked</button>
                <button class="btn-sm danger" id="pc-purge">Empty trash</button>
                <button class="btn-sm" id="pc-back">Back</button>
            </div>`;
        wireSections(body);
        body.querySelector('#pc-back').addEventListener('click', () => refresh());
        body.querySelector('#pc-restore').addEventListener('click', async () => {
            const chosen = checkedRows(body);
            if (!chosen.length) { ui.showToast('Nothing checked', 'info'); return; }
            try {
                const res = await restorePieces(chosen);
                const n = (res.restored || []).length;
                dirty = dirty || n > 0;
                ui.showToast(`Restored ${n} piece${n === 1 ? '' : 's'}`
                    + ((res.skipped || []).length ? ` · ${res.skipped.length} skipped (${res.skipped[0].why})` : ''),
                    'success');
            } catch (e) { ui.showToast(e?.message || 'Restore failed', 'error'); }
            await refresh();
        });
        body.querySelector('#pc-purge').addEventListener('click', async () => {
            if (!confirm(`Empty the trash? ${trash.length} piece(s) will be gone for good.`)) return;
            try {
                const res = await purgeTrash();
                dirty = dirty || (res.purged || 0) > 0;
                ui.showToast(`Purged ${res.purged || 0} piece(s)`, 'success');
            } catch (e) { ui.showToast(e?.message || 'Purge failed', 'error'); }
            await refresh();
        });
    };

    // Recompute counts after any action so the menu never lies.
    const refresh = async () => {
        try {
            usageResp = await getPieceUsage();
            trash = await listTrash();
        } catch { /* keep stale rather than blank */ }
        const u = usageResp.usage || {};
        const vr = usageResp.vault_referenced || {};
        orphans.length = 0;
        for (const [type, entries] of Object.entries(components || {})) {
            for (const key of Object.keys(entries)) {
                if (componentSources?.[type]?.[key]) continue;
                if (vaultPieces?.[type]?.has?.(key)) continue;
                if ((u[type]?.[key] || []).length) continue;
                if (!stillLive(type, key)) continue;
                const vaultFlag = (vr[type] || []).includes(key);
                orphans.push({ type, key, users: [], vaultFlag,
                               checked: !vaultFlag && !lockedNoData });
            }
        }
        orphans.sort((a, b) => (a.type + a.key).localeCompare(b.type + b.key));
        menu();
    };

    menu();
}
