// features/scene.js - Scene state polling, LLM indicator, spice/story status
import * as api from '../api.js';
import * as audio from '../audio.js';
import { getElements, setTtsEnabled, setSttEnabled, setSttReady, setPromptPrivacyRequired } from '../core/state.js';
import { updateStoryIndicator } from './story.js';

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
        
        // Update TTS playing status in audio.js
        audio.setLocalTtsPlaying(status?.tts_playing || false);

        setPromptPrivacyRequired(status?.prompt_privacy_required || false);
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

