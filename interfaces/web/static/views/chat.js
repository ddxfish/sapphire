// views/chat.js - Chat view module with settings sidebar
import * as api from '../api.js';
import * as ui from '../ui.js';
import * as eventBus from '../core/event-bus.js';
import { getElements, getIsProc } from '../core/state.js';
import { updateScene, updateSendButtonLLM } from '../features/scene.js';
import { applyTrimColor, applyBackground } from '../features/chat-settings.js';
import { handleNewChat, handleDeleteChat, handleChatChange } from '../features/chat-manager.js';
import { getInitData, refreshInitData, getInitDataSync } from '../shared/init-data.js';
import { updateSettingsBatch } from '../shared/settings-api.js';
import { switchView } from '../core/router.js';
import { loadPersona, createFromChat, avatarImg, avatarFallback, avatarUrl } from '../shared/persona-api.js';
import { initAgentStatus } from '../features/agent-status.js';
import { mountScenePicker } from '../shared/scene-picker.js';
import { setupModalClose } from '../shared/modal.js';
import { getMotions, setChatMotion, chatMotionId } from '../core/motions.js';
import { initAccordions } from '../shared/accordion.js';
import {
    renderScopeDropdowns,
    fetchScopeData,
    populateScopeOptions,
    readScopeSettings
} from '../shared/scope-dropdowns.js';

let sidebarLoaded = false;
let saveTimer = null;
let pendingSaveChatName = null;  // captured at debounce schedule time, not fire time
let llmProviders = [];
let llmMetadata = {};
let personasList = [];
let defaultPersonaName = '';
let _trimColor = '';   // active chat's own accent ('' = riding the global default); seeds the Appearance modal
let _docClickHandler = null;
let _personaHandler = null;

const SAVE_DEBOUNCE = 500;

export default {
    init(container) {
        // Agent pill bar
        initAgentStatus();

        // Sidebar collapse/expand
        const toggle = container.querySelector('#chat-sidebar-toggle');
        if (toggle) toggle.addEventListener('click', () => toggleSidebar(container));
        const expand = container.querySelector('#chat-sidebar-expand');
        if (expand) expand.addEventListener('click', () => toggleSidebar(container));

        // Restore sidebar state
        const collapsed = localStorage.getItem('sapphire-chat-sidebar') === 'collapsed';
        const sidebar = container.querySelector('.chat-sidebar');
        if (sidebar && collapsed) sidebar.classList.add('collapsed');

        // Reload sidebar settings whenever active chat changes.
        // IMPORTANT: we listen for 'chat-activated' (dispatched by handleChatChange
        // AFTER api.activateChat() succeeds) instead of 'change'. Listening on 'change'
        // would race with handleChatChange — loadSidebar's GET /api/chats/{name}/settings
        // would fire before the backend had switched active chats, hitting the fallback
        // file-lookup path which 404s because chats live in SQLite, not JSON files.
        const chatSelect = getElements().chatSelect || document.getElementById('chat-select');
        if (chatSelect) {
            // chat-activated carries {chat, settings} from the activate
            // response — paint straight from that truth (fix C); a re-GET
            // can lose a race it doesn't need to run. chat-list-ready has
            // no settings; it stays on the fetch path.
            chatSelect.addEventListener('chat-activated',
                (e) => loadSidebar(e.detail?.settings || null, e.detail?.chat || null));
            chatSelect.addEventListener('chat-list-ready', () => loadSidebar());
        }

        // Refresh toolset dropdown count when tools change (e.g. tool_load).
        // When Sapphire switches her own toolset (switch_toolset publishes
        // {name}), select that toolset — otherwise the pill would snap back to
        // the pre-switch value and lie about which toolset is live.
        eventBus.on(eventBus.Events.TOOLSET_CHANGED, async (data) => {
            await refreshInitData();
            const container = document.getElementById('view-chat');
            const toolsetSel = container?.querySelector('#sb-toolset');
            if (!toolsetSel) return;
            const currentVal = toolsetSel.value;
            const init = await getInitData();
            if (init?.toolsets?.list) {
                toolsetSel.innerHTML = init.toolsets.list
                    .filter(t => t.type !== 'module')
                    .map(t => `<option value="${t.name}">${t.name} (${t.function_count})</option>`)
                    .join('');
                toolsetSel.value = data?.name || currentVal;
            }
        });

        // Refresh voice dropdown when TTS provider changes
        eventBus.on('settings_changed', async (data) => {
            if (data?.key === 'TTS_PROVIDER') refreshVoiceDropdown();
            if (data?.key === 'LLM_PROVIDERS' || data?.key === 'LLM_CUSTOM_PROVIDERS') loadSidebar();
            if (data?.key === 'PERSONA_FAVORITES') { await refreshInitData(); loadSidebar(); }
        });

        // Refresh prompt dropdown when a user actually saves/deletes a prompt.
        // IMPORTANT: PROMPT_CHANGED fires with TWO different actions:
        //   - "saved"  → user modified a prompt in the Prompts view (needs loadSidebar)
        //   - "loaded" → prompt was applied as a side effect of _apply_chat_settings
        //                during a chat switch (does NOT need loadSidebar; chat-activated
        //                already handles it)
        // Listening on "loaded" was causing a race: during chat switch, flushPendingSave's
        // PUT for the OLD chat fires PROMPT_CHANGED:loaded via SSE, which arrived before
        // activateChat had switched the backend. loadSidebar's GET for the NEW chat name
        // hit the non-active-chat file-lookup path and 404'd.
        eventBus.on(eventBus.Events.PROMPT_CHANGED, async (data) => {
            if (data?.action === 'loaded') return;  // side effect, not user-initiated
            // Await the cache bust BEFORE painting (same pattern as the
            // TOOLSET_CHANGED handler above) — module listener order isn't
            // guaranteed, so an un-awaited loadSidebar could paint from the
            // stale init cache (vault names lingering after a lock).
            await refreshInitData();
            loadSidebar();
        });
        eventBus.on(eventBus.Events.PROMPT_DELETED, async () => {
            await refreshInitData();
            loadSidebar();
        });

        // Refresh spice dropdown when spice sets change
        eventBus.on(eventBus.Events.SPICE_CHANGED, () => loadSidebar());

        // Sapphire's set_scene tool changes the chat background live (publishes {background}).
        // Her switch_model tool publishes {settings:{llm_primary}} — resync the sidebar
        // model dropdown + send-button badge from truth, or the UI keeps showing the old
        // model after she moves herself to a different (e.g. cloud/paid) provider.
        eventBus.on(eventBus.Events.CHAT_SETTINGS_CHANGED, (data) => {
            if (!data) return;
            // Foreign-chat guard (hunt 2026-08-17): publishes carry `chat`;
            // a background lane's write (phone/cron/agent) must not repaint
            // the chat the operator is looking at. No chat key = legacy
            // payload — apply as before.
            const cur = document.getElementById('chat-select')?.value;
            const foreign = typeof data.chat === 'string' && data.chat
                && cur && data.chat !== cur;
            // Tools publish top-level {background|motion}; the settings PUT
            // route and set_voice publish nested {settings:{...}} — read
            // both, so a scene-modal change in one tab reaches the others.
            const s = (data.settings && typeof data.settings === 'object') ? data.settings : {};
            const bg = typeof data.background === 'string' ? data.background
                : (typeof s.background === 'string' ? s.background : null);
            const motion = typeof data.motion === 'string' ? data.motion
                : (typeof s.motion === 'string' ? s.motion : null);
            if (!foreign) {
                if (bg !== null) applyBackground(bg);
                if (motion !== null) setChatMotion(motion);
            }
            if (s.llm_primary) {
                // The badge itself — loadSidebar repaints dropdowns but never
                // touches #send-btn, so a switch made elsewhere (her
                // switch_model tool, another tab, a room-sidebar save) left
                // the tint on the old provider (Krem 2026-08-23).
                if (!foreign) updateSendButtonLLM(s.llm_primary, s.llm_model || '');
                loadSidebar();
            }
        });

        // Refresh sidebar scope dropdowns when scopes are created/deleted in
        // the Mind view. Without this, users see stale options until a full
        // page refresh — or worse, select a scope in the sidebar that the
        // backend no longer knows about and silently fall through to 'default'.
        eventBus.on('scope_changed', () => loadSidebar());

        // Backend transient notices (dangling toolset detected, empty-content
        // fallback after tool calls, etc.) — surfaced as toasts so the user
        // sees them clearly instead of having to scan the chat for "(no
        // response)" or chase a missing toolset in logs.
        eventBus.on('chat_notice', (data) => {
            if (!data?.message) return;
            ui.showToast(data.message, data.severity || 'warning');
        });

        // Refresh sidebar (incl. scope dropdowns) when a plugin is toggled.
        // Plugin scopes are only shown when the owning plugin is enabled, so a
        // toggle changes which dropdowns should be visible. Also refreshes init
        // data so newly-loaded plugin scope_declarations land.
        document.addEventListener('sapphire:plugin_toggled', async () => {
            try { await refreshInitData(); } catch (e) { /* fail-soft */ }
            loadSidebar();
        });

        // Accordion behavior + persisted open-state — shared/accordion.js
        // (Phase 1 extraction; replaces the local delegate + persistence).
        // One-time migration: the old storage key's entries ("core:<title>" /
        // "plugin:<name>") match the data-acc ids exactly, so state carries.
        try {
            const old = localStorage.getItem('sapphire_sb_accordion_state');
            if (old) {
                if (!localStorage.getItem('accordion-state:chat-sidebar')) {
                    localStorage.setItem('accordion-state:chat-sidebar', old);
                }
                localStorage.removeItem('sapphire_sb_accordion_state');
            }
        } catch { /* localStorage unavailable — persistence is a nicety */ }
        const sbRoot = container.querySelector('.chat-sidebar');
        if (sbRoot) initAccordions(sbRoot, 'chat-sidebar');

        // Sidebar chat picker
        const sbPicker = container.querySelector('#sb-chat-picker');
        const sbPickerBtn = container.querySelector('#sb-chat-picker-btn');
        if (sbPicker && sbPickerBtn) {
            sbPickerBtn.addEventListener('click', e => {
                e.stopPropagation();
                sbPicker.classList.toggle('open');
            });
            const sbDropdown = container.querySelector('#sb-chat-picker-dropdown');
            if (sbDropdown) {
                sbDropdown.addEventListener('click', async e => {
                    const item = e.target.closest('.chat-picker-item');
                    if (!item) return;
                    // "See more" tail item → Chat Manager (the full list)
                    if (item.classList.contains('chat-picker-more')) {
                        sbPicker.classList.remove('open');
                        const { switchView } = await import('../core/router.js');
                        switchView('chat-manage');
                        return;
                    }
                    const chatName = item.dataset.chat;
                    if (!chatName) return;

                    // Block chat switch while streaming/processing
                    if (getIsProc()) {
                        sbPicker.classList.remove('open');
                        ui.showToast('Cannot switch chats while generating', 'error');
                        return;
                    }

                    sbPicker.classList.remove('open');

                    // Update active states in dropdown
                    sbDropdown.querySelectorAll('.chat-picker-item').forEach(i => {
                        const active = i.dataset.chat === chatName;
                        i.classList.toggle('active', active);
                        i.querySelector('.chat-picker-item-check').textContent = active ? '\u2713' : '';
                    });

                    // Update sidebar chat name
                    const displayName = item.querySelector('.chat-picker-item-name')?.textContent || chatName;
                    const nameEl = container.querySelector('#sb-chat-name');
                    if (nameEl) nameEl.textContent = displayName;

                    // Sync hidden select and trigger change
                    const chatSelect = getElements().chatSelect;
                    if (chatSelect) chatSelect.value = chatName;
                    // handleChatChange dispatches chat-activated → the
                    // override paint. A second loadSidebar here would
                    // supersede it and turn fix C into a no-op.
                    await handleChatChange();
                });
            }
        }

        // Sidebar new/delete chat
        container.querySelector('#sb-new-chat')?.addEventListener('click', async () => {
            await handleNewChat();  // chat-activated inside paints the sidebar
        });
        container.querySelector('#sb-delete-chat')?.addEventListener('click', async () => {
            await handleDeleteChat();
            await loadSidebar();
        });

        // The eyeball — VAULT TOGGLE, one job (Krem's ruling 2026-08-14:
        // "10 pounds of options in a 1 pound toggle switch" — no more).
        // Locked → unlock dialog. Unlocked → lock (server evicts a private
        // active chat itself). No vault → setup dialog. The eyeball NEVER
        // touches any chat's private flag: membership is stamped by TALKING
        // while the vault is open (server-side, chat_streaming) and unmade
        // only in Chat Manager. Paint lives in scene.js (vault state).
        const eyeBtn = container.querySelector('#sb-privacy-eye');
        if (eyeBtn && window.__managed) eyeBtn.style.display = 'none';

        eyeBtn?.addEventListener('click', async () => {
            try {
                const { vaultSetup, vaultUnlock, vaultLock, vaultStatus } =
                    await import('../shared/vault-api.js');
                const { keyPrompt } = await import('../shared/key-prompt.js');
                const v = await vaultStatus();
                if (!v) {
                    ui.showToast("Sapphire isn't reachable right now — vault state unknown", 'error');
                    return;
                }
                if (!v.exists) {
                    const res = await keyPrompt({
                        title: 'Set up your vault',
                        message: 'Private mode lives behind an encrypted vault. Pick a passphrase.',
                        mode: 'setup',
                        validate: async (key) => { await vaultSetup(key); return ''; }
                    });
                    if (!res?.key) return;
                    ui.showToast('Vault created — private mode ON. Talking in a chat marks it private.', 'success');
                } else if (!v.unlocked) {
                    const res = await keyPrompt({
                        title: 'Unlock the vault',
                        message: 'Enter your vault passphrase.',
                        validate: async (key) => { await vaultUnlock(key); return ''; }
                    });
                    if (!res?.key) return;
                    ui.showToast('Private mode ON — talking in a chat marks it private', 'success');
                } else {
                    await vaultLock();
                    ui.showToast('Vault locked', 'success');
                }
                const { populateChatDropdown } = await import('../features/chat-manager.js');
                await populateChatDropdown();
                await updateScene();
            } catch (err) {
                console.error('Vault toggle failed:', err);
                ui.showToast(err?.message || 'Vault toggle failed', 'error');
            }
        });

        // Close sidebar picker on outside click (added/removed in show/hide)
        _docClickHandler = e => {
            if (!e.target.closest('#sb-chat-picker')) {
                container.querySelector('#sb-chat-picker')?.classList.remove('open');
            }
        };

        // Toggle buttons (Spice, Date/Time) — PER-CHAT (saved via debouncedSave).
        container.querySelectorAll('.sb-toggle').forEach(btn => {
            btn.addEventListener('click', () => {
                const active = btn.dataset.active !== 'true';
                btn.dataset.active = active;
                btn.classList.toggle('active', active);
                debouncedSave(container);
            });
        });

        // Conversation mode moved to the composer's mic flyout (features/convo.js).

        // Auto-save on any sidebar input change.
        // EVENT DELEGATION: bind ONCE to the chat-sidebar parent so dynamically-added
        // elements (e.g., scope dropdowns rendered later by shared/scope-dropdowns.js
        // inside #sb-scope-dropdowns) get caught too. Direct querySelectorAll at init
        // time would miss them — they don't exist yet.
        const sidebarRoot = container.querySelector('.chat-sidebar');
        if (sidebarRoot) {
            const handleSidebarInput = (e) => {
                const el = e.target;
                if (!el || !el.tagName) return;
                if (!['SELECT', 'INPUT', 'TEXTAREA'].includes(el.tagName)) return;
                // Don't auto-save on the chat-name input or hidden picker
                if (el.id === 'sb-chat-name' || el.id === 'sb-chat-picker') return;

                // Immediate visual feedback for specific elements
                if (el.id === 'sb-pitch') {
                    const label = container.querySelector('#sb-pitch-val');
                    if (label) label.textContent = el.value;
                    updateSliderFill(el);
                }
                if (el.id === 'sb-speed') {
                    const label = container.querySelector('#sb-speed-val');
                    if (label) label.textContent = el.value;
                    updateSliderFill(el);
                }
                if (el.id === 'sb-llm-primary') {
                    updateModelSelector(container, el.value, '');
                }
                if (el.id === 'sb-spice-turns') {
                    const toggle = container.querySelector('#sb-spice-toggle');
                    if (toggle) toggle.textContent = `Spice \u00b7 ${el.value}`;
                }
                debouncedSave(container);
            };
            // Both 'change' (selects, checkboxes, color) and 'input' (range sliders, textareas)
            sidebarRoot.addEventListener('change', handleSidebarInput);
            sidebarRoot.addEventListener('input', handleSidebarInput);
        }

        // Appearance: one modal for accent color + scene + motion (2026-08-29;
        // was a color circle + a scene button crowding the header row).
        const appearanceBtn = container.querySelector('#sb-appearance-btn');
        if (appearanceBtn && !appearanceBtn.dataset.bound) {
            appearanceBtn.dataset.bound = '1';
            appearanceBtn.addEventListener('click', openAppearanceModal);
        }

        // "Go to Mind" buttons are now wired by the shared/scope-dropdowns.js renderer
        // via the onNavigate callback in loadSidebar(). Don't bind here at init() time —
        // the buttons don't exist in the DOM yet (rendered dynamically with each loadSidebar).

        // "Go to view" buttons — navigate to Prompts/Toolsets with selection.
        // data-tab targets a specific Settings tab (e.g. provider → Settings > LLM).
        container.querySelectorAll('.sb-goto-view').forEach(btn => {
            btn.addEventListener('click', () => {
                const selectId = btn.dataset.select;
                const val = selectId && container.querySelector(`#${selectId}`)?.value;
                if (val) window._viewSelect = val;
                if (btn.dataset.tab) window._settingsTab = btn.dataset.tab;
                switchView(btn.dataset.view);
            });
        });

        // Sidebar mode tabs (Easy/Full)
        initPersonaStrip(container);

        // Listen for persona-loaded events (added/removed in show/hide)
        _personaHandler = () => loadSidebar();

        // Save As New Persona button
        // Document upload handler
        const docUpload = container.querySelector('#sb-doc-upload');
        if (docUpload) {
            docUpload.addEventListener('change', async () => {
                const file = docUpload.files[0];
                if (!file) return;
                const chatName = (getElements().chatSelect || document.getElementById('chat-select'))?.value;
                if (!chatName) return;
                const form = new FormData();
                form.append('file', file);
                try {
                    const resp = await fetch(`/api/chats/${encodeURIComponent(chatName)}/documents`, {
                        method: 'POST', body: form
                    });
                    if (resp.ok) {
                        const data = await resp.json();
                        ui.showToast(`Uploaded ${data.filename} (${data.chunks} chunks)`, 'success');
                        loadDocuments(container, chatName);
                    } else {
                        const err = await resp.json().catch(() => ({}));
                        ui.showToast(err.detail || 'Upload failed', 'error');
                    }
                } catch (e) {
                    ui.showToast('Upload failed', 'error');
                }
                docUpload.value = '';
            });
        }

        // Document delete handler (event delegation)
        const docList = container.querySelector('#sb-doc-list');
        if (docList) {
            docList.addEventListener('click', async e => {
                const btn = e.target.closest('.sb-doc-del');
                if (!btn) return;
                const filename = btn.dataset.filename;
                const chatName = (getElements().chatSelect || document.getElementById('chat-select'))?.value;
                if (!chatName || !filename) return;
                try {
                    const resp = await fetch(`/api/chats/${encodeURIComponent(chatName)}/documents/${encodeURIComponent(filename)}`, {
                        method: 'DELETE'
                    });
                    if (resp.ok) {
                        ui.showToast(`Removed ${filename}`, 'success');
                        loadDocuments(container, chatName);
                    }
                } catch (e) {
                    ui.showToast('Delete failed', 'error');
                }
            });
        }
    },

    async show() {
        if (_docClickHandler) document.addEventListener('click', _docClickHandler);
        if (_personaHandler) window.addEventListener('persona-loaded', _personaHandler);
        await steerOffGameChat();
        await refreshInitData();
        await loadSidebar();
    },

    hide() {
        if (_docClickHandler) document.removeEventListener('click', _docClickHandler);
        if (_personaHandler) window.removeEventListener('persona-loaded', _personaHandler);
    }
};

// Game/story sessions belong to their plugin surface (isolation ruling
// 2026-08-03 — renderChatDropdown already hides them from the picker).
// Refreshing or navigating into Chat while a game session is still the
// SERVER-active chat must not render it here: steer to the most recent
// regular chat instead (the list is updated_at DESC). No eligible target,
// or a turn mid-stream → leave things alone; the picker still hides it.
async function steerOffGameChat() {
    try {
        const data = await api.fetchChatList();
        const active = (data.chats || []).find(c => c.name === data.active_chat);
        if (!active || active.mode !== 'game') return;
        const target = (data.chats || []).find(c => c.mode !== 'game' && !c.archived);
        if (!target || getIsProc()) return;
        const chatSelect = getElements().chatSelect || document.getElementById('chat-select');
        if (!chatSelect) return;
        if (![...chatSelect.options].some(o => o.value === target.name)) {
            const opt = document.createElement('option');
            opt.value = opt.textContent = target.name;
            chatSelect.appendChild(opt);
        }
        chatSelect.value = target.name;
        await handleChatChange();
    } catch (e) {
        console.warn('[CHAT] game-chat steer failed:', e);
    }
}

function toggleSidebar(container) {
    const sidebar = container.querySelector('.chat-sidebar');
    if (!sidebar) return;
    const collapsed = sidebar.classList.toggle('collapsed');
    localStorage.setItem('sapphire-chat-sidebar', collapsed ? 'collapsed' : 'expanded');
}

async function loadDocuments(container, chatName) {
    const list = container.querySelector('#sb-doc-list');
    const badge = container.querySelector('#sb-doc-count');
    if (!list) return;
    try {
        const resp = await fetch(`/api/chats/${encodeURIComponent(chatName)}/documents`);
        if (!resp.ok) return;
        const data = await resp.json();
        const docs = data.documents || [];
        list.innerHTML = docs.map(d => {
            const fn = escapeHtml(d.filename);
            return `<div class="sb-doc-item">
                <span title="${fn} (${d.chunks} chunks)">${fn}</span>
                <button class="sb-doc-del" data-filename="${fn}" title="Remove">&times;</button>
            </div>`;
        }).join('');
        if (badge) {
            badge.textContent = docs.length;
            badge.style.display = docs.length ? '' : 'none';
        }
    } catch (e) {
        console.warn('Failed to load documents:', e);
    }
}

// ── Sidebar accordion open/closed memory ──────────────────────────────
// Stores the user's open/closed preference for each accordion in
// localStorage so it survives reloads. Plugin accordions are keyed by
// `data-plugin-accordion`; core accordions are keyed by their header
// label (which is stable across templates). Closed = absence from the
// stored map (smaller storage, default-closed for new accordions).
// 2026-04-30 — addresses "I keep forgetting to open the avatar."

// Accordion open-state persistence moved to shared/accordion.js (Phase 1,
// tmp/chat-surface-plan.md) — data-acc ids preserve the old key format.

async function _loadPluginAccordions(container, init) {
    const slot = container.querySelector('#sb-plugin-accordions');
    if (!slot) return;

    // Get plugin list with accordion declarations
    const enabledPlugins = new Set(init?.plugins_config?.enabled || []);
    let plugins = [];
    try {
        const resp = await fetch('/api/webui/plugins');
        if (resp.ok) {
            const data = await resp.json();
            plugins = (data.plugins || []).filter(p =>
                enabledPlugins.has(p.name) && p.sidebar_accordion
            );
        }
    } catch (e) { return; }

    // Clear previous plugin accordions
    slot.innerHTML = '';

    // Build DOM for all accordions first (synchronous, preserves order), then
    // fire HTML/script fetches in parallel. Previously awaited each plugin's
    // fetches sequentially which made sidebar load ~N*RTT on chat switch.
    const pending = [];
    for (const plugin of plugins) {
        const acc = plugin.sidebar_accordion;
        const section = document.createElement('div');
        section.className = 'sidebar-section sidebar-accordion';
        section.dataset.pluginAccordion = plugin.name;
        section.dataset.acc = `plugin:${plugin.name}`;   // accordion.js persistence id

        const header = document.createElement('div');
        header.className = 'sidebar-accordion-header';
        header.innerHTML = `<span class="accordion-arrow">&#x25B6;</span>` +
            `<span>${acc.icon || ''} ${acc.title || plugin.name}</span>`;

        const content = document.createElement('div');
        content.className = 'sidebar-accordion-content';
        content.style.display = 'none';

        section.appendChild(header);
        section.appendChild(content);
        slot.appendChild(section);

        // Sequence: HTML must land in content.innerHTML BEFORE the script's
        // init() runs — init typically queries content for DOM nodes that
        // come from the HTML. On first page load this races by luck (script
        // import is slower than HTML fetch); on revisit the module is cached
        // so import() returns instantly, beating the HTML, and init bails
        // out finding nothing. Avatar disappearing after tab-switch root
        // cause. 2026-05-13.
        let htmlReady = Promise.resolve();
        if (acc.content) {
            htmlReady = fetch(`/plugin-web/${plugin.name}/${acc.content}`)
                .then(r => r.ok ? r.text() : Promise.reject())
                .then(html => { content.innerHTML = html; })
                .catch(() => {
                    content.innerHTML = `<div class="sb-field" style="color:var(--error)">Failed to load</div>`;
                });
            pending.push(htmlReady);
        }
        if (acc.script) {
            pending.push(
                htmlReady
                    .then(() => import(`/plugin-web/${plugin.name}/${acc.script}`))
                    .then(mod => { if (mod.init) mod.init(content, plugin.name); })
                    .catch(e => console.warn(`[SIDEBAR] Failed to load accordion script for ${plugin.name}:`, e))
            );
        }
    }
    await Promise.all(pending);
}

let _sbPaintSeq = 0;

async function loadSidebar(overrideSettings = null, overrideChat = null) {
    const container = document.getElementById('view-chat');
    if (!container) return;

    const chatSelect = getElements().chatSelect || document.getElementById('chat-select');
    const chatName = chatSelect?.value;
    if (!chatName) return;
    const mySeq = ++_sbPaintSeq;

    // Paint VALUES straight from the caller's settings (the activate
    // response) when offered. Guard hard: an empty or mismatched override
    // falls through to the fetch path — a malformed activate response
    // (`result?.settings || {}`) would otherwise paint pure defaults that
    // the next debouncedSave WRITES to the chat (silent-default class).
    const useOverride = !!(overrideSettings && typeof overrideSettings === 'object'
        && Object.keys(overrideSettings).length && overrideChat === chatName);

    try {
        // Get init data first so we know which scope_declarations to fetch.
        // (Phase 2: scope fetches are no longer hardcoded — driven by /api/init.)
        const initDataPromise = getInitData();
        const initEarly = await initDataPromise;
        const scopeDeclarations = initEarly?.scope_declarations || [];

        const [settingsResp, initData, llmResp, scopeDataResp, spiceSetsResp, personasResp, ttsVoicesResp] = await Promise.allSettled([
            useOverride ? Promise.resolve({ settings: overrideSettings }) : api.getChatSettings(chatName),
            initDataPromise,
            fetch('/api/llm/providers').then(r => r.ok ? r.json() : null),
            fetchScopeData(scopeDeclarations),
            fetch('/api/spice-sets').then(r => r.ok ? r.json() : null),
            fetch('/api/personas').then(r => r.ok ? r.json() : null),
            fetch('/api/tts/voices').then(r => r.ok ? r.json() : null)
        ]);

        // Discard stale paints. The seq is the real guard — monotonic, so a
        // newer load always supersedes an older one even across A→B→A or two
        // concurrent loads for the SAME chat (the old name-only check was
        // blind to both). The name check stays as a belt.
        if (mySeq !== _sbPaintSeq) return;
        const chatNow = chatSelect?.value;
        if (chatNow !== chatName) {
            console.log(`[SIDEBAR] Chat changed during load (${chatName} → ${chatNow}), discarding`);
            return;
        }

        const settings = settingsResp.status === 'fulfilled' ? settingsResp.value.settings : {};
        ui.setCurrentPersona(settings.persona || null);
        const init = initData.status === 'fulfilled' ? initData.value : null;
        const llmData = llmResp.status === 'fulfilled' ? llmResp.value : null;
        const scopeFetchedData = scopeDataResp.status === 'fulfilled' ? scopeDataResp.value : {};
        const spiceSetsData = spiceSetsResp.status === 'fulfilled' ? spiceSetsResp.value : null;
        const personasData = personasResp.status === 'fulfilled' ? personasResp.value : null;
        const ttsVoicesData = ttsVoicesResp.status === 'fulfilled' ? ttsVoicesResp.value : null;
        personasList = personasData?.personas || [];
        defaultPersonaName = personasData?.default || init?.personas?.default || '';

        // Sync sidebar chat name from hidden select
        const selectedOpt = chatSelect?.options?.[chatSelect.selectedIndex];
        const sbName = container.querySelector('#sb-chat-name');
        if (sbName && selectedOpt) sbName.textContent = selectedOpt.text;

        // Eyeball painting moved WHOLLY to scene.js's status poll — it keys
        // on VAULT state now (the one-job toggle), which settings alone
        // can't know. Painting the chat flag here fought the poll.

        // Populate prompt dropdown (🗝 = vault entry while unlocked)
        const promptSel = container.querySelector('#sb-prompt');
        if (promptSel && init?.prompts?.list) {
            promptSel.innerHTML = init.prompts.list.map(p =>
                `<option value="${p.name}">${p.name.charAt(0).toUpperCase() + p.name.slice(1)}${p.vault ? ' \u{1F5DD}' : ''}</option>`
            ).join('');
            setSelect(promptSel, settings.prompt || 'sapphire');
            // Dangling vault name (locked): setSelect synthesized a bare
            // option — label it so the user knows the prompt is asleep,
            // not gone. Referenced names are the one surface allowed to
            // show while sealed (refs index, ruling C).
            const vrefs = init?.prompts?.vault_refs || {};
            const cur = settings.prompt;
            if (cur && cur in vrefs && !init.prompts.list.some(p => p.name === cur)) {
                const opt = [...promptSel.options].find(o => o.value === cur);
                if (opt) opt.textContent = `${cur} \u{1F5DD} (vault)`;
            }
        }

        // Populate toolset dropdown (exclude raw module entries)
        const toolsetSel = container.querySelector('#sb-toolset');
        if (toolsetSel && init?.toolsets?.list) {
            toolsetSel.innerHTML = init.toolsets.list
                .filter(t => t.type !== 'module')
                .map(t => `<option value="${t.name}">${t.name} (${t.function_count})</option>`)
                .join('');
            setSelect(toolsetSel, settings.toolset || settings.ability || 'all');
        }

        // Populate spice set dropdown (fresh from API, not cached init)
        const spiceSetSel = container.querySelector('#sb-spice-set');
        const spiceSets = spiceSetsData?.spice_sets || init?.spice_sets?.list || [];
        const currentSpiceSet = spiceSetsData?.current || init?.spice_sets?.current || 'default';
        if (spiceSetSel && spiceSets.length) {
            spiceSetSel.innerHTML = spiceSets
                .map(s => `<option value="${s.name}">${s.emoji ? s.emoji + ' ' : ''}${s.name} (${s.category_count})</option>`)
                .join('');
            setSelect(spiceSetSel, settings.spice_set || currentSpiceSet);
        }

        // Populate LLM dropdown
        if (llmData) {
            llmProviders = llmData.providers || [];
            llmMetadata = llmData.metadata || {};
            const llmSel = container.querySelector('#sb-llm-primary');
            if (llmSel) {
                const coreProv = llmProviders.filter(p => p.enabled && p.is_core);
                const customProv = llmProviders.filter(p => p.enabled && !p.is_core);
                let llmOpts = '<option value="auto">Auto</option><option value="none">None</option>';
                if (coreProv.length) {
                    llmOpts += coreProv.map(p =>
                        `<option value="${p.key}">${p.display_name}${p.is_local ? ' \uD83C\uDFE0' : ' \u2601\uFE0F'}</option>`
                    ).join('');
                }
                if (customProv.length) {
                    llmOpts += '<option disabled>\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500</option>';
                    llmOpts += customProv.map(p => {
                        const model = p.model ? ` (${p.model.split('/').pop()})` : '';
                        return `<option value="${p.key}">${p.display_name}${model}${p.is_local ? ' \uD83C\uDFE0' : ' \u2601\uFE0F'}</option>`;
                    }).join('');
                }
                llmSel.innerHTML = llmOpts;
                setSelect(llmSel, settings.llm_primary || 'auto');
                updateModelSelector(container, settings.llm_primary || 'auto', settings.llm_model || '');
            }
        }

        // Render + populate all scope dropdowns from /api/init scope_declarations.
        // This is the shared renderer used by sidebar, persona editor, and trigger editor.
        // Phase 2 replaced 9 hardcoded blocks (~100 lines) with this single call.
        const scopeContainer = container.querySelector('#sb-scope-dropdowns');
        if (scopeContainer && scopeDeclarations.length) {
            const enabledPlugins = new Set(init?.plugins_config?.enabled || []);
            const rendererOptions = {
                idPrefix: 'sb-',
                enabledPlugins,
                onNavigate: (navTarget, scopeValue) => {
                    // navTarget is e.g. "mind:memories" — the part after ':' is
                    // now its own view (Mind split into sibling views). Carry the
                    // selected scope so the view lands on the same scope.
                    const [group, tab] = navTarget.split(':');
                    if (scopeValue && scopeValue !== 'none') {
                        window._mindScope = scopeValue;
                    }
                    switchView(tab || group);
                },
            };
            renderScopeDropdowns(scopeContainer, scopeDeclarations, settings, rendererOptions);
            await populateScopeOptions(scopeContainer, scopeDeclarations, scopeFetchedData, settings, rendererOptions);
        }

        // Populate voice dropdown from active TTS provider
        const voiceSel = container.querySelector('#sb-voice');
        const voices = ttsVoicesData?.voices || [];
        const ttsProvider = ttsVoicesData?.provider || 'none';
        if (voiceSel) {
            if (voices.length) {
                voiceSel.innerHTML = voices.map(v =>
                    `<option value="${v.voice_id}">${v.name}${v.category ? ' (' + v.category + ')' : ''}</option>`
                ).join('');
            } else if (ttsProvider && ttsProvider !== 'none') {
                voiceSel.innerHTML = '<option value="">Default</option>';
            } else {
                voiceSel.innerHTML = '<option value="">No TTS active</option>';
            }
            // Build dynamic name map for easy mode
            _voiceNames = {};
            for (const v of voices) _voiceNames[v.voice_id] = v.name;
        }

        // Set remaining form values — fall back to provider default if stored voice isn't in list
        const desiredVoice = settings.voice || (ttsProvider === 'kokoro' ? 'af_heart' : '');
        setVal(container, '#sb-voice', desiredVoice);
        if (voiceSel && desiredVoice && voiceSel.value !== desiredVoice && ttsVoicesData?.default_voice) {
            setVal(container, '#sb-voice', ttsVoicesData.default_voice);
        }
        setVal(container, '#sb-pitch', settings.pitch || 0.98);
        setVal(container, '#sb-speed', settings.speed || 1.3);
        // Update speed slider range from provider limits
        _updateSpeedRange(container, ttsVoicesData);
        setVal(container, '#sb-spice-turns', settings.spice_turns || 3);
        setVal(container, '#sb-custom-context', settings.custom_context || '');
        setVal(container, '#sb-ghost-context', settings.ghost_context || '');

        // Toggle buttons
        setToggle(container, '#sb-spice-toggle', settings.spice_enabled !== false,
            `Spice \u00b7 ${settings.spice_turns || 3}`);
        setToggle(container, '#sb-datetime-toggle', settings.inject_datetime === true);

        // Trim color — edited in the Appearance modal, persisted from there
        _trimColor = settings.trim_color || '';
        applyTrimColor(_trimColor);

        // Scene background (resolved server-side: chat override > persona default > none)
        applyBackground(settings.background || '');

        // Per-chat motion (resolution in core/motions.js: chat > global pick > theme)
        setChatMotion(settings.motion || '');

        // Update labels
        const pitchLabel = container.querySelector('#sb-pitch-val');
        if (pitchLabel) pitchLabel.textContent = settings.pitch || 0.98;
        const speedLabel = container.querySelector('#sb-speed-val');
        if (speedLabel) speedLabel.textContent = settings.speed || 1.3;

        // Update slider fills
        const pitchSlider = container.querySelector('#sb-pitch');
        const speedSlider = container.querySelector('#sb-speed');
        if (pitchSlider) updateSliderFill(pitchSlider);
        if (speedSlider) updateSliderFill(speedSlider);

        renderPersonaStrip(container, settings, init);

        // RAG context level
        setVal(container, '#sb-rag-context', settings.rag_context || 'normal');

        // Load per-chat documents
        loadDocuments(container, chatName);

        // Inject plugin-registered accordions
        await _loadPluginAccordions(container, init);

        // Restore each accordion's open/closed state from localStorage.
        // Runs after plugin accordions are in the DOM so it covers core
        // AND plugin sections in one pass (initAccordions restores every
        // call; its click delegate binds once). 2026-04-30 / 2026-08-02.
        const sbRootNow = container.querySelector('.chat-sidebar');
        if (sbRootNow) initAccordions(sbRootNow, 'chat-sidebar');

        sidebarLoaded = true;
    } catch (e) {
        console.warn('Failed to load sidebar:', e);
    }
}

function debouncedSave(container) {
    clearTimeout(saveTimer);
    // CAPTURE the chat name NOW, before any chat switch. When the debounce fires
    // (or flushPendingSave runs during a chat switch), chatSelect.value may have
    // already moved to the new chat, but the save belongs to the OLD chat.
    const chatSelect = getElements().chatSelect || document.getElementById('chat-select');
    pendingSaveChatName = chatSelect?.value || null;
    // Vault hunt R8: clear the pair AT fire time. A fired timer that left
    // saveTimer/pendingSaveChatName standing let a later flushPendingSave
    // re-fire a full sidebar payload at a name captured chats ago.
    saveTimer = setTimeout(() => {
        saveTimer = null;
        const n = pendingSaveChatName;
        pendingSaveChatName = null;
        saveSettings(container, n);
    }, SAVE_DEBOUNCE);
}

/** Cancel any pending debounced save — called on chat switch to prevent cross-chat writes */
export function cancelPendingSave() {
    clearTimeout(saveTimer);
    saveTimer = null;
    pendingSaveChatName = null;
}

/** Flush any pending debounced save — fires the save synchronously for the OLD chat
 *  before a chat switch proceeds. Uses the chat name captured at debounce-schedule
 *  time, NOT the current chatSelect.value (which may already point at the new chat). */
export async function flushPendingSave() {
    if (!saveTimer) return;
    clearTimeout(saveTimer);
    saveTimer = null;
    const chatName = pendingSaveChatName;
    pendingSaveChatName = null;
    const container = document.getElementById('view-chat');
    if (container && chatName) {
        try { await saveSettings(container, chatName); }
        catch (e) { console.warn('Flush-pending save failed:', e); }
    }
}

function openAppearanceModal() {
    const current = document.getElementById('chatbg')?.dataset.scene || '';
    const globalTrim = localStorage.getItem('sapphire-trim') || '#4a9eff';
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `
        <div class="modal-base">
            <div class="modal-header"><h3>Appearance</h3><button class="close-btn modal-x" type="button">&times;</button></div>
            <div class="modal-body">
                <div class="appearance-accent-row">
                    <input type="color" id="appearance-trim" value="${escapeHtml(_trimColor || globalTrim)}" title="Accent color">
                    <button class="btn btn-secondary btn-sm" id="appearance-trim-reset" type="button">Reset</button>
                    <span class="appearance-accent-help" id="appearance-trim-help"></span>
                </div>
                <div id="scene-picker-mount"></div>
                <div class="scene-motion-sect">
                    <h4>Motion</h4>
                    <div id="scene-motion-row" class="motion-row"></div>
                    <div class="scene-motion-help">A per-chat animation. Default follows your pick in Settings &gt; Visual; a chat pick runs even when your system prefers reduced motion.</div>
                </div>
            </div>
            <div class="modal-footer"><button class="btn btn-secondary modal-close" type="button">Done</button></div>
        </div>`;
    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add('active'));  // .modal-overlay is display:none until .active
    const close = () => { overlay.classList.remove('active'); setTimeout(() => overlay.remove(), 300); };
    overlay.querySelector('.modal-x')?.addEventListener('click', close);
    overlay.querySelector('.modal-close')?.addEventListener('click', close);
    setupModalClose(overlay, close);

    // Accent: live-apply on every input tick, persist on change (a color
    // picker drag fires dozens of 'input' events — one PUT per commit).
    // Same direct-PUT path as scene/motion below; trim_color left the
    // sidebar's collectSettings, so ordinary sidebar saves can't clobber it.
    const trimInput = overlay.querySelector('#appearance-trim');
    const trimHelp = overlay.querySelector('#appearance-trim-help');
    const paintHelp = () => { trimHelp.textContent = _trimColor ? 'This chat\'s accent' : 'Default accent'; };
    const persistTrim = (color) => {
        _trimColor = color;
        applyTrimColor(color);
        paintHelp();
        const chatName = document.getElementById('chat-select')?.value;
        if (chatName) api.updateChatSettings(chatName, { trim_color: color })
            .catch(() => ui.showToast('Accent shown but not saved — the chat refused the write', 'error', 4000));
    };
    paintHelp();
    trimInput.addEventListener('input', () => applyTrimColor(trimInput.value));
    trimInput.addEventListener('change', () => persistTrim(trimInput.value));
    overlay.querySelector('#appearance-trim-reset').addEventListener('click', () => {
        trimInput.value = globalTrim;
        persistTrim('');
    });

    mountScenePicker(overlay.querySelector('#scene-picker-mount'), {
        current,
        onSelect: (name) => {
            // Apply live (instant preview behind the modal) + persist as a per-chat override.
            applyBackground(name);
            const chatName = document.getElementById('chat-select')?.value;
            // Optimistic apply, but never a SILENT persist failure — a vault
            // refusal here left the user seeing a scene that reverts on the
            // next chat load.
            if (chatName) api.updateChatSettings(chatName, { background: name })
                .catch(() => ui.showToast('Scene shown but not saved — the chat refused the write', 'error', 4000));
        }
    });
    _renderModalMotionRow(overlay);
}

// Per-chat motion cards in the scene modal. 'Default' clears the override
// (falls through to the global Settings > Visual pick / theme default).
// Registry is already loaded — main.js runs initMotions() at boot.
function _renderModalMotionRow(overlay) {
    const row = overlay.querySelector('#scene-motion-row');
    if (!row) return;
    const chatPick = chatMotionId();
    const cards = [{ id: '', name: 'Default' }, ...getMotions()].map(mo => {
        const active = (mo.id || '') === chatPick;
        return `<div class="motion-card${active ? ' active' : ''}" data-motion="${escapeHtml(mo.id)}" title="${escapeHtml(mo.description || '')}">${escapeHtml(mo.name)}</div>`;
    }).join('');
    row.innerHTML = cards;
    row.querySelectorAll('.motion-card').forEach(c => c.addEventListener('click', () => {
        const id = c.dataset.motion || '';
        // Apply live (visible behind the modal) + persist on the chat row.
        setChatMotion(id);
        const chatName = document.getElementById('chat-select')?.value;
        if (chatName) api.updateChatSettings(chatName, { motion: id })
            .catch(() => ui.showToast('Motion shown but not saved — the chat refused the write', 'error', 4000));
        _renderModalMotionRow(overlay);
    }));
}

async function saveSettings(container, chatNameOverride = null) {
    // Prefer the override (set by debouncedSave / flushPendingSave) over the live
    // chatSelect.value — the override is the chat the user was on when they made
    // the change, which may differ from the current chat if they switched fast.
    const chatSelect = getElements().chatSelect || document.getElementById('chat-select');
    const chatName = chatNameOverride || chatSelect?.value;
    if (!chatName) return;

    const settings = collectSettings(container);

    try {
        const result = await api.updateChatSettings(chatName, settings);
        updateSendButtonLLM(settings.llm_primary, settings.llm_model);

        // Sync toolset dropdown directly from save response.
        // The PUT response returns live toolset/function state so we update
        // the sidebar #sb-toolset dropdown here — no second API call, no race.
        // scene.js updateFuncs() targets abilityPill which doesn't exist in DOM.
        if (result?.toolset) {
            const toolsetSel = container.querySelector('#sb-toolset');
            if (toolsetSel) {
                const selected = toolsetSel.options[toolsetSel.selectedIndex];
                if (selected) {
                    const name = result.toolset.name || selected.value;
                    const total = (result.functions?.length || 0);
                    selected.textContent = `${name} (${total})`;
                }
            }
        }
    } catch (e) {
        console.warn('Auto-save failed:', e);
    }
}

function collectSettings(container) {
    // Pull scope values from the shared renderer's dropdowns.
    // Init data is cached after the first /api/init call, so getInitDataSync()
    // returns the same scope_declarations the renderer was built from.
    const scopeDecls = getInitDataSync()?.scope_declarations || [];
    const scopeContainer = container.querySelector('#sb-scope-dropdowns');
    const scopeValues = scopeContainer
        ? readScopeSettings(scopeContainer, scopeDecls, { idPrefix: 'sb-' })
        : {};

    return {
        prompt: getVal(container, '#sb-prompt'),
        toolset: getVal(container, '#sb-toolset'),
        spice_set: getVal(container, '#sb-spice-set') || 'default',
        voice: getVal(container, '#sb-voice'),
        pitch: parseFloat(getVal(container, '#sb-pitch')) || 0.98,
        speed: parseFloat(getVal(container, '#sb-speed')) || 1.3,
        spice_enabled: getToggle(container, '#sb-spice-toggle'),
        spice_turns: parseInt(getVal(container, '#sb-spice-turns')) || 3,
        inject_datetime: getToggle(container, '#sb-datetime-toggle'),
        custom_context: getVal(container, '#sb-custom-context'),
        ghost_context: getVal(container, '#sb-ghost-context'),
        llm_primary: getVal(container, '#sb-llm-primary') || 'auto',
        llm_model: getSelectedModel(container),
        ...scopeValues,
        rag_context: getVal(container, '#sb-rag-context') || 'normal'
    };
}

function updateModelSelector(container, providerKey, currentModel) {
    const group = container.querySelector('#sb-model-group');
    const customGroup = container.querySelector('#sb-model-custom-group');
    const select = container.querySelector('#sb-llm-model');
    const custom = container.querySelector('#sb-llm-model-custom');

    if (group) group.style.display = 'none';
    if (customGroup) customGroup.style.display = 'none';

    if (providerKey === 'auto' || providerKey === 'none' || !providerKey) return;

    const meta = llmMetadata[providerKey];
    const conf = llmProviders.find(p => p.key === providerKey);

    if (meta?.model_options && Object.keys(meta.model_options).length > 0) {
        const defaultModel = conf?.model || '';
        const defaultLabel = defaultModel ?
            `Default (${meta.model_options[defaultModel] || defaultModel})` : 'Default';

        select.innerHTML = `<option value="">${defaultLabel}</option>` +
            Object.entries(meta.model_options).map(([k, v]) =>
                `<option value="${k}" ${k === currentModel ? 'selected' : ''}>${v}</option>`
            ).join('');

        if (currentModel && !meta.model_options[currentModel]) {
            select.innerHTML += `<option value="${currentModel}" selected>${currentModel}</option>`;
        }
        if (group) group.style.display = '';
    } else {
        // Custom/generic providers — free-text model input
        if (custom) custom.value = currentModel || '';
        if (customGroup) customGroup.style.display = '';
    }
}

function getSelectedModel(container) {
    const provider = getVal(container, '#sb-llm-primary');
    if (provider === 'auto' || provider === 'none') return '';

    const group = container.querySelector('#sb-model-group');
    if (group && group.style.display !== 'none') {
        return getVal(container, '#sb-llm-model') || '';
    }

    const customGroup = container.querySelector('#sb-model-custom-group');
    if (customGroup && customGroup.style.display !== 'none') {
        return (container.querySelector('#sb-llm-model-custom')?.value || '').trim();
    }
    return '';
}

// === Faces strip ===

function initPersonaStrip(container) {
    // ↗ anchored top-right of the strip → full roster
    container.querySelector('#sb-personas-goto')?.addEventListener('click', () => switchView('personas'));
    container.querySelector('#sb-persona-grid')?.addEventListener('click', async e => {
        const cell = e.target.closest('.sb-pgrid-cell');
        if (!cell) return;

        // "+ New" cell
        if (cell.dataset.action === 'new') {
            const name = prompt('Name for the new persona:');
            if (!name?.trim()) return;
            try {
                const res = await createFromChat(name.trim());
                if (res?.name) {
                    ui.showToast(`Persona "${res.name}" created`, 'success');
                    window.dispatchEvent(new CustomEvent('persona-select', { detail: { name: res.name } }));
                    switchView('personas');
                }
            } catch (err) {
                ui.showToast(err.message || 'Failed', 'error');
            }
            return;
        }

        const pName = cell.dataset.name;
        if (!pName) return;
        try {
            await loadPersona(pName);
            ui.showToast(`Loaded: ${pName}`, 'success');
            updateScene();
            await loadSidebar();
        } catch (e) {
            ui.showToast(e.message || 'Failed', 'error');
        }
    });
}

// Dynamic voice name map — populated from /api/tts/voices in loadSidebar()
let _voiceNames = {};

function _updateSpeedRange(container, ttsData) {
    if (!ttsData) return;
    const slider = container.querySelector('#sb-speed');
    if (!slider) return;
    const lo = ttsData.speed_min ?? 0.5;
    const hi = ttsData.speed_max ?? 2.5;
    slider.min = lo;
    slider.max = hi;
    // Clamp current value into new range
    const cur = parseFloat(slider.value);
    if (cur < lo) slider.value = lo;
    else if (cur > hi) slider.value = hi;
    updateSliderFill(slider);
    const label = container.querySelector('#sb-speed-val');
    if (label) label.textContent = slider.value;
}

async function refreshVoiceDropdown() {
    const container = document.getElementById('view-chat');
    if (!container) return;
    const voiceSel = container.querySelector('#sb-voice');
    if (!voiceSel) return;
    try {
        const resp = await fetch('/api/tts/voices');
        if (!resp.ok) return;
        const data = await resp.json();
        const voices = data.voices || [];
        const currentVoice = voiceSel.value;
        if (voices.length) {
            voiceSel.innerHTML = voices.map(v =>
                `<option value="${v.voice_id}">${v.name}${v.category ? ' (' + v.category + ')' : ''}</option>`
            ).join('');
        } else {
            voiceSel.innerHTML = '<option value="">No TTS active</option>';
        }
        _voiceNames = {};
        for (const v of voices) _voiceNames[v.voice_id] = v.name;
        // Keep current voice if it exists in new list, otherwise use provider default
        let voiceChanged = false;
        if (voices.some(v => v.voice_id === currentVoice)) {
            voiceSel.value = currentVoice;
        } else if (data.default_voice) {
            voiceSel.value = data.default_voice;
            voiceChanged = true;
        }
        // Update speed slider range for new provider
        _updateSpeedRange(container, data);
        // Save the new voice to chat so backend TTS uses it immediately
        if (voiceChanged) {
            // Vault hunt R8: capture the name like debouncedSave does — an
            // armed timer WITHOUT pendingSaveChatName desynced the pair, so
            // a chat switch inside the 100ms window flushed a full foreign
            // payload onto whatever stale name the pair still held.
            if (saveTimer) clearTimeout(saveTimer);
            const chatSel = document.getElementById('chat-select');
            pendingSaveChatName = chatSel?.value || null;
            saveTimer = setTimeout(() => {
                saveTimer = null;
                const n = pendingSaveChatName;
                pendingSaveChatName = null;
                saveSettings(container, n);
            }, 100);
        }
    } catch (e) {
        console.warn('[chat] Failed to refresh voice dropdown:', e);
    }
}

function renderPersonaStrip(container, settings, init) {
    const gridEl = container.querySelector('#sb-persona-grid');
    if (!gridEl) return;
    const personaName = settings.persona;

    // Favorites curate the strip: PERSONA_FAVORITES (ordered) picks + orders
    // the faces. Empty list = show all (fresh installs unchanged). The ⭐
    // default and the ACTIVE persona always show even when unfavorited.
    const favs = init?.settings?.PERSONA_FAVORITES || [];
    let shown = personasList;
    if (favs.length) {
        const byName = new Map(personasList.map(p => [p.name, p]));
        shown = favs.map(n => byName.get(n)).filter(Boolean);
        if (defaultPersonaName && byName.has(defaultPersonaName)
                && !shown.some(p => p.name === defaultPersonaName)) {
            shown.unshift(byName.get(defaultPersonaName));
        }
        if (personaName && byName.has(personaName)
                && !shown.some(p => p.name === personaName)) {
            shown.push(byName.get(personaName));
        }
    }

    gridEl.innerHTML = shown.map(p => `
        <div class="sb-pgrid-cell${p.name === personaName ? ' active' : ''}" data-name="${p.name}" title="${escapeHtml(p.name)} — drag to reorder" draggable="true">
            ${avatarImg(p.name, p.trim_color, 'sb-pgrid-avatar', p.avatar)}${p.name === defaultPersonaName ? '<span class="sb-pgrid-default" title="Default persona">&#x2B50;</span>' : ''}
            <span class="sb-pgrid-name">${escapeHtml(p.name)}</span>
        </div>
    `).join('') + `
        <div class="sb-pgrid-cell sb-pgrid-new" data-action="new" title="New persona from this chat">
            <span class="sb-pgrid-new-icon">+</span>
            <span class="sb-pgrid-name">New...</span>
        </div>`;

    _bindStripDnD(gridEl);
    // Keep the active face in view
    gridEl.querySelector('.sb-pgrid-cell.active')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

// Desktop drag-to-reorder (Fork D: v1 is DnD-only — phones inherit the order).
// On drop, the RENDERED order becomes the new PERSONA_FAVORITES — in show-all
// mode a drag adopts the full roster in your order; stars later prune it.
let _stripDragName = null;
let _stripDragFrom = null;
function _bindStripDnD(gridEl) {
    gridEl.querySelectorAll('.sb-pgrid-cell[data-name]').forEach(cell => {
        // Browsers native-drag <img> — that would hijack the cell drag
        cell.querySelector('img')?.setAttribute('draggable', 'false');
        cell.addEventListener('dragstart', e => {
            _stripDragName = cell.dataset.name;
            _stripDragFrom = [...gridEl.querySelectorAll('.sb-pgrid-cell[data-name]')].map(c => c.dataset.name).join(',');
            cell.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            try { e.dataTransfer.setData('text/plain', _stripDragName); } catch { /* IE-era quirk */ }
        });
    });
    if (gridEl.dataset.dndBound) return;   // grid-level listeners survive re-renders
    gridEl.dataset.dndBound = '1';
    gridEl.addEventListener('dragover', e => {
        if (!_stripDragName) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        const dragging = gridEl.querySelector('.sb-pgrid-cell.dragging');
        if (!dragging) return;
        const cells = [...gridEl.querySelectorAll('.sb-pgrid-cell[data-name]:not(.dragging)')];
        const after = cells.find(c => {
            const r = c.getBoundingClientRect();
            return e.clientX < r.left + r.width / 2;
        });
        gridEl.insertBefore(dragging, after || gridEl.querySelector('.sb-pgrid-new'));
    });
    gridEl.addEventListener('drop', e => { if (_stripDragName) e.preventDefault(); });
    gridEl.addEventListener('dragend', async () => {
        gridEl.querySelector('.sb-pgrid-cell.dragging')?.classList.remove('dragging');
        if (!_stripDragName) return;
        _stripDragName = null;
        const names = [...gridEl.querySelectorAll('.sb-pgrid-cell[data-name]')].map(c => c.dataset.name);
        if (names.join(',') === _stripDragFrom) return;   // no-op drag: don't adopt the roster
        try {
            await updateSettingsBatch({ PERSONA_FAVORITES: names });
            await refreshInitData();
        } catch (e) {
            ui.showToast('Order shown but not saved', 'error');
        }
    });
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
}

// Helpers
function getVal(c, sel) { return c.querySelector(sel)?.value || ''; }
function setVal(c, sel, v) { const el = c.querySelector(sel); if (el) el.value = v; }
function setSelect(sel, v) {
    sel.value = v;
    if (sel.selectedIndex === -1) {
        // The saved value isn't among the options (e.g. a stale/missing list).
        // Inject it as a synthetic option rather than silently snapping to
        // option 0 — that snap is the "wrong-prompt" class of bug. Empty/null
        // values keep the old fall-to-first behavior.
        if (v != null && v !== '') {
            const opt = document.createElement('option');
            opt.value = v;
            opt.textContent = v;
            sel.insertBefore(opt, sel.firstChild);
            sel.value = v;
        } else if (sel.options.length) {
            sel.selectedIndex = 0;
        }
    }
}
function getChecked(c, sel) { return c.querySelector(sel)?.checked || false; }
function setChecked(c, sel, v) { const el = c.querySelector(sel); if (el) el.checked = v; }
function getToggle(c, sel) { return c.querySelector(sel)?.dataset.active === 'true'; }
function setToggle(c, sel, active, label) {
    const el = c.querySelector(sel);
    if (!el) return;
    el.dataset.active = active;
    el.classList.toggle('active', active);
    if (label) el.textContent = label;
}

// Sets --pct on slider; CSS handles the gradient rendering.
function updateSliderFill(slider) {
    const min = parseFloat(slider.min) || 0;
    const max = parseFloat(slider.max) || 100;
    const pct = ((parseFloat(slider.value) - min) / (max - min)) * 100;
    slider.style.setProperty('--pct', `${pct}%`);
}
