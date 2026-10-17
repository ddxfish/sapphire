// core/plugins/mcp_client/web/index.js — Settings UI for MCP Client plugin

import { registerPluginSettings } from '/static/shared/plugin-registry.js';
import pluginsAPI from '/static/shared/plugins-api.js';

const PLUGIN_NAME = 'mcp_client';
const CSRF = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

registerPluginSettings({
    id: PLUGIN_NAME,
    name: 'MCP Client',
    icon: '\uD83D\uDD0C',
    helpText: 'Connect to Model Context Protocol servers for external tools',

    render(container, settings) {
        container.innerHTML = `
            <h4 style="margin:0 0 12px">MCP Servers</h4>
            <div id="mcp-server-list"></div>
            <div id="mcp-wizard" style="display:none"></div>
            <div style="display:flex;gap:8px;margin-top:12px">
                <button class="btn btn-sm btn-primary" id="mcp-add-local">+ Add Local (stdio)</button>
                <button class="btn btn-sm" id="mcp-add-remote">+ Add Remote (HTTP)</button>
            </div>
        `;

        _loadServers(container);

        container.querySelector('#mcp-add-local')?.addEventListener('click', () => _showWizard(container, 'stdio'));
        container.querySelector('#mcp-add-remote')?.addEventListener('click', () => _showWizard(container, 'http'));
    },

    load: () => pluginsAPI.getSettings(PLUGIN_NAME),
});

async function _loadServers(container) {
    const list = container.querySelector('#mcp-server-list');
    if (!list) return;

    try {
        const res = await fetch('/api/plugin/mcp_client/servers');
        if (!res.ok) throw new Error('Failed to fetch servers');
        const data = await res.json();
        const servers = data.servers || [];

        if (servers.length === 0) {
            list.innerHTML = '<p class="text-muted" style="font-size:0.9em">No MCP servers configured. Add one to get started.</p>';
            return;
        }

        list.innerHTML = servers.map(s => {
            const icon = s.status === 'connected' ? '\uD83D\uDFE2' : '\uD83D\uDD34';
            const statusText = s.status === 'connected'
                ? `<span style="color:var(--success)">${s.tool_count} tools</span>`
                : `<span class="text-muted">${_esc(s.status)}</span>`;
            const detail = s.type === 'stdio' ? (s.command || '') : (s.url || '');
            return `
            <div class="setting-row" style="padding:10px 0;border-bottom:1px solid var(--border)">
                <div class="setting-label">
                    <label>${icon} ${_esc(s.name)}</label>
                    <div class="setting-help">${s.type} ${_esc(detail)} \u00B7 ${statusText}</div>
                </div>
                <div class="setting-input" style="display:flex;gap:4px">
                    <button class="btn btn-sm mcp-reconnect" data-name="${_esc(s.name)}" title="Reconnect">\u21BB</button>
                    <button class="btn btn-sm btn-danger mcp-remove" data-name="${_esc(s.name)}" title="Remove">\u2715</button>
                </div>
            </div>`;
        }).join('');

        // Reconnect buttons
        list.querySelectorAll('.mcp-reconnect').forEach(btn => {
            btn.addEventListener('click', async () => {
                const name = btn.dataset.name;
                btn.disabled = true;
                btn.textContent = '...';
                try {
                    await fetch(`/api/plugin/mcp_client/servers/${name}/reconnect`, {
                        method: 'POST',
                        headers: { 'X-CSRF-Token': CSRF() }
                    });
                } catch (e) { console.error('Reconnect failed:', e); }
                _loadServers(container);
            });
        });

        // Remove buttons
        list.querySelectorAll('.mcp-remove').forEach(btn => {
            btn.addEventListener('click', async () => {
                const name = btn.dataset.name;
                if (!confirm(`Remove MCP server "${name}"?`)) return;
                btn.disabled = true;
                try {
                    await fetch(`/api/plugin/mcp_client/servers/${name}`, {
                        method: 'DELETE',
                        headers: { 'X-CSRF-Token': CSRF() }
                    });
                } catch (e) { console.error('Remove failed:', e); }
                _loadServers(container);
            });
        });
    } catch (e) {
        list.innerHTML = `<p style="color:var(--error)">Could not load servers: ${e.message}</p>`;
    }
}

function _showWizard(container, type) {
    const wizard = container.querySelector('#mcp-wizard');
    if (!wizard) return;
    wizard.style.display = 'block';

    const isStdio = type === 'stdio';
    wizard.innerHTML = `
        <div style="padding:14px;background:var(--bg-secondary);border-radius:var(--radius-sm);border:1px solid var(--border);margin-top:12px">
            <h5 style="margin:0 0 10px">${isStdio ? '\uD83D\uDDA5\uFE0F Add Local Server' : '\uD83C\uDF10 Add Remote Server'}</h5>
            <div class="setting-row" style="padding:4px 0">
                <div class="setting-label"><label>Name</label><div class="setting-help">Short label (e.g. "filesystem", "github")</div></div>
                <div class="setting-input"><input type="text" id="mcp-name" placeholder="${isStdio ? 'filesystem' : 'canva'}" style="width:100%"></div>
            </div>
            ${isStdio ? `
            <div class="setting-row" style="padding:4px 0">
                <div class="setting-label"><label>Command</label><div class="setting-help">Executable to run (e.g. "npx", "python")</div></div>
                <div class="setting-input"><input type="text" id="mcp-command" placeholder="npx" style="width:100%"></div>
            </div>
            <div class="setting-row" style="padding:4px 0">
                <div class="setting-label"><label>Arguments</label><div class="setting-help">Space-separated args</div></div>
                <div class="setting-input"><input type="text" id="mcp-args" placeholder="-y @modelcontextprotocol/server-filesystem /home/user" style="width:100%"></div>
            </div>
            <div class="setting-row" style="padding:4px 0">
                <div class="setting-label"><label>Environment</label><div class="setting-help">Optional: KEY=value, KEY2=value2</div></div>
                <div class="setting-input"><input type="text" id="mcp-env" placeholder="GITHUB_TOKEN=ghp_xxx" style="width:100%"></div>
            </div>
            ` : `
            <div class="setting-row" style="padding:4px 0">
                <div class="setting-label"><label>URL</label><div class="setting-help">MCP server endpoint</div></div>
                <div class="setting-input"><input type="text" id="mcp-url" placeholder="https://mcp.example.com" style="width:100%"></div>
            </div>
            <div class="setting-row" style="padding:4px 0">
                <div class="setting-label"><label>API Key</label><div class="setting-help">Optional — for servers using bearer auth</div></div>
                <div class="setting-input"><input type="text" id="mcp-apikey" placeholder="optional" style="width:100%"></div>
            </div>
            `}
            <div style="display:flex;gap:8px;margin-top:10px">
                <button class="btn btn-primary btn-sm" id="mcp-connect">Connect</button>
                <button class="btn btn-sm" id="mcp-cancel">Cancel</button>
            </div>
            <div id="mcp-status" class="text-muted" style="margin-top:8px;font-size:0.85em"></div>
        </div>
    `;

    wizard.querySelector('#mcp-cancel')?.addEventListener('click', () => {
        wizard.style.display = 'none';
        wizard.innerHTML = '';
    });

    wizard.querySelector('#mcp-connect')?.addEventListener('click', async () => {
        const name = wizard.querySelector('#mcp-name')?.value?.trim();
        if (!name) { _setStatus(wizard, 'Name required', true); return; }

        const body = { name, type };
        if (isStdio) {
            const command = wizard.querySelector('#mcp-command')?.value?.trim();
            if (!command) { _setStatus(wizard, 'Command required', true); return; }
            body.command = command;
            body.args = wizard.querySelector('#mcp-args')?.value?.trim() || '';
            body.env = wizard.querySelector('#mcp-env')?.value?.trim() || '';
        } else {
            const url = wizard.querySelector('#mcp-url')?.value?.trim();
            if (!url) { _setStatus(wizard, 'URL required', true); return; }
            body.url = url;
            body.api_key = wizard.querySelector('#mcp-apikey')?.value?.trim() || '';
        }

        const btn = wizard.querySelector('#mcp-connect');
        btn.disabled = true;
        btn.textContent = 'Connecting...';
        _setStatus(wizard, 'Connecting to MCP server...');

        try {
            const res = await fetch('/api/plugin/mcp_client/servers', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
                body: JSON.stringify(body)
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);

            const msg = data.status === 'connected'
                ? `Connected \u2014 ${data.tool_count} tools discovered`
                : `Status: ${data.status}`;
            _setStatus(wizard, msg, data.status !== 'connected');

            setTimeout(() => {
                wizard.style.display = 'none';
                wizard.innerHTML = '';
                _loadServers(container);
            }, 1500);
        } catch (e) {
            _setStatus(wizard, e.message, true);
            btn.disabled = false;
            btn.textContent = 'Connect';
        }
    });
}

function _setStatus(wizard, msg, isError = false) {
    const el = wizard.querySelector('#mcp-status');
    if (!el) return;
    el.textContent = msg;
    el.style.color = isError ? 'var(--error)' : 'var(--text-muted)';
}

function _esc(str) {
    if (!str) return '';
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}

export default { init() {} };
