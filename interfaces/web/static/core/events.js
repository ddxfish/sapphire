// core/events.js - Centralized event binding
import * as audio from '../audio.js';
import { getElements } from './state.js';

// Features
import { handleVolumeChange, handleMuteToggle } from '../features/volume.js';
import { handleMicPress, handleMicRelease, handleMicLeave, handleVisibilityChange } from '../features/mic.js';
import {
    handleChatChange,
    handleNewChat,
    handleDeleteChat,
    handleClearChat,
    handleExportChat,
    handleImportChat,
    handleImportFile,
    toggleKebab,
    closeAllKebabs,
    handleLogout,
    handleRestart
} from '../features/chat-manager.js';

// Handlers
import { handleSend, handleStop, handleInput, handleKeyDown, triggerSendWithText } from '../handlers/send-handlers.js';
import { handleToolbar } from '../handlers/message-handlers.js';

// Nav rail integration
import { setChatHeaderName } from './nav-rail.js';

export function bindAllEvents() {
    const el = getElements();

    // Form events
    el.form.addEventListener('submit', e => e.preventDefault());
    el.input.addEventListener('keydown', handleKeyDown);
    el.input.addEventListener('input', handleInput);
    el.sendBtn.addEventListener('click', handleSend);
    el.stopBtn.addEventListener('click', handleStop);

    // Volume controls
    el.volumeSlider.addEventListener('input', handleVolumeChange);
    el.muteBtn.addEventListener('click', handleMuteToggle);

    // Mic button - dual purpose (TTS stop or record)
    el.micBtn.addEventListener('mousedown', handleMicPress);
    el.micBtn.addEventListener('mouseup', () => handleMicRelease(triggerSendWithText));
    el.micBtn.addEventListener('touchstart', e => { e.preventDefault(); handleMicPress(); });
    el.micBtn.addEventListener('touchend', () => handleMicRelease(triggerSendWithText));
    el.micBtn.addEventListener('contextmenu', e => {
        e.preventDefault();
        audio.forceStop(el.micBtn, triggerSendWithText);
    });
    el.micBtn.addEventListener('mouseleave', () => handleMicLeave(triggerSendWithText));

    // Chat container toolbar clicks (event delegation)
    el.container.addEventListener('click', e => {
        const btn = e.target.closest('.toolbar button');
        if (btn) {
            e.stopPropagation();
            const action = btn.dataset.action;
            const idx = parseInt(btn.dataset.messageIndex);
            if (!isNaN(idx)) {
                handleToolbar(action, idx);
            }
        }
    });

    // Chat selector (hidden <select> for compatibility)
    el.chatSelect.addEventListener('change', handleChatChange);

    // Chat menu buttons (in header kebab)
    document.getElementById('new-chat-btn')?.addEventListener('click', handleNewChat);
    document.getElementById('delete-chat-btn')?.addEventListener('click', handleDeleteChat);
    el.clearChatBtn?.addEventListener('click', handleClearChat);
    el.importChatBtn?.addEventListener('click', handleImportChat);
    el.exportChatBtn?.addEventListener('click', handleExportChat);
    el.importFileInput?.addEventListener('change', handleImportFile);

    // Close kebab menus on outside click
    document.addEventListener('click', e => {
        if (!e.target.closest('.kebab-menu')) {
            closeAllKebabs();
        }
    });

    // Chat menu kebab toggle
    el.chatMenu?.querySelector('.kebab-btn')?.addEventListener('click', e => {
        e.stopPropagation();
        toggleKebab(el.chatMenu);
    });

    // Logout / Restart (may not exist until settings view is built)
    document.getElementById('logout-btn')?.addEventListener('click', handleLogout);
    document.getElementById('restart-btn')?.addEventListener('click', handleRestart);

    // Setup Wizard
    document.getElementById('setup-wizard-btn')?.addEventListener('click', () => {
        closeAllKebabs();
        if (window.sapphireSetupWizard) {
            window.sapphireSetupWizard.open(true);
        }
    });

    // Nav rail custom events (from flyout)
    document.addEventListener('nav:new-chat', () => handleNewChat());
    document.addEventListener('nav:switch-chat', e => {
        const chatName = e.detail?.chat;
        if (!chatName) return;
        el.chatSelect.value = chatName;
        handleChatChange();
        setChatHeaderName(chatName);
    });

    // Document-level events
    document.addEventListener('visibilitychange', () => handleVisibilityChange(triggerSendWithText));

    // Image ready handler (for inline cloning)
    document.addEventListener('imageReady', handleImageReady);
}

function handleImageReady(event) {
    const loadedImg = event.target;

    const message = loadedImg.closest('.message');
    if (!message) return;

    const content = message.querySelector('.message-content');
    if (!content) return;

    const accordions = content.querySelectorAll('details');
    const lastAccordion = accordions[accordions.length - 1];

    const inlineImg = loadedImg.cloneNode(true);
    inlineImg.dataset.inlineClone = 'true';

    if (lastAccordion) {
        lastAccordion.insertAdjacentElement('afterend', inlineImg);
    } else {
        content.insertBefore(inlineImg, content.firstChild);
    }

    // Force scroll
    import('../ui.js').then(ui => ui.forceScrollToBottom());
}

export function bindCleanupEvents(cleanupFn) {
    window.addEventListener('beforeunload', cleanupFn);
}
