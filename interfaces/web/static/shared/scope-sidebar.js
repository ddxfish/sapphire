// shared/scope-sidebar.js - The Mind scope selector (left panel), wrapping the
// shared panel-list. Select = per-view (caller handles). Create/delete = LOCKSTEP
// across all Mind sections via scope-api, with a thorough typed-DELETE confirm
// since deleting a scope wipes its data in every section.
import { renderPanelList, bindPanelList } from './panel-list.js';
import { createScopeEverywhere, deleteScopeEverywhere } from './scope-api.js';
import * as ui from '../ui.js';

function esc(s) {
    if (s == null) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

/**
 * @param {{name:string,count?:number}[]} scopes
 * @param {string} selectedScope
 */
export function renderScopeSidebar(scopes, selectedScope) {
    // Fallback so a fetch failure (listScopes → []) still shows 'default'
    // rather than an empty, unusable sidebar.
    const items = (scopes && scopes.length) ? scopes : [{ name: 'default' }];
    return renderPanelList({
        title: 'Scopes',
        items,
        selectedId: selectedScope,
        idKey: 'name',
        renderItem: s => `<span class="ts-item-name">${esc(s.name)}</span>`
            + (s.count != null ? `<span class="ts-item-count">${s.count}</span>` : ''),
        addTitle: 'New scope (all Mind sections)',
        showDelete: true,
        // Only enable the trash when the selected scope actually exists in the
        // list (not a ghost/stale selection) and isn't 'default'.
        deletable: items.some(s => s.name === selectedScope) && selectedScope !== 'default',
        deleteTitle: `Delete scope "${selectedScope || ''}" everywhere`,
    });
}

/**
 * Bind the scope sidebar. onScopeChange(name) fires on selection; create/delete
 * are handled here (lockstep + confirm) and call onChanged(name|null) to refresh.
 * Optional describeScope(name) → Promise<string>: a concrete-stakes line shown
 * in the typed-DELETE confirm (e.g. per-layer counts from the Mind Palace).
 */
export function bindScopeSidebar(container, { onScopeChange, onChanged, describeScope } = {}) {
    bindPanelList(container, {
        onSelect: (name) => onScopeChange && onScopeChange(name),
        onAdd: async () => {
            const raw = prompt('New scope name — created in ALL Mind sections (memories, people, knowledge, goals):');
            const name = (raw || '').trim().toLowerCase();
            if (!name) return;
            if (!/^[a-z0-9_]{1,32}$/.test(name)) {
                ui.showToast('Invalid name — use a-z, 0-9, _ (max 32)', 'error');
                return;
            }
            const res = await createScopeEverywhere(name);
            if (res.ok) {
                const partial = res.done < res.total;
                ui.showToast(partial ? `Scope "${name}" created in ${res.done}/${res.total} sections (some failed)` : `Scope "${name}" created`, partial ? 'warning' : 'success');
                onChanged && onChanged(name);
            } else ui.showToast('Failed to create scope', 'error');
        },
        onDelete: async () => {
            const sel = container.querySelector('.panel-list-item.active');
            const name = sel?.dataset.plId;
            if (!name || name === 'default') return;
            let stakes = '';
            if (describeScope) {
                try { stakes = await describeScope(name) || ''; } catch { /* no line */ }
            }
            const confirmed = await confirmScopeDelete(name, stakes);
            if (!confirmed) return;
            const res = await deleteScopeEverywhere(name);
            if (res.ok) {
                const partial = res.done < res.total;
                ui.showToast(partial ? `Scope "${name}" deleted from ${res.done}/${res.total} sections (some failed)` : `Scope "${name}" deleted`, partial ? 'warning' : 'success');
                onChanged && onChanged(null);
            } else ui.showToast('Failed to delete scope', 'error');
        },
    });
}

// Thorough typed-DELETE confirm. Resolves true only if the user types DELETE.
// `stakes`: optional caller-supplied line with the concrete contents at risk.
function confirmScopeDelete(name, stakes = '') {
    ensureStyles();
    return new Promise(resolve => {
        document.querySelector('.scope-confirm-overlay')?.remove();
        const ov = document.createElement('div');
        ov.className = 'scope-confirm-overlay';
        ov.innerHTML = `
            <div class="scope-confirm-box">
                <h3>Delete scope “${esc(name)}”?</h3>
                <p>This removes the <strong>${esc(name)}</strong> scope and <strong>all of its data</strong>
                   in <strong>every Mind section</strong> — not just this tab: memories, self, entities,
                   knowledge, and goals. This cannot be undone.</p>
                ${stakes ? `<p class="scope-confirm-stakes"><strong>${esc(stakes)}</strong></p>` : ''}
                <p class="scope-confirm-prompt">Type <code>DELETE</code> to confirm:</p>
                <input type="text" class="scope-confirm-input" autocomplete="off" spellcheck="false">
                <div class="scope-confirm-actions">
                    <button class="btn-sm" data-act="cancel">Cancel</button>
                    <button class="btn-sm danger" data-act="confirm" disabled>Delete everywhere</button>
                </div>
            </div>`;
        document.body.appendChild(ov);
        const input = ov.querySelector('.scope-confirm-input');
        const ok = ov.querySelector('[data-act="confirm"]');
        input.focus();
        const done = (val) => { ov.remove(); resolve(val); };
        input.addEventListener('input', () => { ok.disabled = input.value.trim() !== 'DELETE'; });
        input.addEventListener('keydown', e => {
            if (e.key === 'Escape') done(false);
            if (e.key === 'Enter' && !ok.disabled) done(true);
        });
        ov.addEventListener('click', e => {
            const act = e.target.closest('[data-act]')?.dataset.act;
            if (act === 'cancel') done(false);
            else if (act === 'confirm' && !ok.disabled) done(true);
            else if (e.target === ov) done(false);
        });
    });
}

function ensureStyles() {
    if (document.getElementById('scope-confirm-styles')) return;
    const s = document.createElement('style');
    s.id = 'scope-confirm-styles';
    s.textContent = `
    .scope-confirm-overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:1000;
      display:flex;align-items:center;justify-content:center}
    .scope-confirm-box{background:var(--bg-secondary,#1b1b1b);border:1px solid var(--border,#2a2a2a);
      border-radius:10px;padding:20px 22px;max-width:460px;width:90%;box-shadow:0 12px 40px rgba(0,0,0,.4)}
    .scope-confirm-box h3{margin:0 0 10px;color:var(--text,#eee)}
    .scope-confirm-box p{margin:0 0 10px;color:var(--text-muted,#bbb);line-height:1.5;font-size:.9em}
    .scope-confirm-box code{background:var(--bg,#111);padding:1px 6px;border-radius:4px;color:#ff6b6b}
    .scope-confirm-stakes{color:#ffb86b !important}
    .scope-confirm-input{width:100%;padding:8px 10px;margin:2px 0 14px;background:var(--input-bg,#111);
      border:1px solid var(--border,#2a2a2a);border-radius:6px;color:var(--text,#eee);font:inherit}
    .scope-confirm-actions{display:flex;justify-content:flex-end;gap:8px}`;
    document.head.appendChild(s);
}
