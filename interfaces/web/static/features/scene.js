// features/scene.js - Scene state polling, LLM indicator, spice status
import * as api from '../api.js';
import * as audio from '../audio.js';
import { getElements, setTtsEnabled, setSttEnabled, setSttReady, setPromptPrivacyRequired } from '../core/state.js';

// Call this when chat's primary LLM is known (from chat-manager, chat-settings)
export function updateSendButtonLLM(primary, model = '') {
    const sendBtn = document.getElementById('send-btn');
    const indicator = document.getElementById('llm-indicator');
    if (!sendBtn) return;

    // Remove all mode classes first
    sendBtn.classList.remove('llm-local', 'llm-cloud', 'llm-auto');
    if (indicator) indicator.classList.remove('cloud');

    // Detect local vs cloud — local providers have local URLs (localhost, 127.0.0.1)
    // Default to cloud for any named provider that isn't obviously local
    const localPatterns = ['lmstudio', 'ollama'];
    const isLocal = localPatterns.includes(primary) || primary === 'none';
    const isCloud = !isLocal && primary !== 'auto';

    // Build display name
    const displayName = primary === 'none' ? 'Off' :
                       primary ? primary.replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) : 'Local';

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

// Vault hunt U5: newest-wins guard, same pattern as every sibling painter
// (_sbPaintSeq, _listPaintSeq). Two overlapping status fetches during a
// lock→unlock storm could let the OLDER response paint the eyeball/border
// with stale vault state.
let _sceneSeq = 0;

export async function updateScene() {
    const mySeq = ++_sceneSeq;
    try {
        // Use unified status endpoint - single call for all state
        const status = await api.fetchStatus();
        if (mySeq !== _sceneSeq) return null;   // superseded — discard

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

        // Private-chat eyeball + toolset cloud warning (status is the sync
        // source — covers chat switches, persona loads, and other tabs).
        // Amber = private chat whose vault is asleep (exists && !unlocked);
        // this poll is the authoritative painter for that third state.
        const priv = !!status?.chat_settings?.private_chat;
        const eye = document.getElementById('sb-privacy-eye');
        const vaultOpen = !!status?.vault?.exists && !!status?.vault?.unlocked;
        const vaultAsleep = priv && !!status?.vault?.exists && !status?.vault?.unlocked;
        if (eye) {
            // Eyeball = VAULT STATE, one job (Krem's ruling 2026-08-14):
            // blue = private mode armed (talking marks chats private);
            // amber = standing in a private chat while sealed (the rare
            // failed-eviction edge). Per-CHAT privacy shows on the chatbg
            // border and the 🗝 in the dropdown, never on the toggle.
            eye.classList.toggle('private-on', vaultOpen);
            eye.classList.toggle('vault-locked', vaultAsleep);
            eye.title = vaultAsleep ? 'Private chat, vault locked — click to unlock'
                : vaultOpen ? 'Private mode ON — talking marks the chat private; click to lock'
                : 'Private mode off — click to unlock the vault';
        }
        // Privacy border on the chat area: blue glow = private + vault open
        // (or plain v1 private), amber = private + vault asleep.
        const chatbg = document.getElementById('chatbg');
        if (chatbg) {
            chatbg.classList.toggle('privacy-open', priv && !vaultAsleep);
            chatbg.classList.toggle('privacy-asleep', vaultAsleep);
        }
        const toolsetSel = document.getElementById('sb-toolset');
        if (toolsetSel) {
            const warn = priv && !!status?.has_cloud_tools;
            toolsetSel.classList.toggle('toolset-cloud-warn', warn);
            toolsetSel.title = warn ? 'This toolset contains web tools — they will refuse in a private chat' : '';
        }

        return status;
    } catch {
        return null;
    }
}


