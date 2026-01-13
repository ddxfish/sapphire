// core/events.js - Centralized event binding
import * as audio from '../audio.js';
import { getElements, getIsProc } from './state.js';

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
import { 
    openSettingsModal, 
    saveSettings, 
    closeSettingsModal, 
    saveAsDefaults,
    handlePitchInput,
    handleSpeedInput,
    handleModalBackdropClick
} from '../features/chat-settings.js';
import { 
    showPromptDropdown, 
    showAbilityDropdown, 
    handlePillRightClick,
    handleOutsideClick,
    closePillDropdowns,
    handleSpiceToggle
} from '../features/pills.js';

// Handlers
import { handleSend, handleStop, handleInput, handleKeyDown, triggerSendWithText } from '../handlers/send-handlers.js';
import { handleToolbar } from '../handlers/message-handlers.js';

// Check if mobile viewport
function isMobile() {
    return window.innerWidth <= 768;
}

// Check if sidebar is open
function isSidebarOpen() {
    return !document.body.classList.contains('sidebar-collapsed');
}

// Close sidebar
function closeSidebar() {
    document.body.classList.add('sidebar-collapsed');
}

export function bindAllEvents() {
    const el = getElements();
    
    // Form events
    el.form.addEventListener('submit', e => e.preventDefault());
    el.input.addEventListener('keydown', handleKeyDown);
    el.input.addEventListener('input', handleInput);
    el.sendBtn.addEventListener('click', handleSend);
    el.stopBtn.addEventListener('click', handleStop);
    
    // Sidebar toggle
    el.toggleBtn.addEventListener('click', () => document.body.classList.toggle('sidebar-collapsed'));
    
    // Stop TTS button (if exists)
    if (el.stopTtsBtn) {
        el.stopTtsBtn.addEventListener('click', () => audio.stop());
    }
    
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
                console.log(`Toolbar action: ${action} on message ${idx}`);
                handleToolbar(action, idx);
            }
        }
    });
    
    // Chat selector and menu
    el.chatSelect.addEventListener('change', handleChatChange);
    document.getElementById('new-chat-btn')?.addEventListener('click', handleNewChat);
    document.getElementById('delete-chat-btn')?.addEventListener('click', handleDeleteChat);
    document.getElementById('settings-gear-btn')?.addEventListener('click', openSettingsModal);
    el.clearChatBtn?.addEventListener('click', handleClearChat);
    el.importChatBtn?.addEventListener('click', handleImportChat);
    el.exportChatBtn?.addEventListener('click', handleExportChat);
    el.importFileInput?.addEventListener('change', handleImportFile);
    
    // Settings modal
    document.getElementById('save-settings').addEventListener('click', saveSettings);
    document.getElementById('cancel-settings').addEventListener('click', closeSettingsModal);
    document.getElementById('modal-cancel-btn')?.addEventListener('click', closeSettingsModal);
    document.getElementById('save-as-defaults')?.addEventListener('click', saveAsDefaults);
    document.getElementById('setting-pitch').addEventListener('input', handlePitchInput);
    document.getElementById('setting-speed').addEventListener('input', handleSpeedInput);
    el.settingsModal.addEventListener('click', handleModalBackdropClick);
    
    // Pills
    el.promptPill?.addEventListener('click', showPromptDropdown);
    el.abilityPill?.addEventListener('click', showAbilityDropdown);
    el.promptPill?.addEventListener('contextmenu', handlePillRightClick);
    el.abilityPill?.addEventListener('contextmenu', handlePillRightClick);
    
    // Spice indicator
    el.spiceIndicator?.addEventListener('click', handleSpiceToggle);
    
    // Close dropdowns and sidebar on outside click
    document.addEventListener('click', e => {
        handleOutsideClick(e);
        
        // Close kebab menus if click outside
        if (!e.target.closest('.kebab-menu')) {
            closeAllKebabs();
        }
        
        // Mobile: close sidebar when clicking outside it
        if (isMobile() && isSidebarOpen()) {
            const sidebar = document.querySelector('.sidebar');
            const toggleBtn = document.getElementById('toggle-sidebar');
            
            // If click is not inside sidebar and not on toggle button, close sidebar
            if (!sidebar.contains(e.target) && !toggleBtn.contains(e.target)) {
                closeSidebar();
            }
        }
    });
    
    // Kebab menu toggles
    el.appMenu?.querySelector('.kebab-btn')?.addEventListener('click', e => {
        e.stopPropagation();
        toggleKebab(el.appMenu);
    });
    el.chatMenu?.querySelector('.kebab-btn')?.addEventListener('click', e => {
        e.stopPropagation();
        toggleKebab(el.chatMenu);
    });
    
    // Logout
    document.getElementById('logout-btn')?.addEventListener('click', handleLogout);
    
    // Restart
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
    const imageId = event.detail.imageId;
    
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