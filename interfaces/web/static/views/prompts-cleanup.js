// views/prompts-cleanup.js - proper delete modal + cleanup tools (orphans,
// trash) for the Prompts page. Everything rides two server primitives: the
// piece-usage index and the piece trash (soft delete). Vault semantics:
// while LOCKED, vault prompts are invisible to the index, so rows lean on
// the salted piece-refs hashes (vault_referenced) — flagged rows default
// unchecked; with NO sidecar data every row defaults unchecked and the
// banner says to unlock once. The unlock-time reconcile restores any
// trashed piece a vault prompt still references, as the last net.
import { showModal } from '../shared/modal.js';
import { openChecklist, listHTML, wireList, checkedRows, esc, GENERIC_TYPES } from '../shared/checklist-modal.js';
import { getPrompt, deletePrompt, getPieceUsage, stripDanglers,
         trashPieces, restorePieces, purgeTrash, listTrash } from '../shared/prompt-api.js';
import * as ui from '../ui.js';

// Rows, sections, filters (All/None/Main/Custom) and their wiring live in
// shared/checklist-modal.js since 2026-09-08 — this file builds row objects
// and decides what each modal does with the checked ones.

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
const byTypeSections = (rows, extra = {}) => {
    const byType = {};
    rows.forEach(r => (byType[r.type] = byType[r.type] || []).push(r));
    return Object.keys(byType)
        .sort((a, b) => typeRank(a) - typeRank(b) || a.localeCompare(b))
        .map(t => ({ id: t, rows: byType[t], ...extra }));
};

// A piece row for the delete / orphan lists: usage badges, vault flags.
function pieceRow(r) {
    const badges = [];
    if (r.isVaultPiece) badges.push('\u{1F5DD} vault piece — trashed inside the vault');
    if (r.vaultFlag) badges.push('\u{1F5DD} used by a vault prompt');
    if (r.users?.length) badges.push(`in ${r.users.length} other prompt${r.users.length > 1 ? 's' : ''}`);
    else if (!r.vaultFlag) badges.push('only used here');
    return { type: r.type, key: r.key, checked: !!r.checked, flag: !!r.vaultFlag,
             stock: !!r.stock, pack: !!r.pack, badges,
             title: (r.users || []).join(', ') };
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

// ── proper delete (single prompt or roster bulk) ──

export async function openDeleteModal({ names, componentSources, vaultPieces, stockPieces,
                                        details, onDone }) {
    let usageResp;
    try { usageResp = await getPieceUsage(); }
    catch (e) { ui.showToast('Could not load the usage index', 'error'); return; }
    const usage = usageResp.usage || {};
    const vault = usageResp.vault || {};
    const vaultRef = usageResp.vault_referenced || {};
    const lockedNoData = vault.exists && !vault.unlocked && !vault.piece_refs_available;

    const pieces = new Map();
    for (const name of names) {
        let p = details?.[name] || null;   // the view already fetched every prompt
        if (!p) { try { p = await getPrompt(name); } catch { /* deleted elsewhere */ } }
        if (p?.type !== 'assembled' || !p.components) continue;
        for (const [type, val] of Object.entries(p.components)) {
            if (type.startsWith('_')) continue;
            const keys = Array.isArray(val) ? val : (val ? [val] : []);
            for (const k of keys) {
                if (!k || componentSources?.[type]?.[k]) continue;  // pack-owned
                pieces.set(`${type} ${k}`, { type, key: k });
            }
        }
    }
    const rows = [...pieces.values()].map(({ type, key }) => {
        const users = (usage[type]?.[key] || []).filter(n => !names.includes(n));
        const isVaultPiece = !!vaultPieces?.[type]?.has?.(key);
        const vaultFlag = (vaultRef[type] || []).includes(key);
        return { type, key, users, isVaultPiece, vaultFlag,
                 stock: !!stockPieces?.[type]?.has?.(key),
                 checked: !users.length && !vaultFlag && !lockedNoData };
    }).sort(byCanonical).map(pieceRow);

    const title = names.length > 1 ? `Delete ${names.length} prompts` : `Delete "${names[0]}"`;
    const modal = openChecklist({
        title, wide: true, saveLabel: 'Delete',
        intro: `The prompt record${names.length > 1 ? 's' : ''} (${names.map(esc).join(', ')}) `
             + `will be deleted. Checked pieces below move to the piece <b>trash</b> `
             + `(restorable); unchecked pieces stay.`,
        banner: vaultBanner(vault) + (rows.length ? ''
            : '<p style="font-size:var(--font-xs);opacity:0.7">No deletable pieces (monolith or pack-owned pieces only).</p>'),
        sections: rows.length ? [{ id: 'pieces', flat: true, rows }] : [],
        filters: ['all', 'none', 'main', 'custom'],
        maxHeight: '40vh',
        onSave: async (chosen) => {
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
        },
    });
    void modal;
}

// ── cleanup modal (menu → tool views, one modal, no stacking) ──

export async function openCleanupModal({ components, componentSources, vaultPieces, stockPieces, onDone }) {
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
                           stock: !!stockPieces?.[type]?.has?.(key),
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

    let danglers = usageResp.danglers || {};
    const danglerCount = () =>
        Object.values(danglers).reduce((n, refs) => n + refs.length, 0);

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
                <b>Dangling references (${danglerCount()})</b>
                <p style="font-size:var(--font-xs);opacity:0.8;margin:4px 0">Prompts pointing at
                pieces that no longer exist — the assembler skips them silently.</p>
                <button class="btn-sm" id="pc-open-danglers" ${danglerCount() ? '' : 'disabled'}>Review…</button>
            </div>
            <div style="${CARD}">
                <b>Piece trash (${trash.length})</b>
                <p style="font-size:var(--font-xs);opacity:0.8;margin:4px 0">Soft-deleted pieces.
                Restore, or empty the trash for good.</p>
                <button class="btn-sm" id="pc-open-trash" ${trash.length ? '' : 'disabled'}>Review…</button>
            </div>`;
        body.querySelector('#pc-open-orphans')?.addEventListener('click', orphanView);
        body.querySelector('#pc-open-danglers')?.addEventListener('click', danglerView);
        body.querySelector('#pc-open-trash')?.addEventListener('click', trashView);
    };

    // Danglers are read-only rows (nothing to preserve — the pieces are
    // gone); one button strips all. STRIP is refused while a vault exists
    // and is locked: sealed pieces LOOK missing, and stripping would
    // destroy valid refs — the server 409s as belt and braces.
    const danglerView = () => {
        const vaultSealed = vault.exists && !vault.unlocked;
        body.innerHTML = `
            ${vaultSealed ? `<p style="font-size:var(--font-xs);color:#f59e0b;margin:4px 0">
                \u{1F5DD} Vault locked — refs to sealed vault pieces look dangling,
                so Strip will refuse until you unlock the vault.</p>` : ''}
            <div style="max-height:45vh;overflow-y:auto;border:1px solid var(--border);border-radius:6px;padding:6px 10px;font-size:var(--font-xs)">
                ${Object.keys(danglers).sort().map(name => `
                    <div style="padding:3px 0">
                        <b>${esc(name)}</b>:
                        ${danglers[name].map(r => `${esc(r.type)}/${esc(r.key)}`).join(', ')}
                    </div>`).join('')}
            </div>
            <div style="display:flex;gap:8px;margin-top:8px">
                <button class="btn-sm danger" id="pc-strip">Strip all dead references</button>
                <button class="btn-sm" id="pc-back">Back</button>
            </div>`;
        body.querySelector('#pc-back').addEventListener('click', () => refresh());
        body.querySelector('#pc-strip')?.addEventListener('click', async () => {
            try {
                const res = await stripDanglers();
                dirty = dirty || (res.count || 0) > 0;
                ui.showToast(`Stripped ${res.count || 0} dead reference(s)`, 'success');
            } catch (e) { ui.showToast(e?.message || 'Strip failed', 'error'); }
            await refresh();
        });
    };

    const orphanView = () => {
        body.innerHTML = `
            ${vaultBanner(vault)}
            ${listHTML({ sections: byTypeSections(orphans.map(pieceRow)),
                         filters: ['all', 'none', 'main', 'custom'] })}
            <div style="display:flex;gap:8px;margin-top:8px">
                <button class="btn-sm" id="pc-orphan-go">Move checked to trash</button>
                <button class="btn-sm" id="pc-back">Back</button>
            </div>`;
        wireList(body);
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

    const trashRow = it => ({
        type: it.type, key: it.key, data: { store: it.store || 'plain' },
        label: `${esc(it.type)}/${esc(it.key)}${it.store === 'vault' ? ' \u{1F5DD}' : ''}`,
        badges: [it.deleted_at ? new Date(it.deleted_at * 1000).toLocaleString() : ''],
    });

    const trashView = () => {
        body.innerHTML = `
            ${vault.exists && !vault.unlocked ? `<p style="font-size:var(--font-xs);color:#f59e0b;margin:4px 0">
                \u{1F5DD} Vault locked — vault-piece trash (if any) stays sealed and
                hidden until unlock; Empty trash can't touch it.</p>` : ''}
            ${listHTML({ sections: byTypeSections(trash.map(trashRow)), filters: ['all', 'none', 'main'] })}
            <div style="display:flex;gap:8px;margin-top:8px">
                <button class="btn-sm" id="pc-restore">Restore checked</button>
                <button class="btn-sm danger" id="pc-purge">Empty trash</button>
                <button class="btn-sm" id="pc-back">Back</button>
            </div>`;
        wireList(body);
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
        danglers = usageResp.danglers || {};
        orphans.length = 0;
        for (const [type, entries] of Object.entries(components || {})) {
            for (const key of Object.keys(entries)) {
                if (componentSources?.[type]?.[key]) continue;
                if (vaultPieces?.[type]?.has?.(key)) continue;
                if ((u[type]?.[key] || []).length) continue;
                if (!stillLive(type, key)) continue;
                const vaultFlag = (vr[type] || []).includes(key);
                orphans.push({ type, key, users: [], vaultFlag,
                               stock: !!stockPieces?.[type]?.has?.(key),
                               checked: !vaultFlag && !lockedNoData });
            }
        }
        orphans.sort((a, b) => (a.type + a.key).localeCompare(b.type + b.key));
        menu();
    };

    menu();
}

// ── bulk vault move (roster multi-check 🗝) ─────────────────────────────
// One modal, one direction. One SECTION per selected prompt: its record row
// first, then every piece it uses — so what's going in is visible where it
// matters (17 prompt rows used to push the pieces below the fold of one
// flat box, and a prompt-kind move never cascades pieces server-side, so a
// scrolled-past piece meant a half-move; Krem 2026-09-08). A piece shared by
// two selected prompts appears under both, mirrored as one logical row.
// Plugin-pack pieces and pieces already in the destination render disabled
// with a badge — nothing is silently skipped. Main preset by default:
// records + story pieces checked, generic extras/emotions left. ONE batch
// request (/api/vault/move-batch); the modal stays open until it resolves.

export async function openBulkVaultModal({ names, direction = 'in',
        componentSources, vaultPieces, vaultNames, stockPieces, stockNames,
        details, onStart, onDone }) {
    const goingIn = direction === 'in';
    let usageResp;
    try { usageResp = await getPieceUsage(); }
    catch { ui.showToast('Could not load the usage index', 'error'); return; }
    const usage = usageResp.usage || {};
    if (!usageResp.vault?.unlocked) {
        ui.showToast('The vault is locked — unlock it to move prompts', 'error');
        return;
    }
    const already = goingIn ? 'already in vault' : 'already plaintext';
    const sections = [];
    const seenPieces = new Set();
    let nPieces = 0, nAlready = 0, nPack = 0;
    for (const name of names.slice().sort()) {
        let p = details?.[name] || null;   // the view already fetched every prompt
        if (!p) { try { p = await getPrompt(name); } catch { /* deleted elsewhere */ } }
        const recMovable = goingIn ? !vaultNames?.has?.(name) : !!vaultNames?.has?.(name);
        const rows = [{
            type: '__prompt', key: name, data: { kind: 'prompt' },
            label: `<b>${esc(name)}</b> — prompt record`,
            stock: !!stockNames?.has?.(name),
            checked: recMovable, disabled: !recMovable,
            badges: [recMovable ? '' : already],
        }];
        const pieceObjs = [];
        if (p?.type === 'assembled' && p.components) {
            for (const [type, val] of Object.entries(p.components)) {
                if (type.startsWith('_')) continue;
                const keys = Array.isArray(val) ? val : (val ? [val] : []);
                for (const key of keys) {
                    if (!key) continue;
                    const pack = componentSources?.[type]?.[key];
                    const inVault = !!vaultPieces?.[type]?.has?.(key);
                    const movable = !pack && (goingIn ? !inVault : inVault);
                    const others = (usage[type]?.[key] || []).filter(n => n !== name);
                    const alsoSelected = others.filter(n => names.includes(n));
                    const outside = others.filter(n => !names.includes(n));
                    const badges = [];
                    if (pack) badges.push(`🧩 ${esc(pack)} — plugin piece, can't move`);
                    else if (!movable) badges.push(already);
                    if (alsoSelected.length) badges.push(`also under ${alsoSelected.map(esc).join(', ')}`);
                    if (outside.length) badges.push(`in ${outside.length} other prompt${outside.length > 1 ? 's' : ''}`);
                    pieceObjs.push({
                        type, key, data: { kind: 'piece' },
                        stock: !!stockPieces?.[type]?.has?.(key), pack: !!pack,
                        checked: movable && !GENERIC_TYPES.includes(type),
                        disabled: !movable, badges, title: others.join(', '),
                    });
                    const id = `${type} ${key}`;
                    if (!seenPieces.has(id)) {
                        seenPieces.add(id);
                        nPieces++;
                        if (pack) nPack++; else if (!movable) nAlready++;
                    }
                }
            }
        }
        pieceObjs.sort(byCanonical);
        sections.push({ id: `prompt:${name}`, name, open: true,
                        label: `<b>${esc(name)}</b>`, rows: rows.concat(pieceObjs) });
    }
    const plural = names.length > 1 ? 's' : '';
    const intro = (goingIn
        ? 'Checked items are encrypted into the vault. Pieces are shared — moving one vault-routes it for every prompt using it.'
        : 'Checked items are written to the regular store as plaintext on disk.')
        + ` <span style="opacity:0.75">${names.length} prompt${plural} · ${nPieces} piece${nPieces === 1 ? '' : 's'}`
        + (nAlready ? ` (${nAlready} ${already})` : '')
        + (nPack ? ` (${nPack} plugin-owned)` : '') + '</span>';
    const title = goingIn ? `Move ${names.length} prompt${plural} + pieces to the vault`
                          : `Move ${names.length} prompt${plural} + pieces to plaintext`;
    openChecklist({
        title, intro, sections, mirrorDuplicates: true, wide: true,
        filters: ['all', 'none', 'main', 'custom'],
        saveLabel: goingIn ? 'Move to vault' : 'Move to plaintext',
        onSave: async (chosen) => {
            if (!chosen.length) { ui.showToast('Nothing checked', 'info'); return; }
            // ONE request: the server runs the same per-item movers and publishes
            // one change event at the end. The old client loop fired one request
            // per item and every success echoed back as a whole-view reload —
            // 150 items took five minutes with the modal already gone (2026-09-08).
            // showModal keeps this modal open ("Working…") until we resolve.
            const items = chosen.map(c => c.kind === 'piece'
                ? { kind: 'piece', comp_type: c.type, key: c.key }
                : { kind: 'prompt', name: c.key });
            const dest = goingIn ? 'the vault' : 'plaintext';
            onStart?.();
            let res;
            try {
                const { vaultMoveBatch } = await import('../shared/vault-api.js');
                res = await vaultMoveBatch({ direction, items });
            } catch (e) {
                ui.showToast(`Move failed: ${e?.message || 'request failed'}`, 'error');
                onDone?.();
                return;
            }
            const failed = (res?.results || []).filter(r => !r.ok);
            const label = r => r.item.kind === 'piece' ? `${r.item.comp_type}/${r.item.key}` : r.item.name;
            if (failed.length) {
                const shown = failed.slice(0, 3).map(r => `${label(r)}: ${r.msg}`).join(' · ');
                ui.showToast(`Moved ${res.moved} to ${dest} · ${failed.length} failed — ${shown}`
                             + (failed.length > 3 ? ` (+${failed.length - 3} more, see the log)` : ''), 'error', 12000);
                console.warn('[vault] batch failures:', failed);
            } else {
                ui.showToast(`Moved ${res?.moved ?? items.length} to ${dest}`, 'success');
            }
            onDone?.();
        },
    });
}
