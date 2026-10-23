// setup-steps/plugins.js - the featured plugins, ready to tick on.
// The list is the server's (core/routes/setup.py). A tick is a live toggle
// (the plugin manager's route); missing packages install first under their
// own row (shared/extras.js asks); a radio group turns the other one off
// first. "set up →" leaves for the plugin's Settings tab.
import pluginsAPI from '../../shared/plugins-api.js';
import { ensureExtra } from '../../shared/extras.js';
import { fetchWithTimeout } from '../../shared/fetch.js';
import { showToast } from '../../shared/toast.js';
import { escapeHtml as esc } from '../../shared/modal.js';

const shortDep = d => String(d).split('@')[0].split(/[<>=!~\[]/)[0].trim();

export default w => {
    let list = [];
    let rowsEl = null;

    const row = p => `
        <label class="feat-row${p.enabled ? ' on' : ''}${p.busy ? ' busy' : ''}" data-name="${esc(p.name)}" title="${esc(p.description || '')}">
            <input type="${p.group ? 'radio' : 'checkbox'}" ${p.group ? `name="feat-${esc(p.group)}"` : ''} ${p.enabled ? 'checked' : ''} ${p.busy ? 'disabled' : ''}>
            <span class="feat-icon">${p.icon || '🧩'}</span>
            <span class="feat-text">
                <span class="feat-title">${esc(p.title)}</span>
                <span class="feat-blurb">${esc(p.blurb)}</span>
                <span class="feat-note">${esc(p.note || (p.missing_deps?.length ? `installs ${p.missing_deps.map(shortDep).join(', ')} first` : ''))}</span>
            </span>
            <span class="feat-side">${p.enabled && p.has_settings ? `<button type="button" class="btn-link" data-setup="${esc(p.name)}">set up →</button>` : ''}</span>
        </label>`;

    function paint() {
        if (!rowsEl) return;
        const groups = [];
        for (const p of list) if (p.group && !groups.includes(p.group)) groups.push(p.group);
        rowsEl.innerHTML = list.filter(p => !p.group).map(row).join('')
            + groups.map(g => `<div class="feat-group"><div class="feat-group-head">One of these</div>${list.filter(p => p.group === g).map(row).join('')}</div>`).join('');
    }

    // Flip one plugin to `on`. The route TOGGLES, so only when it differs.
    async function flip(p, on) {
        if (p.enabled === on) return;
        if (on && p.missing_deps?.length) {
            p.note = `installing ${p.missing_deps.map(shortDep).join(', ')}…`; paint();
            const ok = await ensureExtra(`plugin:${p.name}`);
            if (!ok) { p.note = ''; return; }
            p.missing_deps = [];
        }
        p.note = on ? 'turning on…' : 'turning off…'; paint();
        const r = await pluginsAPI.togglePlugin(p.name);
        if (r && typeof r.enabled === 'boolean') p.enabled = r.enabled;
        else p.enabled = on;
        if (p.enabled !== on) throw new Error(`the plugin did not turn ${on ? 'on' : 'off'}`);
        p.note = '';
    }

    async function onChange(e) {
        const input = e.target.closest('input');
        const el = e.target.closest('.feat-row');
        if (!input || !el) return;
        const p = list.find(x => x.name === el.dataset.name);
        if (!p || p.busy) return;
        const want = input.checked;
        const group = p.group ? list.filter(x => x.group === p.group && x !== p) : [];
        for (const x of [p, ...group]) x.busy = true;
        paint();
        try {
            for (const other of group) await flip(other, false);
            await flip(p, want);
        } catch (err) {
            p.note = `could not: ${err.message}`;
            showToast(`${p.title}: ${err.message}`, 'error');
        } finally {
            for (const x of [p, ...group]) x.busy = false;
            paint();
        }
    }

    return {
        id: 'plugins', title: 'Plugins', optional: true,

        async render(body) {
            body.innerHTML = `
                <p class="setup-lead">A few plugins worth turning on now. All of them, and many more, live under
                Settings › Plugins and the Store.</p>
                <div class="feat-rows"><span class="setting-help">Loading…</span></div>`;
            rowsEl = body.querySelector('.feat-rows');
            rowsEl.addEventListener('change', onChange);
            rowsEl.addEventListener('click', e => {
                const b = e.target.closest('[data-setup]');
                if (b) { e.preventDefault(); w.navigate(b.dataset.setup); }
            });
            try { list = (await fetchWithTimeout('/api/setup/featured', {}, 15000)).featured || []; }
            catch (e) { rowsEl.innerHTML = `<span class="setting-help">The list could not be read: ${esc(e.message)}</span>`; return; }
            if (!list.length) { rowsEl.innerHTML = '<span class="setting-help">Nothing to offer on this install.</span>'; return; }
            paint();
        },

        unmount() { rowsEl = null; list = []; },
    };
};
