// shared/persona-tabs.js - Shared tab bar for persona group views
import { switchView } from '../core/router.js';

const TABS = [
    { id: 'personas', label: 'Personas', icon: '\u{1F3AD}' },
    { id: 'prompts', label: 'Prompts', icon: '\u{1F464}' },
    { id: 'toolsets', label: 'Toolsets', icon: '\u{1F527}' },
    { id: 'spices', label: 'Spices', icon: '\u{1F336}\u{FE0F}' },
];

/**
 * Render the shared tab bar HTML for persona group views.
 * @param {string} activeId - Currently active tab ID
 * @returns {string} HTML string
 */
export function renderPersonaTabs(activeId) {
    return `<div class="persona-tabs">
        ${TABS.map(t => `<button class="persona-tab${t.id === activeId ? ' active' : ''}" data-view="${t.id}">${t.icon} ${t.label}</button>`).join('')}
    </div>`;
}

/**
 * Bind click events on persona tabs within a container.
 * Call once per render (event delegation safe).
 * @param {HTMLElement} container
 */
export function bindPersonaTabs(container) {
    const tabs = container.querySelector('.persona-tabs');
    if (!tabs) return;
    tabs.addEventListener('click', e => {
        const btn = e.target.closest('.persona-tab');
        if (!btn) return;
        const viewId = btn.dataset.view;
        if (viewId) switchView(viewId);
    });
}
