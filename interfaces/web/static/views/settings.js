// views/settings.js - the Settings SHELL: the tab rail, the mobile menu, tab switching,
// the Save / Reload buttons and the plugin settings tabs. Everything a tab module
// touches (the snapshot, ctx, field renderers, generic listeners, the batch save) is
// the HOST in shared/settings-host.js, which the setup wizard (views/setup.js) mounts
// the same tab modules through. Tab handlers live in settings-tabs/*.js — this file
// stays lean.
import * as api from '../shared/settings-api.js';
import * as ui from '../ui.js';
import { createHost } from '../shared/settings-host.js';

// Tab registry
import dashboardTab from './settings-tabs/dashboard.js';
import appearanceTab from './settings-tabs/appearance.js';
import audioTab from './settings-tabs/audio.js';
import ttsTab from './settings-tabs/tts.js';
import sttTab from './settings-tabs/stt.js';
import embeddingTab from './settings-tabs/embedding.js';
import llmTab from './settings-tabs/llm.js';
import toolsTab from './settings-tabs/tools.js';
import networkTab from './settings-tabs/network.js';
import privacyTab from './settings-tabs/privacy.js';
import wakewordTab from './settings-tabs/wakeword.js';
import conversationTab from './settings-tabs/conversation.js';
import pluginsTab from './settings-tabs/plugins.js';
import imagesTab from './settings-tabs/images.js';

import backupTab from './settings-tabs/backup.js';
import devicesTab from './settings-tabs/devices.js';
import mcpTab from './settings-tabs/mcp.js';
import systemTab from './settings-tabs/system.js';
import helpTab from './settings-tabs/help-tab.js';
import videosTab from './settings-tabs/videos-tab.js';
import storeTab from './settings-tabs/store-tab.js';

import { getRegisteredTabs } from '../shared/plugin-registry.js';
import { escapeHtml } from '../shared/modal.js';

const STATIC_TABS = [dashboardTab, appearanceTab, audioTab, ttsTab, sttTab, embeddingTab, llmTab, imagesTab, toolsTab, networkTab, privacyTab, wakewordTab, conversationTab, pluginsTab, storeTab, backupTab, devicesTab, mcpTab, systemTab, helpTab, videosTab];

let container = null;
let activeTab = 'dashboard';
let dynamicTabs = [];
let mobileMenuCleanup = null;

// The host: this shell's snapshot and ctx. The callbacks are how tabs reach the
// shell (switch tab, repaint, the plugin-tab doors, the Save button's busy state).
const host = createHost({
    contentEl: () => container?.querySelector('#settings-content'),
    repaint: () => renderTabContent(),
    isTabActive: id => activeTab === id,
    navigate: tab => goToTab(tab),
    loadPluginTab,
    syncDynamicTabs,
    refreshSidebar,
    // Auto-load settings tabs for enabled plugins that have a web UI.
    // Parallelized — sequential awaits here were making Settings tab load ~N*RTT
    // slow (once per enabled plugin). Promise.all gives us max(RTT) instead.
    afterPlugins: async pluginList => {
        const tabLoads = pluginList
            .filter(p => p.enabled && p.settingsUI)
            .map(p => loadPluginTab(p.name, p.settingsUI).catch(() => {}));
        await Promise.all(tabLoads);
    },
    onSaveBusy: busy => {
        const saveBtn = container?.querySelector('#settings-save');
        if (saveBtn) { saveBtn.disabled = busy; saveBtn.textContent = busy ? 'Saving...' : 'Save Changes'; }
    },
});

export default {
    init(el) { container = el; },
    async show() {
        // One-shot deep-link from other views (e.g. sidebar provider ↗ → LLM tab)
        if (window._settingsTab) {
            activeTab = window._settingsTab;
            window._settingsTab = null;
        }
        await host.loadData();
        render();
    },
    hide() {}
};

// ── Plugin settings tabs ──

async function loadPluginTab(name, source) {
    try {
        if (source === 'manifest') {
            const plugin = host.pluginList.find(p => p.name === name);
            if (!plugin?.settings_schema) return;
            const { renderSettingsForm, readSettingsForm } = await import('/static/shared/plugin-settings-renderer.js');
            const { registerPluginSettings } = await import('/static/shared/plugin-registry.js');
            const pluginsAPI = (await import('/static/shared/plugins-api.js')).default;
            const managed = host.managed;
            registerPluginSettings({
                id: name,
                name: plugin.title || name,
                icon: plugin.icon || '⚙️',
                helpText: `${plugin.title || name} settings`,
                render: (box, settings) => renderSettingsForm(box, plugin.settings_schema, settings, { managed }),
                load: () => pluginsAPI.getSettings(name),
                save: (s) => pluginsAPI.saveSettings(name, s),
                getSettings: (box) => readSettingsForm(box, plugin.settings_schema),
                reset: () => pluginsAPI.resetSettings(name),
            });
            syncDynamicTabs();
            return;
        }
        if (source !== 'plugin') return;
        const _v = window.__v ? `?v=${window.__v}` : '';
        const mod = await import(`/plugin-web/${name}/index.js${_v}`);
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
                box.innerHTML = `<p style="color:var(--error)">Couldn't load this tab. ${escapeHtml(e.message)}</p>`;
            }
        }
    }));
    // Alphabetize so the Plugin Settings list is stable + scannable.
    dynamicTabs.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
}

function getAllTabs() {
    // Insert dynamic tabs between plugins and system
    const tabs = STATIC_TABS.filter(t => !host.managedHides(t.id));
    const idx = tabs.findIndex(t => t.id === 'system');
    const before = idx >= 0 ? tabs.slice(0, idx) : tabs;
    const after = idx >= 0 ? tabs.slice(idx) : [];
    return [...before, ...dynamicTabs, ...after];
}

// ── Rendering ──

function renderSidebarItems(tabs) {
    const coreTabs = tabs.filter(t => !t.isPlugin);
    const pluginTabs = tabs.filter(t => t.isPlugin);
    const pluginChildActive = pluginTabs.some(t => t.id === activeTab);

    let html = '';
    for (const t of coreTabs) {
        html += `<button class="settings-nav-item${t.id === activeTab ? ' active' : ''}" data-tab="${t.id}">
            <span class="settings-nav-icon">${t.icon}</span>
            <span class="settings-nav-label">${t.name}</span>
        </button>`;
        // After the plugins tab, inject the collapsible plugin settings group
        if (t.id === 'plugins' && pluginTabs.length) {
            const open = pluginChildActive ? ' open' : '';
            html += `<details class="settings-plugin-group"${open}>
                <summary class="settings-plugin-group-label">Plugin Settings</summary>
                ${pluginTabs.map(pt => `
                    <button class="settings-nav-item settings-plugin-child${pt.id === activeTab ? ' active' : ''}" data-tab="${pt.id}">
                        <span class="settings-nav-icon">${pt.icon}</span>
                        <span class="settings-nav-label">${pt.name}</span>
                    </button>
                `).join('')}
            </details>`;
        }
    }
    return html;
}

function renderMobileItems(tabs) {
    return tabs.map(t => `
        <button class="settings-mobile-option${t.id === activeTab ? ' active' : ''}" data-tab="${t.id}">
            <span class="settings-mobile-opt-icon">${t.icon}</span>
            <span>${t.name}</span>
        </button>
    `).join('');
}

function render() {
    if (!container) return;
    const meta = getTabMeta();

    const tabs = getAllTabs();
    container.innerHTML = `
        <div class="settings-view">
            <div class="settings-sidebar">
                ${renderSidebarItems(tabs)}
            </div>
            <div class="settings-main">
                <div class="settings-mobile-nav">
                    <button class="settings-mobile-trigger" id="settings-mobile-trigger">
                        <span class="settings-mobile-icon">${meta.icon}</span>
                        <span class="settings-mobile-label">${meta.name}</span>
                        <span class="settings-mobile-arrow">&#x25BE;</span>
                    </button>
                    <div class="settings-mobile-menu hidden" id="settings-mobile-menu">
                        ${renderMobileItems(tabs)}
                    </div>
                </div>
                <div class="settings-header">
                    <div class="view-header-left">
                        <h2 id="stab-title">${meta.icon} ${meta.name}</h2>
                        <span class="view-subtitle" id="stab-desc">${meta.description || ''}</span>
                    </div>
                    <div class="view-header-actions">
                        <button class="btn-sm" id="settings-plugin-reset" style="display:none" title="Delete this plugin's stored settings — defaults take over">Reset to Defaults</button>
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
    host.mount(tab, el);
    updateSaveButtonForTab(tab);
}

function updateSaveButtonForTab(tab) {
    // Self-saving plugin tabs — no getSettings in their registration — persist
    // through their OWN buttons. Showing the global Save there made it a lie:
    // it "saved" stub values and toasted success while the visible form's edits
    // were never written (the months-old two-save-buttons bug, found 2026-07-04).
    // Contract: provide getSettings+save to participate in global Save; omit
    // them and the button hides on your tab. See docs/PLUGINS.md.
    const saveBtn = container?.querySelector('#settings-save');
    if (!saveBtn) return;
    // Core tabs that are CRUD pages say so with `selfSaving: true` (Devices).
    const selfSaving = !!(tab.selfSaving || (tab.isPlugin && tab._reg && !tab._reg.getSettings));
    saveBtn.style.display = selfSaving ? 'none' : '';
    const resetBtn = container?.querySelector('#settings-plugin-reset');
    if (resetBtn) resetBtn.style.display = (tab.isPlugin && tab._reg?.reset) ? '' : 'none';
    let hint = container.querySelector('#settings-selfsave-hint');
    if (selfSaving && !hint) {
        hint = document.createElement('span');
        hint.id = 'settings-selfsave-hint';
        hint.style.cssText = 'font-size:0.75rem;color:var(--text-muted);align-self:center';
        hint.textContent = 'This page saves with its own buttons';
        saveBtn.parentElement?.insertBefore(hint, saveBtn);
    } else if (!selfSaving && hint) {
        hint.remove();
    }
}

// ── Events ──

// ONE door for every tab switch (settings-navigate event, desktop sidebar,
// mobile menu). U1 layer (a), 2026-09-20: the three copies all repainted from
// the snapshot taken at view entry, so a provider-card PUT, a self-management
// toggle or the dashboard name looked reverted after a switch. Now: flush
// typed-but-unblurred values → refetch the blob → paint. If the user clicks
// on while the fetch is out, that click's own goToTab paints; this one yields.
async function goToTab(tabId) {
    host.flushCurrentInputs();
    activeTab = tabId;
    container.querySelectorAll('.settings-nav-item').forEach(b =>
        b.classList.toggle('active', b.dataset.tab === activeTab));
    // Auto-expand the plugin group when landing on a plugin tab
    const pluginGroup = container.querySelector('.settings-plugin-group');
    if (pluginGroup && pluginGroup.querySelector(`.settings-nav-item[data-tab="${tabId}"]`)) pluginGroup.open = true;
    const meta = getTabMeta();
    const title = container.querySelector('#stab-title');
    const desc = container.querySelector('#stab-desc');
    if (title) title.textContent = `${meta.icon} ${meta.name}`;
    if (desc) desc.textContent = meta.description || '';
    // Mobile trigger + menu stay in sync whichever door was used
    const mobileTrigger = container.querySelector('#settings-mobile-trigger');
    const mobileMenu = container.querySelector('#settings-mobile-menu');
    if (mobileTrigger) {
        const set = (sel, v) => { const n = mobileTrigger.querySelector(sel); if (n) n.textContent = v; };
        set('.settings-mobile-icon', meta.icon);
        set('.settings-mobile-label', meta.name);
        set('.settings-mobile-arrow', '▾');
    }
    if (mobileMenu) {
        mobileMenu.querySelectorAll('.settings-mobile-option').forEach(o =>
            o.classList.toggle('active', o.dataset.tab === activeTab));
        mobileMenu.classList.add('hidden');
    }
    await host.loadSettingsOnly();
    if (activeTab !== tabId) return;
    renderTabContent();
}

let _shellNavBound = false;
function bindShellEvents() {
    // Navigate to a specific tab programmatically (used by plugin gear icons).
    // Bound ONCE: `container` is the persistent #view-settings and render()
    // runs on every visit — this listener stacked per visit, so a gear click
    // ran renderTabContent N times (and N provider-tab second paints).
    // DOM-refresh hunt 2026-09-08. The other shell listeners sit on nodes
    // render() replaces, so they don't stack.
    if (!_shellNavBound) container.addEventListener('settings-navigate', e => {
        const tabId = e.detail?.tab;
        if (!tabId) return;
        goToTab(tabId);
    });
    _shellNavBound = true;

    // Sidebar nav
    container.querySelector('.settings-sidebar')?.addEventListener('click', e => {
        const btn = e.target.closest('.settings-nav-item');
        if (!btn) return;
        goToTab(btn.dataset.tab);
    });

    // Mobile tab dropdown
    if (mobileMenuCleanup) mobileMenuCleanup();
    const mobileTrigger = container.querySelector('#settings-mobile-trigger');
    const mobileMenu = container.querySelector('#settings-mobile-menu');
    if (mobileTrigger && mobileMenu) {
        mobileTrigger.addEventListener('click', () => {
            mobileMenu.classList.toggle('hidden');
            mobileTrigger.querySelector('.settings-mobile-arrow').textContent =
                mobileMenu.classList.contains('hidden') ? '▾' : '▴';
        });

        mobileMenu.addEventListener('click', e => {
            const opt = e.target.closest('.settings-mobile-option');
            if (!opt) return;
            goToTab(opt.dataset.tab);
        });

        const outsideHandler = e => {
            if (!mobileMenu.classList.contains('hidden') && !e.target.closest('.settings-mobile-nav')) {
                mobileMenu.classList.add('hidden');
                const arrow = mobileTrigger.querySelector('.settings-mobile-arrow');
                if (arrow) arrow.textContent = '▾';
            }
        };
        document.addEventListener('click', outsideHandler);
        mobileMenuCleanup = () => document.removeEventListener('click', outsideHandler);
    }

    container.querySelector('#settings-save')?.addEventListener('click', onSaveClick);

    container.querySelector('#settings-reload')?.addEventListener('click', async () => {
        try {
            await api.reloadSettings();
            ui.showToast('Reloaded from disk', 'success');
            await host.loadData();
            renderTabContent();
        } catch { ui.showToast('Reload failed', 'error'); }
    });

    container.querySelector('#settings-plugin-reset')?.addEventListener('click', async () => {
        const tab = getTabMeta();
        const reg = tab._reg;
        if (!reg?.reset) return;
        if (!confirm(`Reset all ${reg.name} settings to their defaults? ` +
                     `Your customizations for this plugin will be cleared.`)) return;
        try {
            await reg.reset();
            ui.showToast(`${reg.name} settings reset to defaults`, 'success');
            renderTabContent();
        } catch (e) {
            ui.showToast(`Reset failed: ${e.message}`, 'error');
        }
    });
}

// A plugin toggle with a settings UI adds/removes its nav tab. Repaint the
// NAV only — the old full render() tore the plugin list down under the user
// (scroll lost; plugins.js' in-place row update then ran on a detached node).
// DOM-refresh hunt 2026-09-08.
function refreshSidebar() {
    const tabs = getAllTabs();
    const wasOpen = !!container?.querySelector('.settings-plugin-group')?.open;
    const sb = container?.querySelector('.settings-sidebar');
    if (sb) sb.innerHTML = renderSidebarItems(tabs);
    const grp = container?.querySelector('.settings-plugin-group');
    if (grp && wasOpen) grp.open = true;
    const mm = container?.querySelector('#settings-mobile-menu');
    if (mm) mm.innerHTML = renderMobileItems(tabs);
}

// ── Save ──

async function onSaveClick() {
    // Plugin tabs have their own save flow
    const tab = getTabMeta();
    if (tab.isPlugin && tab._reg) {
        const reg = tab._reg;
        // Self-saving tab (button normally hidden) — never fake-save; be honest.
        if (!reg.getSettings) {
            ui.showToast('This page saves with its own buttons', 'info');
            return;
        }
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
    await host.save();
}
