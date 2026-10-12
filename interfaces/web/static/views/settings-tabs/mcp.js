// settings-tabs/mcp.js - Settings > MCP Server (tmp/device-manager-upgrade-plan.md §9)
//
// The door at POST /mcp: on or off, and which of her tools a client may call.
// `ask` and `tell` are always offered. Plugins that keep their own memory
// scopes are listed greyed, not offered yet. The list is one setting,
// MCP_SERVER_TOOLS, saved by the page's own Save like any other. The chat
// rooms block is this Sapphire as a GUEST (core/room_guest.py): whether
// another Sapphire may invite her, the chat she decides in, and the persona
// a room's chat starts as when she does not pick her own.
import { fetchWithTimeout } from '../../shared/fetch.js';
import { escapeHtml as esc } from '../../shared/modal.js';

export default {
    id: 'mcp',
    name: 'MCP Server',
    icon: '\u{1F50C}',
    description: 'Her tools for an MCP client: another Sapphire, Claude Code, Claude Desktop',
    essentialKeys: ['MCP_SERVER_ENABLED', 'INSTANCE_NAME', 'MCP_SERVER_MIC'],

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
                    A token that speaks as a persona (pick one when you add the key) also gets
                    <b>speak</b> and <b>ding</b> on this machine's speakers and that persona's own
                    memory. The microphone switch below adds <b>listen</b>.
                </p>
                ${ctx.renderFields(this.essentialKeys)}
            </div>
            <div class="settings-group">
                <h3>\u{1FA91} Chat rooms</h3>
                <p class="text-muted" style="font-size:var(--font-sm);line-height:1.5">
                    Another Sapphire that holds one of her tokens may invite her into a chat room
                    over there. She decides in one turn of her own, with her memory behind her, and
                    joins by calling <code>chatroom_join</code> with what she brings: a persona, a
                    model, a toolset, a name, a few lines for herself. What she leaves out comes
                    from the persona below. The host then sends her the room's lines in a chat made
                    here, <code>room-&lt;host&gt;-&lt;room&gt;</code>, and her answers are her lines.
                    A room's chat is never private: the host reads every word of it anyway.
                    Her tools run here and their results stay here; the host can be told their names.
                </p>
                ${ctx.renderFields(['MCP_ROOM_INVITES', 'MCP_ROOM_SHARE_TOOLS'])}
                <div class="settings-grid">
                    ${this._row(ctx, 'MCP_ROOM_LOBBY', 'Decides in', 'mcp-room-lobby')}
                    ${this._row(ctx, 'MCP_ROOM_PERSONA', 'Room chats start as', 'mcp-room-persona')}
                </div>
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

    _row(ctx, key, label, id) {
        return `
            <div class="setting-row" data-key="${key}">
                <div class="setting-label">
                    <div class="setting-label-row"><label>${label}</label>
                        <span class="help-icon" data-help-key="${key}" title="Details">?</span></div>
                </div>
                <div class="setting-input"><select id="${id}" data-key="${key}"><option value="">Loading...</option></select></div>
                <div class="setting-actions"></div>
            </div>`;
    },

    async _fillRooms(ctx, el) {
        const lobby = el.querySelector('#mcp-room-lobby');
        const persona = el.querySelector('#mcp-room-persona');
        if (!lobby || !persona) return;
        const opt = (v, label, cur) => `<option value="${esc(v)}" ${v === cur ? 'selected' : ''}>${esc(label)}</option>`;
        const keep = (sel, cur, first) => {
            // the saved name stays selectable even when the list no longer has it
            const have = [...sel.options].some(o => o.value === cur);
            if (cur && !have) sel.insertAdjacentHTML('beforeend', opt(cur, `${cur} (not found)`, cur));
            else if (!cur) sel.value = '';
            if (first) sel.insertAdjacentHTML('afterbegin', opt('', first, cur));
            sel.value = cur || '';
        };
        try {
            const data = await fetchWithTimeout('/api/chats?slim=1', {}, 10000);
            const cur = String(ctx.getValue('MCP_ROOM_LOBBY') || '');
            // a private chat never decides an invitation (her reply leaves the box), so it is not offered
            const chats = (data.chats || []).filter(c => (!c.mode || c.mode === 'chat') && !c.private_chat && !c.archived);
            lobby.innerHTML = chats.map(c => opt(c.name, c.name, cur)).join('');
            keep(lobby, cur, 'A fresh turn as her default self (keeps nothing)');
        } catch (e) { lobby.innerHTML = `<option value="">Could not list her chats: ${esc(e.message)}</option>`; }
        try {
            const data = await fetchWithTimeout('/api/personas', {}, 10000);
            const cur = String(ctx.getValue('MCP_ROOM_PERSONA') || '');
            persona.innerHTML = (data.personas || []).map(p => opt(p.name, p.name, cur)).join('');
            keep(persona, cur, `Her default persona${data.default ? ` (${data.default})` : ''}`);
        } catch (e) { persona.innerHTML = `<option value="">Could not list her personas: ${esc(e.message)}</option>`; }
        // saved by the page's own change delegate (data-key): pendingChanges + the row's unsaved mark
    },

    attachListeners(ctx, el) {
        this._fillRooms(ctx, el);
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
