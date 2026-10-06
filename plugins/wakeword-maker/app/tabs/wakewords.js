// Wakewords tab: the overview. A card per wake word (phrase, how far along, what is in it, what to do next),
// a dotted "+ New" card first. The word lists live in a modal behind each card's Words button.
import { api, esc, fmtBytes } from '../api.js';
import { pickFolder } from '../folder.js';
import { qbtn, bindHelp } from '../help.js';
import { showConfirm, showModal } from '/static/shared/modal.js';

const lines = (t) => String(t || '').split('\n').map(s => s.trim()).filter(Boolean);

export function wordsModal(ctx, p, fresh = false) {
    const found = p.negatives_auto || [];
    showModal(`"${p.phrase}"`, [
        { type: 'html', value: `<div style="color:var(--text-muted);font-size:var(--font-sm);margin-bottom:6px">${fresh ? 'Optional, and you can come back to it from the card.' : ''} One per line.</div>` },
        { id: 'spellings', type: 'textarea', rows: 3, label: 'Alternate spellings that should match (how the voices read it)', value: (p.spellings || [p.phrase]).join('\n') },
        { id: 'negatives', type: 'textarea', rows: 3, label: 'Similar words that should not match', value: (p.negatives || []).join('\n') },
        ...(found.length ? [{ id: 'negatives_auto', type: 'textarea', rows: 5, label: 'Found for you (delete any you do not want)', value: found.join('\n') }]
            : [{ type: 'html', value: '<div style="color:var(--text-muted);font-size:var(--font-sm)">More sound-alikes are found for you when you make the set.</div>' }]),
    ], async (data) => {
        const patch = { spellings: lines(data.spellings).length ? lines(data.spellings) : [p.phrase], negatives: lines(data.negatives) };
        if (found.length) patch.negatives_auto = lines(data.negatives_auto);
        try { const r = await api(`/projects/${p.slug}`, { method: 'PUT', body: patch }); Object.assign(p, r.project); }
        catch (e) { ctx.toast(e.message, 'error'); }
    }, { saveLabel: 'Save', wide: true });
}

export function newProject(ctx) {
    const base = (ctx.state.storage?.default_projects_dir || '') + '/';
    showModal('New wake word', [
        { id: 'phrase', type: 'text', label: 'The phrase, as you would say it', value: '' },
        { id: 'folder', type: 'text', label: 'Its folder (recordings, samples, models)', value: base },
        { type: 'html', value: `<div style="color:var(--text-muted);font-size:var(--font-xs)">Ending in / means a folder named after the phrase is made inside it. A full path is used as is.</div>` },
    ], async (data) => {
        const phrase = String(data.phrase || '').trim();
        if (!phrase) return ctx.toast('A phrase is needed', 'error');
        try {
            const r = await api('/projects', { method: 'POST', body: { phrase, folder: String(data.folder || '').trim() || undefined } });
            ctx.state.projects.push(r.project);
            ctx.select(r.project.slug);
            await ctx.refresh();
            wordsModal(ctx, ctx.state.project || r.project, true);
        } catch (e) { ctx.toast(e.message, 'error'); }
    }, { saveLabel: 'Create' });
}

function folderModal(ctx, p) {
    showModal(`Folder for "${p.phrase}"`, [
        { type: 'html', value: `<div style="color:var(--text-secondary);font-size:var(--font-sm);margin-bottom:8px">Everything of this wake word lives here: recordings, synthetic clips, ratings, models. Changing it moves the folder.</div>` },
        { id: 'folder', type: 'text', label: 'Folder', value: p.folder || '' },
    ], async (data) => {
        const folder = String(data.folder || '').trim();
        if (!folder || folder === p.folder) return;
        try {
            const r = await api(`/projects/${p.slug}`, { method: 'PUT', body: { folder } });
            Object.assign(p, r.project);
            ctx.toast(`Moved to ${r.project.folder}`, 'success');
            await ctx.refresh();
        } catch (e) { ctx.toast(e.message, 'error'); }
    }, { saveLabel: 'Move' });
}

export function render(el, ctx) {
    const projects = ctx.state.projects || [];
    const sel = ctx.state.slug;
    const s = ctx.state.status || {};
    const st = ctx.state.storage || {};
    const count = (c, prefix) => Object.entries(c || {}).filter(([k]) => k.startsWith(prefix)).reduce((a, [, v]) => a + v, 0);
    const shared = (st.voices || 0) + (st.datasets || 0) + (st.jobs || 0);
    el.innerHTML = `
      <div class="wwm-card wwm-path">
        <div class="wwm-row wwm-between">
          <div><b>Path to save models and data</b> ${qbtn('folder')}<div class="wwm-pathline">${esc(s.data_dir)}${s.root_ok ? (s.default_dir ? ' <span class="wwm-muted">(in the Sapphire folder; kept out of git and the nightly backup)</span>' : '') : ` <span class="wwm-error">${esc(s.root_error || 'not usable')}</span>`}</div></div>
          <button class="wwm-btn ${s.root_ok ? '' : 'primary'}" id="wwm-path-change">${s.root_ok ? 'Change' : 'Choose folder'}</button>
        </div>
        ${s.root_ok ? `<div class="wwm-muted wwm-pathsum">voices ${fmtBytes(st.voices || 0)} · datasets ${fmtBytes(st.datasets || 0)} · ${projects.length} wake word${projects.length === 1 ? '' : 's'} ${fmtBytes((st.projects || []).reduce((a, x) => a + x.bytes, 0))} · ${fmtBytes(s.disk?.free)} free${shared + (st.projects || []).reduce((a, x) => a + x.bytes, 0) > 0 ? '' : ''}</div>` : ''}
      </div>
      ${s.root_ok ? `<div class="wwm-cards">
        <button class="wwm-wcard new" id="wwm-new-card"><span class="plus">+</span><span>New wake word</span></button>
        ${projects.map(p => `
          <div class="wwm-wcard ${p.slug === sel ? 'on' : ''}" data-slug="${esc(p.slug)}">
            <div class="wwm-wtop"><b>${esc(p.phrase)}</b><span class="pct">${p.progress?.pct ?? 0}%</span></div>
            <div class="wwm-bar"><i style="width:${p.progress?.pct ?? 0}%"></i></div>
            <div class="wwm-wrows">
              <div><span>Says it</span><b>${count(p.counts, 'positive/').toLocaleString()}</b></div>
              <div><span>Doesn't say it</span><b>${count(p.counts, 'negative/').toLocaleString()}</b></div>
              <div><span>Room sound</span><b>${count(p.counts, 'ambient/').toLocaleString()}</b></div>
            </div>
            <div class="wwm-wfolder" title="${esc(p.folder || '')}">📁 ${esc(shortPath(p.folder, st.default_projects_dir))} <button class="wwm-x" data-folder title="change this wake word's folder">✎</button></div>
            <div class="wwm-wfoot">
              <span class="wwm-next" data-tab="${esc(p.progress?.tab || 'voices')}">Next: ${esc(p.progress?.next || '')}</span>
              <span class="wwm-wbtns"><button class="wwm-btn ghost" data-words>Words</button><button class="wwm-x" data-del title="delete">✕</button></span>
            </div>
          </div>`).join('')}
      </div>` : ''}`;
    bindHelp(el);
    el.querySelector('#wwm-path-change').addEventListener('click', () => pickFolder(ctx, s.data_dir || ''));
    el.querySelector('#wwm-new-card')?.addEventListener('click', () => newProject(ctx));
    el.querySelectorAll('.wwm-wcard[data-slug]').forEach(card => {
        const slug = card.dataset.slug;
        const p = projects.find(x => x.slug === slug);
        card.addEventListener('click', (e) => {
            if (e.target.closest('[data-words]')) return wordsModal(ctx, p);
            if (e.target.closest('[data-folder]')) return folderModal(ctx, p);
            if (e.target.closest('[data-del]')) {
                return showConfirm(`Delete "${p.phrase}" with every recording and clip in it? This cannot be undone.`, async () => {
                    await api(`/projects/${slug}`, { method: 'DELETE' });
                    if (ctx.state.slug === slug) ctx.state.slug = null;
                    await ctx.refresh();
                }, { title: 'Delete wake word', saveLabel: 'Delete' });
            }
            if (e.target.closest('.wwm-next')) { if (slug !== sel) ctx.state.slug = slug; ctx.select(slug); ctx.go(e.target.closest('.wwm-next').dataset.tab); return; }
            if (slug !== sel) ctx.select(slug);
        });
    });
}


function shortPath(folder, base) {
    if (!folder) return '';
    if (base && folder.startsWith(base + '/')) return '…/projects/' + folder.slice(base.length + 1);
    return folder.length > 48 ? '…' + folder.slice(-46) : folder;
}
