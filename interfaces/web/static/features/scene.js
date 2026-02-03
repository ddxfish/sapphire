// features/scene.js - Scene state, prompt display, functions display
import * as api from '../api.js';
import * as audio from '../audio.js';
import { getElements, setTtsEnabled } from '../core/state.js';

let hasCloudTools = false;
let ttsPlaying = false;

export function getHasCloudTools() {
    return hasCloudTools;
}

export function getTtsPlaying() {
    return ttsPlaying;
}

// Call this when chat's primary LLM is known (from chat-manager, chat-settings)
export function updateSendButtonLLM(primary, model = '') {
    const sendBtn = document.getElementById('send-btn');
    const indicator = document.getElementById('llm-indicator');
    if (!sendBtn) return;

    // Remove all mode classes first
    sendBtn.classList.remove('llm-local', 'llm-cloud', 'llm-auto');
    if (indicator) indicator.classList.remove('cloud');

    // Cloud providers
    const cloudProviders = ['claude', 'openai', 'fireworks', 'other'];
    const isCloud = cloudProviders.includes(primary);

    // Build display name - capitalize first letter
    const displayName = primary === 'lmstudio' ? 'LM Studio' :
                       primary === 'none' ? 'Off' :
                       primary ? primary.charAt(0).toUpperCase() + primary.slice(1) : 'Local';

    // Build title suffix for model
    const modelSuffix = model ? ` (${model.split('/').pop()})` : '';

    if (primary === 'auto') {
        sendBtn.classList.add('llm-auto');
        sendBtn.title = 'Send (auto LLM selection)';
        if (indicator) indicator.textContent = 'Auto';
    } else if (isCloud) {
        sendBtn.classList.add('llm-cloud');
        sendBtn.title = `Send: ${primary}${modelSuffix}`;
        if (indicator) {
            indicator.textContent = displayName;
            indicator.classList.add('cloud');
        }
    } else {
        // lmstudio, none, or unknown = local
        sendBtn.classList.add('llm-local');
        sendBtn.title = primary === 'none' ? 'Send (LLM disabled)' : `Send: ${primary || 'local'}${modelSuffix}`;
        if (indicator) indicator.textContent = displayName;
    }
}

export async function updateScene() {
    try {
        // Use unified status endpoint - single call for all state
        const status = await api.fetchStatus();
        
        if (status?.tts_enabled !== undefined) {
            setTtsEnabled(status.tts_enabled);
            const volumeRow = document.querySelector('.sidebar-row-3');
            if (volumeRow) volumeRow.style.display = status.tts_enabled ? '' : 'none';
        }
        
        // Track cloud tools status
        hasCloudTools = status?.has_cloud_tools || false;
        
        // Track TTS playing status (from unified endpoint)
        // Update both local state and audio.js
        ttsPlaying = status?.tts_playing || false;
        audio.setLocalTtsPlaying(ttsPlaying);
        
        updatePrompt(status?.prompt, status?.prompt_name, status?.prompt_char_count);
        updateFuncs(status?.functions, status?.ability, hasCloudTools);
        updateSpice(status?.spice);
        
        return status;
    } catch {
        return null;
    }
}

function updateSpice(spice) {
    const { spiceIndicator } = getElements();
    if (!spiceIndicator) return;
    
    const tooltipEl = spiceIndicator.querySelector('.spice-tooltip');
    
    // Handle missing spice data
    if (!spice) {
        spiceIndicator.classList.remove('active', 'unavailable');
        spiceIndicator.title = 'Spice status unknown';
        tooltipEl.textContent = '';
        return;
    }
    
    // Not available in monolith mode
    if (!spice.available) {
        spiceIndicator.classList.remove('active');
        spiceIndicator.classList.add('unavailable');
        spiceIndicator.title = 'Spice unavailable (monolith prompt)';
        tooltipEl.textContent = 'Monolith mode';
        return;
    }
    
    spiceIndicator.classList.remove('unavailable');
    
    if (spice.enabled) {
        spiceIndicator.classList.add('active');
        spiceIndicator.title = 'Spice enabled (click to disable)';
        tooltipEl.textContent = spice.current || 'No spice yet';
    } else {
        spiceIndicator.classList.remove('active');
        spiceIndicator.title = 'Spice disabled (click to enable)';
        tooltipEl.textContent = 'Spice disabled';
    }
}

function updatePrompt(state, promptName, charCount) {
    const { promptPill } = getElements();
    if (!promptPill) return;
    
    const textEl = promptPill.querySelector('.pill-text');
    const tooltipEl = promptPill.querySelector('.pill-tooltip');
    
    // Format char count (2400 -> 2.4k)
    const formatCount = (n) => n >= 1000 ? (n / 1000).toFixed(1) + 'k' : n;
    const displayName = promptName || 'Unknown';
    const displayCount = charCount !== undefined ? ` (${formatCount(charCount)})` : '';
    
    textEl.textContent = `${displayName}${displayCount}`;
    
    // Build tooltip from state
    if (state) {
        const parts = [];
        ['location', 'persona', 'goals', 'scenario', 'relationship', 'format'].forEach(k => {
            const v = state[k];
            if (v && v !== 'default' && v !== 'none') parts.push(`${k}: ${v}`);
        });
        if (state.extras?.length > 0) parts.push(`extras: ${state.extras.join(', ')}`);
        if (state.emotions?.length > 0) parts.push(`emotions: ${state.emotions.join(', ')}`);
        tooltipEl.textContent = parts.length ? parts.join('\n') : 'Monolith prompt';
    } else {
        tooltipEl.textContent = '';
    }
}

function updateFuncs(funcs, ability, cloudTools) {
    const { abilityPill } = getElements();
    if (!abilityPill) return;
    
    const textEl = abilityPill.querySelector('.pill-text');
    const tooltipEl = abilityPill.querySelector('.pill-tooltip');
    
    if (!funcs || funcs.length === 0) {
        textEl.textContent = 'None (0)';
        tooltipEl.textContent = 'No functions enabled';
        abilityPill.classList.remove('cloud-tools');
        return;
    }
    
    const name = ability?.name || 'Custom';
    const count = ability?.function_count || funcs.length;
    
    textEl.textContent = `${name} (${count})`;
    tooltipEl.textContent = funcs.join(', ');
    
    // Yellow indicator only for toolsets with network/cloud tools
    if (cloudTools) {
        abilityPill.classList.add('cloud-tools');
        tooltipEl.textContent = '🌐 Network tools enabled\n' + funcs.join(', ');
    } else {
        abilityPill.classList.remove('cloud-tools');
    }
}