// Google Calendar settings plugin (multi-account)
// Uses shared account-manager for list/navigation, custom editor for OAuth + calendar config.

import { registerPluginSettings } from '/static/shared/plugin-registry.js';
import { createAccountManager } from '/static/shared/account-manager.js';

const manager = createAccountManager({
    prefix: 'gcal',
    entityName: 'Calendar',
    listEndpoint: '/api/gcal/accounts',
    listKey: 'accounts',
    deleteEndpoint: (scope) => `/api/gcal/accounts/${encodeURIComponent(scope)}`,
    formatItem: (item) => ({
        name: item.label || item.scope,
        detail: item.has_token ? 'Connected' : 'Not connected'
    }),
    hint: 'Each calendar maps to a chat scope. Select which calendar to use per-chat in the sidebar.',
    addLabel: '+ Add Calendar',
    addPrompt: 'Name for this calendar (e.g. "work", "personal"):',
    renderEditor: renderCalendarEditor,
});


function renderCalendarEditor(body, scope, item, helpers) {
    const s = item || {};

    body.innerHTML = `
        <div class="am-group">
            <label for="gcal-client-id">Google Client ID (advanced)</label>
            <input type="text" id="gcal-client-id" value="${s.client_id || ''}" placeholder="Leave blank for Easy Connect">
            <div class="am-hint">Blank = Easy Connect, no Google Console needed. Or paste your own OAuth2 Client ID to use your own Google client.</div>
        </div>

        <div class="am-group">
            <label for="gcal-client-secret">Google Client Secret (advanced)</label>
            <input type="password" id="gcal-client-secret" value="" placeholder="${item ? 'Leave blank to keep existing...' : 'Only needed with your own Client ID'}">
        </div>

        <div class="am-group">
            <label for="gcal-calendar-id">Calendar ID</label>
            <input type="text" id="gcal-calendar-id" value="${s.calendar_id || 'primary'}" placeholder="primary">
            <div class="am-hint">Use "primary" for main calendar, or the long ID from Google Calendar settings.</div>
        </div>

        <div class="am-row" style="gap:12px;margin-top:8px">
            <button type="button" class="am-action-btn" id="gcal-save-btn">Save</button>
            <button type="button" class="am-action-btn" id="gcal-connect-btn" ${!item ? 'disabled title="Save first"' : ''}>
                ${s.has_token ? 'Reconnect' : 'Connect Google'}
            </button>
            ${s.has_token ? '<button type="button" class="am-action-btn" id="gcal-disconnect-btn" style="border-color:var(--error,#dc3545);color:var(--error,#dc3545)">Disconnect</button>' : ''}
        </div>

        ${s.has_token ? '<div class="am-hint" style="color:var(--success,#28a745)">Connected to Google Calendar</div>' : ''}
    `;

    body.querySelector('#gcal-save-btn').addEventListener('click', async () => {
        const btn = body.querySelector('#gcal-save-btn');
        const client_id = body.querySelector('#gcal-client-id').value.trim();
        const client_secret = body.querySelector('#gcal-client-secret').value.trim();
        const calendar_id = body.querySelector('#gcal-calendar-id').value.trim() || 'primary';

        btn.disabled = true;
        btn.textContent = 'Saving...';

        try {
            const res = await fetch(`/api/gcal/accounts/${encodeURIComponent(scope)}`, {
                method: 'PUT',
                headers: helpers.csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ client_id, client_secret, calendar_id, label: scope })
            });
            const data = await res.json();
            if (data.success) {
                btn.textContent = 'Saved';
                btn.className = 'am-action-btn success';
                // Enable connect button now
                const connectBtn = body.querySelector('#gcal-connect-btn');
                if (connectBtn) { connectBtn.disabled = false; }
                await manager.loadItems();
            } else {
                throw new Error(data.detail || 'Save failed');
            }
        } catch (e) {
            btn.textContent = 'Error';
            btn.className = 'am-action-btn error';
            helpers.showResult(false, e.message);
        }
        setTimeout(() => { btn.textContent = 'Save'; btn.className = 'am-action-btn'; btn.disabled = false; }, 3000);
    });

    body.querySelector('#gcal-connect-btn').addEventListener('click', () => {
        // Redirect to OAuth — pass scope so the right account gets the token
        window.location.href = `/api/plugin/google-calendar/auth?scope=${encodeURIComponent(scope)}`;
    });

    body.querySelector('#gcal-disconnect-btn')?.addEventListener('click', async () => {
        const res = await fetch('/api/plugin/google-calendar/disconnect', {
            method: 'POST',
            headers: helpers.csrfHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ scope })
        });
        if (res.ok) {
            await manager.loadItems();
            helpers.reloadList();
        }
    });
}


export default {
    name: 'google-calendar',

    init(container) {
        registerPluginSettings({
            id: 'google-calendar',
            name: 'Google Calendar',
            icon: '\uD83D\uDCC5',
            helpText: 'Manage Google Calendar accounts. Each account can use a different calendar. Select which to use per-chat in the sidebar.',
            render: (c) => manager.renderList(c),
            load: async () => { await manager.loadItems(); return {}; },
        });
    },

    destroy() {}
};
