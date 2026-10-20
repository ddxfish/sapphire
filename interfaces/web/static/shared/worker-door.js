// "Shared with other computers": who may use one of this computer's shared
// workers (core/worker_door.py, /api/worker-door/<door>). Adding a client
// shows its pairing string once, to paste on that computer; removing one
// shuts it out. Used by the TTS tab (kokoro) and plugins with a client mode.

const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export function mountWorkerDoor(el, door, { title = 'Shared with other computers', note = '' } = {}) {
    el.innerHTML = `
        <div style="display:flex;flex-direction:column;gap:8px;">
            <div style="font-weight:600;">${esc(title)}</div>
            <div class="wd-note" style="color:var(--text-muted);font-size:var(--font-sm,12px);">${esc(note)}</div>
            <div class="wd-list" style="display:flex;flex-direction:column;gap:4px;"></div>
            <div style="display:flex;gap:6px;flex-wrap:wrap;">
                <input class="wd-name" type="text" placeholder="name (e.g. vm-sapphire)" style="flex:1;min-width:140px;">
                <button class="btn btn-secondary wd-add" type="button">Add a computer</button>
            </div>
            <div class="wd-pairing" style="display:none;flex-direction:column;gap:4px;">
                <div style="font-size:var(--font-sm,12px);">Paste this on that computer. It is shown once: add the name again for a new one.</div>
                <code class="wd-string" style="display:block;word-break:break-all;padding:6px 8px;border-radius:6px;background:var(--bg-tertiary,rgba(127,127,127,.12));user-select:all;"></code>
                <button class="btn btn-sm wd-copy" type="button" style="align-self:flex-start;">Copy</button>
            </div>
            <div class="wd-msg" style="font-size:var(--font-sm,12px);color:var(--color-error,#f44336);"></div>
        </div>`;
    const $ = s => el.querySelector(s);
    const msg = t => { $('.wd-msg').textContent = t || ''; };

    async function call(method, path = '', body) {
        const res = await fetch(`/api/worker-door/${encodeURIComponent(door)}${path}`, {
            method,
            headers: { 'X-CSRF-Token': csrf(), ...(body ? { 'Content-Type': 'application/json' } : {}) },
            body: body ? JSON.stringify(body) : undefined,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || res.status);
        return data;
    }

    async function refresh() {
        try {
            const { clients, address } = await call('GET');
            $('.wd-note').textContent = note || `Others reach it at ${address}.`;
            $('.wd-list').innerHTML = clients.length
                ? clients.map(n => `<div style="display:flex;gap:8px;align-items:center;">
                        <span style="flex:1;">${esc(n)}</span>
                        <button class="btn btn-sm wd-remove" data-name="${esc(n)}" type="button">Remove</button></div>`).join('')
                : '<div style="color:var(--text-muted);font-size:var(--font-sm,12px);">No computers yet.</div>';
            el.querySelectorAll('.wd-remove').forEach(b => b.onclick = async () => {
                try { await call('DELETE', `/${encodeURIComponent(b.dataset.name)}`); refresh(); }
                catch (e) { msg(e.message); }
            });
        } catch (e) { msg(e.message); }
    }

    $('.wd-add').onclick = async () => {
        msg('');
        try {
            const { pairing } = await call('POST', '', { name: $('.wd-name').value });
            $('.wd-name').value = '';
            $('.wd-string').textContent = pairing;
            $('.wd-pairing').style.display = 'flex';
            refresh();
        } catch (e) { msg(e.message); }
    };
    $('.wd-copy').onclick = () => (navigator.clipboard?.writeText($('.wd-string').textContent) || Promise.reject())
        .then(() => { $('.wd-copy').textContent = 'Copied'; }, () => msg('Copy failed: select the text instead'));
    refresh();
}
