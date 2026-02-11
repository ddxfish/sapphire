// features/chat-settings.js - Chat settings modal
import * as api from '../api.js';
import * as ui from '../ui.js';
import { getElements, getTtsEnabled } from '../core/state.js';
import { closeAllKebabs } from './chat-manager.js';
import { updateScene, updateSendButtonLLM } from './scene.js';
import { getInitData } from '../shared/init-data.js';

let llmProviders = [];
let llmMetadata = {};

export async function openSettingsModal() {
    closeAllKebabs();
    const { chatSelect, settingsModal } = getElements();
    
    try {
        const chatName = chatSelect.value;
        if (!chatName) return;
        
        const response = await api.getChatSettings(chatName);
        const settings = response.settings;
        
        // Load prompts and abilities from init data (refreshed on SSE events)
        const initData = await getInitData();
        if (initData?.prompts?.list) {
            const promptSelect = document.getElementById('setting-prompt');
            promptSelect.innerHTML = '';
            initData.prompts.list.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p.name;
                opt.textContent = p.name.charAt(0).toUpperCase() + p.name.slice(1);
                promptSelect.appendChild(opt);
            });
        }

        // Load abilities list from init data cache
        if (initData?.abilities?.list) {
            const abilitySelect = document.getElementById('setting-ability');
            abilitySelect.innerHTML = '';
            initData.abilities.list.forEach(a => {
                const opt = document.createElement('option');
                opt.value = a.name;
                opt.textContent = `${a.name} (${a.function_count} functions)`;
                abilitySelect.appendChild(opt);
            });
        }
        
        // Fetch supplemental data in parallel to avoid HTTP/1.1 connection queuing
        const [llmResult, scopesResult, presetsResult] = await Promise.allSettled([
            fetch('/api/llm/providers').then(r => r.ok ? r.json() : null).catch(() => null),
            fetch('/api/memory/scopes').then(r => r.ok ? r.json() : null).catch(() => null),
            fetch('/api/state/presets').then(r => r.ok ? r.json() : null).catch(() => null)
        ]);

        // Process LLM providers
        if (llmResult.status === 'fulfilled' && llmResult.value) {
            llmProviders = llmResult.value.providers || [];
            llmMetadata = llmResult.value.metadata || {};
            populateLlmDropdown(settings);
        }

        // Process memory scopes
        if (scopesResult.status === 'fulfilled' && scopesResult.value) {
            const scopeSelect = document.getElementById('setting-memory-scope');
            scopeSelect.innerHTML = '<option value="none">None (disabled)</option>';
            (scopesResult.value.scopes || []).forEach(s => {
                const opt = document.createElement('option');
                opt.value = s.name;
                opt.textContent = `${s.name} (${s.count})`;
                scopeSelect.appendChild(opt);
            });
            scopeSelect.value = settings.memory_scope || 'default';
        }

        // Process state presets
        if (presetsResult.status === 'fulfilled' && presetsResult.value) {
            const presetSelect = document.getElementById('setting-state-preset');
            presetSelect.innerHTML = '<option value="">None</option>';
            (presetsResult.value.presets || []).forEach(p => {
                const opt = document.createElement('option');
                opt.value = p.name;
                opt.textContent = `${p.display_name} (${p.key_count} keys)`;
                presetSelect.appendChild(opt);
            });
        }

        // Load current state info if enabled
        await loadStateInfo(chatName, settings);
        
        // Populate form
        document.getElementById('setting-prompt').value = settings.prompt || 'sapphire';
        document.getElementById('setting-ability').value = settings.ability || 'default';
        document.getElementById('setting-voice').value = settings.voice || 'af_heart';
        document.getElementById('setting-pitch').value = settings.pitch || 0.94;
        document.getElementById('setting-speed').value = settings.speed || 1.3;
        document.getElementById('setting-spice').checked = settings.spice_enabled !== false;
        document.getElementById('setting-spice-turns').value = settings.spice_turns || 3;
        document.getElementById('setting-datetime').checked = settings.inject_datetime === true;
        document.getElementById('setting-custom-context').value = settings.custom_context || '';
        
        // Trim color - get current computed value as fallback
        const trimColorInput = document.getElementById('setting-trim-color');
        const currentTrim = getComputedStyle(document.documentElement).getPropertyValue('--trim').trim();
        const defaultTrim = getComputedStyle(document.documentElement).getPropertyValue('--accent-blue').trim() || '#4a9eff';
        
        // If chat has custom trim_color, use it; otherwise show current (may be global)
        if (settings.trim_color) {
            trimColorInput.value = settings.trim_color;
            trimColorInput.dataset.cleared = 'false';
        } else {
            trimColorInput.value = currentTrim || defaultTrim;
            trimColorInput.dataset.cleared = 'true';  // No custom color = use global
        }
        
        // Reset button clears the per-chat override (lets global take over)
        const resetBtn = document.getElementById('reset-trim-color');
        if (resetBtn) {
            resetBtn.onclick = () => {
                // Get current global trim to display (visual feedback)
                const globalTrim = localStorage.getItem('sapphire-trim') || defaultTrim;
                trimColorInput.value = globalTrim;
                trimColorInput.dataset.cleared = 'true';  // Mark as cleared
            };
        }
        
        // When user picks a color, mark as not cleared
        trimColorInput.addEventListener('input', () => {
            trimColorInput.dataset.cleared = 'false';
        });
        
        // New memory scope button
        const newScopeBtn = document.getElementById('new-memory-scope-btn');
        if (newScopeBtn) {
            newScopeBtn.onclick = async () => {
                const name = prompt('New memory slot name (lowercase, no spaces):');
                if (!name) return;
                
                const clean = name.trim().toLowerCase().replace(/[^a-z0-9_]/g, '');
                if (!clean || clean.length > 32) {
                    ui.showToast('Invalid name (use lowercase, numbers, underscore)', 'error');
                    return;
                }
                
                try {
                    const res = await fetch('/api/memory/scopes', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ name: clean })
                    });
                    if (res.ok) {
                        const scopeSelect = document.getElementById('setting-memory-scope');
                        const opt = document.createElement('option');
                        opt.value = clean;
                        opt.textContent = `${clean} (0)`;
                        scopeSelect.appendChild(opt);
                        scopeSelect.value = clean;
                        ui.showToast(`Created: ${clean}`, 'success');
                    } else {
                        const err = await res.json();
                        ui.showToast(err.error || 'Failed to create', 'error');
                    }
                } catch (e) {
                    ui.showToast('Failed to create scope', 'error');
                }
            };
        }
        
        // State engine settings
        document.getElementById('setting-state-enabled').checked = settings.state_engine_enabled === true;
        document.getElementById('setting-state-preset').value = settings.state_preset || '';
        document.getElementById('setting-state-story-in-prompt').checked = settings.state_story_in_prompt !== false;
        document.getElementById('setting-state-vars-in-prompt').checked = settings.state_vars_in_prompt === true;
        
        // TTS accordion toggle
        const ttsHeader = document.getElementById('tts-header');
        const ttsContent = document.getElementById('tts-content');
        if (ttsHeader) {
            ttsHeader.onclick = () => {
                const isOpen = ttsHeader.classList.toggle('open');
                ttsContent.style.display = isOpen ? 'block' : 'none';
            };
        }
        
        // System Prompt accordion toggle
        const sysPromptHeader = document.getElementById('system-prompt-header');
        const sysPromptContent = document.getElementById('system-prompt-content');
        if (sysPromptHeader) {
            sysPromptHeader.onclick = () => {
                const isOpen = sysPromptHeader.classList.toggle('open');
                sysPromptContent.style.display = isOpen ? 'block' : 'none';
            };
        }
        
        // State engine accordion toggle
        const stateHeader = document.getElementById('state-engine-header');
        const stateContent = document.getElementById('state-engine-content');
        if (stateHeader) {
            stateHeader.onclick = () => {
                const isOpen = stateHeader.classList.toggle('open');
                stateContent.style.display = isOpen ? 'block' : 'none';
            };
        }
        
        // Update state badge
        updateStateBadge(settings.state_engine_enabled);
        
        // State action buttons
        setupStateActionButtons(chatName);
        
        document.getElementById('pitch-value').textContent = settings.pitch || 0.94;
        document.getElementById('speed-value').textContent = settings.speed || 1.3;
        
        // Hide TTS accordion if TTS disabled
        const ttsEnabled = getTtsEnabled();
        const ttsSection = document.getElementById('tts-accordion-section');
        if (ttsSection) ttsSection.style.display = ttsEnabled ? '' : 'none';
        
        // Show modal with animation
        settingsModal.style.display = 'flex';
        requestAnimationFrame(() => {
            settingsModal.classList.add('active');
            initModalSliders();
        });
        
    } catch (e) {
        console.error('Failed to load settings:', e);
        ui.showToast('Failed to load settings: ' + e.message, 'error');
    }
}

function populateLlmDropdown(settings) {
    const primarySelect = document.getElementById('setting-llm-primary');
    if (!primarySelect) return;
    
    // Build primary options - Auto, None, plus enabled providers
    const baseOptions = '<option value="auto">Auto</option>' +
        '<option value="none">None (disabled)</option>';
    
    const providerOptions = llmProviders
        .filter(p => p.enabled)
        .map(p => `<option value="${p.key}">${p.display_name}${p.is_local ? ' 🏠' : ' ☁️'}</option>`)
        .join('');
    
    primarySelect.innerHTML = baseOptions + providerOptions;
    primarySelect.value = settings.llm_primary || 'auto';
    
    // Attach change handler for model selector
    primarySelect.onchange = () => updateModelSelector(primarySelect.value, '');
    
    // Initialize model selector with current settings
    updateModelSelector(settings.llm_primary || 'auto', settings.llm_model || '');
}

function updateModelSelector(providerKey, currentModel) {
    const modelSelectGroup = document.getElementById('model-select-group');
    const modelCustomGroup = document.getElementById('model-custom-group');
    const modelSelect = document.getElementById('setting-llm-model');
    const modelCustom = document.getElementById('setting-llm-model-custom');
    
    if (!modelSelectGroup || !modelSelect) return;
    
    // Hide both by default
    modelSelectGroup.style.display = 'none';
    modelCustomGroup.style.display = 'none';
    
    if (providerKey === 'auto' || providerKey === 'none' || !providerKey) {
        return;
    }
    
    const meta = llmMetadata[providerKey];
    const providerConfig = llmProviders.find(p => p.key === providerKey);
    
    if (meta?.model_options && Object.keys(meta.model_options).length > 0) {
        // Provider has predefined model options - show dropdown
        const defaultModel = providerConfig?.model || '';
        const defaultLabel = defaultModel ? 
            `Provider default (${meta.model_options[defaultModel] || defaultModel})` : 
            'Provider default';
        
        modelSelect.innerHTML = `<option value="">${defaultLabel}</option>` +
            Object.entries(meta.model_options)
                .map(([k, v]) => `<option value="${k}"${k === currentModel ? ' selected' : ''}>${v}</option>`)
                .join('');
        
        if (currentModel && !meta.model_options[currentModel]) {
            // Custom model not in list - add it
            modelSelect.innerHTML += `<option value="${currentModel}" selected>${currentModel}</option>`;
        }
        
        modelSelectGroup.style.display = '';
    } else if (providerKey === 'other') {
        // "Other" provider - free-form model input
        modelCustom.value = currentModel || '';
        modelCustomGroup.style.display = '';
    }
    // LM Studio (model_options: null, not 'other') - no model selector needed
}

function getSelectedModel() {
    const primarySelect = document.getElementById('setting-llm-primary');
    const modelSelect = document.getElementById('setting-llm-model');
    const modelCustom = document.getElementById('setting-llm-model-custom');
    const modelSelectGroup = document.getElementById('model-select-group');
    const modelCustomGroup = document.getElementById('model-custom-group');
    
    const providerKey = primarySelect?.value || 'auto';
    
    if (providerKey === 'auto' || providerKey === 'none') {
        return '';
    }
    
    // Check which input is visible
    if (modelSelectGroup?.style.display !== 'none' && modelSelect) {
        return modelSelect.value || '';
    }
    if (modelCustomGroup?.style.display !== 'none' && modelCustom) {
        return modelCustom.value.trim() || '';
    }
    
    return '';
}

export async function saveSettings() {
    const { chatSelect, settingsModal } = getElements();
    
    try {
        const chatName = chatSelect.value;
        if (!chatName) return;
        
        const trimColorInput = document.getElementById('setting-trim-color');
        // If cleared (reset clicked), save empty to use global; otherwise save the value
        const trimColor = trimColorInput?.dataset.cleared === 'true' ? '' : (trimColorInput?.value || '');
        
        const settings = {
            prompt: document.getElementById('setting-prompt').value,
            ability: document.getElementById('setting-ability').value,
            voice: document.getElementById('setting-voice').value,
            pitch: parseFloat(document.getElementById('setting-pitch').value),
            speed: parseFloat(document.getElementById('setting-speed').value),
            spice_enabled: document.getElementById('setting-spice').checked,
            spice_turns: parseInt(document.getElementById('setting-spice-turns').value) || 3,
            inject_datetime: document.getElementById('setting-datetime').checked,
            custom_context: document.getElementById('setting-custom-context').value,
            llm_primary: document.getElementById('setting-llm-primary')?.value || 'auto',
            llm_model: getSelectedModel(),
            trim_color: trimColor,
            memory_scope: document.getElementById('setting-memory-scope')?.value || 'default',
            state_engine_enabled: document.getElementById('setting-state-enabled')?.checked || false,
            state_preset: document.getElementById('setting-state-preset')?.value || null,
            state_story_in_prompt: document.getElementById('setting-state-story-in-prompt')?.checked !== false,
            state_vars_in_prompt: document.getElementById('setting-state-vars-in-prompt')?.checked || false
        };
        
        await api.updateChatSettings(chatName, settings);
        closeSettingsModal();
        ui.showToast('Settings saved', 'success');
        await updateScene();
        updateSendButtonLLM(settings.llm_primary, settings.llm_model);
        
        // Apply trim color immediately
        applyTrimColor(trimColor);
        
    } catch (e) {
        console.error('Failed to save settings:', e);
        ui.showToast('Failed to save settings', 'error');
    }
}

export function closeSettingsModal() {
    const { settingsModal } = getElements();
    settingsModal.classList.remove('active');
    setTimeout(() => {
        settingsModal.style.display = 'none';
    }, 300);
}

// Apply trim color to CSS variable and derived colors
export function applyTrimColor(color) {
    const root = document.documentElement;
    
    // If no per-chat color, check for global trim in localStorage
    if (!color || !color.match(/^#[0-9a-f]{6}$/i)) {
        color = localStorage.getItem('sapphire-trim') || '';
    }
    
    if (color && color.match(/^#[0-9a-f]{6}$/i)) {
        // Set main trim color
        root.style.setProperty('--trim', color);
        
        // Generate derived colors (same logic as main.js initAppearance)
        const r = parseInt(color.slice(1, 3), 16);
        const g = parseInt(color.slice(3, 5), 16);
        const b = parseInt(color.slice(5, 7), 16);
        root.style.setProperty('--trim-glow', `rgba(${r}, ${g}, ${b}, 0.35)`);
        root.style.setProperty('--trim-light', `rgba(${r}, ${g}, ${b}, 0.15)`);
        root.style.setProperty('--trim-border', `rgba(${r}, ${g}, ${b}, 0.4)`);
        root.style.setProperty('--trim-50', `rgba(${r}, ${g}, ${b}, 0.5)`);
        root.style.setProperty('--accordion-header-bg', `rgba(${r}, ${g}, ${b}, 0.08)`);
        root.style.setProperty('--accordion-header-hover', `rgba(${r}, ${g}, ${b}, 0.12)`);
    } else {
        // No global trim either - reset to CSS defaults from shared.css
        root.style.removeProperty('--trim');
        root.style.removeProperty('--trim-glow');
        root.style.removeProperty('--trim-light');
        root.style.removeProperty('--trim-border');
        root.style.removeProperty('--trim-50');
        root.style.removeProperty('--accordion-header-bg');
        root.style.removeProperty('--accordion-header-hover');
    }
    
    // Update volume slider fill (uses inline style that needs refresh)
    import('./volume.js').then(vol => vol.updateSliderFill()).catch(() => {});
}

export async function saveAsDefaults() {
    if (!confirm('Save these settings as defaults for all new chats?')) {
        return;
    }
    
    try {
        const trimColorInput = document.getElementById('setting-trim-color');
        const trimColor = trimColorInput?.value || '';
        
        const settings = {
            prompt: document.getElementById('setting-prompt').value,
            ability: document.getElementById('setting-ability').value,
            voice: document.getElementById('setting-voice').value,
            pitch: parseFloat(document.getElementById('setting-pitch').value),
            speed: parseFloat(document.getElementById('setting-speed').value),
            spice_enabled: document.getElementById('setting-spice').checked,
            spice_turns: parseInt(document.getElementById('setting-spice-turns').value) || 3,
            inject_datetime: document.getElementById('setting-datetime').checked,
            custom_context: document.getElementById('setting-custom-context').value,
            llm_primary: document.getElementById('setting-llm-primary')?.value || 'auto',
            llm_model: getSelectedModel(),
            trim_color: trimColor,
            memory_scope: document.getElementById('setting-memory-scope')?.value || 'default',
            state_engine_enabled: document.getElementById('setting-state-enabled')?.checked || false,
            state_preset: document.getElementById('setting-state-preset')?.value || null,
            state_story_in_prompt: document.getElementById('setting-state-story-in-prompt')?.checked !== false,
            state_vars_in_prompt: document.getElementById('setting-state-vars-in-prompt')?.checked || false
        };
        
        const res = await fetch('/api/settings/chat-defaults', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        
        if (res.ok) {
            ui.showToast('Saved as default for new chats', 'success');
        } else {
            throw new Error('Failed to save');
        }
    } catch (e) {
        console.error('Failed to save defaults:', e);
        ui.showToast('Failed to save defaults', 'error');
    }
}

export function handlePitchInput(e) {
    document.getElementById('pitch-value').textContent = e.target.value;
    updateRangeSliderFill(e.target);
}

export function handleSpeedInput(e) {
    document.getElementById('speed-value').textContent = e.target.value;
    updateRangeSliderFill(e.target);
}

function updateRangeSliderFill(slider) {
    const min = parseFloat(slider.min) || 0;
    const max = parseFloat(slider.max) || 100;
    const val = parseFloat(slider.value);
    const percent = ((val - min) / (max - min)) * 100;
    
    const styles = getComputedStyle(document.documentElement);
    let fillColor = styles.getPropertyValue('--trim').trim();
    
    if (!fillColor || fillColor === 'transparent' || fillColor.startsWith('var(')) {
        fillColor = styles.getPropertyValue('--accent-blue').trim() || '#4a9eff';
    }
    
    let bgColor = styles.getPropertyValue('--bg-tertiary').trim() || '#2a2a2a';
    
    slider.style.background = `linear-gradient(to right, ${fillColor} 0%, ${fillColor} ${percent}%, ${bgColor} ${percent}%, ${bgColor} 100%)`;
}

export function initModalSliders() {
    const pitchSlider = document.getElementById('setting-pitch');
    const speedSlider = document.getElementById('setting-speed');
    if (pitchSlider) updateRangeSliderFill(pitchSlider);
    if (speedSlider) updateRangeSliderFill(speedSlider);
}

export function handleModalBackdropClick(e) {
    const { settingsModal } = getElements();
    if (e.target === settingsModal) closeSettingsModal();
}

// =============================================================================
// STATE ENGINE HELPERS
// =============================================================================

async function loadStateInfo(chatName, settings) {
    const stateInfo = document.getElementById('state-info');
    const stateActions = document.getElementById('state-actions');
    const turnInfo = document.getElementById('state-turn-info');
    const keyInfo = document.getElementById('state-key-info');
    
    if (!settings.state_engine_enabled) {
        if (stateInfo) stateInfo.style.display = 'none';
        if (stateActions) stateActions.style.display = 'none';
        return;
    }
    
    try {
        const ctrl = new AbortController();
        setTimeout(() => ctrl.abort(), 5000);
        const resp = await fetch(`/api/state/${encodeURIComponent(chatName)}`, { signal: ctrl.signal });
        if (resp.ok) {
            const data = await resp.json();
            if (turnInfo) turnInfo.textContent = `Turn: ${Object.values(data.state || {})[0]?.turn || 0}`;
            if (keyInfo) keyInfo.textContent = `Keys: ${data.key_count || 0}`;
            if (stateInfo) stateInfo.style.display = 'flex';
            if (stateActions) stateActions.style.display = 'flex';
        }
    } catch (e) {
        console.warn('Could not load state info:', e);
    }
}

function updateStateBadge(enabled) {
    const badge = document.getElementById('state-badge');
    if (badge) {
        badge.style.display = enabled ? 'inline-block' : 'none';
    }
    
    // Also update checkbox change handler
    const checkbox = document.getElementById('setting-state-enabled');
    if (checkbox) {
        checkbox.onchange = () => {
            updateStateBadge(checkbox.checked);
            const stateInfo = document.getElementById('state-info');
            const stateActions = document.getElementById('state-actions');
            if (!checkbox.checked) {
                if (stateInfo) stateInfo.style.display = 'none';
                if (stateActions) stateActions.style.display = 'none';
            }
        };
    }
}

function setupStateActionButtons(chatName) {
    const viewBtn = document.getElementById('state-view-btn');
    const resetBtn = document.getElementById('state-reset-btn');
    const historyBtn = document.getElementById('state-history-btn');
    
    if (viewBtn) {
        viewBtn.onclick = async () => {
            try {
                const resp = await fetch(`/api/state/${encodeURIComponent(chatName)}`);
                if (resp.ok) {
                    const data = await resp.json();
                    const stateStr = Object.entries(data.state || {})
                        .map(([k, v]) => `${v.label || k}: ${JSON.stringify(v.value)}`)
                        .join('\n');
                    alert(`State for ${chatName}:\n\n${stateStr || '(empty)'}`);
                }
            } catch (e) {
                ui.showToast('Failed to load state', 'error');
            }
        };
    }
    
    if (resetBtn) {
        resetBtn.onclick = async () => {
            const preset = document.getElementById('setting-state-preset')?.value;
            if (!confirm(`Reset state${preset ? ` to preset "${preset}"` : ''}?`)) return;
            
            try {
                const resp = await fetch(`/api/state/${encodeURIComponent(chatName)}/reset`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ preset: preset || null })
                });
                if (resp.ok) {
                    ui.showToast('State reset', 'success');
                    // Refresh state info
                    const settings = { state_engine_enabled: true };
                    await loadStateInfo(chatName, settings);
                } else {
                    const err = await resp.json();
                    ui.showToast(err.error || 'Reset failed', 'error');
                }
            } catch (e) {
                ui.showToast('Failed to reset state', 'error');
            }
        };
    }
    
    if (historyBtn) {
        historyBtn.onclick = async () => {
            try {
                const resp = await fetch(`/api/state/${encodeURIComponent(chatName)}/history?limit=20`);
                if (resp.ok) {
                    const data = await resp.json();
                    const histStr = (data.history || [])
                        .map(h => `[T${h.turn}] ${h.key}: ${JSON.stringify(h.old_value)} → ${JSON.stringify(h.new_value)} (${h.changed_by})`)
                        .join('\n');
                    alert(`Recent state history:\n\n${histStr || '(no history)'}`);
                }
            } catch (e) {
                ui.showToast('Failed to load history', 'error');
            }
        };
    }
}