// core-widgets/updates.js — Updates panel (version + plugin updates).

function _esc(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function _agoStr(ts) {
    const sec = Math.floor((Date.now() / 1000) - ts);
    if (sec < 60) return 'just now';
    if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
    return `${Math.floor(sec / 86400)}d ago`;
}

export async function render(container, ctx) {
    let aborted = false;
    let updateStatus = null;

    function paintLine1() {
        if (!updateStatus) {
            container.querySelector('.upd-line1').innerHTML = '<span class="dim">checking...</span>';
            return;
        }
        const line1 = container.querySelector('.upd-line1');
        if (updateStatus.pending_update) {
            // A scheduled update is waiting on a restart — surface it and
            // offer the exit (a stranded marker used to lock updates forever).
            line1.innerHTML = `<span class="dash-pill warn" data-attention="warn">update pending restart</span> <a href="#" class="upd-cancel dim">cancel</a>`;
            line1.querySelector('.upd-cancel')?.addEventListener('click', async (ev) => {
                ev.preventDefault();
                try {
                    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                    const res = await ctx.api.fetch('/api/system/update', { method: 'DELETE', headers: { 'X-CSRF-Token': csrf } });
                    if (!res.ok) {
                        const err = await res.json().catch(() => ({}));
                        throw new Error(err.detail || `HTTP ${res.status}`);
                    }
                    ctx.api.toast('Pending update cancelled', 'success');
                    check();
                } catch (e) {
                    ctx.api.toast(`Cancel failed: ${e.message}`, 'error');
                }
            });
            return;
        }
        if (updateStatus.available) {
            const ago = updateStatus.last_check ? _agoStr(updateStatus.last_check) : 'just now';
            line1.innerHTML = `<span class="dash-pill warn" data-attention="warn">v${_esc(updateStatus.latest)} available</span> <span class="dim">· running v${_esc(updateStatus.current)} · ${_esc(ago)}</span>`;
        } else if (!updateStatus.last_check) {
            // No successful check yet — "✓ current" here would be a guess.
            line1.innerHTML = `<span class="dash-pill">not checked yet</span> <strong>v${_esc(updateStatus.current)}</strong> <span class="dim">· use Check now</span>`;
        } else {
            line1.innerHTML = `<span class="dash-pill success">✓ current</span> <strong>v${_esc(updateStatus.current)}</strong> <span class="dim">· ${_esc(_agoStr(updateStatus.last_check))}</span>`;
        }
    }

    async function check(force = false, retry = 0) {
        try {
            const res = await ctx.api.fetch('/api/system/update-check' + (force ? '?force=1' : ''));
            if (!res.ok) throw new Error('Check failed');
            updateStatus = await res.json();
            if (aborted) return;
            // Cold boot: the endpoint fires its first GitHub check async and
            // returns last_check=0 meanwhile — poll briefly before painting
            // "not checked yet" (same pattern as dashboard.js checkForUpdate).
            if (!force && !updateStatus.last_check && !updateStatus.pending_update && retry < 3) {
                setTimeout(() => { if (!aborted) check(false, retry + 1); }, 2000);
                return;
            }
            paintLine1();
            if (updateStatus.available) {
                window.dispatchEvent(new CustomEvent('update-available', { detail: updateStatus }));
            }
        } catch (e) {
            if (aborted) return;
            container.querySelector('.upd-line1').innerHTML = '<span class="dim">could not check</span>';
        }
    }

    async function loadPluginUpdates() {
        try {
            const data = await ctx.api.listStorePlugins({ featured: true, perPage: 5 });
            if (aborted) return;
            const items = (data && data.items) || [];
            const updateCount = items.filter(i => i.installed_state === 'update_available').length;
            const line2 = container.querySelector('.upd-line2');
            if (updateCount > 0) {
                line2.innerHTML = `<span class="dash-pill warn" data-attention="warn">${updateCount}</span> plugin update${updateCount === 1 ? '' : 's'}`;
            } else {
                line2.innerHTML = '<span class="dash-pill success">✓</span> plugins current';
            }
        } catch {
            const line2 = container.querySelector('.upd-line2');
            if (line2) line2.innerHTML = '<span class="dim">store unavailable</span>';
        }
    }

    container.innerHTML = `
        <div class="dash-action-panel-info-line upd-line1"><span class="dim">checking...</span></div>
        <div class="dash-action-panel-info-line upd-line2"><span class="dim">plugin updates: —</span></div>
    `;

    check();
    loadPluginUpdates();

    return {
        title: 'Updates',
        actions: [
            {
                icon: '↑', label: 'Check now',
                onClick: () => {
                    container.querySelector('.upd-line1').innerHTML = '<span class="dim">checking...</span>';
                    check(true);
                },
            },
            {
                icon: '⤴', label: 'Force pull (git)',
                onClick: async () => {
                    if (!confirm('Schedule an update? Sapphire will pre-flight the git state, take a backup, then restart to pull and install dependencies.')) return;
                    try {
                        const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                        const res = await ctx.api.fetch('/api/system/update', { method: 'POST', headers: { 'X-CSRF-Token': csrf } });
                        if (!res.ok) {
                            const err = await res.json().catch(() => ({}));
                            throw new Error(err.detail || `HTTP ${res.status}`);
                        }
                        const data = await res.json();
                        if (data.status === 'scheduled') {
                            ctx.api.toast(data.message || 'Update scheduled. Restarting...', 'success');
                            ctx.api.pollForRestart?.();
                        } else {
                            ctx.api.toast(data.message || 'No update needed', 'success');
                        }
                    } catch (e) {
                        ctx.api.toast(`Update refused: ${e.message}`, 'error');
                    }
                },
            },
        ],
        cleanup: () => { aborted = true; },
    };
}
