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
import { refreshInitData } from './shared/init-data.js';
import { initPrivacy } from './features/privacy.js';
import { initUserProfile } from './features/user-profile.js';
import { handleAutoRefresh } from './handlers/message-handlers.js';
import { setupImageHandlers } from './handlers/send-handlers.js';
import { setupImageModal } from './ui-images.js';
import * as eventBus from './core/event-bus.js';
import { getInitData } from './shared/init-data.js';

// New architecture
import { registerView, initRouter } from './core/router.js';
import { initNavRail, setChatHeaderName } from './core/nav-rail.js';

// View modules loaded dynamically — a broken view cannot kill the app
const _v = window.__v ? `?v=${window.__v}` : '';
const VIEW_MODULES = {
    chat:     `./views/chat.js${_v}`,
    personas: `./views/personas.js${_v}`,
    prompts:  `./views/prompts.js${_v}`,
    toolsets: `./views/toolsets.js${_v}`,
    spices:   `./views/spices.js${_v}`,
    schedule: `./views/schedule.js${_v}`,
    mind:     `./views/mind.js${_v}`,
    settings: `./views/settings.js${_v}`,
};

async function loadViews() {
    await Promise.allSettled(
        Object.entries(VIEW_MODULES).map(async ([id, path]) => {
            try {
                const mod = await import(path);
                registerView(id, mod.default);
            } catch (e) {
                console.error(`[Views] Failed to load '${id}' from ${path}:`, e);
                registerView(id, {
                    init(el) {
                        el.innerHTML = `<div class="view-placeholder">
                            <h2>Failed to load ${id}</h2>
                            <p style="color:var(--text-muted);font-size:var(--font-sm)">${e.message}</p>
                            <p style="color:var(--text-muted);font-size:var(--font-sm)">Try a hard refresh (Ctrl+Shift+R)</p>
                        </div>`;
                    },
                    show() {},
                    hide() {}
                });
            }
        })
    );
}

// Initialize appearance settings from localStorage (theme, density, font)
// Trim color is per-persona now — default cyan set in CSS body
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

    // Clean up stale trim localStorage (now per-persona)
    localStorage.removeItem('sapphire-trim');

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

        // Load views dynamically (isolated — one broken view won't kill the app)
        await loadViews();

        // Init nav rail + router
        initNavRail();
        initRouter('chat');

        // Hide nav items for disabled plugins (non-blocking)
        syncNavWithPlugins();

        // === DATA FETCH (can fail without killing the app) ===
        let initData = null;
        try {
            initData = await getInitData();
            ui.initFromInitData(initData);
        } catch (e) {
            console.warn('[Init] Could not fetch init data:', e);
        }

        // Use allSettled so one failure doesn't kill the other
        const [sceneResult, refreshResult] = await Promise.allSettled([
            updateScene(),
            refresh(false)
        ]);

        const status = sceneResult.status === 'fulfilled' ? sceneResult.value : null;
        const historyLen = refreshResult.status === 'fulfilled' ? refreshResult.value : 0;

        if (sceneResult.status === 'rejected') console.warn('[Init] updateScene failed:', sceneResult.reason);
        if (refreshResult.status === 'rejected') console.warn('[Init] refresh failed:', refreshResult.reason);

        setHistLen(historyLen);

        // Populate chat dropdown + picker
        if (status?.chats) {
            ui.renderChatDropdown(status.chats, status.active_chat);
        } else {
            try { await populateChatDropdown(); } catch (e) { console.warn('[Init] Chat dropdown failed:', e); }
        }

        // Apply chat settings
        const settings = status?.chat_settings || {};
        updateSendButtonLLM(settings.llm_primary || 'auto', settings.llm_model || '');
        applyTrimColor(settings.trim_color || '');

        // === UI WIRING (must always run) ===
        initVolumeControls();
        startMicIconPolling();
        bindAllEvents();
        setupImageHandlers();
        setupImageModal();
        initPrivacy();
        initUserProfile();

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

        // Setup wizard auto-show on first launch
        const wizardStep = initData?.wizard_step;
        if (typeof wizardStep === 'number' && wizardStep < 3) {
            setTimeout(async () => {
                try {
                    const mod = await import(`./plugins/setup-wizard/index.js${_v}`);
                    if (mod.default?.init) await mod.default.init();
                } catch (e) { console.warn('[Init] Setup wizard failed:', e); }
            }, 500);
        }

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

        // Still wire up core UI even on error
        try {
            initVolumeControls();
            startMicIconPolling();
            bindAllEvents();
            setupImageHandlers();
            setupImageModal();
            initPrivacy();
            initUserProfile();
            initEventBus();
        } catch (e2) {
            console.error('UI wiring failed:', e2);
        }
    }
}

// Plugins that own a nav-rail view — hide nav if plugin disabled
const PLUGIN_NAV_MAP = { continuity: 'schedule' };

function syncNavWithPlugins() {
    fetch('/api/webui/plugins').then(r => r.ok ? r.json() : null).then(d => {
        if (!d?.plugins) return;
        for (const [plugin, view] of Object.entries(PLUGIN_NAV_MAP)) {
            const p = d.plugins.find(x => x.name === plugin);
            if (p && !p.enabled) {
                const btn = document.querySelector(`.nav-item[data-view="${view}"]`);
                if (btn) btn.style.display = 'none';
            }
        }
    }).catch(() => {});
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

    // Server restart detection — full state resync
    eventBus.on(eventBus.Events.SERVER_RESTARTED, async () => {
        console.log('[Main] Server restarted — full resync');
        await refreshInitData();
        await populateChatDropdown();
        await refresh(false);
        await updateScene();
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
