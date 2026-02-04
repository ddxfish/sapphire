// features/privacy.js - Privacy Mode UI
import { fetchWithTimeout } from '../shared/fetch.js';
import { showToast } from '../shared/toast.js';

let privacyEnabled = false;

// DOM elements
let privacyOverlay = null;
let privacyToggleBtn = null;

export function isPrivacyMode() {
    return privacyEnabled;
}

export async function fetchPrivacyStatus() {
    try {
        const data = await fetchWithTimeout('/api/privacy');
        privacyEnabled = data.privacy_mode;
        updatePrivacyUI();
        return data;
    } catch (e) {
        console.warn('[Privacy] Failed to fetch status:', e);
    }
    return null;
}

export async function setPrivacyMode(enabled) {
    try {
        const data = await fetchWithTimeout('/api/privacy', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled })
        });

        privacyEnabled = data.privacy_mode;
        updatePrivacyUI();
        showToast(data.message, 'success');
        return true;
    } catch (e) {
        console.error('[Privacy] Failed to set mode:', e);
        showToast('Failed to toggle privacy mode', 'error');
    }
    return false;
}

export async function togglePrivacyMode() {
    return setPrivacyMode(!privacyEnabled);
}

function updatePrivacyUI() {
    // Update body class for CSS
    document.body.classList.toggle('privacy-mode', privacyEnabled);

    // Update toggle button text
    if (privacyToggleBtn) {
        privacyToggleBtn.textContent = privacyEnabled ? '🔓 Exit Privacy Mode' : '🔒 Privacy Mode';
    }

    // Create or remove overlay
    if (privacyEnabled && !privacyOverlay) {
        createPrivacyOverlay();
    } else if (!privacyEnabled && privacyOverlay) {
        privacyOverlay.remove();
        privacyOverlay = null;
    }
}

function createPrivacyOverlay() {
    privacyOverlay = document.createElement('div');
    privacyOverlay.className = 'privacy-overlay';
    privacyOverlay.innerHTML = `
        <div class="privacy-border"></div>
        <div class="privacy-tab">PRIVATE</div>
    `;
    document.body.appendChild(privacyOverlay);
}

export function initPrivacy() {
    // Create toggle button in app menu
    const appMenuDropdown = document.querySelector('#app-menu .kebab-dropdown');
    if (appMenuDropdown) {
        // Insert before setup wizard button
        const setupBtn = document.getElementById('setup-wizard-btn');
        privacyToggleBtn = document.createElement('button');
        privacyToggleBtn.id = 'privacy-toggle-btn';
        privacyToggleBtn.textContent = '🔒 Privacy Mode';
        privacyToggleBtn.addEventListener('click', async () => {
            await togglePrivacyMode();
            // Close menu after toggle
            appMenuDropdown.parentElement.classList.remove('open');
        });

        if (setupBtn) {
            appMenuDropdown.insertBefore(privacyToggleBtn, setupBtn);
        } else {
            appMenuDropdown.prepend(privacyToggleBtn);
        }
    }

    // Fetch initial status
    fetchPrivacyStatus();
}
