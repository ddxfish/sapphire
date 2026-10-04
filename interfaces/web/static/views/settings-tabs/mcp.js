// settings-tabs/mcp.js - Settings > MCP Server (tmp/device-manager-upgrade-plan.md §9)
//
// The door at POST /mcp: on or off, and which of her tools a client may call.
// `ask` and `tell` are always offered. Plugins that keep their own memory
// scopes are listed greyed, not offered yet. The list is one setting,
// MCP_SERVER_TOOLS, saved by the page's own Save like any other.
import { fetchWithTimeout } from '../../shared/fetch.js';
import { escapeHtml as esc } from '../../shared/modal.js';

export default {
    id: 'mcp',
    name: 'MCP Server',
    icon: '\u{1F50C}',
    description: 'Her tools for an MCP client: another Sapphire, Claude Code, Claude Desktop',
    essentialKeys: ['MCP_SERVER_ENABLED', 'INSTANCE_NAME'],

    render(ctx) {
        return `
            <div class="settings-group">
                <h3>\u{1F50C} MCP Server</h3>
                <p class="text-muted" style="font-size:var(--font-sm);line-height:1.5">
                    The Model Context Protocol, served at <code>/mcp</code>. A client with one of
                    her API tokens (System &gt; API Keys) can <b>ask</b> her something in a named
                    chat and get her answer, <b>tell</b> her something without waiting, and call
                    the tools ticked below. Another Sapphire added as a device uses this door;
                    so can Claude Code: <code>claude mcp add --transport http sapphire
                    https://this-machine:8073/mcp --header "Authorization: Bearer &lt;token&gt;"</code>.
                </p>
                ${ctx.renderFields(this.essentialKeys)}
            </div>
            <div class="settings-group">
                <h3>Tools shared over MCP</h3>
                <p class="text-muted" style="font-size:var(--font-sm)">
                    Besides ask and tell. Nothing is shared until you tick it. Tools of plugins
                    that keep their own memory scopes are not offered yet.
                </p>
                <div id="mcp-tools"><p class="text-muted">Loading...</p></div>
            </div>`;
    },

    attachListeners(ctx, el) {
        const box = el.querySelector('#mcp-tools');
        if (!box) return;
        const chosen = () => new Set(Array.isArray(ctx.getValue('MCP_SERVER_TOOLS')) ? ctx.getValue('MCP_SERVER_TOOLS') : []);
        fetchWithTimeout('/api/mcp/tools', {}, 15000).then(data => {
            const tools = (data.tools || []).filter(t => !t.own);
            if (!tools.length) {
                box.innerHTML = '<p class="text-muted" style="font-size:0.9em">She has no tools loaded.</p>';
                return;
            }
            const on = chosen();
            const groups = new Map();
            for (const t of tools) {
                const name = (t.emoji ? t.emoji + ' ' : '') + t.module;
                if (!groups.has(name)) groups.set(name, []);
                groups.get(name).push(t);
            }
            box.innerHTML = [...groups.entries()].map(([group, list]) => `
                <details style="margin:4px 0">
                    <summary style="cursor:pointer;font-weight:600">${esc(group)}
                        <span class="text-muted" style="font-weight:400"> ${list.filter(t => on.has(t.name)).length ? list.filter(t => on.has(t.name)).length + ' of ' : ''}${list.length}</span></summary>
                    <div style="margin:6px 0 10px 18px;display:flex;flex-direction:column;gap:4px">
                        ${list.map(t => `
                        <label style="display:flex;gap:8px;align-items:baseline;${t.scoped ? 'opacity:0.5' : ''}" title="${esc(t.description)}">
                            <input type="checkbox" data-tool="${esc(t.name)}" ${on.has(t.name) ? 'checked' : ''} ${t.scoped ? 'disabled' : ''}>
                            <span><code>${esc(t.name)}</code>${t.scoped ? ' <span class="text-muted">(keeps a memory scope - not yet)</span>' : ''}
                                <span class="text-muted" style="font-size:var(--font-sm)"> ${esc(t.description.slice(0, 90))}</span></span>
                        </label>`).join('')}
                    </div>
                </details>`).join('');
            box.addEventListener('change', e => {
                const cb = e.target.closest('input[data-tool]');
                if (!cb) return;
                const now = chosen();
                if (cb.checked) now.add(cb.dataset.tool); else now.delete(cb.dataset.tool);
                ctx.markChanged('MCP_SERVER_TOOLS', [...now].sort());
            });
        }).catch(e => {
            box.innerHTML = `<p style="color:var(--error)">Could not list her tools: ${esc(e.message)}</p>`;
        });
    }
};
