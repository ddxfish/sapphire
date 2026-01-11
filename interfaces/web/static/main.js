// main.js - Application orchestrator (optimized for parallel loading)
import * as audio from './audio.js';
import * as ui from './ui.js';
import * as api from './api.js';
import { initElements, initAvatar, refresh, setHistLen, getElements } from './core/state.js';
import { bindAllEvents, bindCleanupEvents } from './core/events.js';
import { initVolumeControls } from './features/volume.js';
import { startMicIconPolling, stopMicIconPolling } from './features/mic.js';
import { populateChatDropdown } from './features/chat-manager.js';
import { updateScene, updateSendButtonLLM } from './features/scene.js';
import { handleAutoRefresh } from './handlers/message-handlers.js';

async function init() {
    const t0 = performance.now();
    
    try {
        // Initialize DOM references (sync, instant)
        initElements();
        
        // Start with sidebar collapsed on mobile
        if (window.innerWidth <= 768) {
            document.body.classList.add('sidebar-collapsed');
        }
        
        // Parallel initialization - these are all independent operations
        const [, , historyLen] = await Promise.all([
            initAvatar(),           // Load plugins (can be slow)
            populateChatDropdown(), // Fetch chat list
            refresh(false),         // Fetch chat history
            updateScene()           // Fetch system status (prompts, abilities, TTS)
        ]);
        
        setHistLen(historyLen);
        
        // Update send button based on active chat's LLM setting
        try {
            const { chatSelect } = getElements();
            if (chatSelect?.value) {
                const response = await api.getChatSettings(chatSelect.value);
                updateSendButtonLLM(response?.settings?.llm_primary || 'auto');
            }
        } catch (e) {
            updateSendButtonLLM('auto');
        }
        
        // These are fast sync operations
        initVolumeControls();
        startMicIconPolling();
        bindAllEvents();
        
        // Scroll to bottom after render
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                ui.forceScrollToBottom();
            });
        });
        
        // Start auto-refresh interval
        setInterval(handleAutoRefresh, 3000);
        
        console.log(`[Init] Complete in ${(performance.now() - t0).toFixed(0)}ms`);
        
    } catch (e) {
        console.error('Init error:', e);
    }
}

function cleanup() {
    stopMicIconPolling();
    audio.stop();
}

// Boot
document.addEventListener('DOMContentLoaded', init);
bindCleanupEvents(cleanup);