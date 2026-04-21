/**
 * Reusable provider selector for service tabs (STT, TTS, Embeddings).
 *
 * Each service tab declares:
 *   providerKey   – setting key (e.g. 'STT_PROVIDER')
 *   providers     – { value: { label, essentialKeys, advancedKeys } }
 *   commonKeys    – fields shown for every non-disabled provider
 *   commonAdvancedKeys – advanced fields for every non-disabled provider
 *   disabledMessage – text when provider is 'none'
 */

/**
 * Fetch plugin-registered providers from the registry API and merge into tabConfig.
 * Call this before renderProviderTab() to include dynamically registered providers.
 */
export async function mergeRegistryProviders(tabConfig) {
    const apiMap = {
        'TTS_PROVIDER': '/api/tts/providers',
        'STT_PROVIDER': '/api/stt/providers',
        'EMBEDDING_PROVIDER': '/api/embedding/providers',
    };
    const url = apiMap[tabConfig.providerKey];
    if (!url) return tabConfig;
    try {
        const res = await fetch(url);
        if (!res.ok) return tabConfig;
        const data = await res.json();
        const merged = { ...tabConfig, providers: { ...tabConfig.providers } };
        for (const p of (data.providers || [])) {
            if (!merged.providers[p.key]) {
                merged.providers[p.key] = {
                    label: p.display_name || p.key,
                    essentialKeys: [],
                    advancedKeys: [],
                    _plugin: true,
                };
            }
        }
        return merged;
    } catch (e) {
        return tabConfig;
    }
}

export function renderProviderTab(tabConfig, ctx) {
    const effective = _filterProviders(tabConfig, ctx);
    const current = _currentProvider(effective, ctx);
    const providerDef = effective.providers[current] || effective.providers.none;

    let html = _renderDropdown(effective, current, ctx);

    if (current === 'none') {
        html += `<p class="setting-help" style="padding:12px 0;opacity:0.7">
            ${effective.disabledMessage || 'Disabled. Select a provider above to enable.'}
        </p>`;
        return html;
    }

    // Plugin providers: render inline settings from plugin manifest + test button
    if (providerDef._plugin && !providerDef.essentialKeys?.length) {
        html += `<div id="plugin-provider-settings" data-plugin="${current}" style="padding:8px 0"></div>`;
    }

    // Provider-specific essential fields
    if (providerDef.essentialKeys?.length) {
        html += ctx.renderFields(providerDef.essentialKeys);
    }

    // Common fields (shared across all active providers)
    if (effective.commonKeys?.length) {
        html += ctx.renderFields(effective.commonKeys);
    }

    // Advanced: provider-specific + common
    const advKeys = [
        ...(providerDef.advancedKeys || []),
        ...(effective.commonAdvancedKeys || [])
    ];
    if (advKeys.length) {
        html += ctx.renderAccordion(`${effective.providerKey}-adv`, advKeys);
    }

    return html;
}

export function attachProviderListeners(tabConfig, ctx, el, tabModule) {
    const dropdown = el.querySelector(`#setting-${tabConfig.providerKey}`);
    if (!dropdown) return;

    // Load inline plugin settings if a plugin provider is selected
    _loadPluginProviderSettings(el);

    dropdown.addEventListener('change', () => {
        ctx.markChanged(tabConfig.providerKey, dropdown.value);
        // Re-render to show/hide provider-specific fields
        // pendingChanges persists across re-renders
        const content = el.closest('.settings-main')?.querySelector('#settings-content');
        if (content) {
            const body = content.querySelector('.settings-tab-body');
            if (body) {
                // Use tab's full render if available (preserves test buttons etc.)
                body.innerHTML = tabModule?.render ? tabModule.render(ctx) : renderProviderTab(tabConfig, ctx);
                // Re-attach all listeners (provider dropdown + tab-specific like test buttons)
                if (tabModule?.attachListeners) {
                    tabModule.attachListeners(ctx, content);
                } else {
                    attachProviderListeners(tabConfig, ctx, content);
                }
                // Re-attach generic listeners (accordion, input tracking, etc.)
                if (ctx.attachAccordionListeners) ctx.attachAccordionListeners(content);
            }
        }
    });
}

// ── Internal ──

// Providers hidden in managed mode (local hardware — not available in Docker)
const MANAGED_HIDE = new Set(['faster_whisper', 'kokoro', 'local']);
// Providers hidden when NOT managed (internal routing — not useful for self-hosted)
const UNMANAGED_HIDE = new Set(['sapphire_router']);

function _filterProviders(tabConfig, ctx) {
    const hide = ctx.managed ? MANAGED_HIDE : UNMANAGED_HIDE;
    const filtered = {};
    for (const [key, val] of Object.entries(tabConfig.providers)) {
        // Don't hide plugin-registered providers — user explicitly enabled them
        if (val._plugin || !hide.has(key)) filtered[key] = val;
    }
    return { ...tabConfig, providers: filtered };
}

function _currentProvider(tabConfig, ctx) {
    // Pending change takes priority over saved setting
    if (tabConfig.providerKey in ctx.pendingChanges) {
        return ctx.pendingChanges[tabConfig.providerKey];
    }
    return ctx.settings[tabConfig.providerKey] || 'none';
}

function _renderDropdown(tabConfig, current, ctx) {
    const key = tabConfig.providerKey;
    const h = ctx.help[key];
    const isOverridden = ctx.overrides.includes(key);

    const options = Object.entries(tabConfig.providers)
        .map(([value, def]) =>
            `<option value="${value}" ${value === current ? 'selected' : ''}>${def.label}</option>`
        ).join('');

    return `
        <div class="settings-grid">
            <div class="setting-row full-width${isOverridden ? ' overridden' : ''}" data-key="${key}">
                <div class="setting-label">
                    <div class="setting-label-row">
                        <label>${ctx.formatLabel(key)}</label>
                        ${h ? `<span class="help-icon" data-help-key="${key}" title="Details">?</span>` : ''}
                        ${isOverridden ? '<span class="override-badge">Custom</span>' : ''}
                    </div>
                    ${h?.short ? `<div class="setting-help">${h.short}</div>` : ''}
                </div>
                <div class="setting-input">
                    <select id="setting-${key}" data-key="${key}">${options}</select>
                </div>
                <div class="setting-actions">
                    ${isOverridden ? `<button class="btn-icon reset-btn" data-reset-key="${key}" title="Reset to default">\u21BA</button>` : ''}
                </div>
            </div>
        </div>
    `;
}

async function _loadPluginProviderSettings(el) {
    const box = el.querySelector('#plugin-provider-settings');
    if (!box) return;
    const pluginName = box.dataset.plugin;
    if (!pluginName) return;

    try {
        // Fetch plugin manifest + settings
        const [pluginsRes, settingsRes] = await Promise.all([
            fetch('/api/webui/plugins'),
            fetch(`/api/webui/plugins/${pluginName}/settings`),
        ]);
        if (!pluginsRes.ok || !settingsRes.ok) {
            box.innerHTML = `<p class="setting-help">Configure in Settings \u2192 Plugins \u2192 ${pluginName}</p>`;
            return;
        }
        const pluginsData = await pluginsRes.json();
        const settingsData = await settingsRes.json();
        const plugin = (pluginsData.plugins || []).find(p => p.name === pluginName);
        const schema = plugin?.settings_schema || [];
        const values = settingsData.settings || {};

        if (!schema.length) {
            box.innerHTML = `<p class="setting-help">No settings for this provider.</p>`;
            return;
        }

        // Render using the shared plugin settings renderer
        const { renderSettingsForm, readSettingsForm } = await import('./plugin-settings-renderer.js');
        renderSettingsForm(box, schema, values);

        // Auto-save on change
        box.addEventListener('change', async () => {
            const updated = readSettingsForm(box, schema);
            try {
                const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
                await fetch(`/api/webui/plugins/${pluginName}/settings`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
                    body: JSON.stringify(updated),
                });
            } catch (e) {
                console.warn('Failed to save plugin provider settings:', e);
            }
        });
    } catch (e) {
        box.innerHTML = `<p class="setting-help">Configure in Settings \u2192 Plugins \u2192 ${pluginName}</p>`;
    }
}
