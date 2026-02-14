// views/settings.js - Settings view core
// Tab handlers live in settings-tabs/*.js — this file stays lean.
import * as api from '../shared/settings-api.js';
import * as ui from '../ui.js';

// Tab registry
import identityTab from './settings-tabs/identity.js';
import appearanceTab from './settings-tabs/appearance.js';
import audioTab from './settings-tabs/audio.js';
import ttsTab from './settings-tabs/tts.js';
import sttTab from './settings-tabs/stt.js';
import llmTab from './settings-tabs/llm.js';
import toolsTab from './settings-tabs/tools.js';
import networkTab from './settings-tabs/network.js';
import wakewordTab from './settings-tabs/wakeword.js';
import pluginsTab from './settings-tabs/plugins.js';
import systemTab from './settings-tabs/system.js';

import { getRegisteredTabs } from '../plugins/plugins-modal/plugin-registry.js';

const STATIC_TABS = [identityTab, appearanceTab, audioTab, ttsTab, sttTab, llmTab, toolsTab, networkTab, wakewordTab, pluginsTab, systemTab];

let container = null;
let activeTab = 'identity';
let settings = {};
let help = {};
let overrides = [];
let pendingChanges = {};
let wakewordModels = [];
let availableThemes = ['dark'];
let avatarPaths = { user: null, assistant: null };
let providerMeta = {};
let dynamicTabs = [];
let pluginList = [];
let lockedPlugins = [];
let mobileMenuCleanup = null;

export default {
    init(el) { container = el; },
    async show() {
        await loadData();
        render();
    },
    hide() {}
};

// ── Data Loading ──

async function loadData() {
    try {
        const [settingsData, helpData] = await Promise.all([
            api.getAllSettings(),
            api.getSettingsHelp().catch(() => ({ help: {} }))
        ]);
        settings = settingsData.settings || {};
        overrides = settingsData.user_overrides || [];
        help = helpData.help || {};

        await Promise.all([loadThemes(), loadWakewordModels(), loadAvatarPaths(), loadProviderMeta(), loadPluginList()]);
    } catch (e) {
        console.warn('Settings load failed:', e);
    }
}

async function loadPluginList() {
    try {
        const res = await fetch('/api/webui/plugins');
        if (res.ok) {
            const d = await res.json();
            pluginList = d.plugins || [];
            lockedPlugins = d.locked || [];
            // Auto-load settings tabs for enabled plugins
            for (const p of pluginList) {
                if (p.enabled) await loadPluginTab(p.name).catch(() => {});
            }
        }
    } catch {}
}

async function loadPluginTab(name) {
    try {
        const mod = await import(`/static/plugins/${name}/index.js`);
        const plugin = mod.default;
        if (plugin?.init) {
            const dummy = document.createElement('div');
            plugin.init(dummy);
        }
        syncDynamicTabs();
    } catch {
        // Plugin has no settings tab — that's fine
    }
}

function syncDynamicTabs() {
    const registered = getRegisteredTabs();
    dynamicTabs = registered.map(reg => ({
        id: reg.id,
        name: reg.name,
        icon: reg.icon,
        description: reg.helpText || `${reg.name} plugin settings`,
        isPlugin: true,
        _reg: reg,
        render(ctx) {
            return `<div class="plugin-tab-container" id="ptab-${reg.id}"></div>`;
        },
        async attachListeners(ctx, el) {
            const box = el.querySelector(`#ptab-${reg.id}`);
            if (!box) return;
            try {
                const settings = await reg.load();
                reg.render(box, settings);
            } catch (e) {
                box.innerHTML = `<p style="color:var(--error)">Failed to load: ${e.message}</p>`;
            }
        }
    }));
}

function getAllTabs() {
    // Insert dynamic tabs between plugins and system
    const idx = STATIC_TABS.findIndex(t => t.id === 'system');
    const before = STATIC_TABS.slice(0, idx);
    const after = STATIC_TABS.slice(idx);
    return [...before, ...dynamicTabs, ...after];
}

async function loadThemes() {
    try {
        const res = await fetch('/static/themes/themes.json');
        if (res.ok) { const d = await res.json(); availableThemes = d.themes || ['dark']; }
    } catch {}
}

async function loadWakewordModels() {
    try {
        const res = await fetch('/api/settings/wakeword-models');
        if (res.ok) { const d = await res.json(); wakewordModels = d.all || []; }
    } catch {}
}

async function loadProviderMeta() {
    try {
        const res = await fetch('/api/llm/providers');
        if (res.ok) { const d = await res.json(); providerMeta = d.metadata || {}; }
    } catch {}
}

async function loadAvatarPaths() {
    try {
        const [u, a] = await Promise.all([
            api.checkAvatar('user').catch(() => ({ exists: false })),
            api.checkAvatar('assistant').catch(() => ({ exists: false }))
        ]);
        avatarPaths.user = u.exists ? u.path : '/static/users/user.webp';
        avatarPaths.assistant = a.exists ? a.path : '/static/users/assistant.webp';
    } catch {}
}

// ── Rendering ──

function render() {
    if (!container) return;
    const meta = getTabMeta();

    const tabs = getAllTabs();
    container.innerHTML = `
        <div class="settings-view">
            <div class="settings-sidebar">
                ${tabs.map(t => `
                    <button class="settings-nav-item${t.id === activeTab ? ' active' : ''}${t.isPlugin ? ' plugin-tab' : ''}" data-tab="${t.id}">
                        <span class="settings-nav-icon">${t.icon}</span>
                        <span class="settings-nav-label">${t.name}</span>
                    </button>
                `).join('')}
            </div>
            <div class="settings-main">
                <div class="settings-mobile-nav">
                    <button class="settings-mobile-trigger" id="settings-mobile-trigger">
                        <span class="settings-mobile-icon">${meta.icon}</span>
                        <span class="settings-mobile-label">${meta.name}</span>
                        <span class="settings-mobile-arrow">&#x25BE;</span>
                    </button>
                    <div class="settings-mobile-menu hidden" id="settings-mobile-menu">
                        ${tabs.map(t => `
                            <button class="settings-mobile-option${t.id === activeTab ? ' active' : ''}" data-tab="${t.id}">
                                <span class="settings-mobile-opt-icon">${t.icon}</span>
                                <span>${t.name}</span>
                            </button>
                        `).join('')}
                    </div>
                </div>
                <div class="settings-header">
                    <div class="view-header-left">
                        <h2 id="stab-title">${meta.icon} ${meta.name}</h2>
                        <span class="view-subtitle" id="stab-desc">${meta.description || ''}</span>
                    </div>
                    <div class="view-header-actions">
                        <button class="btn-sm" id="settings-reload">Reload</button>
                        <button class="btn-primary" id="settings-save">Save Changes</button>
                    </div>
                </div>
                <div class="settings-content view-scroll" id="settings-content"></div>
            </div>
        </div>
    `;

    renderTabContent();
    bindShellEvents();
}

function getTabMeta() {
    return getAllTabs().find(t => t.id === activeTab) || STATIC_TABS[0];
}

function renderTabContent() {
    const el = container?.querySelector('#settings-content');
    if (!el) return;

    const tab = getTabMeta();
    const ctx = createCtx();
    el.innerHTML = `<div class="settings-tab-body">${tab.render(ctx)}</div>`;

    // Generic listeners (input changes, resets, help, accordion)
    attachGenericListeners(el);

    // Tab-specific listeners
    if (tab.attachListeners) tab.attachListeners(ctx, el);
}

// ── Context Object (passed to tab handlers) ──

function createCtx() {
    return {
        settings, help, overrides, pendingChanges,
        wakewordModels, availableThemes, avatarPaths, providerMeta,
        pluginList, lockedPlugins,
        renderFields, renderAccordion, renderInput, formatLabel,
        attachAccordionListeners,
        markChanged(key, value) { pendingChanges[key] = value; },
        async refreshTab() {
            await loadData();
            renderTabContent();
        },
        loadPluginTab,
        syncDynamicTabs,
        refreshSidebar() { render(); }
    };
}

// ── Generic Field Renderer ──

function renderFields(keys) {
    const rows = keys.map(key => {
        const value = settings[key];
        if (value === undefined) return '';

        const isOverridden = overrides.includes(key);
        const inputType = api.getInputType(value);
        const h = help[key];

        const isFullWidth = key.endsWith('_ENABLED');
        return `
            <div class="setting-row${isOverridden ? ' overridden' : ''}${isFullWidth ? ' full-width' : ''}" data-key="${key}">
                <div class="setting-label">
                    <div class="setting-label-row">
                        <label>${formatLabel(key)}</label>
                        ${h ? `<span class="help-icon" data-help-key="${key}" title="Details">?</span>` : ''}
                        ${isOverridden ? '<span class="override-badge">Custom</span>' : ''}
                    </div>
                    ${h?.short ? `<div class="setting-help">${h.short}</div>` : ''}
                </div>
                <div class="setting-input">
                    ${renderInput(key, value, inputType)}
                </div>
                <div class="setting-actions">
                    ${isOverridden ? `<button class="btn-icon reset-btn" data-reset-key="${key}" title="Reset to default">\u21BA</button>` : ''}
                </div>
            </div>
        `;
    }).join('');
    return `<div class="settings-grid">${rows}</div>`;
}

function renderInput(key, value, type) {
    const id = `setting-${key}`;

    // Special dropdowns
    if (key === 'WAKEWORD_MODEL' && wakewordModels.length) {
        return `<select id="${id}" data-key="${key}">
            ${wakewordModels.map(m => `<option value="${m}" ${value === m ? 'selected' : ''}>${m.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</option>`).join('')}
        </select>`;
    }
    if (key === 'WAKEWORD_FRAMEWORK') {
        return `<select id="${id}" data-key="${key}">
            ${['onnx', 'tflite'].map(f => `<option value="${f}" ${value === f ? 'selected' : ''}>${f.toUpperCase()}</option>`).join('')}
        </select>`;
    }

    if (type === 'checkbox') {
        return `<label class="setting-toggle">
            <input type="checkbox" id="${id}" data-key="${key}" ${value ? 'checked' : ''}>
            <span>${value ? 'Enabled' : 'Disabled'}</span>
        </label>`;
    }
    if (type === 'json') {
        return `<textarea id="${id}" data-key="${key}" class="setting-json" rows="4">${JSON.stringify(value, null, 2)}</textarea>`;
    }
    if (type === 'number') {
        return `<input type="number" id="${id}" data-key="${key}" value="${value}" step="any">`;
    }
    return `<input type="text" id="${id}" data-key="${key}" value="${escapeAttr(String(value))}">`;
}

function renderAccordion(id, keys, title = 'Advanced Settings') {
    return `
        <div class="settings-accordion" data-accordion="${id}">
            <div class="settings-accordion-header collapsed" data-accordion-toggle="${id}">
                <span class="accordion-arrow">\u25B6</span>
                <h4>${title}</h4>
            </div>
            <div class="settings-accordion-body collapsed" data-accordion-body="${id}">
                ${renderFields(keys)}
            </div>
        </div>
    `;
}

// ── Events ──

function bindShellEvents() {
    // Sidebar nav
    container.querySelector('.settings-sidebar')?.addEventListener('click', e => {
        const btn = e.target.closest('.settings-nav-item');
        if (!btn) return;
        activeTab = btn.dataset.tab;
        container.querySelectorAll('.settings-nav-item').forEach(b =>
            b.classList.toggle('active', b.dataset.tab === activeTab));

        const meta = getTabMeta();
        const title = container.querySelector('#stab-title');
        const desc = container.querySelector('#stab-desc');
        if (title) title.textContent = `${meta.icon} ${meta.name}`;
        if (desc) desc.textContent = meta.description || '';

        renderTabContent();
    });

    // Mobile tab dropdown
    if (mobileMenuCleanup) mobileMenuCleanup();
    const mobileTrigger = container.querySelector('#settings-mobile-trigger');
    const mobileMenu = container.querySelector('#settings-mobile-menu');
    if (mobileTrigger && mobileMenu) {
        mobileTrigger.addEventListener('click', () => {
            mobileMenu.classList.toggle('hidden');
            mobileTrigger.querySelector('.settings-mobile-arrow').textContent =
                mobileMenu.classList.contains('hidden') ? '\u25BE' : '\u25B4';
        });

        mobileMenu.addEventListener('click', e => {
            const opt = e.target.closest('.settings-mobile-option');
            if (!opt) return;
            activeTab = opt.dataset.tab;

            // Sync desktop sidebar
            container.querySelectorAll('.settings-nav-item').forEach(b =>
                b.classList.toggle('active', b.dataset.tab === activeTab));

            // Update mobile trigger + header
            const meta = getTabMeta();
            mobileTrigger.querySelector('.settings-mobile-icon').textContent = meta.icon;
            mobileTrigger.querySelector('.settings-mobile-label').textContent = meta.name;
            mobileTrigger.querySelector('.settings-mobile-arrow').textContent = '\u25BE';

            const title = container.querySelector('#stab-title');
            const desc = container.querySelector('#stab-desc');
            if (title) title.textContent = `${meta.icon} ${meta.name}`;
            if (desc) desc.textContent = meta.description || '';

            // Update menu active states
            mobileMenu.querySelectorAll('.settings-mobile-option').forEach(o =>
                o.classList.toggle('active', o.dataset.tab === activeTab));

            mobileMenu.classList.add('hidden');
            renderTabContent();
        });

        const outsideHandler = e => {
            if (!mobileMenu.classList.contains('hidden') && !e.target.closest('.settings-mobile-nav')) {
                mobileMenu.classList.add('hidden');
                const arrow = mobileTrigger.querySelector('.settings-mobile-arrow');
                if (arrow) arrow.textContent = '\u25BE';
            }
        };
        document.addEventListener('click', outsideHandler);
        mobileMenuCleanup = () => document.removeEventListener('click', outsideHandler);
    }

    container.querySelector('#settings-save')?.addEventListener('click', saveChanges);

    container.querySelector('#settings-reload')?.addEventListener('click', async () => {
        try {
            await api.reloadSettings();
            ui.showToast('Reloaded from disk', 'success');
            await loadData();
            renderTabContent();
        } catch { ui.showToast('Reload failed', 'error'); }
    });
}

function attachGenericListeners(el) {
    // Input changes → pendingChanges
    el.addEventListener('change', e => {
        const key = e.target.dataset.key;
        if (!key || key === 'undefined') return;
        const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
        pendingChanges[key] = value;

        const row = e.target.closest('.setting-row');
        if (row) row.classList.add('modified');

        // Update toggle label
        if (e.target.type === 'checkbox') {
            const span = e.target.parentElement?.querySelector('span');
            if (span) span.textContent = e.target.checked ? 'Enabled' : 'Disabled';
        }
    });

    // Reset + Help clicks
    el.addEventListener('click', async e => {
        const resetBtn = e.target.closest('[data-reset-key]');
        if (resetBtn) {
            const key = resetBtn.dataset.resetKey;
            if (!confirm(`Reset "${formatLabel(key)}" to default?`)) return;
            try {
                await api.deleteSetting(key);
                ui.showToast(`Reset ${formatLabel(key)}`, 'success');
                delete pendingChanges[key];
                await loadData();
                renderTabContent();
            } catch { ui.showToast('Reset failed', 'error'); }
            return;
        }

        const helpBtn = e.target.closest('[data-help-key]');
        if (helpBtn) showHelpPopup(helpBtn.dataset.helpKey);
    });

    attachAccordionListeners(el);
}

function attachAccordionListeners(el) {
    el.querySelectorAll('[data-accordion-toggle]').forEach(header => {
        header.addEventListener('click', () => {
            const id = header.dataset.accordionToggle;
            const body = el.querySelector(`[data-accordion-body="${id}"]`);
            const arrow = header.querySelector('.accordion-arrow');
            header.classList.toggle('collapsed');
            if (body) body.classList.toggle('collapsed');
            if (arrow) arrow.style.transform = header.classList.contains('collapsed') ? '' : 'rotate(90deg)';
        });
    });
}

// ── Save ──

async function saveChanges() {
    // Plugin tabs have their own save flow
    const tab = getTabMeta();
    if (tab.isPlugin && tab._reg) {
        const reg = tab._reg;
        const box = container?.querySelector(`#ptab-${reg.id}`);
        if (box && reg.getSettings && reg.save) {
            const saveBtn = container?.querySelector('#settings-save');
            if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Saving...'; }
            try {
                const s = reg.getSettings(box);
                await reg.save(s);
                ui.showToast(`${reg.name} settings saved`, 'success');
            } catch (e) {
                ui.showToast(`Save failed: ${e.message}`, 'error');
            } finally {
                if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save Changes'; }
            }
        }
        return;
    }

    const valid = {};
    for (const [key, value] of Object.entries(pendingChanges)) {
        if (key && key !== 'undefined') valid[key] = value;
    }

    if (!Object.keys(valid).length) {
        ui.showToast('No changes to save', 'info');
        return;
    }

    const saveBtn = container?.querySelector('#settings-save');
    if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Saving...'; }

    try {
        const parsed = {};
        for (const [key, value] of Object.entries(valid)) {
            parsed[key] = api.parseValue(value, settings[key]);
        }

        const result = await api.updateSettingsBatch(parsed);
        await api.reloadSettings();
        ui.showToast(`Saved ${Object.keys(parsed).length} settings`, 'success');

        if (result.restart_required) {
            const keys = result.restart_keys || [];
            ui.showToast(`Restart required for: ${keys.join(', ') || 'some settings'}`, 'warning');
        }

        pendingChanges = {};
        await loadData();
        renderTabContent();
    } catch (e) {
        ui.showToast('Save failed: ' + e.message, 'error');
    } finally {
        if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save Changes'; }
    }
}

// ── Help Popup ──

function showHelpPopup(key) {
    const h = help[key];
    if (!h) return;

    const popup = document.createElement('div');
    popup.className = 'sched-editor-overlay';
    popup.innerHTML = `
        <div style="background:var(--bg-secondary);border-radius:var(--radius-lg);padding:20px;max-width:500px;width:90%">
            <div style="display:flex;justify-content:space-between;margin-bottom:12px">
                <h3 style="margin:0">${formatLabel(key)}</h3>
                <button class="btn-icon" id="help-close">&times;</button>
            </div>
            <p style="line-height:1.5;color:var(--text)">${h.long || h.short || ''}</p>
            ${h.short && h.long ? `<p style="margin-top:12px;font-size:var(--font-sm);color:var(--text-muted)"><strong>Summary:</strong> ${h.short}</p>` : ''}
        </div>
    `;
    document.body.appendChild(popup);
    popup.addEventListener('click', e => { if (e.target === popup) popup.remove(); });
    popup.querySelector('#help-close')?.addEventListener('click', () => popup.remove());
}

// ── Helpers ──

function formatLabel(key) {
    return key.replace(/_/g, ' ').split(' ')
        .map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join(' ');
}

function escapeAttr(str) {
    return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
