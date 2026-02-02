// features/pills.js - Prompt and ability pill dropdowns with caching
import * as ui from '../ui.js';
import * as api from '../api.js';
import { getElements } from '../core/state.js';
import { updateScene } from './scene.js';
import { openSettingsModal } from './chat-settings.js';
import * as eventBus from '../core/event-bus.js';

// Cache for dropdown data - avoids fetch on every click
let promptsCache = null;
let abilitiesCache = null;

// Initialize cache and subscribe to SSE events for invalidation
export function initPillsCache() {
    // Invalidate caches when relevant events occur
    eventBus.on(eventBus.Events.PROMPT_CHANGED, () => { promptsCache = null; });
    eventBus.on(eventBus.Events.PROMPT_DELETED, () => { promptsCache = null; });
    eventBus.on(eventBus.Events.ABILITY_CHANGED, () => { abilitiesCache = null; });
    // Note: No pre-fetch - plugins already fetch this data at init
    // Cache populated on first dropdown click
}

export function closePillDropdowns() {
    const { promptPill, abilityPill } = getElements();
    promptPill?.classList.remove('dropdown-open');
    abilityPill?.classList.remove('dropdown-open');
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

    // Use cache if available, otherwise fetch
    let prompts = promptsCache;
    if (!prompts) {
        dropdown.innerHTML = '<div class="pill-dropdown-item" style="color:var(--text-muted)">Loading...</div>';
        try {
            const res = await fetch('/api/prompts');
            if (!res.ok) throw new Error('Failed to fetch');
            const data = await res.json();
            prompts = data.prompts || [];
            promptsCache = prompts;
        } catch (err) {
            dropdown.innerHTML = '<div class="pill-dropdown-item" style="color:var(--error)">Error loading</div>';
            return;
        }
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

    // Use cache if available, otherwise fetch
    let abilities = abilitiesCache;
    if (!abilities) {
        dropdown.innerHTML = '<div class="pill-dropdown-item" style="color:var(--text-muted)">Loading...</div>';
        try {
            const res = await fetch('/api/abilities');
            if (!res.ok) throw new Error('Failed to fetch');
            const data = await res.json();
            abilities = data.abilities || [];
            abilitiesCache = abilities;
        } catch (err) {
            dropdown.innerHTML = '<div class="pill-dropdown-item" style="color:var(--error)">Error loading</div>';
            return;
        }
    }

    const currentAbility = abilityPill.querySelector('.pill-text').textContent.split(' (')[0];

    dropdown.innerHTML = abilities.map(a =>
        `<button class="pill-dropdown-item${a.name === currentAbility ? ' active' : ''}" data-name="${a.name}">${a.name} (${a.function_count})</button>`
    ).join('');

    dropdown.querySelectorAll('.pill-dropdown-item').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const name = btn.dataset.name;
            try {
                await fetch(`/api/abilities/${encodeURIComponent(name)}/activate`, { method: 'POST' });
                closePillDropdowns();
                await updateScene();
                ui.showToast(`Ability: ${name}`, 'success');
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
