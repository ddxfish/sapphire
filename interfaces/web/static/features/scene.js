// features/scene.js - Scene state, prompt display, functions display
import * as api from '../api.js';
import * as audio from '../audio.js';
import { getElements, setTtsEnabled, setSttEnabled, setSttReady, setPromptPrivacyRequired } from '../core/state.js';
import { updateStoryIndicator } from './story.js';

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

        if (status?.stt_enabled !== undefined) {
            setSttEnabled(status.stt_enabled);
            setSttReady(status.stt_ready ?? true);
            const { micBtn } = getElements();
            if (micBtn) {
                const canRecord = status.stt_enabled && status.stt_ready;
                const needsRestart = status.stt_enabled && !status.stt_ready;
                micBtn.classList.toggle('stt-disabled', !canRecord);
                micBtn.classList.toggle('stt-needs-restart', needsRestart);
                // Update title for clarity
                if (!status.stt_enabled) {
                    micBtn.dataset.sttTitle = 'STT disabled';
                } else if (!status.stt_ready) {
                    micBtn.dataset.sttTitle = 'STT loading — downloading speech model';
                } else {
                    micBtn.dataset.sttTitle = 'Hold to record';
                }
            }
        }
        
        // Track cloud tools status
        hasCloudTools = status?.has_cloud_tools || false;
        
        // Track TTS playing status (from unified endpoint)
        // Update both local state and audio.js
        ttsPlaying = status?.tts_playing || false;
        audio.setLocalTtsPlaying(ttsPlaying);
        
        updatePrompt(status?.prompt, status?.prompt_name, status?.prompt_char_count, status?.prompt_privacy_required);
        setPromptPrivacyRequired(status?.prompt_privacy_required || false);
        updateFuncs(status?.functions, status?.toolset, hasCloudTools, status?.state_tools);
        updateSpice(status?.spice);
        updateStoryIndicator(status?.story);

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
        const parts = [];
        if (spice.current) parts.push(`Active: ${spice.current}`);
        if (spice.next) parts.push(`Next: ${spice.next}`);
        tooltipEl.textContent = parts.length ? parts.join('\n') : 'No spice yet';
    } else {
        spiceIndicator.classList.remove('active');
        spiceIndicator.title = 'Spice disabled (click to enable)';
        tooltipEl.textContent = 'Spice disabled';
    }
}

// NOTE: promptPill is NOT in state.js initElements() — always undefined.
// Prompt display is the #sb-prompt dropdown in chat.js.
// This function is dead code but kept for future pill UI (Phase 6).
function updatePrompt(state, promptName, charCount, privacyRequired) {
    const { promptPill } = getElements();
    if (!promptPill) return;

    const textEl = promptPill.querySelector('.pill-text');
    const tooltipEl = promptPill.querySelector('.pill-tooltip');

    // Format char count (2400 -> 2.4k)
    const formatCount = (n) => n >= 1000 ? (n / 1000).toFixed(1) + 'k' : n;
    const displayName = promptName || 'Unknown';
    const displayCount = charCount !== undefined ? ` (${formatCount(charCount)})` : '';
    const lockIcon = privacyRequired ? '🔒 ' : '';

    textEl.textContent = `${lockIcon}${displayName}${displayCount}`;

    // Add/remove private class for styling
    if (privacyRequired) {
        promptPill.classList.add('prompt-private');
    } else {
        promptPill.classList.remove('prompt-private');
    }
    
    // Build tooltip from state
    if (state) {
        const parts = [];
        ['location', 'character', 'goals', 'scenario', 'relationship', 'format'].forEach(k => {
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

// NOTE: abilityPill is NOT in state.js initElements() — always undefined.
// Toolset display is the #sb-toolset dropdown in chat.js, synced via saveSettings().
// This function is dead code but kept for future pill UI (Phase 6).
function updateFuncs(funcs, toolset, cloudTools, stateTools) {
    const { abilityPill } = getElements();
    if (!abilityPill) return;

    const textEl = abilityPill.querySelector('.pill-text');
    const tooltipEl = abilityPill.querySelector('.pill-tooltip');

    // Total count includes both user tools and state tools
    const totalCount = (funcs?.length || 0) + (stateTools?.length || 0);

    if (totalCount === 0) {
        textEl.textContent = 'None (0)';
        tooltipEl.textContent = 'No functions enabled';
        abilityPill.classList.remove('cloud-tools');
        return;
    }

    const name = toolset?.name || 'Custom';
    const storyCount = toolset?.story_tools || 0;
    textEl.textContent = storyCount ? `${name} + Story (${totalCount})` : `${name} (${totalCount})`;

    // Build tooltip with sections
    const parts = [];
    if (cloudTools) parts.push('⚠️ Network tools enabled');
    if (stateTools?.length) parts.push(`📖 Story: ${stateTools.join(', ')}`);
    const customStory = toolset?.story_custom_tools || [];
    if (customStory.length) parts.push(`📖 Custom: ${customStory.join(', ')}`);
    if (funcs?.length) parts.push(`🔧 Tools: ${funcs.join(', ')}`);

    tooltipEl.textContent = parts.join('\n');

    // Yellow indicator only for toolsets with network/cloud tools
    if (cloudTools) {
        abilityPill.classList.add('cloud-tools');
    } else {
        abilityPill.classList.remove('cloud-tools');
    }
}