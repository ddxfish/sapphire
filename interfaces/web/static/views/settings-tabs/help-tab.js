// settings-tabs/help-tab.js — Links to Help view (doesn't embed to avoid clobbering module state)

export default {
    id: 'help',
    name: 'Help',
    icon: '\uD83D\uDCD6',
    description: 'Guides, shortcuts, and troubleshooting',

    render() {
        return `<div style="padding:20px;text-align:center">
            <h3 style="margin:0 0 12px">\uD83D\uDCD6 Help & Documentation</h3>
            <p class="text-muted" style="margin:0 0 16px">Guides, keyboard shortcuts, and troubleshooting</p>
            <button class="btn-primary" id="settings-open-help" style="padding:8px 24px">Open Help</button>
        </div>`;
    },

    attachListeners(ctx, el) {
        el.querySelector('#settings-open-help')?.addEventListener('click', () => {
            import('../../core/router.js').then(r => r.switchView('help'));
        });
    }
};
