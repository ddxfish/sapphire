// features/chat-settings.js - Chat settings modal
import * as api from '../api.js';
import * as ui from '../ui.js';
import { getElements, getTtsEnabled } from '../core/state.js';
import { closeAllKebabs } from './chat-manager.js';
import { updateScene } from './scene.js';

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
        });
        
    } catch (e) {
        console.error('Failed to load settings:', e);
        ui.showToast('Failed to load settings', 'error');
    }
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
            custom_context: document.getElementById('setting-custom-context').value
        };
        
        await api.updateChatSettings(chatName, settings);
        closeSettingsModal();
        ui.showToast('Settings saved', 'success');
        await updateScene();
        
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
            custom_context: document.getElementById('setting-custom-context').value
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
}

export function handleSpeedInput(e) {
    document.getElementById('speed-value').textContent = e.target.value;
}

export function handleModalBackdropClick(e) {
    const { settingsModal } = getElements();
    if (e.target === settingsModal) closeSettingsModal();
}