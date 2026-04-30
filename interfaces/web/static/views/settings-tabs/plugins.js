// settings-tabs/plugins.js - Plugin Manager
import * as ui from '../../ui.js';
import { showDangerConfirm } from '../../shared/danger-confirm.js';
import pluginsAPI from '../../shared/plugins-api.js';

// Infrastructure plugins hidden from toggle list.
// (Phase 2 v7: backup + continuity were dead core-ui leftovers; the real backup
// UI lives at views/settings-tabs/backup.js and the real continuity UI is the
// schedule view. setup-wizard is the only live core-ui plugin.)
const HIDDEN = new Set(['setup-wizard']);

// Danger confirmation configs for risky plugins
const DANGER_PLUGINS = {
    ssh: {
        title: 'Enable SSH — Remote Command Execution',
        warnings: [
            'The AI can execute shell commands on configured servers',
            'Commands run with the permissions of the SSH user',
            'There is no confirmation before command execution',
            'A blacklist blocks obvious destructive commands, but it is not comprehensive',
        ],
        buttonLabel: 'Enable SSH',
        doubleConfirm: true,
        stage2Title: '\u26A0 Final Confirmation — Shell Access',
        stage2Warnings: [
            'The AI can delete files, kill processes, and modify system configuration',
            'A single bad command can brick a server or destroy data',
            'Review your blacklist and keep SSH out of chats with scheduled tasks',
        ],
    },
    bitcoin: {
        title: 'Enable Bitcoin — Autonomous Transactions',
        warnings: [
            'The AI can send Bitcoin from any configured wallet',
            'Transactions are irreversible — sent BTC cannot be recovered',
            'There is no amount limit or address whitelist',
            'A single hallucinated tool call can result in permanent loss of funds',
        ],
        buttonLabel: 'Enable Bitcoin',
        doubleConfirm: true,
        stage2Title: '\u26A0 Final Confirmation — Real Money',
        stage2Warnings: [
            'You are enabling autonomous control over real financial assets',
            'Ensure your toolsets are configured carefully',
            'Consider keeping BTC tools out of chats with scheduled tasks',
        ],
    },
    email: {
        title: 'Enable Email — AI Sends From Your Address',
        warnings: [
            'The AI can read your inbox and send emails to whitelisted contacts',
            'The AI can reply to any email regardless of whitelist',
            'The AI can archive (permanently move) messages',
            'Emails are sent from your real email address',
        ],
        buttonLabel: 'Enable Email',
    },
    homeassistant: {
        title: 'Enable Home Assistant — Smart Home Control',
        warnings: [
            'The AI can control lights, switches, thermostats, and scenes',
            'The AI can read presence data (who is home)',
            'The AI can trigger HA scripts which may have broad permissions',
            'Locks and covers are blocked by default — review your blacklist',
        ],
        buttonLabel: 'Enable Home Assistant',
    },
    toolmaker: {
        title: 'Enable Tool Maker — AI Code Execution',
        warnings: [
            'The AI can write Python code and install it as a live tool',
            'Custom tools run inside the Sapphire process with full access',
            'Validation catches common dangerous patterns but is not a sandbox',
            'A motivated prompt injection could bypass validation',
        ],
        buttonLabel: 'Enable Tool Maker',
        doubleConfirm: true,
        stage2Title: '\u26A0 Final Confirmation — Code Execution',
        stage2Warnings: [
            'Custom tools persist across restarts',
            'Review AI-created plugins in user/plugins/ periodically',
            'Consider keeping Tool Maker out of public-facing chats',
        ],
    },
};

// Plugins that own a nav-rail view
const PLUGIN_NAV_MAP = { continuity: 'schedule' };

// Prevent double-click race condition on toggles
const toggling = new Set();

// Active filter
let activeFilter = 'all';

function _esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

function _sortPlugins(plugins) {
    // Pure alphabetical — toggling a plugin does NOT reorder the grid.
    // 2026-04-30: previously we sorted enabled-first which caused the
    // tile layout to shift around any time the user flipped a toggle.
    return [...plugins].sort((a, b) =>
        (a.title || a.name).localeCompare(b.title || b.name)
    );
}

function _trustClass(p, locked) {
    if (locked) return 'pm-trust-core';
    const tier = p.verify_tier;
    if (tier === 'official' || (!tier && p.verified === true)) return 'pm-trust-official';
    if (tier === 'verified_author') return 'pm-trust-author';
    if (tier === 'failed' || (!tier && p.verified === false && p.verify_msg && p.verify_msg !== 'unsigned'))
        return 'pm-trust-tampered';
    return 'pm-trust-unsigned';
}

// ── In-place tile update — avoids the full grid re-render (and the
// staggered fade-in animation flash) on every toggle. The toggle handler
// at the bottom of attachListeners calls these instead of refreshTab().
// 2026-04-30.

function _bindGearClick(el, gearBtn, name) {
    gearBtn.addEventListener('click', () => {
        const settingsView = el.closest('.settings-view') || el.closest('[data-view="settings"]');
        if (settingsView) {
            settingsView.dispatchEvent(new CustomEvent('settings-navigate',
                { detail: { tab: name }, bubbles: true }));
        }
    });
}

function _bindReloadClick(el, ctx, reloadBtn, name) {
    reloadBtn.addEventListener('click', async e => {
        e.stopPropagation();
        reloadBtn.disabled = true;
        const orig = reloadBtn.textContent;
        reloadBtn.textContent = 'Reloading...';
        try {
            const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
            const res = await fetch(`/api/plugins/${name}/reload`,
                { method: 'POST', headers: { 'X-CSRF-Token': csrf } });
            if (!res.ok) {
                const body = await res.json().catch(() => ({}));
                throw new Error(body.detail || `HTTP ${res.status}`);
            }
            ui.showToast(`Reloaded ${name}`, 'success');
            await ctx.refreshTab();
        } catch (err) {
            ui.showToast(`Reload failed: ${err.message}`, 'error');
            reloadBtn.disabled = false;
            reloadBtn.textContent = orig;
        }
    });
}

function _updateTileInPlace(el, ctx, name, locked) {
    const cached = (ctx.pluginList || []).find(p => p.name === name);
    if (!cached) return;
    const card = el.querySelector(`.pm-card[data-plugin="${CSS.escape(name)}"]`);
    if (!card) return;

    // Visual state — opacity, border tint via .pm-enabled.
    card.classList.toggle('pm-enabled', !!cached.enabled);
    // Sync the toggle input in case it drifted.
    const toggleInput = card.querySelector('input[data-plugin-toggle]');
    if (toggleInput) toggleInput.checked = !!cached.enabled;

    // Gear button (visible only when enabled + has settingsUI).
    const controlsRight = card.querySelector('.pm-tile-controls-right');
    if (controlsRight) {
        const existingGear = controlsRight.querySelector('.pm-gear');
        const wantGear = cached.enabled && cached.settingsUI;
        if (wantGear && !existingGear) {
            const tmpl = document.createElement('div');
            tmpl.innerHTML = `<button class="pm-gear" data-settings-tab="${_esc(name)}" title="Plugin settings" type="button">⚙️</button>`;
            const newGear = tmpl.firstElementChild;
            controlsRight.insertBefore(newGear, controlsRight.firstChild);
            _bindGearClick(el, newGear, name);
        } else if (!wantGear && existingGear) {
            existingGear.remove();
        }
    }

    // Kebab Reload item (visible only when enabled and not locked).
    const kebabMenu = card.querySelector('.pm-kebab-menu');
    if (kebabMenu) {
        const existingReload = kebabMenu.querySelector('.pm-kebab-reload');
        const wantReload = cached.enabled && !locked;
        if (wantReload && !existingReload) {
            const tmpl = document.createElement('div');
            tmpl.innerHTML = `<button class="pm-kebab-item pm-kebab-reload" data-plugin="${_esc(name)}">Reload</button>`;
            const newReload = tmpl.firstElementChild;
            const uninstall = kebabMenu.querySelector('.plugin-uninstall-btn');
            if (uninstall) kebabMenu.insertBefore(newReload, uninstall);
            else kebabMenu.appendChild(newReload);
            _bindReloadClick(el, ctx, newReload, name);
        } else if (!wantReload && existingReload) {
            existingReload.remove();
        }
    }

    // Missing-deps warning strip — sync against cached.missing_deps.
    const existingWarn = card.querySelector('.pm-deps-warning');
    const deps = cached.missing_deps || [];
    if (deps.length && !existingWarn) {
        const tmpl = document.createElement('div');
        tmpl.innerHTML = `<div class="pm-deps-warning" data-plugin-deps="${_esc(name)}">
            <span class="pm-deps-icon">&#x26A0;</span>
            <span class="pm-deps-text">Missing: ${_esc(deps.join(', '))}</span>
            <button class="btn btn-sm pm-deps-fix-btn" data-deps-plugin="${_esc(name)}">Install</button>
        </div>`.trim();
        card.appendChild(tmpl.firstElementChild);
        // .pm-deps-fix-btn is delegated, no manual bind needed.
    } else if (!deps.length && existingWarn) {
        existingWarn.remove();
    }
}

function _updateFilterCountsInPlace(el, plugins) {
    const counts = _counts(plugins);
    ['all', 'enabled', 'disabled', 'official', 'user'].forEach(key => {
        const countEl = el.querySelector(`.pm-filter[data-filter="${key}"] .pm-filter-count`);
        if (countEl) countEl.textContent = counts[key];
    });
    const summary = el.querySelector('.pm-count-total');
    if (summary) summary.textContent = `${counts.all} total · ${counts.enabled} enabled`;
}

function _fadeOutAndRemove(card) {
    card.style.transition = 'opacity 0.25s ease, transform 0.25s ease';
    card.style.opacity = '0';
    card.style.transform = 'scale(0.94)';
    setTimeout(() => card.remove(), 260);
}

function _filterPlugins(plugins, filter) {
    if (filter === 'all') return plugins;
    if (filter === 'enabled') return plugins.filter(p => p.enabled);
    if (filter === 'disabled') return plugins.filter(p => !p.enabled);
    if (filter === 'official') return plugins.filter(p => p.verify_tier === 'official' || (!p.verify_tier && p.verified === true));
    if (filter === 'user') return plugins.filter(p => p.band === 'user');
    return plugins;
}

function _counts(plugins) {
    return {
        all: plugins.length,
        enabled: plugins.filter(p => p.enabled).length,
        disabled: plugins.filter(p => !p.enabled).length,
        official: plugins.filter(p => p.verify_tier === 'official' || (!p.verify_tier && p.verified === true)).length,
        user: plugins.filter(p => p.band === 'user').length,
    };
}

function _badgeHTML(p, locked) {
    if (locked) return '<span class="pm-badge pm-badge-core">Core</span>';
    const tier = p.verify_tier;
    if (tier === 'official' || (!tier && p.verified === true))
        return '<span class="pm-badge pm-badge-official">Official</span>';
    if (tier === 'verified_author')
        return `<span class="pm-badge pm-badge-author">${_esc(p.verified_author || 'Verified')}</span>`;
    if (tier === 'unsigned' || (!tier && p.verify_msg === 'unsigned'))
        return '<span class="pm-badge pm-badge-unsigned">Unsigned</span>';
    if (tier === 'failed' || (!tier && p.verified === false && p.verify_msg && p.verify_msg !== 'unsigned'))
        return '<span class="pm-badge pm-badge-tampered">Tampered</span>';
    return '';
}

function _renderCard(p, locked) {
    // 2026-04-30 redesign: square-ish tiles, kebab menu for less-frequent
    // actions, gear stays prominent for plugin settings, trust signaled
    // by tile border color + a chip near the bottom.
    const hasSettings = p.settingsUI && p.enabled;
    const isUser = p.band === 'user';
    const icon = p.icon || '\uD83D\uDD0C';
    const trustClass = _trustClass(p, locked);

    // Kebab items — less-frequent actions tucked away. Update + gear stay
    // on the tile face. Uninstall is destructive so it's red, in the menu.
    // Existing handlers keyed by .plugin-uninstall-btn / .plugin-update-btn
    // pick up these items unchanged — we just style them as menu rows here.
    const kebabItems = [];
    if (p.url) {
        kebabItems.push(
            `<a href="${_esc(p.url)}" target="_blank" rel="noopener" class="pm-kebab-item">Open website</a>`
        );
    }
    if (isUser) {
        kebabItems.push(
            `<button class="pm-kebab-item plugin-update-btn" data-plugin="${_esc(p.name)}">Check for updates</button>`
        );
    }
    if (p.enabled && !locked) {
        kebabItems.push(
            `<button class="pm-kebab-item pm-kebab-reload" data-plugin="${_esc(p.name)}">Reload</button>`
        );
    }
    if (isUser) {
        kebabItems.push(
            `<button class="pm-kebab-item pm-kebab-danger plugin-uninstall-btn" data-plugin="${_esc(p.name)}">Uninstall</button>`
        );
    }
    const kebab = kebabItems.length ? `
        <div class="pm-kebab" data-plugin="${_esc(p.name)}">
            <button class="pm-kebab-btn" type="button" aria-label="More actions">\u22EE</button>
            <div class="pm-kebab-menu" hidden>
                ${kebabItems.join('')}
            </div>
        </div>
    ` : '';

    const gearBtn = hasSettings
        ? `<button class="pm-gear" data-settings-tab="${p.name}" title="Plugin settings" type="button">\u2699\uFE0F</button>`
        : '';

    // Update button stays on the tile for user plugins as a primary
    // visible affordance. The existing handler transitions it to
    // "Update to vX.Y" when an update is found.
    const updateBtn = isUser
        ? `<button class="btn btn-sm plugin-update-btn pm-tile-update-btn" data-plugin="${_esc(p.name)}">Update</button>`
        : '';

    const titleText = _esc(p.title || p.name);
    const verLine = p.version ? `<div class="pm-tile-version">v${_esc(p.version)}</div>` : '';

    return `
        <div class="pm-card pm-tile ${trustClass}${p.enabled ? ' pm-enabled' : ''}" data-plugin="${_esc(p.name)}">
            ${kebab}
            <div class="pm-tile-content">
                <div class="pm-tile-icon">${icon}</div>
                <div class="pm-tile-title" title="${titleText}">${titleText}</div>
                ${verLine}
                <div class="pm-tile-badge-row">${_badgeHTML(p, locked)}</div>
            </div>
            <div class="pm-tile-controls">
                <div class="pm-tile-controls-left">${updateBtn}</div>
                <div class="pm-tile-controls-right">
                    ${gearBtn}
                    <label class="pm-toggle">
                        <input type="checkbox" data-plugin-toggle="${_esc(p.name)}"
                               ${p.enabled ? 'checked' : ''} ${locked ? 'disabled' : ''}>
                        <span class="pm-slider"></span>
                    </label>
                </div>
            </div>
            ${p.missing_deps?.length ? `
            <div class="pm-deps-warning" data-plugin-deps="${_esc(p.name)}">
                <span class="pm-deps-icon">&#x26A0;</span>
                <span class="pm-deps-text">Missing: ${_esc(p.missing_deps.join(', '))}</span>
                <button class="btn btn-sm pm-deps-fix-btn" data-deps-plugin="${_esc(p.name)}">Install</button>
            </div>` : ''}
        </div>
    `;
}

export default {
    id: 'plugins',
    name: 'Plugins',
    icon: '🔌',
    description: 'Enable or disable feature plugins',

    render(ctx) {
        const visible = (ctx.pluginList || []).filter(p => !HIDDEN.has(p.name));
        if (!visible.length) return '<p class="text-muted">No feature plugins available.</p>';

        const allowUnsigned = ctx.settings?.ALLOW_UNSIGNED_PLUGINS ?? false;
        const managedLocked = ctx.managed && !ctx.unrestricted;
        const counts = _counts(visible);
        const sorted = _sortPlugins(_filterPlugins(visible, activeFilter));

        return `
            <div class="pm-header">
                <div class="pm-header-top">
                    <div class="pm-summary">Plugins <span class="pm-count-total">${counts.all} total \u00b7 ${counts.enabled} enabled</span></div>
                    <div class="pm-header-actions">
                        <button class="btn btn-sm pm-action-btn" id="check-all-updates-btn">\uD83D\uDD0D Check Updates</button>
                        <button class="btn btn-sm pm-action-btn" id="rescan-plugins-btn">\uD83D\uDD04 Rescan</button>
                        <button class="btn btn-sm btn-primary pm-action-btn" id="pm-install-toggle">+ Install Plugin</button>
                    </div>
                </div>
                ${!managedLocked ? `<div class="pm-unsigned-banner ${allowUnsigned ? 'pm-unsigned-on' : 'pm-unsigned-off'}">
                    <span class="pm-unsigned-icon">${allowUnsigned ? '⚠' : '🔒'}</span>
                    <div class="pm-unsigned-text">
                        <div class="pm-unsigned-state">${allowUnsigned ? 'Unsigned plugins ALLOWED' : 'Unsigned plugins blocked'}</div>
                        <div class="pm-unsigned-sub">${allowUnsigned ? 'Plugins without a verified signature can be enabled. Be careful what you install.' : 'Only signed plugins will load. Recommended for safety.'}</div>
                    </div>
                    <label class="pm-unsigned-switch">
                        <input type="checkbox" id="allow-unsigned-toggle" ${allowUnsigned ? 'checked' : ''}>
                        <span class="pm-unsigned-slider"></span>
                    </label>
                </div>` : ''}
                <div class="pm-filters">
                    <button class="pm-filter${activeFilter === 'all' ? ' active' : ''}" data-filter="all">All <span class="pm-filter-count">${counts.all}</span></button>
                    <button class="pm-filter${activeFilter === 'enabled' ? ' active' : ''}" data-filter="enabled">Enabled <span class="pm-filter-count">${counts.enabled}</span></button>
                    <button class="pm-filter${activeFilter === 'disabled' ? ' active' : ''}" data-filter="disabled">Disabled <span class="pm-filter-count">${counts.disabled}</span></button>
                    <button class="pm-filter${activeFilter === 'official' ? ' active' : ''}" data-filter="official">Official <span class="pm-filter-count">${counts.official}</span></button>
                    <button class="pm-filter${activeFilter === 'user' ? ' active' : ''}" data-filter="user">User <span class="pm-filter-count">${counts.user}</span></button>
                </div>
            </div>
            <div class="pm-install-section" id="pm-install-section" style="display:none">
                <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">
                    <input type="text" id="plugin-install-url" placeholder="GitHub URL (e.g. https://github.com/user/plugin)"
                           style="flex:1;padding:8px 10px;background:var(--input-bg);border:1px solid var(--border-light);border-radius:var(--radius-sm);color:var(--text-light);font-size:0.9em;">
                    <button class="btn btn-sm" id="plugin-install-url-btn">Install</button>
                </div>
                <div style="display:flex;gap:8px;align-items:center;">
                    <input type="file" id="plugin-install-file" accept=".zip" style="flex:1;font-size:0.85em;color:var(--text-muted);">
                    <button class="btn btn-sm" id="plugin-install-file-btn">Upload</button>
                </div>
            </div>
            <div class="pm-grid">
                ${sorted.length ? sorted.map(p => _renderCard(p, ctx.lockedPlugins.includes(p.name))).join('')
                    : '<p class="text-muted" style="grid-column:1/-1;text-align:center;padding:24px 0;">No plugins match this filter.</p>'}
            </div>
        `;
    },

    attachListeners(ctx, el) {
        // Install section toggle
        el.querySelector('#pm-install-toggle')?.addEventListener('click', () => {
            const section = el.querySelector('#pm-install-section');
            if (section) section.style.display = section.style.display === 'none' ? 'block' : 'none';
        });

        // Sideloading toggle
        const unsignedToggle = el.querySelector('#allow-unsigned-toggle');
        if (unsignedToggle) {
            unsignedToggle.addEventListener('change', async e => {
                const enabling = e.target.checked;
                if (enabling) {
                    const confirmed = await showDangerConfirm({
                        title: 'Allow Unsigned Plugins — No Signature Verification',
                        warnings: [
                            'Unsigned plugins have not been verified by Sapphire',
                            'They can execute arbitrary code with full system access',
                            'A malicious plugin could steal credentials, modify files, or exfiltrate data',
                            'Only enable this if you trust the source of your plugins',
                        ],
                        buttonLabel: 'Allow Unsigned',
                        doubleConfirm: true,
                        stage2Title: 'Final Confirmation — Unsigned Plugins',
                        stage2Warnings: [
                            'You are disabling signature verification for all non-system plugins',
                            'This cannot be undone automatically — you must manually disable plugins if compromised',
                            'Sapphire cannot guarantee the safety of unsigned code',
                        ],
                    });
                    if (!confirmed) { e.target.checked = false; return; }
                }
                try {
                    const res = await fetch('/api/settings/batch', {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ settings: { ALLOW_UNSIGNED_PLUGINS: enabling } })
                    });
                    if (!res.ok) throw new Error('Failed to save');
                    ctx.settings.ALLOW_UNSIGNED_PLUGINS = enabling;
                    ui.showToast(`Unsigned plugins ${enabling ? 'allowed' : 'blocked'}`, enabling ? 'warning' : 'success');
                    if (!enabling) await ctx.refreshTab();
                } catch (err) {
                    e.target.checked = !enabling;
                    ui.showToast(`Setting failed: ${err.message}`, 'error');
                }
            });
        }

        // Rescan button
        const rescanBtn = el.querySelector('#rescan-plugins-btn');
        if (rescanBtn) {
            rescanBtn.addEventListener('click', async () => {
                rescanBtn.disabled = true;
                rescanBtn.textContent = 'Scanning...';
                try {
                    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                    const res = await fetch('/api/plugins/rescan', { method: 'POST', headers: { 'X-CSRF-Token': csrf } });
                    if (!res.ok) throw new Error('Rescan failed');
                    const data = await res.json();
                    const added = data.added?.length || 0;
                    const removed = data.removed?.length || 0;
                    if (added || removed) {
                        ui.showToast(`Rescan: ${added} added, ${removed} removed`, 'success');
                        await ctx.refreshTab();
                    } else {
                        ui.showToast('No new plugins found', 'info');
                    }
                } catch (err) {
                    ui.showToast(`Rescan failed: ${err.message}`, 'error');
                } finally {
                    rescanBtn.disabled = false;
                    rescanBtn.textContent = 'Rescan';
                }
            });
        }

        // Check All Updates button
        const checkAllBtn = el.querySelector('#check-all-updates-btn');
        if (checkAllBtn) {
            checkAllBtn.addEventListener('click', async () => {
                // Find all user-installed plugins (they have Update buttons)
                const updateBtns = el.querySelectorAll('.plugin-update-btn');
                if (!updateBtns.length) {
                    ui.showToast('No user-installed plugins to check', 'info');
                    return;
                }
                checkAllBtn.disabled = true;
                checkAllBtn.textContent = 'Checking...';
                let updatesFound = 0;
                const results = await Promise.allSettled(
                    Array.from(updateBtns).map(async (btn) => {
                        const name = btn.dataset.plugin;
                        try {
                            const result = await pluginsAPI.checkUpdate(name);
                            if (result.update_available) {
                                updatesFound++;
                                btn.textContent = `Update to v${result.remote_version}`;
                                btn.classList.add('btn-primary');
                                btn.classList.remove('plugin-update-btn');
                                btn.addEventListener('click', async () => {
                                    btn.disabled = true;
                                    btn.textContent = 'Updating...';
                                    try {
                                        await pluginsAPI.installPlugin({ url: result.source_url, force: true });
                                        ui.showToast(`Updated ${name} → v${result.remote_version}`, 'success');
                                        await ctx.refreshTab();
                                    } catch (err) {
                                        ui.showToast(`Update failed: ${err.message}`, 'error', 5000);
                                        btn.disabled = false;
                                        btn.textContent = `Update to v${result.remote_version}`;
                                    }
                                }, { once: true });
                            }
                        } catch { /* skip failed checks */ }
                    })
                );
                if (updatesFound > 0) {
                    ui.showToast(`${updatesFound} update${updatesFound > 1 ? 's' : ''} available`, 'success');
                } else {
                    ui.showToast('All plugins up to date', 'success');
                }
                checkAllBtn.disabled = false;
                checkAllBtn.textContent = 'Check Updates';
            });
        }

        // Filter pills
        el.querySelectorAll('.pm-filter').forEach(btn => {
            btn.addEventListener('click', () => {
                activeFilter = btn.dataset.filter;
                ctx.refreshTab();
            });
        });

        // Gear → navigate to plugin settings tab via custom event
        el.querySelectorAll('.pm-gear[data-settings-tab]').forEach(btn => {
            btn.addEventListener('click', () => {
                const tabName = btn.dataset.settingsTab;
                const settingsView = el.closest('.settings-view') || el.closest('[data-view="settings"]');
                if (settingsView) {
                    settingsView.dispatchEvent(new CustomEvent('settings-navigate', { detail: { tab: tabName }, bubbles: true }));
                }
            });
        });

        // Kebab open/close. Click the dots → toggle the menu. Click anywhere
        // else → close all open menus. ESC also closes. The kebab items
        // themselves carry .plugin-update-btn / .plugin-uninstall-btn /
        // .pm-kebab-reload classes — those handlers fire normally; we just
        // close the menu after the click bubbles. 2026-04-30.
        el.querySelectorAll('.pm-kebab-btn').forEach(btn => {
            btn.addEventListener('click', e => {
                e.stopPropagation();
                const menu = btn.parentElement.querySelector('.pm-kebab-menu');
                if (!menu) return;
                // Close all other open kebabs first
                el.querySelectorAll('.pm-kebab-menu').forEach(m => {
                    if (m !== menu) m.hidden = true;
                });
                menu.hidden = !menu.hidden;
            });
        });
        // Close any open kebab on outside click / ESC. Bind once per render.
        if (!el._kebabOutsideBound) {
            el._kebabOutsideBound = true;
            const closeAll = () => el.querySelectorAll('.pm-kebab-menu').forEach(m => m.hidden = true);
            document.addEventListener('click', e => {
                if (!e.target.closest('.pm-kebab')) closeAll();
            });
            document.addEventListener('keydown', e => {
                if (e.key === 'Escape') closeAll();
            });
        }

        // Reload plugin (kebab item) — fresh handler since this is a new
        // affordance. POST /api/plugins/{name}/reload, then refreshTab.
        el.querySelectorAll('.pm-kebab-reload').forEach(btn => {
            btn.addEventListener('click', async e => {
                e.stopPropagation();
                const name = btn.dataset.plugin;
                btn.disabled = true;
                btn.textContent = 'Reloading...';
                try {
                    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                    const res = await fetch(`/api/plugins/${name}/reload`, {
                        method: 'POST', headers: { 'X-CSRF-Token': csrf },
                    });
                    if (!res.ok) {
                        const body = await res.json().catch(() => ({}));
                        throw new Error(body.detail || `HTTP ${res.status}`);
                    }
                    ui.showToast(`Reloaded ${name}`, 'success');
                    await ctx.refreshTab();
                } catch (err) {
                    ui.showToast(`Reload failed: ${err.message}`, 'error');
                    btn.disabled = false;
                    btn.textContent = 'Reload';
                }
            });
        });

        // Install from URL
        const installUrlBtn = el.querySelector('#plugin-install-url-btn');
        if (installUrlBtn) {
            installUrlBtn.addEventListener('click', async () => {
                const urlInput = el.querySelector('#plugin-install-url');
                const url = urlInput?.value?.trim();
                if (!url) { ui.showToast('Enter a GitHub URL', 'warning'); return; }
                installUrlBtn.disabled = true;
                installUrlBtn.textContent = 'Installing...';
                try {
                    const result = await pluginsAPI.installPlugin({ url });
                    if (result.conflict) {
                        const confirmed = await showDangerConfirm({
                            title: `Replace Plugin: ${result.name}`,
                            warnings: [
                                `Installed: v${result.existing_version || '?'} by ${result.existing_author || 'unknown'}`,
                                `New: v${result.version || '?'} by ${result.author || 'unknown'}`,
                                'The existing plugin and its settings will be replaced',
                                'Plugin state data will be preserved',
                            ],
                            buttonLabel: 'Replace',
                        });
                        if (!confirmed) return;
                        const forced = await pluginsAPI.installPlugin({ url, force: true });
                        ui.showToast(`Updated ${forced.plugin_name} \u2192 v${forced.version}`, 'success');
                    } else {
                        ui.showToast(`Installed ${result.plugin_name} v${result.version}`, 'success');
                    }
                    urlInput.value = '';
                    await ctx.refreshTab();
                } catch (err) {
                    ui.showToast(`Install failed: ${err.message}`, 'error', 5000);
                } finally {
                    installUrlBtn.disabled = false;
                    installUrlBtn.textContent = 'Install';
                }
            });
        }

        // Install from zip
        const installFileBtn = el.querySelector('#plugin-install-file-btn');
        if (installFileBtn) {
            installFileBtn.addEventListener('click', async () => {
                const fileInput = el.querySelector('#plugin-install-file');
                const file = fileInput?.files?.[0];
                if (!file) { ui.showToast('Select a zip file', 'warning'); return; }
                installFileBtn.disabled = true;
                installFileBtn.textContent = 'Uploading...';
                try {
                    const result = await pluginsAPI.installPlugin({ file });
                    if (result.conflict) {
                        const confirmed = await showDangerConfirm({
                            title: `Replace Plugin: ${result.name}`,
                            warnings: [
                                `Installed: v${result.existing_version || '?'} by ${result.existing_author || 'unknown'}`,
                                `New: v${result.version || '?'} by ${result.author || 'unknown'}`,
                                'The existing plugin and its settings will be replaced',
                                'Plugin state data will be preserved',
                            ],
                            buttonLabel: 'Replace',
                        });
                        if (!confirmed) return;
                        const forced = await pluginsAPI.installPlugin({ file, force: true });
                        ui.showToast(`Updated ${forced.plugin_name} \u2192 v${forced.version}`, 'success');
                    } else {
                        ui.showToast(`Installed ${result.plugin_name} v${result.version}`, 'success');
                    }
                    fileInput.value = '';
                    await ctx.refreshTab();
                } catch (err) {
                    ui.showToast(`Install failed: ${err.message}`, 'error', 5000);
                } finally {
                    installFileBtn.disabled = false;
                    installFileBtn.textContent = 'Upload';
                }
            });
        }

        // Store ctx for delegated handlers
        el._pluginCtx = ctx;
        if (el._pluginsBound) return;
        el._pluginsBound = true;

        // ── Uninstall (delegated) ──
        el.addEventListener('click', async e => {
            const btn = e.target.closest('.plugin-uninstall-btn');
            if (!btn) return;
            const name = btn.dataset.plugin;
            const ctx = el._pluginCtx;
            const plugin = ctx.pluginList?.find(p => p.name === name);

            const confirmed = await showDangerConfirm({
                title: `Uninstall Plugin: ${plugin?.title || name}`,
                warnings: [
                    'The plugin and all its settings will be permanently deleted',
                    'Plugin state data will also be removed',
                    'This cannot be undone',
                ],
                buttonLabel: 'Uninstall',
            });
            if (!confirmed) return;

            btn.disabled = true;
            btn.textContent = 'Removing...';
            try {
                await pluginsAPI.uninstallPlugin(name);
                try {
                    const { unregisterPluginSettings } = await import('../../shared/plugin-registry.js');
                    unregisterPluginSettings(name);
                    ctx.syncDynamicTabs();
                } catch (_) {}
                ui.showToast(`Uninstalled ${plugin?.title || name}`, 'success');
                window.dispatchEvent(new CustomEvent('functions-changed'));
                await ctx.refreshTab();
            } catch (err) {
                ui.showToast(`Uninstall failed: ${err.message}`, 'error', 5000);
                btn.disabled = false;
                btn.textContent = 'Uninstall';
            }
        });

        // ── Check update (delegated) ──
        el.addEventListener('click', async e => {
            const btn = e.target.closest('.plugin-update-btn');
            if (!btn) return;
            const name = btn.dataset.plugin;
            const ctx = el._pluginCtx;

            btn.disabled = true;
            btn.textContent = 'Checking...';
            try {
                const result = await pluginsAPI.checkUpdate(name);
                if (result.update_available) {
                    btn.textContent = `Update to v${result.remote_version}`;
                    btn.disabled = false;
                    btn.classList.add('btn-primary');
                    btn.classList.remove('plugin-update-btn');
                    btn.addEventListener('click', async () => {
                        btn.disabled = true;
                        btn.textContent = 'Updating...';
                        try {
                            await pluginsAPI.installPlugin({ url: result.source_url, force: true });
                            ui.showToast(`Updated ${name} \u2192 v${result.remote_version}`, 'success');
                            await ctx.refreshTab();
                        } catch (err) {
                            ui.showToast(`Update failed: ${err.message}`, 'error', 5000);
                            btn.disabled = false;
                            btn.textContent = `Update to v${result.remote_version}`;
                        }
                    }, { once: true });
                } else {
                    btn.textContent = 'Up to date';
                    setTimeout(() => { btn.textContent = 'Check Update'; btn.disabled = false; }, 2000);
                }
            } catch (err) {
                ui.showToast(`Update check failed: ${err.message}`, 'error');
                btn.textContent = 'Check Update';
                btn.disabled = false;
            }
        });

        // ── Install deps (delegated) ──
        el.addEventListener('click', async e => {
            const btn = e.target.closest('.pm-deps-fix-btn');
            if (!btn) return;
            const name = btn.dataset.depsPlugin;
            const ctx = el._pluginCtx;

            btn.disabled = true;
            btn.textContent = 'Checking...';
            try {
                // First check what we're dealing with
                const checkRes = await fetch(`/api/plugins/${name}/check-deps`);
                if (!checkRes.ok) throw new Error('Failed to check deps');
                const depInfo = await checkRes.json();

                if (!depInfo.missing?.length) {
                    ui.showToast('Dependencies already installed — reloading plugin', 'success');
                    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                    await fetch(`/api/plugins/${name}/reload`, { method: 'POST', headers: { 'X-CSRF-Token': csrf } });
                    await ctx.refreshTab();
                    return;
                }

                const cmd = depInfo.command;
                const envLabel = depInfo.env_type === 'conda' ? `conda env "${depInfo.env_name}"`
                    : depInfo.env_type === 'venv' ? `venv "${depInfo.env_name}"` : 'system Python';

                if (!depInfo.can_auto_install) {
                    // System Python — manual only
                    ui.showToast(`Cannot auto-install on ${envLabel}. Run manually:\n${cmd}`, 'warning', 0);
                    btn.textContent = 'Manual';
                    btn.disabled = false;
                    return;
                }

                // Show confirmation with exact command
                const confirmed = await showDangerConfirm({
                    title: `Install Dependencies for ${name}`,
                    warnings: [
                        `This will run: ${cmd}`,
                        `Environment: ${envLabel}`,
                        'You can also run this command yourself in your terminal',
                        'Packages are installed from PyPI (the public Python package index)',
                    ],
                    buttonLabel: 'Install Now',
                });

                if (!confirmed) {
                    // User declined — offer copy
                    try { await navigator.clipboard.writeText(cmd); } catch {}
                    ui.showToast(`Command copied: ${cmd}`, 'info', 5000);
                    btn.textContent = 'Install';
                    btn.disabled = false;
                    return;
                }

                btn.textContent = 'Installing...';
                const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                const installRes = await fetch(`/api/plugins/${name}/install-deps`, {
                    method: 'POST', headers: { 'X-CSRF-Token': csrf },
                });
                const result = await installRes.json();

                if (result.status === 'ok') {
                    ui.showToast(`Dependencies installed for ${name} — plugin reloaded`, 'success');
                    // Update cached plugin data
                    const cached = ctx.pluginList?.find(p => p.name === name);
                    if (cached) cached.missing_deps = [];
                    await ctx.refreshTab();
                } else if (result.status === 'partial') {
                    ui.showToast(`Some deps still missing: ${result.still_missing.join(', ')}`, 'warning', 0);
                    btn.textContent = 'Retry';
                    btn.disabled = false;
                } else {
                    ui.showToast(`Install failed: ${result.message || 'unknown error'}`, 'error', 0);
                    btn.textContent = 'Failed';
                    btn.disabled = false;
                }
            } catch (err) {
                ui.showToast(`Dep install failed: ${err.message}`, 'error', 5000);
                btn.textContent = 'Install';
                btn.disabled = false;
            }
        });

        // ── Toggle (delegated) ──
        el.addEventListener('change', async e => {
            const name = e.target.dataset.pluginToggle;
            if (!name) return;

            const ctx = el._pluginCtx;

            if (toggling.has(name)) {
                e.preventDefault();
                e.target.checked = !e.target.checked;
                return;
            }

            // Per-plugin unsigned gate
            if (e.target.checked) {
                const plugin = ctx.pluginList.find(p => p.name === name);
                if (plugin?.verify_msg === 'unsigned') {
                    toggling.add(name);
                    const unsignedOk = await showDangerConfirm({
                        title: `Enable Unsigned Plugin: ${plugin.title || plugin.name}`,
                        warnings: [
                            'This plugin has no verified signature',
                            'It will execute code with access to your system',
                            'Review the plugin source before enabling',
                        ],
                        buttonLabel: 'Enable Plugin',
                    });
                    toggling.delete(name);
                    if (!unsignedOk) { e.target.checked = false; return; }
                }
            }

            // Danger gate for risky plugins
            const dangerConfig = DANGER_PLUGINS[name];
            if (dangerConfig && e.target.checked) {
                const ackKey = `sapphire_danger_ack_${name}`;
                if (!localStorage.getItem(ackKey)) {
                    toggling.add(name);
                    const confirmed = await showDangerConfirm(dangerConfig);
                    toggling.delete(name);
                    if (!confirmed) { e.target.checked = false; return; }
                    localStorage.setItem(ackKey, Date.now().toString());
                }
            }

            toggling.add(name);
            e.target.disabled = true;

            const card = e.target.closest('.pm-card');

            try {
                const res = await fetch(`/api/webui/plugins/toggle/${name}`, { method: 'PUT' });
                if (!res.ok) {
                    const body = await res.json().catch(() => ({}));
                    throw new Error(body.detail || body.error || res.status);
                }
                const data = await res.json();

                const cached = ctx.pluginList.find(p => p.name === name);
                if (cached) cached.enabled = data.enabled;

                if (data.enabled && cached?.settingsUI) {
                    await ctx.loadPluginTab(name, cached.settingsUI);
                } else if (!data.enabled) {
                    const { unregisterPluginSettings } = await import('../../shared/plugin-registry.js');
                    unregisterPluginSettings(name);
                    ctx.syncDynamicTabs();
                }

                const navView = PLUGIN_NAV_MAP[name];
                if (navView) {
                    const navBtn = document.querySelector(`.nav-item[data-view="${navView}"]`);
                    if (navBtn) navBtn.style.display = data.enabled ? '' : 'none';
                }

                // Sync sidebar (settings tab list) — cheap, no grid rerender.
                if (cached?.settingsUI) ctx.refreshSidebar();

                // Update missing_deps on cached so the in-place tile sync
                // shows/removes the warning strip correctly.
                if (cached) {
                    cached.missing_deps = (data.enabled && data.missing_deps?.length)
                        ? data.missing_deps
                        : [];
                }

                // In-place tile + counts update — avoids the full grid
                // re-render (and the staggered fade-in animation flash)
                // that ctx.refreshTab() previously triggered. 2026-04-30.
                const visible = (ctx.pluginList || []).filter(p => !HIDDEN.has(p.name));
                _updateTileInPlace(el, ctx, name, ctx.lockedPlugins.includes(name));
                _updateFilterCountsInPlace(el, visible);

                // If the active filter would now hide this plugin (e.g.
                // user is on "Enabled" and just disabled it), fade the
                // tile out and remove it. Otherwise leave it in place.
                const stillMatchesFilter = _filterPlugins([cached], activeFilter).length > 0;
                if (!stillMatchesFilter) {
                    const card = el.querySelector(`.pm-card[data-plugin="${CSS.escape(name)}"]`);
                    if (card) _fadeOutAndRemove(card);
                }

                window.dispatchEvent(new CustomEvent('functions-changed'));
                document.dispatchEvent(new CustomEvent('sapphire:plugin_toggled', { detail: data }));
                // Show sticky toast if plugin enabled but has missing deps
                if (data.enabled && data.missing_deps?.length) {
                    ui.showToast(
                        `${cached?.title || name} needs: ${data.missing_deps.join(', ')} — go to Plugins to install`,
                        'warning', 0
                    );
                }

                ui.showToast(`${cached?.title || name} ${data.enabled ? 'enabled' : 'disabled'}`, 'success');
            } catch (err) {
                e.target.checked = !e.target.checked;
                const msg = (err.message || 'Unknown error').replace(/^Plugin blocked:\s*/, '');
                ui.showToast(msg, 'error', 5000);
            } finally {
                toggling.delete(name);
                e.target.disabled = false;
            }
        });
    }
};
