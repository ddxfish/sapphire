// main.js - Application orchestrator
import * as audio from './audio.js';
import * as ui from './ui.js';
import { initElements, refresh, setHistLen, getElements, getIsProc } from './core/state.js';
import { bindAllEvents, bindCleanupEvents } from './core/events.js';
import { initVolumeControls } from './features/volume.js';
import { startMicIconPolling, stopMicIconPolling, updateMicButtonState } from './features/mic.js';
import { populateChatDropdown } from './features/chat-manager.js';
import { updateScene, updateSendButtonLLM } from './features/scene.js';
import { applyTrimColor } from './features/chat-settings.js';
import { initPrivacy } from './features/privacy.js';
import { handleAutoRefresh } from './handlers/message-handlers.js';
import { setupImageHandlers } from './handlers/send-handlers.js';
import { setupImageModal } from './ui-images.js';
import * as eventBus from './core/event-bus.js';
import { getInitData } from './shared/init-data.js';

// New architecture
import { registerView, initRouter } from './core/router.js';
import { initNavRail, updateChatFlyout, setChatHeaderName } from './core/nav-rail.js';
import chatView from './views/chat.js';
import personasView from './views/personas.js';
import toolsetsView from './views/toolsets.js';
import spicesView from './views/spices.js';
import scheduleView from './views/schedule.js';
import settingsView from './views/settings.js';

// Initialize appearance settings from localStorage (theme, density, font, trim)
function initAppearance() {
    const root = document.documentElement;

    // Density
    const density = localStorage.getItem('sapphire-density');
    if (density && density !== 'default') {
        root.setAttribute('data-density', density);
    }

    // Font
    const font = localStorage.getItem('sapphire-font');
    if (font && font !== 'system') {
        root.setAttribute('data-font', font);
    }

    // Trim color - apply if explicitly set
    const trim = localStorage.getItem('sapphire-trim');
    if (trim) {
        root.style.setProperty('--trim', trim);
        const r = parseInt(trim.slice(1, 3), 16);
        const g = parseInt(trim.slice(3, 5), 16);
        const b = parseInt(trim.slice(5, 7), 16);
        root.style.setProperty('--trim-glow', `rgba(${r}, ${g}, ${b}, 0.35)`);
        root.style.setProperty('--trim-light', `rgba(${r}, ${g}, ${b}, 0.15)`);
        root.style.setProperty('--trim-border', `rgba(${r}, ${g}, ${b}, 0.4)`);
        root.style.setProperty('--trim-50', `rgba(${r}, ${g}, ${b}, 0.5)`);
        root.style.setProperty('--accordion-header-bg', `rgba(${r}, ${g}, ${b}, 0.08)`);
        root.style.setProperty('--accordion-header-hover', `rgba(${r}, ${g}, ${b}, 0.12)`);
    }

    // Send button trim preference
    const sendBtnTrim = localStorage.getItem('sapphire-send-btn-trim');
    if (sendBtnTrim === 'true') {
        requestAnimationFrame(() => {
            const sendBtn = document.getElementById('send-btn');
            if (sendBtn) sendBtn.classList.add('use-trim');
        });
    }
}

async function init() {
    const t0 = performance.now();

    try {
        initAppearance();
        initElements();

        const { form, sendBtn, micBtn, input } = getElements();

        // Prevent form submission immediately
        form.addEventListener('submit', e => e.preventDefault());

        // Disable input until loaded
        sendBtn.disabled = true;
        sendBtn.textContent = '\u23F3';
        if (micBtn) {
            micBtn.disabled = true;
            micBtn.style.opacity = '0.5';
        }
        input.placeholder = 'Loading Web UI...';
        input.classList.add('loading');

        ui.showStatus();
        ui.updateStatus('Loading...');

        // Register views with router
        registerView('chat', chatView);
        registerView('personas', personasView);
        registerView('toolsets', toolsetsView);
        registerView('spices', spicesView);
        registerView('schedule', scheduleView);
        registerView('settings', settingsView);

        // Init nav rail + router
        initNavRail();
        initRouter('chat');

        // Fetch init data
        let initData = null;
        try {
            initData = await getInitData();
            ui.initFromInitData(initData);
        } catch (e) {
            console.warn('[Init] Could not fetch init data:', e);
        }

        // Parallel initialization
        const [status, historyLen] = await Promise.all([
            updateScene(),
            refresh(false)
        ]);

        setHistLen(historyLen);

        // Populate chat dropdown + nav flyout
        if (status?.chats) {
            ui.renderChatDropdown(status.chats, status.active_chat);
            updateChatFlyout(
                status.chats.map(c => c.name || c),
                status.active_chat
            );
            setChatHeaderName(status.active_chat);
        } else {
            await populateChatDropdown();
        }

        // Apply chat settings
        const settings = status?.chat_settings || {};
        updateSendButtonLLM(settings.llm_primary || 'auto', settings.llm_model || '');
        applyTrimColor(settings.trim_color || '');

        // Sync operations
        initVolumeControls();
        startMicIconPolling();
        bindAllEvents();
        setupImageHandlers();
        setupImageModal();
        initPrivacy();

        initEventBus();

        // Re-enable input
        sendBtn.disabled = false;
        sendBtn.textContent = 'Send';
        if (micBtn) {
            micBtn.disabled = false;
            micBtn.style.opacity = '1';
        }
        input.placeholder = 'Type message... (paste or drop images)';
        input.classList.remove('loading');
        ui.hideStatus();

        // Scroll to bottom after render
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                ui.forceScrollToBottom();
            });
        });

        // Auto-refresh interval (fallback - events handle most updates)
        setInterval(handleAutoRefresh, 30000);

        console.log(`[Init] Complete in ${(performance.now() - t0).toFixed(0)}ms`);

    } catch (e) {
        console.error('Init error:', e);
        const { sendBtn, micBtn, input } = getElements();
        if (sendBtn) {
            sendBtn.disabled = false;
            sendBtn.textContent = 'Send';
        }
        if (micBtn) {
            micBtn.disabled = false;
            micBtn.style.opacity = '1';
        }
        if (input) {
            input.placeholder = 'Type message... (paste or drop images)';
            input.classList.remove('loading');
        }
        ui.hideStatus();
    }
}

function initEventBus() {
    // Debounced refresh - prevents multiple /api/history calls from racing
    let refreshTimer = null;
    const debouncedRefresh = () => {
        if (refreshTimer) clearTimeout(refreshTimer);
        refreshTimer = setTimeout(() => {
            refreshTimer = null;
            if (!getIsProc()) refresh(false);
        }, 100);
    };

    // AI typing events
    eventBus.on(eventBus.Events.AI_TYPING_START, () => {
        console.log('[EventBus] AI typing started');
    });

    eventBus.on(eventBus.Events.AI_TYPING_END, () => {
        console.log('[EventBus] AI typing ended');
        debouncedRefresh();
    });

    // TTS events
    eventBus.on(eventBus.Events.TTS_PLAYING, () => {
        audio.setLocalTtsPlaying(true);
        updateMicButtonState();
    });

    eventBus.on(eventBus.Events.TTS_STOPPED, () => {
        audio.setLocalTtsPlaying(false);
        updateMicButtonState();
    });

    // Message events
    eventBus.on(eventBus.Events.MESSAGE_ADDED, () => debouncedRefresh());
    eventBus.on(eventBus.Events.MESSAGE_REMOVED, () => debouncedRefresh());
    eventBus.on(eventBus.Events.CHAT_CLEARED, () => debouncedRefresh());

    // Debounced updateScene
    let sceneTimer = null;
    const debouncedUpdateScene = () => {
        if (sceneTimer) clearTimeout(sceneTimer);
        sceneTimer = setTimeout(() => {
            sceneTimer = null;
            updateScene();
        }, 100);
    };

    // System state events
    eventBus.on(eventBus.Events.PROMPT_CHANGED, () => debouncedUpdateScene());
    eventBus.on(eventBus.Events.TOOLSET_CHANGED, () => debouncedUpdateScene());
    eventBus.on(eventBus.Events.SPICE_CHANGED, () => debouncedUpdateScene());
    eventBus.on(eventBus.Events.COMPONENTS_CHANGED, () => debouncedUpdateScene());
    eventBus.on(eventBus.Events.PROMPT_DELETED, () => debouncedUpdateScene());
    eventBus.on(eventBus.Events.SETTINGS_CHANGED, () => debouncedUpdateScene());
    eventBus.on(eventBus.Events.CHAT_SETTINGS_CHANGED, () => debouncedUpdateScene());

    eventBus.on(eventBus.Events.CHAT_SWITCHED, () => {
        populateChatDropdown();
    });

    // STT events
    eventBus.on(eventBus.Events.STT_RECORDING_START, () => {});
    eventBus.on(eventBus.Events.STT_RECORDING_END, () => {});
    eventBus.on(eventBus.Events.STT_PROCESSING, () => {});
    eventBus.on(eventBus.Events.WAKEWORD_DETECTED, () => {});

    // Tool events
    eventBus.on(eventBus.Events.TOOL_EXECUTING, () => {});
    eventBus.on(eventBus.Events.TOOL_COMPLETE, () => {});

    // Error events
    eventBus.on(eventBus.Events.LLM_ERROR, (data) => {
        console.warn('[EventBus] LLM error:', data);
    });

    eventBus.on(eventBus.Events.STT_ERROR, (data) => {
        console.warn('[EventBus] STT error:', data);
        if (data?.message) ui.showToast(data.message, 'error');
    });

    // Connect to server
    eventBus.connect(false);
    window.eventBus = eventBus;
}

function cleanup() {
    stopMicIconPolling();
    eventBus.disconnect();
    audio.stop();
}

// Boot
document.addEventListener('DOMContentLoaded', init);
bindCleanupEvents(cleanup);
