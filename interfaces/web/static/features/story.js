// features/story.js - Story Engine pill indicator and controls
import * as ui from '../ui.js';

let storyMenu = null;
let storyBtn = null;
let isDropdownOpen = false;
let currentPreset = null;
let presetsCache = null;
let saveSlotsCache = null;

export function initStoryIndicator() {
    storyMenu = document.getElementById('story-indicator');
    if (!storyMenu) return;

    storyBtn = storyMenu.querySelector('.story-btn');
    if (!storyBtn) return;

    // Click handler for dropdown toggle
    storyBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        toggleDropdown();
    });

    // Dropdown item handlers
    storyMenu.querySelectorAll('.story-dropdown-item').forEach(item => {
        item.addEventListener('click', async (e) => {
            e.stopPropagation();
            const action = item.dataset.action;
            closeDropdown();
            await handleAction(action);
        });
    });

    // Close on outside click
    document.addEventListener('click', (e) => {
        if (isDropdownOpen && !storyMenu.contains(e.target)) {
            closeDropdown();
        }
    });

    // Load presets for submenu
    loadPresets();
}

function toggleDropdown() {
    if (isDropdownOpen) {
        closeDropdown();
    } else {
        openDropdown();
    }
}

async function openDropdown() {
    if (storyMenu) storyMenu.classList.add('dropdown-open');
    isDropdownOpen = true;
    // Refresh save slots when opening dropdown
    if (currentPreset) {
        await loadSaveSlots();
        populateSaveLoadSubmenus();
    }
}

function closeDropdown() {
    if (storyMenu) storyMenu.classList.remove('dropdown-open');
    isDropdownOpen = false;
}

export function closeStoryDropdown() {
    closeDropdown();
}

async function loadPresets() {
    try {
        const res = await fetch('/api/story/presets');
        if (!res.ok) return;
        const data = await res.json();
        // Filter out base presets (start with _)
        presetsCache = (data.presets || []).filter(p => !p.name.startsWith('_'));
        populatePresetSubmenu();
    } catch (e) {
        console.warn('Failed to load story presets:', e);
    }
}

function populatePresetSubmenu() {
    if (!storyMenu || !presetsCache) return;

    const submenu = storyMenu.querySelector('.story-preset-submenu');
    if (!submenu) return;

    submenu.innerHTML = presetsCache.map(p => `
        <button class="story-preset-item${p.name === currentPreset ? ' active' : ''}" data-preset="${p.name}">
            <span class="story-preset-check">✓</span>
            <span>${p.display_name || p.name}</span>
        </button>
    `).join('');

    // Add click handlers
    submenu.querySelectorAll('.story-preset-item').forEach(item => {
        item.addEventListener('click', async (e) => {
            e.stopPropagation();
            const presetName = item.dataset.preset;
            closeDropdown();
            await switchPreset(presetName);
        });
    });
}

async function switchPreset(presetName) {
    const chatSelect = document.getElementById('chat-select');
    const chatName = chatSelect?.value;
    if (!chatName) return;

    try {
        // Update chat settings with new preset and enable story engine
        const res = await fetch(`/api/chats/${encodeURIComponent(chatName)}/settings`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                settings: {
                    story_engine_enabled: true,
                    story_preset: presetName
                }
            })
        });
        if (!res.ok) throw new Error('Failed to switch preset');

        // Ask if user wants to reset progress
        const shouldReset = confirm(`Switched to "${presetName}". Would you like to reset progress and start fresh?`);

        if (shouldReset) {
            await fetch(`/api/story/${encodeURIComponent(chatName)}/reset`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ preset: presetName })
            });
            ui.showToast('Story switched and reset', 'success');
        } else {
            ui.showToast('Story switched', 'success');
        }

        // Refresh scene to update UI
        const { updateScene } = await import('./scene.js');
        await updateScene();
    } catch (e) {
        ui.showToast('Failed to switch story', 'error');
    }
}

async function handleAction(action) {
    const chatSelect = document.getElementById('chat-select');
    const chatName = chatSelect?.value;
    if (!chatName) return;

    switch (action) {
        case 'view':
            await showStateView(chatName);
            break;
        case 'history':
            await showHistory(chatName);
            break;
        case 'reset':
            await resetState(chatName);
            break;
        case 'disable':
            await disableStory(chatName);
            break;
        case 'enable':
            await enableStory(chatName);
            break;
    }
}

async function showStateView(chatName) {
    try {
        const res = await fetch(`/api/story/${encodeURIComponent(chatName)}`);
        if (!res.ok) throw new Error('Failed to fetch state');
        const data = await res.json();

        const state = data.state || {};
        const keys = Object.keys(state).filter(k => !k.startsWith('_'));

        if (keys.length === 0) {
            ui.showToast('No state variables set', 'info');
            return;
        }

        // Format state for display
        const lines = keys.map(k => {
            const v = state[k];
            const label = v.label || k;
            const value = v.value;
            return `${label}: ${value}`;
        });

        alert(`Story State (${data.preset || 'Unknown'})\n\n${lines.join('\n')}`);
    } catch (e) {
        ui.showToast('Failed to load state', 'error');
    }
}

async function showHistory(chatName) {
    try {
        const res = await fetch(`/api/story/${encodeURIComponent(chatName)}/history?limit=20`);
        if (!res.ok) throw new Error('Failed to fetch history');
        const data = await res.json();

        const history = data.history || [];
        if (history.length === 0) {
            ui.showToast('No state changes yet', 'info');
            return;
        }

        // Format history for display
        const lines = history.map(h => {
            const change = h.old_value !== null
                ? `${h.old_value} → ${h.new_value}`
                : `set to ${h.new_value}`;
            return `Turn ${h.turn}: ${h.key} ${change}`;
        });

        alert(`State History (last ${history.length})\n\n${lines.join('\n')}`);
    } catch (e) {
        ui.showToast('Failed to load history', 'error');
    }
}

async function resetState(chatName) {
    if (!confirm('Reset story progress? This will restart from the beginning.')) {
        return;
    }

    try {
        const res = await fetch(`/api/story/${encodeURIComponent(chatName)}/reset`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ preset: null })  // Reset with current preset
        });
        if (!res.ok) throw new Error('Failed to reset');

        ui.showToast('Story progress reset', 'success');

        // Refresh scene to update UI
        const { updateScene } = await import('./scene.js');
        await updateScene();
    } catch (e) {
        ui.showToast('Failed to reset state', 'error');
    }
}

async function disableStory(chatName) {
    try {
        // Update chat settings to disable state engine
        const res = await fetch(`/api/chats/${encodeURIComponent(chatName)}/settings`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ settings: { story_engine_enabled: false } })
        });
        if (!res.ok) throw new Error('Failed to disable');

        ui.showToast('Story engine disabled', 'success');

        // Refresh scene to update UI
        const { updateScene } = await import('./scene.js');
        await updateScene();
    } catch (e) {
        ui.showToast('Failed to disable story engine', 'error');
    }
}

async function enableStory(chatName) {
    try {
        // Update chat settings to enable state engine
        const res = await fetch(`/api/chats/${encodeURIComponent(chatName)}/settings`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ settings: { story_engine_enabled: true } })
        });
        if (!res.ok) throw new Error('Failed to enable');

        // Ask if user wants to reset progress
        const shouldReset = confirm('Story enabled! Would you like to reset progress and start from the beginning?');

        if (shouldReset) {
            await fetch(`/api/story/${encodeURIComponent(chatName)}/reset`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ preset: null })
            });
            ui.showToast('Story enabled and reset', 'success');
        } else {
            ui.showToast('Story enabled', 'success');
        }

        // Refresh scene to update UI
        const { updateScene } = await import('./scene.js');
        await updateScene();
    } catch (e) {
        ui.showToast('Failed to enable story engine', 'error');
    }
}

/**
 * Update the story indicator based on status data
 * @param {Object|null} story - Story status from /status endpoint
 */
export function updateStoryIndicator(story) {
    if (!storyMenu) {
        storyMenu = document.getElementById('story-indicator');
    }
    if (!storyMenu) return;

    const tooltipEl = storyMenu.querySelector('.story-tooltip');
    const headerTextEl = storyMenu.querySelector('.story-dropdown-header-text');
    const btn = storyMenu.querySelector('.story-btn');

    // Always show the indicator
    storyMenu.style.display = 'inline-flex';

    // Get dropdown content sections
    const activeContent = storyMenu.querySelector('.story-dropdown-active');
    const inactiveContent = storyMenu.querySelector('.story-dropdown-inactive');

    // No story engine active - show greyed out
    if (!story || !story.enabled) {
        storyMenu.classList.remove('active');
        currentPreset = null;
        if (tooltipEl) tooltipEl.textContent = 'Story Engine disabled';
        if (headerTextEl) headerTextEl.textContent = '📖 Select Story';
        if (btn) btn.title = 'Story Engine (disabled)';
        // Show inactive menu options
        if (activeContent) activeContent.style.display = 'none';
        if (inactiveContent) inactiveContent.style.display = 'block';
        populatePresetSubmenu();
        return;
    }

    // Story engine active
    storyMenu.classList.add('active');
    // Show active menu options
    if (activeContent) activeContent.style.display = 'block';
    if (inactiveContent) inactiveContent.style.display = 'none';

    // Story name for header and tooltip
    const storyName = story.preset_display || 'Story';
    const presetChanged = currentPreset !== story.preset;
    currentPreset = story.preset || null;

    // Load save slots if preset changed
    if (presetChanged && currentPreset) {
        loadSaveSlots();
    }

    // Build progress info
    let progressText = '';
    if (story.iterator_value !== undefined) {
        if (story.iterator_max) {
            // Linear: "Scene 2/5"
            const iterName = story.iterator_key === 'scene' ? 'Scene' : story.iterator_key;
            progressText = `${iterName} ${story.iterator_value}/${story.iterator_max}`;
        } else if (typeof story.iterator_value === 'string') {
            // Rooms: room name
            progressText = story.iterator_value.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        } else {
            progressText = `${story.iterator_key} ${story.iterator_value}`;
        }
    }

    // Dropdown header shows story name
    if (headerTextEl) headerTextEl.textContent = `📖 ${storyName}`;

    // Tooltip shows name + progress + stats
    const parts = [storyName];
    if (progressText) parts.push(progressText);
    if (story.turn) parts.push(`Turn ${story.turn}`);
    if (tooltipEl) tooltipEl.textContent = parts.join(' • ');

    // Button title for accessibility
    if (btn) btn.title = storyName;

    // Update submenu checkmarks and save slots
    populatePresetSubmenu();
    populateSaveLoadSubmenus();
}

async function loadSaveSlots() {
    if (!currentPreset) {
        saveSlotsCache = null;
        return;
    }

    try {
        const res = await fetch(`/api/story/saves/${encodeURIComponent(currentPreset)}`);
        if (!res.ok) return;
        const data = await res.json();
        saveSlotsCache = data.slots || [];
    } catch (e) {
        console.warn('Failed to load save slots:', e);
        saveSlotsCache = null;
    }
}

function populateSaveLoadSubmenus() {
    if (!storyMenu) return;

    const saveSubmenu = storyMenu.querySelector('.story-save-submenu');
    const loadSubmenu = storyMenu.querySelector('.story-load-submenu');

    if (!saveSubmenu || !loadSubmenu) return;

    // Generate slot HTML
    const slots = saveSlotsCache || [];
    const slotHtml = (action) => {
        let html = '';
        for (let i = 1; i <= 5; i++) {
            const slot = slots.find(s => s.slot === i);
            const isEmpty = !slot || slot.empty;
            const timestamp = isEmpty ? 'Empty' : formatTimestamp(slot.timestamp);
            const turn = isEmpty ? '' : ` (Turn ${slot.turn})`;
            html += `<button class="story-slot-item" data-action="${action}" data-slot="${i}">
                <span class="story-slot-num">${i}</span>
                <span class="story-slot-info">${timestamp}${turn}</span>
            </button>`;
        }
        return html;
    };

    saveSubmenu.innerHTML = slotHtml('save');
    loadSubmenu.innerHTML = slotHtml('load');

    // Add click handlers
    saveSubmenu.querySelectorAll('.story-slot-item').forEach(item => {
        item.addEventListener('click', async (e) => {
            e.stopPropagation();
            const slot = parseInt(item.dataset.slot);
            closeDropdown();
            await saveGame(slot);
        });
    });

    loadSubmenu.querySelectorAll('.story-slot-item').forEach(item => {
        item.addEventListener('click', async (e) => {
            e.stopPropagation();
            const slot = parseInt(item.dataset.slot);
            const slotData = slots.find(s => s.slot === slot);
            if (!slotData || slotData.empty) {
                ui.showToast(`Slot ${slot} is empty`, 'info');
                return;
            }
            closeDropdown();
            await loadGame(slot);
        });
    });
}

function formatTimestamp(isoString) {
    if (!isoString) return 'Empty';
    try {
        const date = new Date(isoString);
        return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
    } catch {
        return 'Saved';
    }
}

async function saveGame(slot) {
    const chatSelect = document.getElementById('chat-select');
    const chatName = chatSelect?.value;
    if (!chatName) return;

    try {
        const res = await fetch(`/api/story/${encodeURIComponent(chatName)}/save`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ slot })
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.error || 'Save failed');
        }

        ui.showToast(`Game saved to slot ${slot}`, 'success');

        // Refresh save slots
        await loadSaveSlots();
        populateSaveLoadSubmenus();
    } catch (e) {
        ui.showToast(`Save failed: ${e.message}`, 'error');
    }
}

async function loadGame(slot) {
    if (!confirm(`Load save from slot ${slot}? Current progress will be replaced.`)) {
        return;
    }

    const chatSelect = document.getElementById('chat-select');
    const chatName = chatSelect?.value;
    if (!chatName) return;

    try {
        const res = await fetch(`/api/story/${encodeURIComponent(chatName)}/load`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ slot })
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.error || 'Load failed');
        }

        ui.showToast(`Game loaded from slot ${slot}`, 'success');

        // Refresh scene to update UI
        const { updateScene } = await import('./scene.js');
        await updateScene();
    } catch (e) {
        ui.showToast(`Load failed: ${e.message}`, 'error');
    }
}
