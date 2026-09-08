// core/events.js - Centralized event binding
import * as audio from '../audio.js';
import { getElements } from './state.js';

// Features
import { handleVolumeChange, handleMuteToggle } from '../features/volume.js';
import { handleMicPress, handleMicRelease, handleMicLeave, handleVisibilityChange } from '../features/mic.js';
import {
    handleChatChange,
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

export function bindAllEvents() {
    const el = getElements();

    // Form events
    el.form.addEventListener('submit', e => e.preventDefault());
    el.input.addEventListener('keydown', handleKeyDown);
    el.input.addEventListener('input', handleInput);
    el.sendBtn.addEventListener('click', handleSend);
    el.stopBtn.addEventListener('click', handleStop);

    // Audio flyout (volume + conversation) hangs off the mic caret
    el.volumeSlider.addEventListener('input', handleVolumeChange);
    el.muteBtn.addEventListener('click', () => {
        const m = !audio.isMuted();
        audio.setMuted(m);
        el.muteBtn.textContent = m ? '\u{1F507}' : '\u{1F50A}';
        el.muteBtn.classList.toggle('muted', m);
        localStorage.setItem('sapphire-muted', String(m));
    });
    document.getElementById('mic-menu-btn')?.addEventListener('click', e => {
        e.stopPropagation();
        document.getElementById('mic-split')?.classList.toggle('open');
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

    // Header icon buttons (chat picker + new/delete handled by views/chat.js with sb- prefixed IDs)
    el.clearChatBtn?.addEventListener('click', handleClearChat);
    el.importChatBtn?.addEventListener('click', handleImportChat);
    el.exportChatBtn?.addEventListener('click', handleExportChat);
    el.importFileInput?.addEventListener('change', handleImportFile);

    // Close dropdowns on outside click
    document.addEventListener('click', e => {
        if (!e.target.closest('.kebab-menu')) closeAllKebabs();
        if (!e.target.closest('.mic-split')) {
            document.getElementById('mic-split')?.classList.remove('open');
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

    // Document-level events
    document.addEventListener('visibilitychange', () => handleVisibilityChange(triggerSendWithText));

}

// Image ready handler (for inline cloning) — bound at MODULE LOAD, not in
// bindAllEvents: init renders history BEFORE bindAllEvents runs, with awaits
// between, so a browser-CACHED image could load and dispatch into a document
// with no listener — the inline clone silently lost on every refresh of a
// chat whose image was cached (Krem live-hit 2026-08-15). Module scope runs
// at first import, before any render exists to race.
document.addEventListener('imageReady', handleImageReady);

// PATH 2 of 2 for tool images: clones each accordion tool-result image (rendered
// in ui-parsing.js, the loop tagged "PATH 1 of 2") out into the reply body, right
// after the accordion. This is what surfaces tool images into the visible reply.
// Every createImageElement image that loads inside a .message hits this.
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

    // Follow the bottom only while the reader is stuck to it. A FORCED snap
    // here (per image, re-arming sticky) undid the transcript's scroll carry
    // on every background re-render of an image chat (D3-B1, 2026-09-08).
    import('../ui.js').then(ui => ui.followIfSticky());
}

export function bindCleanupEvents(cleanupFn) {
    window.addEventListener('beforeunload', cleanupFn);
}
