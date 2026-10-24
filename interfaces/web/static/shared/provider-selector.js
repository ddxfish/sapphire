/**
 * Reusable provider selector for service tabs (STT, TTS, Embeddings).
 *
 * Each service tab declares:
 *   providerKey   – setting key (e.g. 'STT_PROVIDER')
 *   providers     – { value: { label, essentialKeys, advancedKeys, blurb? } }
 *   commonKeys    – fields shown for every non-disabled provider
 *   commonAdvancedKeys – advanced fields for every non-disabled provider
 *   disabledMessage – text when provider is 'none'
 *
 * The pick is a row of CARDS (2026-10-22, tmp/setup-wizard-plan.md wave 2a): one
 * radio card per provider with its name, a local/cloud badge and a one-line
 * blurb. Blurbs come from the registry (register_core's description=, or a
 * plugin manifest's providers.<system>.description) through mergeRegistryProviders.
 * A DORMANT card is a provider a disabled plugin declares: clicking it installs
 * the plugin's packages if they are missing (shared/extras.js asks first), turns
 * the plugin on, then picks it. The Settings page and the setup wizard both
 * render this through the host (shared/settings-host.js); the wizard paints the
 * compact shape (renderProviderCompact: the cards, one Advanced fold).
 */

const _esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/**
 * Fetch the registry's providers and merge them into tabConfig: plugin providers
 * the tab does not know, every provider's blurb, and the dormant ones.
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
        const merged = { ...tabConfig, providers: {} };
        for (const [k, v] of Object.entries(tabConfig.providers)) merged.providers[k] = { ...v };
        for (const p of (data.providers || [])) {
            if (!merged.providers[p.key]) {
                merged.providers[p.key] = {
                    label: p.display_name || p.key,
                    is_local: p.is_local,
                    essentialKeys: [],
                    advancedKeys: [],
                    _plugin: true,
                };
            }
            const def = merged.providers[p.key];
            if (!def.blurb && p.description) def.blurb = p.description;
            if (p.plugin_enabled === false) {
                def.dormant = true;
                def.plugin = p.plugin_name;
                def.missing_deps = p.missing_deps || [];
            } else {
                def.dormant = false;
            }
        }
        return merged;
    } catch (e) {
        return tabConfig;
    }
}

/**
 * What the cards paint from a config: the provider set AND each one's dormancy.
 * The provider tabs paint from their cached merge, re-merge, and repaint only
 * when this changes (D2#7). Comparing keys alone missed a plugin waking up: the
 * stale paint kept a dashed card whose click toggled plugin `undefined`
 * (ElevenLabs, 2026-10-22).
 */
export function providerSignature(tabConfig) {
    return Object.entries(tabConfig.providers)
        .map(([k, d]) => d.dormant ? `${k}~` : k).join();
}

export function renderProviderTab(tabConfig, ctx) {
    const effective = _filterProviders(tabConfig, ctx);
    const current = _currentProvider(effective, ctx);
    const providerDef = effective.providers[current] || effective.providers.none;

    let html = _renderCards(effective, current, ctx);

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
        html += ctx.renderAccordion(`${tabConfig.providerKey}-adv`, advKeys);
    }

    return html;
}

/**
 * The setup wizard's shape (2026-10-24): the cards alone, then ONE "Advanced"
 * fold holding every field (the provider's own, the common ones, the advanced
 * ones, a plugin provider's form) plus the tab's `fold` html; `after` paints
 * under the fold (a test button). Off = the cards and the disabled line.
 */
export function renderProviderCompact(tabConfig, ctx, { fold = '', after = '' } = {}) {
    const effective = _filterProviders(tabConfig, ctx);
    const current = _currentProvider(effective, ctx);
    const def = effective.providers[current] || effective.providers.none;
    const cards = _renderCards(effective, current, ctx, true);
    if (current === 'none') {
        return cards + `<p class="setting-help" style="padding:12px 0;opacity:0.7">
            ${effective.disabledMessage || 'Disabled. Select a provider above to enable.'}
        </p>`;
    }
    let inner = def._plugin && !def.essentialKeys?.length
        ? `<div id="plugin-provider-settings" data-plugin="${_esc(current)}" style="padding:8px 0"></div>` : '';
    const keys = [...(def.essentialKeys || []), ...(effective.commonKeys || []),
                  ...(def.advancedKeys || []), ...(effective.commonAdvancedKeys || [])];
    if (keys.length) inner += ctx.renderFields(keys);
    return cards + renderFold(inner + fold) + after;
}

/** One closed <details> named Advanced: what the wizard hides under each section. */
export function renderFold(inner, title = 'Advanced') {
    return `<details class="setup-adv"><summary>${_esc(title)}</summary><div class="setup-adv-body">${inner}</div></details>`;
}

export function attachProviderListeners(tabConfig, ctx, el, tabModule) {
    const key = tabConfig.providerKey;
    const grid = el.querySelector(`.prov-cards[data-key="${key}"]`);
    if (!grid) return;

    // Load inline plugin settings if a plugin provider is selected
    _loadPluginProviderSettings(el);

    // A card picked: stage it and repaint for the new provider's fields
    // (pendingChanges carries the pick across the repaint). Through the host:
    // the old reach into `.settings-main #settings-content` found nothing outside
    // the Settings page, so inside the setup wizard a provider change silently
    // never swapped its fields (2026-10-22).
    grid.addEventListener('change', e => {
        const radio = e.target.closest('input[type="radio"]');
        if (!radio || radio.disabled) return;
        ctx.markChanged(key, radio.value);
        ctx.refreshTab();
    });

    // A dormant card: wake its plugin, then pick it.
    grid.addEventListener('click', e => {
        const card = e.target.closest('.prov-card.dormant');
        if (!card || card.classList.contains('busy')) return;
        e.preventDefault();
        const def = tabConfig.providers[card.dataset.value] || {};
        _wakeDormant(card, def, key, card.dataset.value, ctx);
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

function _renderCards(tabConfig, current, ctx, bare = false) {
    const key = tabConfig.providerKey;
    const h = ctx.help[key];
    const isOverridden = ctx.overrides.includes(key);

    const cards = Object.entries(tabConfig.providers).map(([value, def]) => {
        const selected = value === current;
        const dormant = !!def.dormant;
        // Same glyph pair the chat dropdowns use — cloud honesty (2026-08-31)
        const badge = def.is_local === undefined ? '' : (def.is_local ? '🏠 local' : '☁️ cloud');
        const note = !dormant ? '' : (def.missing_deps?.length
            ? `installs ${def.missing_deps.join(', ')}, then turns on the ${def.plugin} plugin`
            : `turns on the ${def.plugin} plugin`);
        return `
            <label class="prov-card${selected ? ' selected' : ''}${dormant ? ' dormant' : ''}" data-value="${_esc(value)}" data-note="${_esc(note)}">
                <input type="radio" name="setting-${key}" data-key="${key}" value="${_esc(value)}"
                       ${selected ? 'checked' : ''} ${dormant ? 'disabled' : ''}>
                <span class="prov-card-head">
                    <span class="prov-card-name">${_esc(def.label)}</span>
                    <span class="prov-card-badge">${badge}</span>
                </span>
                ${def.blurb ? `<span class="prov-card-blurb">${_esc(def.blurb)}</span>` : ''}
                ${dormant ? `<span class="prov-card-note">${_esc(note)}</span>` : ''}
            </label>`;
    }).join('');

    const grid = `<div class="prov-cards" data-key="${key}">${cards}</div>`;
    if (bare) return grid;
    return `
        <div class="settings-grid">
            <div class="setting-row prov-row${isOverridden ? ' overridden' : ''}" data-key="${key}">
                <div class="setting-label">
                    <div class="setting-label-row">
                        <label>${ctx.formatLabel(key)}</label>
                        ${h ? `<span class="help-icon" data-help-key="${key}" title="Details">?</span>` : ''}
                        ${isOverridden ? '<span class="override-badge">Custom</span>' : ''}
                        ${isOverridden ? `<button class="btn-icon reset-btn" data-reset-key="${key}" title="Reset to default">↺</button>` : ''}
                    </div>
                    ${h?.short ? `<div class="setting-help">${h.short}</div>` : ''}
                </div>
                <div class="setting-input">${grid}</div>
            </div>
        </div>
    `;
}

async function _wakeDormant(card, def, key, value, ctx) {
    const note = card.querySelector('.prov-card-note');
    const say = t => { if (note) note.textContent = t; };
    card.classList.add('busy');
    card.classList.remove('failed');
    try {
        if (def.missing_deps?.length) {
            say(`installing ${def.missing_deps.join(', ')}…`);
            // Asks first, installs under its own notification row, waits for pip.
            const { ensureExtra } = await import('./extras.js');
            const ok = await ensureExtra(`plugin:${def.plugin}`);
            if (!ok) { say(card.dataset.note || ''); return; }      // declined
        }
        say(`turning on the ${def.plugin} plugin…`);
        const pluginsAPI = (await import('./plugins-api.js')).default;
        const r = await pluginsAPI.togglePlugin(def.plugin);
        if (r && r.enabled === false) throw new Error('the plugin did not turn on');
        // The cached merge is what the next paint reads: it is on now.
        def.dormant = false;
        def.missing_deps = [];
        ctx.markChanged(key, value);
        await ctx.refreshTab();
    } catch (e) {
        card.classList.add('failed');
        say(`could not turn it on: ${e.message}`);
    } finally {
        card.classList.remove('busy');
    }
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
            box.innerHTML = `<p class="setting-help">Configure in Settings → Plugins → ${pluginName}</p>`;
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
        box.innerHTML = `<p class="setting-help">Configure in Settings → Plugins → ${pluginName}</p>`;
    }
}
