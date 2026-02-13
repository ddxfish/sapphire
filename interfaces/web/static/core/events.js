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
    el.muteBtn.addEventListener('click', () => {
        const compact = document.getElementById('volume-compact');
        if (compact) compact.classList.toggle('open');
    });

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

    // Header icon buttons
    document.getElementById('new-chat-btn')?.addEventListener('click', handleNewChat);
    document.getElementById('delete-chat-btn')?.addEventListener('click', handleDeleteChat);
    el.clearChatBtn?.addEventListener('click', handleClearChat);
    el.importChatBtn?.addEventListener('click', handleImportChat);
    el.exportChatBtn?.addEventListener('click', handleExportChat);
    el.importFileInput?.addEventListener('change', handleImportFile);

    // Chat picker dropdown
    const picker = document.getElementById('chat-picker');
    const pickerBtn = document.getElementById('chat-picker-btn');
    if (picker && pickerBtn) {
        pickerBtn.addEventListener('click', e => {
            e.stopPropagation();
            closeAllKebabs();
            picker.classList.toggle('open');
        });

        // Chat item clicks inside picker (event delegation)
        const dropdown = document.getElementById('chat-picker-dropdown');
        if (dropdown) {
            dropdown.addEventListener('click', e => {
                const item = e.target.closest('.chat-picker-item');
                if (!item) return;
                const chatName = item.dataset.chat;
                if (!chatName) return;
                picker.classList.remove('open');

                // Update active state immediately
                dropdown.querySelectorAll('.chat-picker-item').forEach(i => {
                    const active = i.dataset.chat === chatName;
                    i.classList.toggle('active', active);
                    i.querySelector('.chat-picker-item-check').textContent = active ? '\u2713' : '';
                });

                el.chatSelect.value = chatName;
                handleChatChange();
                setChatHeaderName(chatName);
            });
        }
    }

    // Close dropdowns on outside click
    document.addEventListener('click', e => {
        if (!e.target.closest('.kebab-menu')) closeAllKebabs();
        if (!e.target.closest('.chat-picker')) {
            document.getElementById('chat-picker')?.classList.remove('open');
        }
        if (!e.target.closest('.volume-compact')) {
            document.getElementById('volume-compact')?.classList.remove('open');
        }
    });

    // Chat menu kebab toggle
    el.chatMenu?.querySelector('.kebab-btn')?.addEventListener('click', e => {
        e.stopPropagation();
        document.getElementById('chat-picker')?.classList.remove('open');
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
