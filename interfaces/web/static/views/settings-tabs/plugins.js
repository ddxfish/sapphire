// settings-tabs/plugins.js - Plugin toggles tab
import * as ui from '../../ui.js';

// Infrastructure plugins hidden from toggle list
const HIDDEN = new Set([
    'setup-wizard', 'plugins-modal'
]);

// Plugins that own a nav-rail view
const PLUGIN_NAV_MAP = { continuity: 'schedule' };

// Prevent double-click race condition on toggles
const toggling = new Set();

export default {
    id: 'plugins',
    name: 'Plugins',
    icon: '🔌',
    description: 'Enable or disable feature plugins',

    render(ctx) {
        const visible = (ctx.pluginList || []).filter(p => !HIDDEN.has(p.name));
        if (!visible.length) return '<p class="text-muted">No feature plugins available.</p>';

        return `
            <div class="plugin-toggles-list">
                ${visible.map(p => {
                    const locked = ctx.lockedPlugins.includes(p.name);
                    return `
                        <div class="plugin-toggle-item${p.enabled ? ' enabled' : ''}" data-plugin="${p.name}">
                            <div class="plugin-toggle-info">
                                <span class="plugin-toggle-name">${p.title || p.name}</span>
                                ${locked ? '<span class="plugin-toggle-badge">Core</span>' : ''}
                            </div>
                            <label class="setting-toggle">
                                <input type="checkbox" data-plugin-toggle="${p.name}"
                                       ${p.enabled ? 'checked' : ''} ${locked ? 'disabled' : ''}>
                                <span>${p.enabled ? 'Enabled' : 'Disabled'}</span>
                            </label>
                        </div>
                    `;
                }).join('')}
            </div>
        `;
    },

    attachListeners(ctx, el) {
        el.addEventListener('change', async e => {
            const name = e.target.dataset.pluginToggle;
            if (!name) return;

            // Guard against rapid double-clicks
            if (toggling.has(name)) {
                e.preventDefault();
                e.target.checked = !e.target.checked;  // revert browser toggle
                return;
            }
            toggling.add(name);
            e.target.disabled = true;

            const item = e.target.closest('.plugin-toggle-item');
            const span = e.target.parentElement?.querySelector('span');

            try {
                const res = await fetch(`/api/webui/plugins/toggle/${name}`, { method: 'PUT' });
                if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.status);
                const data = await res.json();

                // Update cached plugin list
                const cached = ctx.pluginList.find(p => p.name === name);
                if (cached) cached.enabled = data.enabled;

                // Load or unload dynamic settings tab
                if (data.enabled) {
                    await ctx.loadPluginTab(name);
                } else {
                    const { unregisterPluginSettings } = await import(
                        '../../plugins/plugins-modal/plugin-registry.js'
                    );
                    unregisterPluginSettings(name);
                    ctx.syncDynamicTabs();
                }

                // Hide/show associated nav-rail item
                const navView = PLUGIN_NAV_MAP[name];
                if (navView) {
                    const navBtn = document.querySelector(`.nav-item[data-view="${navView}"]`);
                    if (navBtn) navBtn.style.display = data.enabled ? '' : 'none';
                }

                ctx.refreshSidebar();
                ui.showToast(`${cached?.title || name} ${data.enabled ? 'enabled' : 'disabled'}`, 'success');
            } catch (err) {
                // Revert checkbox
                e.target.checked = !e.target.checked;
                if (span) span.textContent = e.target.checked ? 'Enabled' : 'Disabled';
                ui.showToast(`Toggle failed: ${err.message}`, 'error');
            } finally {
                toggling.delete(name);
                // checkbox may be on detached DOM after refreshSidebar, that's fine
                e.target.disabled = false;
            }
        });
    }
};
