// folder.js - choosing the shared data folder, from the Wakewords tab or Settings: one modal, checked server-side,
// created on request, saved as the plugin setting.
import { api, putSettings, fmtBytes } from './api.js';
import { showModal, showConfirm } from '/static/shared/modal.js';

export function pickFolder(ctx, current = '') {
    showModal('Where everything is saved', [
        { type: 'html', value: `<div style="color:var(--text-secondary);font-size:var(--font-sm);margin-bottom:8px">Voices, datasets and, by default, every wake word's recordings and models. Budget about 30 GB. Pick a drive with room; avoid temporary folders.</div>` },
        { id: 'path', type: 'text', label: 'Folder', value: current || '' },
    ], async (data) => {
        const path = String(data.path || '').trim();
        if (!path) return;
        try {
            let r = await api('/settings/check-dir', { method: 'POST', body: { path } });
            const use = async () => { await putSettings({ data_dir: r.path }); ctx.toast(`Saving to ${r.path}`, 'success'); await ctx.refresh(); };
            if (!r.exists) {
                if (!r.can_create) return ctx.toast('That folder does not exist and cannot be created there', 'error');
                return showConfirm(`${r.path} does not exist. Create it?`, async () => {
                    r = await api('/settings/check-dir', { method: 'POST', body: { path, create: true } });
                    if (r.ok) await use(); else ctx.toast(r.error || 'could not use that folder', 'error');
                }, { title: 'Create folder', saveLabel: 'Create' });
            }
            if (!r.ok) return ctx.toast(r.error || (r.writable === false ? 'that folder is not writable' : 'could not use that folder'), 'error');
            if (!r.empty && !r.ours) return showConfirm(`That folder is not empty (${fmtBytes(r.disk?.free)} free). Use it anyway? Only subfolders of its own are added.`, use, { title: 'Folder not empty', saveLabel: 'Use it' });
            await use();
        } catch (e) { ctx.toast(e.message, 'error'); }
    }, { saveLabel: 'Use this folder' });
}
