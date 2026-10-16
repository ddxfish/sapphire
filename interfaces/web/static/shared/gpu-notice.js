// shared/gpu-notice.js - one line under the voice tabs when an NVIDIA card is
// there but this torch cannot use it (the Windows default torch has no CUDA),
// and the one button that fixes it: queue the GPU torch install and restart.
// pip runs in the runner's window before Sapphire boots, because a loaded
// torch DLL cannot be replaced on Windows. Says nothing when all is well.
export async function gpuNotice(el, selector) {
    const box = el.querySelector(selector);
    if (!box) return;
    try {
        const res = await fetch('/api/system/gpu');
        if (!res.ok) return;
        const g = await res.json();
        box.textContent = '';
        if (g.pending) {
            box.textContent = '⏳ GPU torch install is queued; it runs on the next start, before Sapphire boots.';
            return;
        }
        if (!g.message) return;
        const msg = document.createElement('span');
        msg.textContent = `⚠ ${g.message}`;
        box.appendChild(msg);
        if (g.last_install && g.last_install.ok === false) {
            const last = document.createElement('div');
            last.textContent = `Last install attempt (${g.last_install.at || ''}) failed: ${g.last_install.message}`;
            box.appendChild(last);
        }
        if (!g.install) return;
        const btn = document.createElement('button');
        btn.className = 'btn btn-secondary';
        btn.style.cssText = 'width:auto;margin-left:0.75rem;margin-top:0.35rem';
        btn.textContent = 'Install GPU torch and restart';
        btn.title = `${g.install.why}. A 2-3 GB download from download.pytorch.org; Sapphire restarts, installs, then boots.`;
        btn.addEventListener('click', async () => {
            btn.disabled = true;
            try {
                const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                const r = await fetch('/api/system/gpu/install', { method: 'POST', headers: { 'X-CSRF-Token': csrf } });
                const d = await r.json().catch(() => ({}));
                box.textContent = r.ok
                    ? `⏳ Restarting to install torch (${d.index}). Watch the console or the launcher pane; this takes a while.`
                    : `Could not queue the install: ${d.detail || r.status}`;
            } catch (e) {
                box.textContent = `Could not queue the install: ${e.message}`;
                btn.disabled = false;
            }
        });
        box.appendChild(btn);
    } catch (_) {
        // no notice beats a broken tab
    }
}
