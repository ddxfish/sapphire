// clips.js - the clip browser shared by Human (the three things you say) and Background (the room: your takes
// and the shared library). A tab per kind, pills to filter, near misses grouped by phrase, pages of 24.
import { api, audioUrl, esc } from './api.js';
import { showPrompt } from '/static/shared/modal.js';

// the four tags on a take: how it was said, where from, what was on, who. Old clips carry one `style`; it maps on read.
export const TAGS = { how: ['normal', 'fast', 'slow', 'soft', 'loud', 'whisper', 'excited', 'tired'], where: ['close', 'across the room', 'next room'], noise: ['quiet', 'TV or music', 'busy'] };
export const TAG_DEFAULT = { how: 'normal', where: 'close', noise: 'quiet', who: '' };
export const TAG_WORD = { how: 'How', where: 'From', noise: 'Noise', who: 'Who' };
export function tagsOf(c) {
    const t = { ...TAG_DEFAULT };
    if (c.style === 'far away') t.where = 'across the room'; else if (c.style === 'TV on') t.noise = 'TV or music'; else if (TAGS.how.includes(c.style)) t.how = c.style;
    for (const k in t) if (c[k]) t[k] = c[k];
    return t;
}
export const tagLabel = (c) => { const t = tagsOf(c); return Object.keys(TAG_DEFAULT).filter(k => t[k] && t[k] !== TAG_DEFAULT[k]).map(k => t[k]).join(' · '); };

const FLAG = { clipped: ['clipped', 'too loud: the top is cut off'], faint: ['faint', 'under -30 dB at its loudest'], noisy: ['noisy', 'voice less than 20 dB above the mic noise'],
    short: ['short', 'under half a second'], long: ['long', 'over 2.5 seconds'], dropped: ['dropped', 'dropped by the rating'] };
const FACETS = ['device', 'source', 'how', 'where', 'noise', 'who', 'judge', 'text'];
const kindOf = (collection, near) => collection.startsWith('positive/') ? 'positive' : collection.startsWith('ambient/') ? 'ambient' : near ? 'near' : 'negative';

function clipRow(p, c, col) {
    const flags = (c.flags || []).map(f => FLAG[f] ? `<span class="wwm-flag ${f}" title="${FLAG[f][1]}">${FLAG[f][0]}</span>` : '').join('');
    const room = col.startsWith('ambient/');
    const bits = room
        ? [c.device, c.library ? (c.project && c.project !== p.slug ? `room day of ${c.project}` : 'room library') : '', c.held ? 'held out' : '', c.original_name]
        : [c.near && c.text ? '"' + c.text + '"' : '', c.device, c.source !== 'synth' ? tagLabel(c) : '', c.voice, c.original_name, c.boost_db ? '+' + c.boost_db + ' dB' : '', c.qa?.score != null ? 'score ' + c.qa.score : ''];
    return `<div class="wwm-clip ${c.flags?.length ? 'flagged' : ''} ${c.verdict === 'drop' ? 'bad' : ''}" data-id="${esc(c.id)}">
            <button class="play" data-play="${audioUrl(p.slug, col, c.id)}" title="play">▶</button>
            <span class="tags">${flags}${c.near ? '<span class="wwm-flag near" title="a near miss: sounds like the phrase, is not">near</span>' : ''}${c.judge ? '<span class="wwm-flag judge" title="for the judge: held out, never trained on">judge</span>' : ''}${c.seconds}s · ${bits.filter(Boolean).map(esc).join(' · ')}${c.note ? ` · <i class="wwm-note">${esc(c.note)}</i>` : ''}</span>
            <button class="wwm-x" title="${c.note ? 'edit the note' : 'add a note (who, where, which mic, anything for later)'}" data-note="${esc(c.id)}">✎</button>
            <button class="wwm-x" title="delete" data-del="${esc(c.id)}">✕</button></div>`;
}

/** Draws the browser into `box` (a card). kinds: [[id, label], ...]; one kind shows no tab strip. onDelete(col) runs after a delete. */
export function browser(box, ctx, p, { kinds, key = 'wwm.bkind', onDelete } = {}) {
    box.innerHTML = `${kinds.length > 1 ? `<div class="wwm-tabs small wwm-btabs">${kinds.map(([k, l]) => `<button data-k="${k}">${l}</button>`).join('')}</div>` : ''}
        <div class="wwm-pills"></div><div class="wwm-bgroups"></div><div class="wwm-clips"></div><div class="wwm-row wwm-between wwm-pager"></div>`;
    const saved = localStorage.getItem(key) || '';
    const B = { kind: kinds.some(k => k[0] === saved) ? saved : kinds[0][0], ...Object.fromEntries(FACETS.map(k => [k, ''])), offset: 0, limit: 24 };
    const btabs = box.querySelector('.wwm-btabs');
    btabs?.querySelectorAll('button').forEach(b => b.addEventListener('click', () => { B.kind = b.dataset.k; FACETS.forEach(k => { B[k] = ''; }); B.offset = 0; localStorage.setItem(key, B.kind); load(); }));
    let playing = null;
    function bindRow(row, col) {
        if (!row) return;
        row.querySelector('[data-play]').addEventListener('click', (e) => {
            const b = e.currentTarget;
            if (playing && playing.btn === b) { playing.audio.pause(); playing = null; b.textContent = '▶'; return; }
            if (playing) { playing.audio.pause(); playing.btn.textContent = '▶'; }
            const audio = new Audio(b.dataset.play);
            playing = { audio, btn: b };
            b.textContent = '■';
            audio.addEventListener('ended', () => { if (playing?.btn === b) { playing = null; b.textContent = '▶'; } });
            audio.play().catch(() => { b.textContent = '▶'; playing = null; });
        });
        row.querySelector('[data-del]').addEventListener('click', async () => {
            try { await api(`/projects/${p.slug}/clips/${col}/${row.dataset.id}`, { method: 'DELETE' }); } catch (e) { ctx.toast(e.message, 'error'); return; }
            if (p.counts && col in p.counts) p.counts[col] = Math.max(0, (p.counts[col] || 1) - 1);
            onDelete?.(col); load();
        });
        row.querySelector('[data-note]').addEventListener('click', async () => {
            const cur = row.querySelector('.wwm-note')?.textContent || '';
            const note = await showPrompt('Note on this clip', 'Who, where, which mic, anything that helps later', cur);
            if (note == null) return;
            await api(`/projects/${p.slug}/clips/${col}/${row.dataset.id}`, { method: 'PUT', body: { note } });
            load();
        });
    }
    async function load() {
        btabs?.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.k === B.kind));
        const clips = box.querySelector('.wwm-clips'), pills = box.querySelector('.wwm-pills'), groups = box.querySelector('.wwm-bgroups'), pager = box.querySelector('.wwm-pager');
        let r;
        try {
            const qs = new URLSearchParams({ kind: B.kind, offset: B.offset, limit: B.limit });
            for (const k of FACETS) if (B[k]) qs.set(k, B[k]);
            r = await api(`/projects/${p.slug}/browse?${qs}`);
        } catch (e) { clips.innerHTML = `<div class="wwm-error">${esc(e.message)}</div>`; return; }
        const pill = (k, v, label, n) => `<button class="wwm-pill ${B[k] === v ? 'on' : ''}" data-f="${k}" data-v="${esc(v)}">${esc(label)}${n != null ? ` <small>${n}</small>` : ''}</button>`;
        const facet = (k, title, f) => Object.keys(f || {}).length ? `<span class="wwm-facet"><span class="wwm-muted">${title}</span>${pill(k, '', 'all')}${Object.entries(f).map(([v, n]) => pill(k, v, v, n)).join('')}</span>` : '';
        const tagFacets = ['how', 'where', 'noise', 'who'].map(k => (r.facets[k] && Object.keys(r.facets[k]).length > 1) ? facet(k, TAG_WORD[k].toLowerCase(), r.facets[k]) : '').filter(Boolean);
        pills.innerHTML = facet('source', 'from', r.facets.source) + (facet('device', 'mic', r.facets.device) ? `<span class="wwm-break"></span>` + facet('device', 'mic', r.facets.device) : '')
            + (tagFacets.length ? `<span class="wwm-break"></span>` + tagFacets.join('') : '')
            + (r.judge ? `<span class="wwm-facet">${pill('judge', B.judge ? '' : '1', 'for the judge', r.judge)}</span>` : '');
        pills.querySelectorAll('[data-f]').forEach(b => b.addEventListener('click', () => { B[b.dataset.f] = b.dataset.v; B.offset = 0; load(); }));
        if (r.groups) {
            groups.innerHTML = `<div class="wwm-groups">${r.groups.map(g => `<button class="wwm-group ${B.text === g.text ? 'on' : ''}" data-t="${esc(g.text)}">${esc(g.text)} <small>${g.n}</small></button>`).join('')}</div>
                <div class="wwm-muted" style="margin:4px 0 6px">${B.text ? `showing "${esc(B.text)}" · <a href="#" data-clear>all phrases</a>` : `${r.groups.length} phrases; click one to open it`}</div>`;
            groups.querySelectorAll('.wwm-group').forEach(b => b.addEventListener('click', () => { B.text = B.text === b.dataset.t ? '' : b.dataset.t; B.offset = 0; load(); }));
            groups.querySelector('[data-clear]')?.addEventListener('click', (e) => { e.preventDefault(); B.text = ''; B.offset = 0; load(); });
            if (!B.text) { clips.innerHTML = ''; pager.innerHTML = `<span class="wwm-muted">${r.total.toLocaleString()} near misses</span>`; return; }
        } else groups.innerHTML = '';
        clips.innerHTML = r.clips.map(c => clipRow(p, c, c.collection)).join('') || '<div class="wwm-muted">nothing here yet</div>';
        r.clips.forEach(c => bindRow(clips.querySelector(`.wwm-clip[data-id="${CSS.escape(c.id)}"]`), c.collection));
        const pages = Math.max(1, Math.ceil(r.total / B.limit)), page = Math.floor(B.offset / B.limit) + 1;
        pager.innerHTML = `<span class="wwm-muted">${r.total.toLocaleString()} clip${r.total === 1 ? '' : 's'}</span>
            <span class="wwm-row"><button class="wwm-btn" data-pg="prev" ${page <= 1 ? 'disabled' : ''}>‹</button><span class="wwm-muted">page ${page} of ${pages}</span><button class="wwm-btn" data-pg="next" ${page >= pages ? 'disabled' : ''}>›</button></span>`;
        pager.querySelector('[data-pg=prev]')?.addEventListener('click', () => { B.offset = Math.max(0, B.offset - B.limit); load(); });
        pager.querySelector('[data-pg=next]')?.addEventListener('click', () => { B.offset += B.limit; load(); });
    }
    load();
    // a take just kept: reload when it lands on the open tab
    const prepend = (clips, col) => { if (kindOf(col, clips?.[0]?.near) === B.kind) load(); };
    return { load, prepend };
}
