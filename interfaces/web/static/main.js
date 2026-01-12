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
    
    // Trim color
    const trim = localStorage.getItem('sapphire-trim');
    if (trim) {
        if (trim === 'none') {
            root.style.setProperty('--trim', 'transparent');
            root.style.setProperty('--trim-glow', 'transparent');
            root.style.setProperty('--trim-light', 'transparent');
            root.style.setProperty('--trim-border', 'transparent');
            root.style.setProperty('--trim-50', 'transparent');
            root.style.setProperty('--accordion-header-bg', 'var(--bg-tertiary)');
            root.style.setProperty('--accordion-header-hover', 'var(--bg-hover)');
        } else {
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
    }
    
    // Sidebar width
    const sidebarWidth = localStorage.getItem('sapphire-sidebar-width');
    if (sidebarWidth) {
        root.style.setProperty('--sidebar-width', sidebarWidth + 'px');
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
        initSidebarResize();
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