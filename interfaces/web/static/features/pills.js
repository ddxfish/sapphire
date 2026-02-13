// features/pills.js - Prompt and ability pill dropdowns with caching
import * as ui from '../ui.js';
import * as api from '../api.js';
import { getElements } from '../core/state.js';
import { updateScene } from './scene.js';
import { openSettingsModal } from './chat-settings.js';
import * as eventBus from '../core/event-bus.js';
import { getInitData, getInitDataSync, refreshInitData } from '../shared/init-data.js';
import { closeStoryDropdown } from './story.js';

// Cache for dropdown data - avoids fetch on every click
let promptsCache = null;
let toolsetsCache = null;

// Initialize cache and subscribe to SSE events for invalidation
export function initPillsCache() {
    // Pre-populate from init data (already fetched by main.js)
    const initData = getInitDataSync();
    if (initData) {
        promptsCache = initData.prompts?.list || null;
        toolsetsCache = initData.toolsets?.list || null;
    }

    // Invalidate caches and refresh init data when relevant events occur
    eventBus.on(eventBus.Events.PROMPT_CHANGED, () => { promptsCache = null; refreshInitData(); });
    eventBus.on(eventBus.Events.PROMPT_DELETED, () => { promptsCache = null; refreshInitData(); });
    eventBus.on(eventBus.Events.TOOLSET_CHANGED, () => { toolsetsCache = null; refreshInitData(); });
}

export function closePillDropdowns() {
    const { promptPill, abilityPill } = getElements();
    promptPill?.classList.remove('dropdown-open');
    abilityPill?.classList.remove('dropdown-open');
    closeStoryDropdown();
}

export async function handleSpiceToggle(e) {
    e.preventDefault();
    e.stopPropagation();

    const { spiceIndicator } = getElements();
    const chatSelect = document.getElementById('chat-select');
    if (!spiceIndicator || !chatSelect?.value) return;

    // Don't toggle if unavailable (monolith mode)
    if (spiceIndicator.classList.contains('unavailable')) {
        ui.showToast('Spice unavailable in monolith mode', 'info');
        return;
    }

    const isCurrentlyEnabled = spiceIndicator.classList.contains('active');
    const newState = !isCurrentlyEnabled;

    try {
        await api.toggleSpice(chatSelect.value, newState);
        await updateScene();
        ui.showToast(newState ? 'Spice enabled' : 'Spice disabled', 'success');
    } catch (err) {
        ui.showToast(`Failed: ${err.message}`, 'error');
    }
}

export async function showPromptDropdown(e) {
    e.preventDefault();
    closePillDropdowns();

    const { promptPill } = getElements();
    const dropdown = promptPill.querySelector('.pill-dropdown');
    promptPill.classList.add('dropdown-open');

    // Use cache if available, otherwise fetch fresh
    let prompts = promptsCache;
    if (!prompts) {
        const initData = await getInitData();
        prompts = initData?.prompts?.list || [];
        promptsCache = prompts;
    }

    const currentPrompt = promptPill.querySelector('.pill-text').textContent.split(' (')[0];

    dropdown.innerHTML = prompts.map(p =>
        `<button class="pill-dropdown-item${p.name === currentPrompt ? ' active' : ''}" data-name="${p.name}">${p.name}</button>`
    ).join('');

    dropdown.querySelectorAll('.pill-dropdown-item').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const name = btn.dataset.name;
            try {
                await fetch(`/api/prompts/${encodeURIComponent(name)}/load`, { method: 'POST' });
                closePillDropdowns();
                await updateScene();
                ui.showToast(`Prompt: ${name}`, 'success');
            } catch (err) {
                ui.showToast(`Failed: ${err.message}`, 'error');
            }
        });
    });
}

export async function showAbilityDropdown(e) {
    e.preventDefault();
    closePillDropdowns();

    const { abilityPill } = getElements();
    const dropdown = abilityPill.querySelector('.pill-dropdown');
    abilityPill.classList.add('dropdown-open');

    // Use cache if available, otherwise fetch fresh
    let tsList = toolsetsCache;
    if (!tsList) {
        const initData = await getInitData();
        tsList = initData?.toolsets?.list || [];
        toolsetsCache = tsList;
    }

    const currentToolset = abilityPill.querySelector('.pill-text').textContent.split(' (')[0];

    dropdown.innerHTML = tsList.map(t =>
        `<button class="pill-dropdown-item${t.name === currentToolset ? ' active' : ''}" data-name="${t.name}">${t.name} (${t.function_count})</button>`
    ).join('');

    dropdown.querySelectorAll('.pill-dropdown-item').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const name = btn.dataset.name;
            try {
                await fetch(`/api/toolsets/${encodeURIComponent(name)}/activate`, { method: 'POST' });
                closePillDropdowns();
                await updateScene();
                ui.showToast(`Toolset: ${name}`, 'success');
            } catch (err) {
                ui.showToast(`Failed: ${err.message}`, 'error');
            }
        });
    });
}

export function handlePillRightClick(e) {
    e.preventDefault();
    openSettingsModal();
}

export function handleOutsideClick(e) {
    if (!e.target.closest('.pill')) {
        closePillDropdowns();
    }
}
