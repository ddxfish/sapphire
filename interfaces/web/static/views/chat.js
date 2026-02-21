// views/chat.js - Chat view module with settings sidebar
import * as api from '../api.js';
import * as ui from '../ui.js';
import { getElements } from '../core/state.js';
import { updateScene, updateSendButtonLLM } from '../features/scene.js';
import { applyTrimColor } from '../features/chat-settings.js';
import { handleNewChat, handleDeleteChat, handleChatChange } from '../features/chat-manager.js';
import { getInitData, refreshInitData } from '../shared/init-data.js';
import { switchView } from '../core/router.js';
import { loadPersona, createFromChat, avatarImg, avatarFallback, avatarUrl } from '../shared/persona-api.js';

let sidebarLoaded = false;
let saveTimer = null;
let llmProviders = [];
let llmMetadata = {};
let personasList = [];
let defaultPersonaName = '';

const SAVE_DEBOUNCE = 500;

function updateStoryPromptLabel(container) {
    const promptSel = container.querySelector('#sb-prompt');
    if (!promptSel) return;

    const existing = promptSel.querySelector('option[data-story]');
    const hadStoryOption = !!existing;
    if (existing) existing.remove();

    const enabled = container.querySelector('#sb-story-enabled')?.checked;
    const preset = container.querySelector('#sb-story-preset')?.value;
    if (enabled && preset) {
        const opt = document.createElement('option');
        opt.value = '__story__';
        opt.dataset.story = 'true';
        const name = preset.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        opt.textContent = `[STORY] ${name}`;
        promptSel.insertBefore(opt, promptSel.firstChild);
        // Only auto-select story prompt on first appearance (entering story mode)
        if (!hadStoryOption) promptSel.value = '__story__';
    }
}

export default {
    init(container) {
        // Sidebar collapse/expand
        const toggle = container.querySelector('#chat-sidebar-toggle');
        if (toggle) toggle.addEventListener('click', () => toggleSidebar(container));
        const expand = container.querySelector('#chat-sidebar-expand');
        if (expand) expand.addEventListener('click', () => toggleSidebar(container));

        // Restore sidebar state
        const collapsed = localStorage.getItem('sapphire-chat-sidebar') === 'collapsed';
        const sidebar = container.querySelector('.chat-sidebar');
        if (sidebar && collapsed) sidebar.classList.add('collapsed');

        // Reload sidebar settings whenever active chat changes
        const chatSelect = getElements().chatSelect || document.getElementById('chat-select');
        if (chatSelect) {
            chatSelect.addEventListener('change', () => loadSidebar());
            chatSelect.addEventListener('chat-list-ready', () => loadSidebar());
        }

        // Accordion headers in sidebar
        container.querySelectorAll('.sidebar-accordion-header').forEach(header => {
            header.addEventListener('click', () => {
                const content = header.nextElementSibling;
                const open = header.classList.toggle('open');
                content.style.display = open ? 'block' : 'none';
            });
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

                    // "New Story..." button opens the story picker modal
                    const storyBtn = e.target.closest('[data-action="new-story"]');
                    if (storyBtn) {
                        sbPicker.classList.remove('open');
                        const { openStoryPicker } = await import('../features/story.js');
                        openStoryPicker();
                        return;
                    }

                    const item = e.target.closest('.chat-picker-item');
                    if (!item) return;
                    const chatName = item.dataset.chat;
                    if (!chatName) return;
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

        // Close sidebar picker on outside click
        document.addEventListener('click', e => {
            if (!e.target.closest('#sb-chat-picker')) {
                container.querySelector('#sb-chat-picker')?.classList.remove('open');
            }
        });

        // Toggle buttons (Spice, Date/Time)
        container.querySelectorAll('.sb-toggle').forEach(btn => {
            btn.addEventListener('click', () => {
                const active = btn.dataset.active !== 'true';
                btn.dataset.active = active;
                btn.classList.toggle('active', active);
                debouncedSave(container);
            });
        });

        // Auto-save on any sidebar input change
        container.querySelectorAll('.chat-sidebar select, .chat-sidebar input, .chat-sidebar textarea').forEach(el => {
            const event = el.type === 'range' ? 'input' : (el.tagName === 'TEXTAREA' ? 'input' : 'change');
            el.addEventListener(event, () => {
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
                if (el.id === 'sb-story-enabled' || el.id === 'sb-story-preset') {
                    updateStoryPromptLabel(container);
                }
                debouncedSave(container);
            });
        });

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

        // "Go to Mind" buttons — navigate to Mind view with target tab + scope
        container.querySelectorAll('.sb-goto-mind').forEach(btn => {
            btn.addEventListener('click', () => {
                const scope = btn.closest('.sb-field-row')?.querySelector('select')?.value;
                window._mindTab = btn.dataset.tab;
                if (scope && scope !== 'none') window._mindScope = scope;
                switchView('mind');
            });
        });

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

        // Listen for persona-loaded events
        window.addEventListener('persona-loaded', () => loadSidebar());

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

        // State engine buttons
        setupStoryButtons(container);

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
        await refreshInitData();
        await loadSidebar();
    },

    hide() {}
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
        list.innerHTML = docs.map(d =>
            `<div class="sb-doc-item">
                <span title="${d.filename} (${d.chunks} chunks)">${d.filename}</span>
                <button class="sb-doc-del" data-filename="${d.filename}" title="Remove">&times;</button>
            </div>`
        ).join('');
        if (badge) {
            badge.textContent = docs.length;
            badge.style.display = docs.length ? '' : 'none';
        }
    } catch (e) {
        console.warn('Failed to load documents:', e);
    }
}

async function loadSidebar() {
    const container = document.getElementById('view-chat');
    if (!container) return;

    const chatSelect = getElements().chatSelect || document.getElementById('chat-select');
    const chatName = chatSelect?.value;
    if (!chatName) return;

    try {
        const [settingsResp, initData, llmResp, scopesResp, goalScopesResp, knowledgeScopesResp, peopleScopesResp, emailAccountsResp, bitcoinWalletsResp, presetsResp, spiceSetsResp, personasResp] = await Promise.allSettled([
            api.getChatSettings(chatName),
            getInitData(),
            fetch('/api/llm/providers').then(r => r.ok ? r.json() : null),
            fetch('/api/memory/scopes').then(r => r.ok ? r.json() : null),
            fetch('/api/goals/scopes').then(r => r.ok ? r.json() : null),
            fetch('/api/knowledge/scopes').then(r => r.ok ? r.json() : null),
            fetch('/api/knowledge/people/scopes').then(r => r.ok ? r.json() : null),
            fetch('/api/email/accounts').then(r => r.ok ? r.json() : null),
            fetch('/api/bitcoin/wallets').then(r => r.ok ? r.json() : null),
            fetch('/api/story/presets').then(r => r.ok ? r.json() : null),
            fetch('/api/spice-sets').then(r => r.ok ? r.json() : null),
            fetch('/api/personas').then(r => r.ok ? r.json() : null)
        ]);

        const settings = settingsResp.status === 'fulfilled' ? settingsResp.value.settings : {};
        ui.setCurrentPersona(settings.persona || null);
        const init = initData.status === 'fulfilled' ? initData.value : null;
        const llmData = llmResp.status === 'fulfilled' ? llmResp.value : null;
        const scopesData = scopesResp.status === 'fulfilled' ? scopesResp.value : null;
        const goalScopesData = goalScopesResp.status === 'fulfilled' ? goalScopesResp.value : null;
        const knowledgeScopesData = knowledgeScopesResp.status === 'fulfilled' ? knowledgeScopesResp.value : null;
        const peopleScopesData = peopleScopesResp.status === 'fulfilled' ? peopleScopesResp.value : null;
        const emailAccountsData = emailAccountsResp.status === 'fulfilled' ? emailAccountsResp.value : null;
        const bitcoinWalletsData = bitcoinWalletsResp.status === 'fulfilled' ? bitcoinWalletsResp.value : null;
        const presetsData = presetsResp.status === 'fulfilled' ? presetsResp.value : null;
        const spiceSetsData = spiceSetsResp.status === 'fulfilled' ? spiceSetsResp.value : null;
        const personasData = personasResp.status === 'fulfilled' ? personasResp.value : null;
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
                llmSel.innerHTML = '<option value="auto">Auto</option><option value="none">None</option>' +
                    llmProviders.filter(p => p.enabled).map(p =>
                        `<option value="${p.key}">${p.display_name}${p.is_local ? ' \uD83C\uDFE0' : ' \u2601\uFE0F'}</option>`
                    ).join('');
                setSelect(llmSel, settings.llm_primary || 'auto');
                updateModelSelector(container, settings.llm_primary || 'auto', settings.llm_model || '');
            }
        }

        // Populate memory scope dropdown
        const scopeSel = container.querySelector('#sb-memory-scope');
        if (scopeSel && scopesData) {
            scopeSel.innerHTML = '<option value="none">None</option>' +
                (scopesData.scopes || []).map(s =>
                    `<option value="${s.name}">${s.name} (${s.count})</option>`
                ).join('');
            setSelect(scopeSel, settings.memory_scope || 'default');
        }

        // Populate goal scope dropdown
        const goalScopeSel = container.querySelector('#sb-goal-scope');
        if (goalScopeSel && goalScopesData) {
            goalScopeSel.innerHTML = '<option value="none">None</option>' +
                (goalScopesData.scopes || []).map(s =>
                    `<option value="${s.name}">${s.name} (${s.count})</option>`
                ).join('');
            setSelect(goalScopeSel, settings.goal_scope || 'default');
        }

        // Populate knowledge scope dropdown
        const knowledgeScopeSel = container.querySelector('#sb-knowledge-scope');
        if (knowledgeScopeSel && knowledgeScopesData) {
            knowledgeScopeSel.innerHTML = '<option value="none">None</option>' +
                (knowledgeScopesData.scopes || []).map(s =>
                    `<option value="${s.name}">${s.name} (${s.count})</option>`
                ).join('');
            setSelect(knowledgeScopeSel, settings.knowledge_scope || 'default');
        }

        // Populate people scope dropdown
        const peopleScopeSel = container.querySelector('#sb-people-scope');
        if (peopleScopeSel && peopleScopesData) {
            peopleScopeSel.innerHTML = '<option value="none">None</option>' +
                (peopleScopesData.scopes || []).map(s =>
                    `<option value="${s.name}">${s.name} (${s.count})</option>`
                ).join('');
            setSelect(peopleScopeSel, settings.people_scope || 'default');
        }

        // Populate email scope dropdown (from configured email accounts)
        const emailScopeSel = container.querySelector('#sb-email-scope');
        if (emailScopeSel) {
            const accounts = emailAccountsData?.accounts || [];
            emailScopeSel.innerHTML = '<option value="none">None</option>' +
                accounts.map(a =>
                    `<option value="${a.scope}">${a.scope}${a.address ? ' (' + a.address + ')' : ''}</option>`
                ).join('');
            setSelect(emailScopeSel, settings.email_scope || 'default');
        }

        // Populate bitcoin scope dropdown (from configured wallets)
        const btcScopeSel = container.querySelector('#sb-bitcoin-scope');
        if (btcScopeSel) {
            const wallets = bitcoinWalletsData?.wallets || [];
            btcScopeSel.innerHTML = '<option value="none">None</option>' +
                wallets.map(w =>
                    `<option value="${w.scope}">${w.scope}${w.address ? ' (' + w.address.slice(0, 8) + '...)' : ''}</option>`
                ).join('');
            setSelect(btcScopeSel, settings.bitcoin_scope || 'default');
        }

        // Populate state preset dropdown
        const presetSel = container.querySelector('#sb-story-preset');
        if (presetSel && presetsData) {
            presetSel.innerHTML = '<option value="">None</option>' +
                (presetsData.presets || []).map(p =>
                    `<option value="${p.name}">${p.display_name} (${p.key_count} keys)</option>`
                ).join('');
            setSelect(presetSel, settings.story_preset ?? settings.state_preset ?? '');
        }

        // Set remaining form values
        setVal(container, '#sb-voice', settings.voice || 'af_heart');
        setVal(container, '#sb-pitch', settings.pitch || 0.94);
        setVal(container, '#sb-speed', settings.speed || 1.3);
        setVal(container, '#sb-spice-turns', settings.spice_turns || 3);
        setVal(container, '#sb-custom-context', settings.custom_context || '');

        // Toggle buttons
        setToggle(container, '#sb-spice-toggle', settings.spice_enabled !== false,
            `Spice \u00b7 ${settings.spice_turns || 3}`);
        setToggle(container, '#sb-datetime-toggle', settings.inject_datetime === true);
        const storyEnabled = (settings.story_engine_enabled ?? settings.state_engine_enabled) === true;
        const storyPreset = settings.story_preset ?? settings.state_preset;
        setChecked(container, '#sb-story-enabled', storyEnabled);
        setChecked(container, '#sb-story-in-prompt', (settings.story_in_prompt ?? settings.state_story_in_prompt) !== false);
        setChecked(container, '#sb-story-vars', (settings.story_vars_in_prompt ?? settings.state_vars_in_prompt) === true);

        // Show [STORY] prefix on prompt when story engine is active
        updateStoryPromptLabel(container);

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
        if (pitchLabel) pitchLabel.textContent = settings.pitch || 0.94;
        const speedLabel = container.querySelector('#sb-speed-val');
        if (speedLabel) speedLabel.textContent = settings.speed || 1.3;

        // Update slider fills
        const pitchSlider = container.querySelector('#sb-pitch');
        const speedSlider = container.querySelector('#sb-speed');
        if (pitchSlider) updateSliderFill(pitchSlider);
        if (speedSlider) updateSliderFill(speedSlider);

        // Swap tab label and content based on story chat
        const isStoryChat = settings.story_chat === true;
        const firstTab = container.querySelector('.sb-mode-tab[data-mode="easy"]');
        if (firstTab) firstTab.textContent = isStoryChat ? 'Story' : 'Persona';

        if (isStoryChat) {
            await updateStoryMode(container, settings);
        } else {
            updateEasyMode(container, settings, init);
        }

        // RAG context level
        setVal(container, '#sb-rag-context', settings.rag_context || 'normal');

        // Load per-chat documents
        loadDocuments(container, chatName);

        sidebarLoaded = true;
    } catch (e) {
        console.warn('Failed to load sidebar:', e);
    }
}

function debouncedSave(container) {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => saveSettings(container), SAVE_DEBOUNCE);
}

async function saveSettings(container) {
    const chatSelect = getElements().chatSelect || document.getElementById('chat-select');
    const chatName = chatSelect?.value;
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
                    const total = (result.functions?.length || 0) + (result.state_tools?.length || 0);
                    const st = result.toolset.story_tools || 0;
                    selected.textContent = st ? `${name} + Story (${total})` : `${name} (${total})`;
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

    return {
        prompt: getVal(container, '#sb-prompt'),
        toolset: getVal(container, '#sb-toolset'),
        spice_set: getVal(container, '#sb-spice-set') || 'default',
        voice: getVal(container, '#sb-voice'),
        pitch: parseFloat(getVal(container, '#sb-pitch')) || 0.94,
        speed: parseFloat(getVal(container, '#sb-speed')) || 1.3,
        spice_enabled: getToggle(container, '#sb-spice-toggle'),
        spice_turns: parseInt(getVal(container, '#sb-spice-turns')) || 3,
        inject_datetime: getToggle(container, '#sb-datetime-toggle'),
        custom_context: getVal(container, '#sb-custom-context'),
        llm_primary: getVal(container, '#sb-llm-primary') || 'auto',
        llm_model: getSelectedModel(container),
        trim_color: trimColor,
        memory_scope: getVal(container, '#sb-memory-scope') || 'default',
        goal_scope: getVal(container, '#sb-goal-scope') || 'default',
        knowledge_scope: getVal(container, '#sb-knowledge-scope') || 'default',
        people_scope: getVal(container, '#sb-people-scope') || 'default',
        email_scope: getVal(container, '#sb-email-scope') || 'default',
        bitcoin_scope: getVal(container, '#sb-bitcoin-scope') || 'default',
        story_engine_enabled: getChecked(container, '#sb-story-enabled'),
        story_preset: getVal(container, '#sb-story-preset') || null,
        story_in_prompt: getChecked(container, '#sb-story-in-prompt'),
        story_vars_in_prompt: getChecked(container, '#sb-story-vars'),
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
    } else if (providerKey === 'other') {
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

function setupStoryButtons(container) {
    container.querySelector('#sb-story-view')?.addEventListener('click', async () => {
        const chatName = (getElements().chatSelect || document.getElementById('chat-select'))?.value;
        if (!chatName) return;
        try {
            const resp = await fetch(`/api/story/${encodeURIComponent(chatName)}`);
            if (resp.ok) {
                const data = await resp.json();
                const str = Object.entries(data.state || {})
                    .map(([k, v]) => `${v.label || k}: ${JSON.stringify(v.value)}`).join('\n');
                alert(`State:\n\n${str || '(empty)'}`);
            }
        } catch (e) { ui.showToast('Failed', 'error'); }
    });

    container.querySelector('#sb-story-reset')?.addEventListener('click', async () => {
        const chatName = (getElements().chatSelect || document.getElementById('chat-select'))?.value;
        if (!chatName || !confirm('Reset story?')) return;
        const preset = getVal(container, '#sb-story-preset');
        try {
            await fetch(`/api/story/${encodeURIComponent(chatName)}/reset`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ preset: preset || null })
            });
            ui.showToast('Story reset', 'success');
        } catch (e) { ui.showToast('Failed', 'error'); }
    });
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
        if (e.target.closest('.sb-pdetail-edit')) switchView('personas');
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

const VOICE_NAMES = {
    am_adam: 'Adam', am_eric: 'Eric', am_liam: 'Liam', am_michael: 'Michael',
    af_bella: 'Bella', af_nicole: 'Nicole', af_heart: 'Heart', af_jessica: 'Jessica',
    af_sarah: 'Sarah', af_river: 'River', af_sky: 'Sky',
    bf_emma: 'Emma', bf_isabella: 'Isabella', bf_alice: 'Alice', bf_lily: 'Lily',
    bm_george: 'George', bm_daniel: 'Daniel', bm_lewis: 'Lewis'
};

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
            <div class="sb-pdetail-row"><span>voice</span><span>${VOICE_NAMES[settings.voice] || settings.voice || 'Heart'}</span></div>
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

// ==================== Story Mode Tab ====================

async function updateStoryMode(container, settings) {
    const gridEl = container.querySelector('#sb-persona-grid');
    const detailEl = container.querySelector('#sb-persona-detail');
    if (!gridEl && !detailEl) return;

    const chatSelect = getElements().chatSelect || document.getElementById('chat-select');
    const chatName = chatSelect?.value;
    const presetName = settings.story_preset ?? settings.state_preset ?? '';

    // Fetch state and save slots in parallel
    const [stateResp, slotsResp] = await Promise.allSettled([
        chatName ? fetch(`/api/story/${encodeURIComponent(chatName)}`).then(r => r.ok ? r.json() : null) : null,
        presetName ? fetch(`/api/story/saves/${encodeURIComponent(presetName)}`).then(r => r.ok ? r.json() : null) : null,
    ]);
    const stateData = stateResp.status === 'fulfilled' ? stateResp.value : null;
    const slotsData = slotsResp.status === 'fulfilled' ? slotsResp.value : null;

    // Story display name
    const storyDisplay = (settings.story_display_name || presetName || 'Story').replace(/^\[STORY\]\s*/, '');

    // Hide persona grid, use detail area for story content
    if (gridEl) gridEl.innerHTML = '';

    if (!detailEl) return;

    // Build state variable rows
    const state = stateData?.state || {};
    const stateKeys = Object.keys(state).filter(k => !k.startsWith('_'));
    let stateRows = '';
    if (stateKeys.length === 0) {
        stateRows = '<div class="sb-story-empty">No state variables</div>';
    } else {
        stateRows = stateKeys.map(k => {
            const v = state[k];
            const label = v.label || k;
            const val = v.value ?? '';
            const displayVal = typeof val === 'object' ? JSON.stringify(val) : String(val);
            return `
                <div class="sb-story-var" data-key="${escapeHtml(k)}">
                    <span class="sb-story-var-label">${escapeHtml(label)}</span>
                    <span class="sb-story-var-value" title="Click to edit">${escapeHtml(displayVal)}</span>
                </div>`;
        }).join('');
    }

    // Build save slot buttons
    const slots = slotsData?.slots || [];
    const slotButtons = (action) => {
        let html = '';
        for (let i = 1; i <= 5; i++) {
            const slot = slots.find(s => s.slot === i);
            const isEmpty = !slot || slot.empty;
            const timestamp = isEmpty ? 'Empty' : formatSlotTime(slot.timestamp);
            const turn = isEmpty ? '' : ` \u2022 Turn ${slot.turn}`;
            html += `<button class="sb-story-slot" data-action="${action}" data-slot="${i}">
                <span class="sb-story-slot-num">${i}</span>
                <span class="sb-story-slot-info">${timestamp}${turn}</span>
            </button>`;
        }
        return html;
    };

    // Progress info
    const turnCount = stateData?.key_count || 0;
    const presetLabel = stateData?.preset || presetName;

    detailEl.innerHTML = `
        <div class="sb-story-header">
            <span class="sb-story-title">${escapeHtml(storyDisplay)}</span>
            <span class="sb-story-meta">${turnCount} variables \u2022 ${escapeHtml(presetLabel)}</span>
        </div>
        ${easyAccordion('State', `<div class="sb-story-vars">${stateRows}</div>`, { desc: `${stateKeys.length} vars` })}
        ${easyAccordion('Save', `<div class="sb-story-slots">${slotButtons('save')}</div>`, { desc: 'Save progress' })}
        ${easyAccordion('Load', `<div class="sb-story-slots">${slotButtons('load')}</div>`, { desc: 'Restore progress' })}
        <div class="sb-story-actions">
            <button class="sb-btn-sm sb-story-reset-btn">Reset Story</button>
        </div>
    `;

    // Wire state variable editing
    detailEl.querySelectorAll('.sb-story-var-value').forEach(el => {
        el.addEventListener('click', () => {
            if (el.querySelector('input')) return; // already editing
            const row = el.closest('.sb-story-var');
            const key = row?.dataset.key;
            if (!key) return;

            const current = el.textContent;
            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'sb-story-var-input';
            input.value = current;
            el.textContent = '';
            el.appendChild(input);
            input.focus();
            input.select();

            const commit = async () => {
                const newVal = input.value;
                el.textContent = newVal;
                if (newVal !== current && chatName) {
                    try {
                        await fetch(`/api/story/${encodeURIComponent(chatName)}/set`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ key, value: newVal })
                        });
                    } catch (e) {
                        el.textContent = current; // revert on error
                        ui.showToast('Failed to update variable', 'error');
                    }
                }
            };
            input.addEventListener('blur', commit, { once: true });
            input.addEventListener('keydown', e => {
                if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
                if (e.key === 'Escape') { el.textContent = current; }
            });
        });
    });

    // Wire save/load slots
    detailEl.querySelectorAll('.sb-story-slot').forEach(btn => {
        btn.addEventListener('click', async () => {
            const action = btn.dataset.action;
            const slot = parseInt(btn.dataset.slot);
            if (!chatName) return;

            if (action === 'save') {
                try {
                    await fetch(`/api/story/${encodeURIComponent(chatName)}/save`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ slot })
                    });
                    ui.showToast(`Saved to slot ${slot}`, 'success');
                    await updateStoryMode(container, settings); // refresh slots
                } catch (e) {
                    ui.showToast('Save failed', 'error');
                }
            } else if (action === 'load') {
                const slotData = slots.find(s => s.slot === slot);
                if (!slotData || slotData.empty) {
                    ui.showToast(`Slot ${slot} is empty`, 'info');
                    return;
                }
                if (!confirm(`Load save from slot ${slot}? Chat history and state will be restored.`)) return;
                try {
                    await fetch(`/api/story/${encodeURIComponent(chatName)}/load`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ slot })
                    });
                    ui.showToast(`Loaded slot ${slot}`, 'success');
                    // Refresh chat messages + story state
                    const { setHistLen, refresh } = await import('../core/state.js');
                    const len = await refresh(false);
                    setHistLen(len);
                    await updateStoryMode(container, settings);
                } catch (e) {
                    ui.showToast('Load failed', 'error');
                }
            }
        });
    });

    // Wire reset button
    detailEl.querySelector('.sb-story-reset-btn')?.addEventListener('click', async () => {
        if (!confirm('Reset story progress? This will restart from the beginning.')) return;
        if (!chatName) return;
        try {
            await fetch(`/api/story/${encodeURIComponent(chatName)}/reset`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ preset: presetName || null })
            });
            ui.showToast('Story reset', 'success');
            await updateStoryMode(container, settings);
        } catch (e) {
            ui.showToast('Reset failed', 'error');
        }
    });

    // Accordion headers are handled by delegated click on #sb-persona-detail (bound once in init)
}

function formatSlotTime(isoString) {
    if (!isoString) return 'Empty';
    try {
        const d = new Date(isoString);
        return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' +
               d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch { return 'Saved'; }
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
