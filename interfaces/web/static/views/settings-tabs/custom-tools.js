// settings-tabs/custom-tools.js - Settings declared by AI-created tools
let toolSettingsData = null;

function escAttr(s) { return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;'); }

function formatLabel(key) {
    return key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function renderToolInput(key, value) {
    const id = `setting-${key}`;
    if (typeof value === 'boolean') {
        return `<label class="setting-toggle">
            <input type="checkbox" id="${id}" data-key="${key}" ${value ? 'checked' : ''}>
            <span>${value ? 'Enabled' : 'Disabled'}</span>
        </label>`;
    }
    if (typeof value === 'number') {
        return `<input type="number" id="${id}" data-key="${key}" value="${value}" step="any">`;
    }
    return `<input type="text" id="${id}" data-key="${key}" value="${escAttr(value)}">`;
}

export default {
    id: 'custom-tools',
    name: 'Custom Tools',
    icon: '\uD83E\uDDE9',
    description: 'Settings from AI-created tools',

    render(ctx) {
        if (!toolSettingsData || Object.keys(toolSettingsData).length === 0) {
            return '<div class="settings-empty">No custom tools have registered settings yet.</div>';
        }

        let html = '';
        for (const [toolName, entries] of Object.entries(toolSettingsData)) {
            const label = toolName.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
            const rows = entries.map(e => {
                const isOverridden = ctx.overrides.includes(e.key);
                const value = ctx.settings[e.key] !== undefined ? ctx.settings[e.key] : e.value;
                const isFullWidth = typeof value === 'boolean';
                return `
                    <div class="setting-row${isOverridden ? ' overridden' : ''}${isFullWidth ? ' full-width' : ''}" data-key="${e.key}">
                        <div class="setting-label">
                            <div class="setting-label-row">
                                <label>${formatLabel(e.key)}</label>
                                ${isOverridden ? '<span class="override-badge">Custom</span>' : ''}
                            </div>
                            ${e.help ? `<div class="setting-help">${e.help}</div>` : ''}
                        </div>
                        <div class="setting-input">
                            ${renderToolInput(e.key, value)}
                        </div>
                        <div class="setting-actions">
                            ${isOverridden ? `<button class="btn-icon reset-btn" data-reset-key="${e.key}" title="Reset to default">\u21BA</button>` : ''}
                        </div>
                    </div>`;
            }).join('');

            html += `
                <div class="custom-tool-section">
                    <h4 class="custom-tool-header">\uD83D\uDD27 ${label}</h4>
                    <div class="settings-grid">${rows}</div>
                </div>`;
        }
        return html;
    },

    async init() {
        try {
            const resp = await fetch('/api/settings/tool-settings');
            if (resp.ok) toolSettingsData = await resp.json();
        } catch (e) { /* no tool settings */ }
    },

    hasContent() {
        return toolSettingsData && Object.keys(toolSettingsData).length > 0;
    }
};
