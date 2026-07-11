// views/palace/common.js - Shared helpers for the Mind Palace view trio
// (memories / entities / knowledge). Mirrors shared/mind-common.js idioms so
// the palace feels like the same Mind the user already knows.
import { csrfHeaders, escHtml, timeAgo } from '../../shared/mind-common.js';

export const API = '/api/plugin/mindpalace';
export const SCOPE_ENDPOINT = `${API}/scopes`;

// Same tab ids as MIND_TABS (ids === view ids, routing unchanged) — only the
// People label shifts: the palace L2 is people/places/THINGS. Self (L0) leads
// the strip — it's the palace-only layer and the identity root of the graph.
// ONE Knowledge tab (2026-07-11): human and AI knowledge are the same layer;
// who wrote it is metadata (meta.added_by), filterable inside the tab.
export const PALACE_TABS = [
    { id: 'self', label: 'Self', icon: '\u{1F4A0}' },
    { id: 'memories', label: 'Memories', icon: '\u{1F9E0}' },
    { id: 'people', label: 'Entities', icon: '\u{1F465}' },
    { id: 'knowledge', label: 'Knowledge', icon: '\u{1F4DA}' },
    { id: 'goals', label: 'Goals', icon: '\u{1F3AF}' },
];

export async function palaceGet(path) {
    const r = await fetch(`${API}/${path}`, { credentials: 'same-origin' });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || data.detail || `HTTP ${r.status}`);
    return data;
}

export async function palaceSend(path, method, body) {
    const r = await fetch(`${API}/${path}`, {
        method,
        credentials: 'same-origin',
        headers: csrfHeaders({ 'Content-Type': 'application/json' }),
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || data.detail || `HTTP ${r.status}`);
    return data;
}

// Concrete-stakes line for the scope-delete confirm (Krem's fork-B ruling,
// 2026-07-11): the typed-DELETE box shows exactly what the whole scope holds.
export async function describeScopeForDelete(name) {
    try {
        const st = await palaceGet(`status?scope=${encodeURIComponent(name)}`);
        const L = st.layers || {};
        return `Right now “${name}” holds ${L.events || 0} memories, `
            + `${L.self || 0} self entries, ${st.entities || 0} entities, and `
            + `${L.knowledge || 0} knowledge chunks — ALL of it goes.`;
    } catch { return ''; }
}

export function labelHue(label) {
    if (!label) return 220;
    let h = 0;
    for (let i = 0; i < label.length; i++) h = (h * 31 + label.charCodeAt(i)) % 360;
    return h;
}

export function labelChip(label) {
    if (!label) return '';
    const hue = labelHue(label);
    return `<span class="mind-mem-label" style="background:hsl(${hue},60%,18%);color:hsl(${hue},80%,72%);border:1px solid hsl(${hue},60%,32%)">${escHtml(label)}</span>`;
}

export function layerChip(layer) {
    return `<span class="palace-layer palace-layer-${escHtml(layer)}">${escHtml(layer)}</span>`;
}

export function keyPill(privateKey) {
    return privateKey
        ? `<span class="mind-mem-key" title="Gated by this private key — only AI calls passing this key can see it">\u{1F512} ${escHtml(privateKey)}</span>`
        : '';
}

export function favStar(id, favorite) {
    return `<button class="palace-fav ${favorite ? 'is-fav' : ''}" data-id="${id}" title="${favorite ? 'Favorite — never fades. Click to unfavorite.' : 'Mark favorite (never fades)'}">${favorite ? '★' : '☆'}</button>`;
}

// The metadata window — every chunk carries its meta JSON; this renders it
// as a compact expandable panel instead of raw JSON.
export function metaPanel(meta) {
    if (!meta) return '';
    const rows = [];
    const prov = ['persona', 'model', 'chat', 'channel', 'session_id']
        .filter(k => meta[k]).map(k => `<span class="palace-meta-kv"><b>${k.replace('_id', '')}</b> ${escHtml(String(meta[k]))}</span>`);
    if (prov.length) rows.push(`<div class="palace-meta-row">${prov.join('')}</div>`);
    if (meta.refers_to_time?.length)
        rows.push(`<div class="palace-meta-row"><b>time refs</b> ${meta.refers_to_time.map(t => `<span class="palace-pill">${escHtml(t)}</span>`).join('')}</div>`);
    if (meta.noun_candidates?.length)
        rows.push(`<div class="palace-meta-row"><b>nouns</b> ${meta.noun_candidates.map(n => `<span class="palace-pill palace-pill-dim">${escHtml(n)}</span>`).join('')}</div>`);
    if (meta.stats) {
        const s = meta.stats;
        const bits = [`${s.words ?? '?'} words`];
        if (s.question) bits.push('question');
        if (s.url) bits.push('url');
        if (s.code) bits.push('code');
        rows.push(`<div class="palace-meta-row palace-meta-dim">${bits.map(escHtml).join(' · ')}</div>`);
    }
    if (meta.import_key) rows.push(`<div class="palace-meta-row palace-meta-dim">imported (${escHtml(String(meta.import_key))})</div>`);
    if (!rows.length) return '';
    return `<details class="palace-meta"><summary>meta</summary>${rows.join('')}</details>`;
}

export function chunkCard(c, { showLayer = true } = {}) {
    const tierChip = c.tier ? `<span class="palace-tier palace-tier-${c.tier}" title="Tier ${c.tier}: ${['', 'headline', 'facts', 'trivia'][c.tier] || ''}">T${c.tier}</span>` : '';
    const entChip = c.entity_name ? `<span class="palace-ent-chip">${escHtml(c.entity_name)}</span>` : '';
    const author = c.meta?.added_by;
    const authorPill = author
        ? `<span class="palace-author-pill" title="Added by ${author === 'ai' ? 'Sapphire' : 'you'}">${author === 'ai' ? '\u{1F916}' : '\u{1F464}'}</span>`
        : '';
    const pruned = !!c.meta?.pruned_at;
    const prunedPill = pruned
        ? `<span class="palace-pruned-pill" title="Retired by the librarian${c.meta.pruned_reason ? ': ' + escHtml(c.meta.pruned_reason) : ''} — hidden from her recall, restorable">\u{1F9F9} retired</span>`
        : '';
    return `
        <div class="mind-mem-card palace-chunk${pruned ? ' palace-chunk-pruned' : ''}" data-id="${c.id}">
            <div class="mind-mem-header">
                ${showLayer ? layerChip(c.layer) : ''}
                ${tierChip}${entChip}${authorPill}
                ${labelChip(c.label)}${prunedPill}
                ${keyPill(c.private_key)}
                <span class="mind-mem-time">${escHtml(timeAgo(c.created))}</span>
                <span class="mind-mem-id">[${c.id}]</span>
                ${favStar(c.id, c.favorite)}
            </div>
            <div class="mind-mem-content">${escHtml(c.content)}</div>
            ${metaPanel(c.meta)}
            <div class="mind-mem-actions">
                ${pruned ? `<button class="mind-btn-sm palace-unprune" data-id="${c.id}" title="Restore to her recall">↩ restore</button>` : ''}
                <button class="mind-btn-sm palace-del-chunk" data-id="${c.id}" title="Delete">✕</button>
            </div>
        </div>`;
}

// Shared binder for chunk-card actions inside a container. onChange re-renders.
export function bindChunkCards(el, onChange, ui) {
    el.querySelectorAll('.palace-fav').forEach(btn => {
        btn.addEventListener('click', async () => {
            const fav = !btn.classList.contains('is-fav');
            try {
                await palaceSend(`chunks/${btn.dataset.id}/favorite`, 'POST', { favorite: fav });
                await onChange();
            } catch (e) { ui.showToast(`Favorite failed: ${e.message}`, 'error'); }
        });
    });
    el.querySelectorAll('.palace-del-chunk').forEach(btn => {
        btn.addEventListener('click', async () => {
            if (!confirm('Delete this memory?')) return;
            try {
                await palaceSend(`chunks/${btn.dataset.id}`, 'DELETE');
                ui.showToast('Deleted', 'success');
                await onChange();
            } catch (e) { ui.showToast(`Delete failed: ${e.message}`, 'error'); }
        });
    });
    el.querySelectorAll('.palace-unprune').forEach(btn => {
        btn.addEventListener('click', async () => {
            try {
                await palaceSend(`chunks/${btn.dataset.id}/unprune`, 'POST', {});
                ui.showToast('Restored to her recall', 'success');
                await onChange();
            } catch (e) { ui.showToast(`Restore failed: ${e.message}`, 'error'); }
        });
    });
}
