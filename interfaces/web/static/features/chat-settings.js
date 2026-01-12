// features/chat-settings.js - Chat settings modal
import * as api from '../api.js';
import * as ui from '../ui.js';
import { getElements, getTtsEnabled } from '../core/state.js';
import { closeAllKebabs } from './chat-manager.js';
import { updateScene, updateSendButtonLLM } from './scene.js';

let llmProviders = [];

export async function openSettingsModal() {
    closeAllKebabs();
    const { chatSelect, settingsModal } = getElements();
    
    try {
        const chatName = chatSelect.value;
        if (!chatName) return;
        
        const response = await api.getChatSettings(chatName);
        const settings = response.settings;
        
        // Load prompts list
        try {
            const promptsResp = await fetch('/api/prompts');
            if (promptsResp.ok) {
                const promptsData = await promptsResp.json();
                const promptSelect = document.getElementById('setting-prompt');
                promptSelect.innerHTML = '';
                
                promptsData.prompts.forEach(p => {
                    const opt = document.createElement('option');
                    opt.value = p.name;
                    opt.textContent = p.name.charAt(0).toUpperCase() + p.name.slice(1);
                    promptSelect.appendChild(opt);
                });
            }
        } catch (e) {
            console.warn('Could not load prompts list:', e);
        }
        
        // Load abilities list
        try {
            const abilitiesResp = await fetch('/api/abilities');
            if (abilitiesResp.ok) {
                const abilitiesData = await abilitiesResp.json();
                const abilitySelect = document.getElementById('setting-ability');
                abilitySelect.innerHTML = '';
                
                abilitiesData.abilities.forEach(a => {
                    const opt = document.createElement('option');
                    opt.value = a.name;
                    opt.textContent = `${a.name} (${a.function_count} functions)`;
                    abilitySelect.appendChild(opt);
                });
            }
        } catch (e) {
            console.warn('Could not load abilities list:', e);
        }
        
        // Load LLM providers list
        try {
            const llmResp = await fetch('/api/llm/providers');
            if (llmResp.ok) {
                const llmData = await llmResp.json();
                llmProviders = llmData.providers || [];
                populateLlmDropdowns(settings);
            }
        } catch (e) {
            console.warn('Could not load LLM providers:', e);
        }
        
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
        
        document.getElementById('pitch-value').textContent = settings.pitch || 0.94;
        document.getElementById('speed-value').textContent = settings.speed || 1.3;
        
        // Hide TTS fields if TTS disabled
        const ttsEnabled = getTtsEnabled();
        ['setting-voice', 'setting-pitch', 'setting-speed'].forEach(id => {
            const el = document.getElementById(id)?.closest('.form-group');
            if (el) el.style.display = ttsEnabled ? '' : 'none';
        });
        
        // Show modal with animation
        settingsModal.style.display = 'flex';
        requestAnimationFrame(() => {
            settingsModal.classList.add('active');
            initModalSliders(); // Initialize slider fills
        });
        
    } catch (e) {
        console.error('Failed to load settings:', e);
        ui.showToast('Failed to load settings', 'error');
    }
}

function populateLlmDropdowns(settings) {
    const primarySelect = document.getElementById('setting-llm-primary');
    const fallbackSelect = document.getElementById('setting-llm-fallback');
    
    if (!primarySelect || !fallbackSelect) return;
    
    // Build options - Auto and None plus enabled providers
    const baseOptions = '<option value="auto">Auto (follow fallback order)</option>' +
        '<option value="none">None (disabled)</option>';
    
    const providerOptions = llmProviders
        .filter(p => p.enabled)
        .map(p => `<option value="${p.key}">${p.display_name}${p.is_local ? ' 🏠' : ' ☁️'}</option>`)
        .join('');
    
    primarySelect.innerHTML = baseOptions + providerOptions;
    fallbackSelect.innerHTML = baseOptions + providerOptions;
    
    // Set current values
    primarySelect.value = settings.llm_primary || 'auto';
    fallbackSelect.value = settings.llm_fallback || 'auto';
}

export async function saveSettings() {
    const { chatSelect, settingsModal } = getElements();
    
    try {
        const chatName = chatSelect.value;
        if (!chatName) return;
        
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
            llm_fallback: document.getElementById('setting-llm-fallback')?.value || 'auto'
        };
        
        await api.updateChatSettings(chatName, settings);
        closeSettingsModal();
        ui.showToast('Settings saved', 'success');
        await updateScene();
        updateSendButtonLLM(settings.llm_primary);
        
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

export async function saveAsDefaults() {
    // Confirm before overwriting defaults
    if (!confirm('Save these settings as defaults for all new chats?')) {
        return;
    }
    
    try {
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
            llm_fallback: document.getElementById('setting-llm-fallback')?.value || 'auto'
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

// Update range slider fill color based on value
function updateRangeSliderFill(slider) {
    const min = parseFloat(slider.min) || 0;
    const max = parseFloat(slider.max) || 100;
    const val = parseFloat(slider.value);
    const percent = ((val - min) / (max - min)) * 100;
    
    // Get computed colors - resolve actual color values
    const styles = getComputedStyle(document.documentElement);
    let fillColor = styles.getPropertyValue('--trim').trim();
    
    // If trim is transparent/empty/unset, use accent-blue
    if (!fillColor || fillColor === 'transparent' || fillColor.startsWith('var(')) {
        fillColor = styles.getPropertyValue('--accent-blue').trim() || '#4a9eff';
    }
    
    // Resolve bg-tertiary to actual color
    let bgColor = styles.getPropertyValue('--bg-tertiary').trim() || '#2a2a2a';
    
    slider.style.background = `linear-gradient(to right, ${fillColor} 0%, ${fillColor} ${percent}%, ${bgColor} ${percent}%, ${bgColor} 100%)`;
}

// Initialize all range sliders in modal with fill
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