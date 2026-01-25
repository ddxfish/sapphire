// main.js - Application orchestrator (optimized for parallel loading)
import * as audio from './audio.js';
import * as ui from './ui.js';
import * as api from './api.js';
import { initElements, initAvatar, refresh, setHistLen, getElements, getIsProc } from './core/state.js';
import { bindAllEvents, bindCleanupEvents } from './core/events.js';
import { initVolumeControls } from './features/volume.js';
import { startMicIconPolling, stopMicIconPolling, updateMicButtonState } from './features/mic.js';
import { populateChatDropdown } from './features/chat-manager.js';
import { updateScene, updateSendButtonLLM } from './features/scene.js';
import { applyTrimColor } from './features/chat-settings.js';
import { handleAutoRefresh } from './handlers/message-handlers.js';
import { setupImageHandlers } from './handlers/send-handlers.js';
import { setupImageModal } from './ui-images.js';
import * as eventBus from './core/event-bus.js';

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
        // Generate derived colors
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
    // If trim not set, CSS defaults from shared.css apply (blue)
    
    // Sidebar width
    const sidebarWidth = localStorage.getItem('sapphire-sidebar-width');
    if (sidebarWidth) {
        root.style.setProperty('--sidebar-width', sidebarWidth + 'px');
    }
    
    // Send button trim preference
    const sendBtnTrim = localStorage.getItem('sapphire-send-btn-trim');
    if (sendBtnTrim === 'true') {
        // Apply after DOM ready
        requestAnimationFrame(() => {
            const sendBtn = document.getElementById('send-btn');
            if (sendBtn) sendBtn.classList.add('use-trim');
        });
    }
}

// Initialize draggable sidebar resize handle
function initSidebarResize() {
    const sidebar = document.querySelector('.sidebar');
    if (!sidebar || window.innerWidth <= 768) return;
    
    // Create resize handle
    const handle = document.createElement('div');
    handle.className = 'sidebar-resize-handle';
    sidebar.appendChild(handle);
    
    let startX, startWidth;
    
    function onMouseDown(e) {
        e.preventDefault();
        startX = e.clientX;
        startWidth = sidebar.offsetWidth;
        handle.classList.add('dragging');
        document.body.classList.add('sidebar-resizing');
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
    }
    
    function onMouseMove(e) {
        const delta = e.clientX - startX;
        const newWidth = Math.min(Math.max(200, startWidth + delta), 500);
        document.documentElement.style.setProperty('--sidebar-width', newWidth + 'px');
    }
    
    function onMouseUp() {
        handle.classList.remove('dragging');
        document.body.classList.remove('sidebar-resizing');
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
        // Save to localStorage
        const currentWidth = sidebar.offsetWidth;
        localStorage.setItem('sapphire-sidebar-width', currentWidth);
    }
    
    handle.addEventListener('mousedown', onMouseDown);
    
    // Double-click to reset to default
    handle.addEventListener('dblclick', () => {
        localStorage.removeItem('sapphire-sidebar-width');
        document.documentElement.style.removeProperty('--sidebar-width');
    });
}

async function init() {
    const t0 = performance.now();
    
    try {
        // Initialize appearance settings first (sync, instant)
        initAppearance();
        
        // Initialize DOM references (sync, instant)
        initElements();
        
        // Sidebar starts collapsed (in HTML). Open on desktop.
        if (window.innerWidth > 768) {
            document.body.classList.remove('sidebar-collapsed');
        }
        
        const { form, sendBtn, micBtn, input } = getElements();
        
        // CRITICAL: Prevent form submission immediately before any async work
        // This prevents page reload if user clicks Send before handlers are bound
        form.addEventListener('submit', e => e.preventDefault());
        
        // Disable interactive input elements until fully loaded
        sendBtn.disabled = true;
        sendBtn.textContent = '⏳';
        if (micBtn) {
            micBtn.disabled = true;
            micBtn.style.opacity = '0.5';
        }
        input.placeholder = 'Loading Web UI...';
        input.classList.add('loading');
        
        // Show loading status in chat area
        ui.showStatus();
        ui.updateStatus('Loading...');
        
        // Parallel initialization - critical path only (no plugins yet)
        const [, historyLen] = await Promise.all([
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
                const settings = response?.settings || {};
                updateSendButtonLLM(settings.llm_primary || 'auto', settings.llm_model || '');
                applyTrimColor(settings.trim_color || '');
            }
        } catch (e) {
            updateSendButtonLLM('auto');
        }
        
        // These are fast sync operations
        initVolumeControls();
        initSidebarResize();
        startMicIconPolling();
        bindAllEvents();
        setupImageHandlers();
        setupImageModal();
        
        // Connect to event bus for real-time updates
        initEventBus();
        
        // Re-enable input elements now that everything is loaded
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
        
        // LAZY: Load plugins in background AFTER UI is interactive
        // This prevents plugin loading from blocking the main UI
        setTimeout(() => {
            initAvatar().then(() => {
                console.log('[Init] Plugins loaded in background');
            }).catch(e => {
                console.warn('[Init] Plugin loading failed:', e);
            });
        }, 100);
        
        // Start auto-refresh interval (fallback sync - events handle most updates)
        setInterval(handleAutoRefresh, 30000);
        
        console.log(`[Init] Complete in ${(performance.now() - t0).toFixed(0)}ms`);
        
    } catch (e) {
        console.error('Init error:', e);
        // Re-enable on error so user isn't stuck
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
    // Register event handlers
    
    // AI typing events
    eventBus.on(eventBus.Events.AI_TYPING_START, () => {
        console.log('[EventBus] AI typing started');
    });
    
    eventBus.on(eventBus.Events.AI_TYPING_END, () => {
        console.log('[EventBus] AI typing ended');
        if (!getIsProc()) {
            refresh(false);
        }
    });
    
    // TTS events
    eventBus.on(eventBus.Events.TTS_PLAYING, () => {
        console.log('[EventBus] TTS playing');
        audio.setLocalTtsPlaying(true);
        updateMicButtonState();
    });
    
    eventBus.on(eventBus.Events.TTS_STOPPED, () => {
        console.log('[EventBus] TTS stopped');
        audio.setLocalTtsPlaying(false);
        updateMicButtonState();
    });
    
    // Message events - instant refresh on changes
    eventBus.on(eventBus.Events.MESSAGE_ADDED, (data) => {
        console.log('[EventBus] Message added:', data?.role);
        if (!getIsProc()) {
            refresh(false);
        }
    });
    
    eventBus.on(eventBus.Events.MESSAGE_REMOVED, () => {
        console.log('[EventBus] Message removed');
        refresh(false);
    });
    
    eventBus.on(eventBus.Events.CHAT_CLEARED, () => {
        console.log('[EventBus] Chat cleared');
        refresh(false);
    });
    
    // System state events
    eventBus.on(eventBus.Events.PROMPT_CHANGED, () => {
        console.log('[EventBus] Prompt changed');
        updateScene();
    });
    
    eventBus.on(eventBus.Events.ABILITY_CHANGED, () => {
        console.log('[EventBus] Ability changed');
        updateScene();
    });
    
    eventBus.on(eventBus.Events.CHAT_SWITCHED, () => {
        console.log('[EventBus] Chat switched');
        populateChatDropdown();
        refresh(false);
        updateScene();
    });
    
    // STT events (for avatar/UI feedback)
    eventBus.on(eventBus.Events.STT_RECORDING_START, () => {
        console.log('[EventBus] STT recording started');
    });
    
    eventBus.on(eventBus.Events.STT_RECORDING_END, () => {
        console.log('[EventBus] STT recording ended');
    });
    
    eventBus.on(eventBus.Events.STT_PROCESSING, () => {
        console.log('[EventBus] STT processing');
    });
    
    // Wakeword event
    eventBus.on(eventBus.Events.WAKEWORD_DETECTED, () => {
        console.log('[EventBus] Wakeword detected');
    });
    
    // Tool events (for avatar "working" state)
    eventBus.on(eventBus.Events.TOOL_EXECUTING, (data) => {
        console.log('[EventBus] Tool executing:', data?.name);
    });
    
    eventBus.on(eventBus.Events.TOOL_COMPLETE, (data) => {
        console.log('[EventBus] Tool complete:', data?.name, data?.success);
    });
    
    // Error events
    eventBus.on(eventBus.Events.LLM_ERROR, (data) => {
        console.warn('[EventBus] LLM error:', data);
    });
    
    eventBus.on(eventBus.Events.STT_ERROR, (data) => {
        console.warn('[EventBus] STT error:', data);
    });
    
    // Connect to server
    eventBus.connect(false);
    
    // Expose for debugging
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