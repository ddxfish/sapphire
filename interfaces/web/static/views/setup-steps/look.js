// setup-steps/look.js - her look: the Visual tab as it is (themes apply live), and the default persona.
import appearanceTab from '../settings-tabs/appearance.js';
import { fetchWithTimeout } from '../../shared/fetch.js';
import { refreshInitData } from '../../shared/init-data.js';
import { showToast } from '../../shared/toast.js';
import { escapeHtml as esc } from '../../shared/modal.js';

export default w => ({
    id: 'look', title: 'Look', optional: true, tabs: [appearanceTab],

    async render(body) {
        body.innerHTML = `
            <div class="setup-sub" data-tab="appearance"></div>
            <section class="setup-sub">
                <h3>Default persona <span class="setup-sub-note">Who she is in a new chat. Personas are made under Personas; this only picks the default.</span></h3>
                <select id="setup-persona"><option value="">Loading…</option></select>
            </section>`;
        w.host.mount(appearanceTab, body.querySelector('[data-tab="appearance"]'));
        const sel = body.querySelector('#setup-persona');
        try {
            const d = await fetchWithTimeout('/api/personas', {}, 15000);
            sel.innerHTML = '<option value="">(None)</option>' + (d.personas || []).map(p =>
                `<option value="${esc(p.name)}"${p.name === d.default ? ' selected' : ''}>${esc(p.name)}</option>`).join('');
        } catch (e) { sel.innerHTML = `<option value="">${esc(e.message)}</option>`; }
        sel.addEventListener('change', async () => {
            try {
                if (sel.value) await fetchWithTimeout('/api/personas/default', {
                    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: sel.value }) }, 15000);
                else await fetchWithTimeout('/api/personas/default', { method: 'DELETE' }, 15000);
                refreshInitData();
                showToast(sel.value ? `${sel.value} is the default persona` : 'No default persona', 'success');
            } catch (e) { showToast(e.message, 'error'); }
        });
    },

    leave: () => w.save(),

    unmount(body) {
        const el = body.querySelector('[data-tab="appearance"]');
        if (el) w.host.unmount(appearanceTab, el);
    },
});
