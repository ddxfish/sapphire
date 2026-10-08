// shared/extras.js - an optional set of packages, installed into Sapphire's
// environment the moment a page needs it (core/extras.py).
//
//   if (!await ensureExtra('flash')) return;     // the person said no, or it failed
//
// Asks the server whether the set is there; if not, one modal: what it is,
// Install, pip's words as they come, done. Nothing else on the page changes.

import { showModal, escapeHtml as esc } from './modal.js';
import { fetchWithTimeout } from './fetch.js';
import { showToast } from './toast.js';

const sleep = ms => new Promise(r => setTimeout(r, ms));

export async function ensureExtra(name) {
    const all = (await fetchWithTimeout('/api/system/extras', {}, 15000)).extras || {};
    const x = all[name];
    if (!x) throw new Error(`No optional set called '${name}'.`);
    if (x.installed) return true;
    return new Promise(resolve => {
        let settled = false;
        const done = ok => { if (!settled) { settled = true; resolve(ok); } };
        const modal = showModal(`\u{1F4E6} ${esc(x.label)}`, [{ type: 'html', value: `
            <p class="setting-help">This needs packages Sapphire does not have yet: ${esc(x.note)}</p>
            <p class="setting-help">Install them into Sapphire's environment now? It takes a minute.${x.restart ? ' Sapphire needs a restart afterwards.' : ''}</p>
            <p class="setting-help" id="extra-status" style="min-height:1.2em"></p>
            <pre id="extra-log" style="display:none;max-height:140px;overflow:auto;font-size:0.75em;opacity:0.7;white-space:pre-wrap"></pre>` }],
            null, { wide: true });
        const body = modal.element;
        const btn = document.createElement('button');
        btn.className = 'btn btn-primary';
        btn.textContent = 'Install';
        body.querySelector('.modal-footer').prepend(btn);
        const gone = new MutationObserver(() => { if (!body.isConnected) { gone.disconnect(); done(false); } });
        gone.observe(document.body, { childList: true });
        btn.addEventListener('click', async () => {
            btn.disabled = true;
            const status = body.querySelector('#extra-status'), log = body.querySelector('#extra-log');
            log.style.display = '';
            try {
                let s = await fetchWithTimeout(`/api/system/extras/${name}/install`, { method: 'POST' }, 15000);
                while (s.state === 'running') {
                    status.textContent = 'Installing...';
                    log.textContent = (s.lines || []).join('\n'); log.scrollTop = log.scrollHeight;
                    await sleep(1000);
                    s = await fetchWithTimeout(`/api/system/extras/${name}`, {}, 15000);
                }
                log.textContent = (s.lines || []).join('\n');
                if (s.state !== 'done') throw new Error(s.error || 'The install failed.');
                showToast(`${x.label}: installed`, 'success');
                modal.close();
                done(true);
            } catch (e) {
                status.textContent = e.message;
                showToast(e.message, 'error');
                btn.disabled = false;
            }
        });
    });
}
