// views/chat.js - Chat view module with settings sidebar
import * as api from '../api.js';
import * as ui from '../ui.js';
import * as eventBus from '../core/event-bus.js';
import { getElements, getIsProc } from '../core/state.js';
import { updateScene, updateSendButtonLLM } from '../features/scene.js';
import { applyTrimColor } from '../features/chat-settings.js';
import { handleNewChat, handleDeleteChat, handleChatChange } from '../features/chat-manager.js';
import { getInitData, refreshInitData, getInitDataSync } from '../shared/init-data.js';
import { switchView } from '../core/router.js';
import { loadPersona, createFromChat, avatarImg, avatarFallback, avatarUrl } from '../shared/persona-api.js';
import { initAgentStatus } from '../features/agent-status.js';
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
            chatSelect.addEventListener('chat-activated', () => loadSidebar());
            chatSelect.addEventListener('chat-list-ready', () => loadSidebar());
        }

        // Refresh toolset dropdown count when tools change (e.g. tool_load)
        eventBus.on(eventBus.Events.TOOLSET_CHANGED, async () => {
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
                toolsetSel.value = currentVal;
            }
        });

        // Refresh voice dropdown when TTS provider changes
        eventBus.on('settings_changed', (data) => {
            if (data?.key === 'TTS_PROVIDER') refreshVoiceDropdown();
            if (data?.key === 'LLM_PROVIDERS' || data?.key === 'LLM_CUSTOM_PROVIDERS') loadSidebar();
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
        eventBus.on(eventBus.Events.PROMPT_CHANGED, (data) => {
            if (data?.action === 'loaded') return;  // side effect, not user-initiated
            loadSidebar();
        });
        eventBus.on(eventBus.Events.PROMPT_DELETED, () => loadSidebar());

        // Refresh spice dropdown when spice sets change
        eventBus.on(eventBus.Events.SPICE_CHANGED, () => loadSidebar());

        // Refresh sidebar scope dropdowns when scopes are created/deleted in
        // the Mind view. Without this, users see stale options until a full
        // page refresh — or worse, select a scope in the sidebar that the
        // backend no longer knows about and silently fall through to 'default'.
        eventBus.on('scope_changed', () => loadSidebar());

        // Refresh sidebar (incl. scope dropdowns) when a plugin is toggled.
        // Plugin scopes are only shown when the owning plugin is enabled, so a
        // toggle changes which dropdowns should be visible. Also refreshes init
        // data so newly-loaded plugin scope_declarations land.
        document.addEventListener('sapphire:plugin_toggled', async () => {
            try { await refreshInitData(); } catch (e) { /* fail-soft */ }
            loadSidebar();
        });

        // Accordion headers in sidebar (event delegation — handles core + plugin accordions)
        const sbFull = container.querySelector('.sb-full-content');
        if (sbFull) sbFull.addEventListener('click', e => {
            const header = e.target.closest('.sidebar-accordion-header');
            if (!header) return;
            const content = header.nextElementSibling;
            const open = header.classList.toggle('open');
            content.style.display = open ? 'block' : 'none';
        });

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
                    // "New Private" button creates a private chat
                    const privBtn = e.target.closest('[data-action="new-private"]');
                    if (privBtn) {
                        sbPicker.classList.remove('open');
                        const name = prompt('Private chat name:');
                        if (!name?.trim()) return;
                        try {
                            const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                            const res = await fetch('/api/chats/private', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
                                body: JSON.stringify({ name: name.trim() })
                            });
                            if (res.ok) {
                                const { populateChatDropdown, handleChatChange } = await import('../features/chat-manager.js');
                                await populateChatDropdown();
                                await handleChatChange();
                            }
                        } catch (err) { console.error('Failed to create private chat:', err); }
                        return;
                    }

                    const item = e.target.closest('.chat-picker-item');
                    if (!item) return;
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
                    await handleChatChange();
                    await loadSidebar();
                });
            }
        }

        // Sidebar new/delete chat
        container.querySelector('#sb-new-chat')?.addEventListener('click', async () => {
            await handleNewChat();
            await loadSidebar();
        });
        container.querySelector('#sb-delete-chat')?.addEventListener('click', async () => {
            await handleDeleteChat();
            await loadSidebar();
        });

        // Close sidebar picker on outside click (added/removed in show/hide)
        _docClickHandler = e => {
            if (!e.target.closest('#sb-chat-picker')) {
                container.querySelector('#sb-chat-picker')?.classList.remove('open');
            }
        };

        // Toggle buttons (Spice, Date/Time)
        container.querySelectorAll('.sb-toggle').forEach(btn => {
            btn.addEventListener('click', () => {
                const active = btn.dataset.active !== 'true';
                btn.dataset.active = active;
                btn.classList.toggle('active', active);
                debouncedSave(container);
            });
        });

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
                if (el.id === 'sb-trim-color') {
                    el.dataset.cleared = 'false';
                    applyTrimColor(el.value);
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

        // Accent circle: double-click to reset to global default
        const accentCircle = container.querySelector('#sb-trim-color');
        if (accentCircle) {
            accentCircle.addEventListener('dblclick', () => {
                const globalTrim = localStorage.getItem('sapphire-trim') || '#4a9eff';
                accentCircle.value = globalTrim;
                accentCircle.dataset.cleared = 'true';
                applyTrimColor('');
                debouncedSave(container);
            });
        }

        // "Go to Mind" buttons are now wired by the shared/scope-dropdowns.js renderer
        // via the onNavigate callback in loadSidebar(). Don't bind here at init() time —
        // the buttons don't exist in the DOM yet (rendered dynamically with each loadSidebar).

        // "Go to view" buttons — navigate to Prompts/Toolsets with selection
        container.querySelectorAll('.sb-goto-view').forEach(btn => {
            btn.addEventListener('click', () => {
                const selectId = btn.dataset.select;
                const val = selectId && container.querySelector(`#${selectId}`)?.value;
                if (val) window._viewSelect = val;
                switchView(btn.dataset.view);
            });
        });

        // Sidebar mode tabs (Easy/Full)
        initSidebarModes(container);

        // Listen for persona-loaded events (added/removed in show/hide)
        _personaHandler = () => loadSidebar();

        // Save As New Persona button
        const saveAsPersonaBtn = container.querySelector('#sb-save-as-persona');
        if (saveAsPersonaBtn) {
            saveAsPersonaBtn.addEventListener('click', async () => {
                const name = prompt('Name for the new persona:');
                if (!name?.trim()) return;
                try {
                    const res = await createFromChat(name.trim());
                    if (res?.name) {
                        ui.showToast(`Persona "${res.name}" created`, 'success');
                    } else {
                        ui.showToast(res?.detail || 'Failed to create persona', 'error');
                    }
                } catch (e) {
                    ui.showToast(e.message || 'Failed', 'error');
                }
            });
        }

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
        await refreshInitData();
        await loadSidebar();
    },

    hide() {
        if (_docClickHandler) document.removeEventListener('click', _docClickHandler);
        if (_personaHandler) window.removeEventListener('persona-loaded', _personaHandler);
    }
};

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

    for (const plugin of plugins) {
        const acc = plugin.sidebar_accordion;
        const section = document.createElement('div');
        section.className = 'sidebar-section sidebar-accordion';
        section.dataset.pluginAccordion = plugin.name;

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

        // Load HTML content from plugin web dir
        if (acc.content) {
            try {
                const htmlResp = await fetch(`/plugin-web/${plugin.name}/${acc.content}`);
                if (htmlResp.ok) content.innerHTML = await htmlResp.text();
            } catch (e) {
                content.innerHTML = `<div class="sb-field" style="color:var(--error)">Failed to load</div>`;
            }
        }

        // Load + init JS module (re-inits on each sidebar reload — module is cached by browser)
        if (acc.script) {
            try {
                const mod = await import(`/plugin-web/${plugin.name}/${acc.script}`);
                if (mod.init) mod.init(content, plugin.name);
            } catch (e) {
                console.warn(`[SIDEBAR] Failed to load accordion script for ${plugin.name}:`, e);
            }
        }
    }
}

async function loadSidebar() {
    const container = document.getElementById('view-chat');
    if (!container) return;

    const chatSelect = getElements().chatSelect || document.getElementById('chat-select');
    const chatName = chatSelect?.value;
    if (!chatName) return;

    try {
        // Get init data first so we know which scope_declarations to fetch.
        // (Phase 2: scope fetches are no longer hardcoded — driven by /api/init.)
        const initDataPromise = getInitData();
        const initEarly = await initDataPromise;
        const scopeDeclarations = initEarly?.scope_declarations || [];

        const [settingsResp, initData, llmResp, scopeDataResp, spiceSetsResp, personasResp, ttsVoicesResp, toolsetCurrentResp] = await Promise.allSettled([
            api.getChatSettings(chatName),
            initDataPromise,
            fetch('/api/llm/providers').then(r => r.ok ? r.json() : null),
            fetchScopeData(scopeDeclarations),
            fetch('/api/spice-sets').then(r => r.ok ? r.json() : null),
            fetch('/api/personas').then(r => r.ok ? r.json() : null),
            fetch('/api/tts/voices').then(r => r.ok ? r.json() : null),
            fetch('/api/toolsets/current').then(r => r.ok ? r.json() : null)
        ]);

        // Guard: if chat changed while fetching, discard stale results
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

        // Populate prompt dropdown
        const promptSel = container.querySelector('#sb-prompt');
        if (promptSel && init?.prompts?.list) {
            promptSel.innerHTML = init.prompts.list.map(p =>
                `<option value="${p.name}">${p.name.charAt(0).toUpperCase() + p.name.slice(1)}</option>`
            ).join('');
            setSelect(promptSel, settings.prompt || 'sapphire');
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
                    // navTarget is e.g. "mind:memories" — split into view and tab
                    const [view, tab] = navTarget.split(':');
                    if (tab) window._mindTab = tab;
                    // Carry the currently-selected scope value into the Mind view
                    // so it lands on the same scope the user was looking at.
                    if (scopeValue && scopeValue !== 'none') {
                        window._mindScope = scopeValue;
                    }
                    if (view) switchView(view);
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

        // Toggle buttons
        setToggle(container, '#sb-spice-toggle', settings.spice_enabled !== false,
            `Spice \u00b7 ${settings.spice_turns || 3}`);
        setToggle(container, '#sb-datetime-toggle', settings.inject_datetime === true);

        // Trim color
        const trimInput = container.querySelector('#sb-trim-color');
        if (trimInput) {
            if (settings.trim_color) {
                trimInput.value = settings.trim_color;
                trimInput.dataset.cleared = 'false';
            } else {
                trimInput.value = localStorage.getItem('sapphire-trim') || '#4a9eff';
                trimInput.dataset.cleared = 'true';
            }
            applyTrimColor(settings.trim_color || '');
        }

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

        const firstTab = container.querySelector('.sb-mode-tab[data-mode="easy"]');
        if (firstTab) firstTab.textContent = 'Persona';
        updateEasyMode(container, settings, init);

        // RAG context level
        setVal(container, '#sb-rag-context', settings.rag_context || 'normal');

        // Load per-chat documents
        loadDocuments(container, chatName);

        // Inject plugin-registered accordions
        await _loadPluginAccordions(container, init);

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
    saveTimer = setTimeout(() => saveSettings(container, pendingSaveChatName), SAVE_DEBOUNCE);
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
    const trimInput = container.querySelector('#sb-trim-color');
    const trimColor = trimInput?.dataset.cleared === 'true' ? '' : (trimInput?.value || '');

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
        llm_primary: getVal(container, '#sb-llm-primary') || 'auto',
        llm_model: getSelectedModel(container),
        trim_color: trimColor,
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

// === Easy/Full sidebar mode ===

function initSidebarModes(container) {
    const tabs = container.querySelectorAll('.sb-mode-tab');
    const easyContent = container.querySelector('.sb-easy-content');
    const fullContent = container.querySelector('.sb-full-content');
    if (!tabs.length || !easyContent || !fullContent) return;

    // Restore saved mode
    const saved = localStorage.getItem('sapphire-sidebar-mode') || 'full';
    setSidebarMode(container, saved);

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const mode = tab.dataset.mode;
            setSidebarMode(container, mode);
            localStorage.setItem('sapphire-sidebar-mode', mode);
        });
    });

    // Easy mode persona grid clicks
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

    // Easy mode detail: accordion toggles, nav links, edit button (delegated, bound once)
    container.querySelector('#sb-persona-detail')?.addEventListener('click', e => {
        // Nav links inside accordion headers
        const navLink = e.target.closest('.sb-pdetail-acc-link');
        if (navLink) {
            e.stopPropagation();
            const view = navLink.dataset.nav;
            if (view) switchView(view);
            return;
        }
        const header = e.target.closest('.sb-pdetail-acc-header');
        if (header) {
            const content = header.nextElementSibling;
            const open = header.classList.toggle('open');
            content.style.display = open ? '' : 'none';
            return;
        }
        if (e.target.closest('.sb-pdetail-edit')) {
            const name = container.querySelector('.sb-pdetail-name')?.textContent?.trim();
            if (name) window._pendingPersonaSelect = name;
            switchView('personas');
        }
    });
}

function setSidebarMode(container, mode) {
    const easyContent = container.querySelector('.sb-easy-content');
    const fullContent = container.querySelector('.sb-full-content');
    if (!easyContent || !fullContent) return;

    easyContent.style.display = mode === 'easy' ? '' : 'none';
    fullContent.style.display = mode === 'full' ? '' : 'none';

    container.querySelectorAll('.sb-mode-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.mode === mode);
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
            if (saveTimer) clearTimeout(saveTimer);
            saveTimer = setTimeout(() => saveSettings(container), 100);
        }
    } catch (e) {
        console.warn('[chat] Failed to refresh voice dropdown:', e);
    }
}

function updateEasyMode(container, settings, init) {
    const gridEl = container.querySelector('#sb-persona-grid');
    const detailEl = container.querySelector('#sb-persona-detail');
    const personaName = settings.persona;

    // Build persona grid
    if (gridEl) {
        gridEl.innerHTML = personasList.map(p => `
            <div class="sb-pgrid-cell${p.name === personaName ? ' active' : ''}" data-name="${p.name}">
                ${avatarImg(p.name, p.trim_color, 'sb-pgrid-avatar', p.avatar)}
                <span class="sb-pgrid-name">${escapeHtml(p.name)}${p.name === defaultPersonaName ? ' &#x2B50;' : ''}</span>
            </div>
        `).join('') + `
            <div class="sb-pgrid-cell sb-pgrid-new" data-action="new">
                <span class="sb-pgrid-new-icon">+</span>
                <span class="sb-pgrid-name">New...</span>
            </div>`;
    }

    // Build detail section
    if (!detailEl) return;
    if (!personaName) {
        detailEl.innerHTML = '<div class="sb-pdetail-empty">No persona loaded</div>';
        return;
    }

    // Look up prompt preset components
    const presets = init?.prompts?.presets || {};
    const presetData = presets[settings.prompt] || {};
    const pretty = s => s ? s.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) : 'None';

    // Prompt pieces
    const promptRows = ['character', 'location', 'relationship', 'goals', 'format', 'scenario']
        .filter(k => presetData[k] && presetData[k] !== 'none')
        .map(k => `<div class="sb-pdetail-row"><span>${k}</span><span>${pretty(presetData[k])}</span></div>`)
        .join('') || '<div class="sb-pdetail-row"><span>preset</span><span>' + pretty(settings.prompt) + '</span></div>';

    const extras = (presetData.extras || []).map(pretty);
    const emotions = (presetData.emotions || []).map(pretty);

    // Build tools list grouped by module
    const toolsetName = settings.toolset || 'all';
    const tsData = (init?.toolsets?.list || []).find(t => t.name === toolsetName);
    const enabledFuncs = new Set(tsData?.functions || []);
    const modules = init?.functions?.modules || {};
    let toolsHtml = `<div class="sb-pdetail-row"><span>active</span><span>${pretty(toolsetName)}</span></div>`;
    const moduleEntries = Object.entries(modules)
        .map(([mod, info]) => {
            const active = (info.functions || []).filter(f => enabledFuncs.has(f.name));
            return [mod, info, active];
        })
        .filter(([, , active]) => active.length > 0)
        .sort(([a], [b]) => a.localeCompare(b));
    if (moduleEntries.length) {
        toolsHtml += '<div class="sb-pdetail-tools">';
        for (const [mod, info, active] of moduleEntries) {
            const emoji = info.emoji || '\u{1F527}';
            toolsHtml += `<div class="sb-pdetail-tool-group"><span class="sb-pdetail-tool-mod">${emoji} ${pretty(mod)}</span>`;
            toolsHtml += active.map(f => `<span class="sb-pdetail-tool">${f.name.replace(/_/g, ' ')}</span>`).join('');
            toolsHtml += '</div>';
        }
        toolsHtml += '</div>';
    }

    // Build detail HTML
    const activePd = personasList.find(p => p.name === personaName);
    detailEl.innerHTML = `
        <div class="sb-pdetail-header">
            ${activePd ? avatarImg(activePd.name, activePd.trim_color, 'sb-pdetail-avatar', activePd.avatar) : ''}
            <div class="sb-pdetail-info">
                <span class="sb-pdetail-name">${escapeHtml(personaName)}</span>
                <span class="sb-pdetail-tagline" id="sb-pdetail-tagline"></span>
            </div>
            <button class="sb-pdetail-edit" title="Edit persona" data-view="personas">\u270E</button>
        </div>
        ${easyAccordion('Prompt', `
            ${promptRows}
            ${extras.length ? `<div class="sb-pdetail-wrap-row"><span>extras</span><span>${extras.join(', ')}</span></div>` : ''}
            ${emotions.length ? `<div class="sb-pdetail-wrap-row"><span>emotions</span><span>${emotions.join(', ')}</span></div>` : ''}
        `, { desc: 'Character & scenario', view: 'prompts' })}
        ${easyAccordion('Toolset', toolsHtml, { desc: 'AI capabilities', view: 'toolsets' })}
        ${easyAccordion('Spice', `
            <div class="sb-pdetail-row"><span>set</span><span>${pretty(settings.spice_set)}</span></div>
            <div class="sb-pdetail-row"><span>enabled</span><span>${settings.spice_enabled !== false ? 'Yes' : 'No'}</span></div>
            <div class="sb-pdetail-row"><span>turns</span><span>${settings.spice_turns || 3}</span></div>
        `, { desc: 'Style & flavor', view: 'spices' })}
        ${easyAccordion('TTS', `
            <div class="sb-pdetail-row"><span>voice</span><span>${_voiceNames[settings.voice] || settings.voice || 'Heart'}</span></div>
            <div class="sb-pdetail-row"><span>pitch</span><span>${settings.pitch || 0.98}</span></div>
            <div class="sb-pdetail-row"><span>speed</span><span>${settings.speed || 1.3}</span></div>
        `, { desc: 'Voice synthesis' })}
        ${easyAccordion('Mind', `
            <div class="sb-pdetail-row"><span>memory</span><span>${pretty(settings.memory_scope)}</span></div>
            <div class="sb-pdetail-row"><span>goals</span><span>${pretty(settings.goal_scope)}</span></div>
            <div class="sb-pdetail-row"><span>knowledge</span><span>${pretty(settings.knowledge_scope)}</span></div>
            <div class="sb-pdetail-row"><span>people</span><span>${pretty(settings.people_scope)}</span></div>
        `, { desc: 'Memory & knowledge' })}
        ${easyAccordion('Model', `
            <div class="sb-pdetail-row"><span>provider</span><span>${pretty(settings.llm_primary)}</span></div>
            ${settings.llm_model ? `<div class="sb-pdetail-row"><span>model</span><span>${settings.llm_model}</span></div>` : ''}
        `, { desc: 'LLM backend' })}
    `;

    // Fetch tagline
    fetch(`/api/personas/${encodeURIComponent(personaName)}`)
        .then(r => r.ok ? r.json() : null)
        .then(p => {
            const el = container.querySelector('#sb-pdetail-tagline');
            if (p?.tagline && el) el.textContent = p.tagline;
        })
        .catch(() => {});
}

function easyAccordion(title, content, opts = {}) {
    const desc = opts.desc ? `<span class="sb-pdetail-acc-desc">${opts.desc}</span>` : '';
    const link = opts.view ? `<span class="sb-pdetail-acc-link" data-nav="${opts.view}">\u2197</span>` : '';
    return `
        <div class="sb-pdetail-acc">
            <div class="sb-pdetail-acc-header"><span class="accordion-arrow">\u25B6</span> ${title}${desc}${link}</div>
            <div class="sb-pdetail-acc-content" style="display:none">${content}</div>
        </div>`;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
}

// Helpers
function getVal(c, sel) { return c.querySelector(sel)?.value || ''; }
function setVal(c, sel, v) { const el = c.querySelector(sel); if (el) el.value = v; }
function setSelect(sel, v) { sel.value = v; if (sel.selectedIndex === -1 && sel.options.length) sel.selectedIndex = 0; }
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
