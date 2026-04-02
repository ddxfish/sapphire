// Status app — live system dashboard

let _interval = null;
let _container = null;

export async function render(container) {
    _container = container;
    container.innerHTML = '<div class="status-loading">Loading status...</div>';
    await refresh();
    _interval = setInterval(refresh, 10000);
}

export function cleanup() {
    if (_interval) clearInterval(_interval);
    _interval = null;
    _container = null;
}

async function refresh() {
    if (!_container) return;
    try {
        const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
        const res = await fetch('/api/plugin/status/full', { headers: { 'X-CSRF-Token': csrf } });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        renderDashboard(_container, data);
    } catch (e) {
        console.error('[Status] Refresh failed:', e);
    }
}

function renderDashboard(el, d) {
    const ident = d.identity || {};
    const sess = d.session || {};
    const svc = d.services || {};
    const tasks = d.tasks || {};
    const daemons = d.daemons || {};
    const providers = d.providers || [];
    const plugins = d.plugins || [];
    const metrics = d.metrics || {};
    const audio = d.audio || {};
    const backup = d.backup || {};
    const update = d.update || {};
    const mind = d.mind || {};

    const upMin = Math.floor((ident.uptime_seconds || 0) / 60);
    const upH = Math.floor(upMin / 60);
    const upM = upMin % 60;
    const env = ident.docker ? 'Docker' : (ident.os || 'Unknown');
    const branch = ident.branch ? ` (${esc(ident.branch)})` : '';

    // Plugin verification badges
    const pluginChips = plugins.map(p => {
        const tier = p.verify_tier || 'unsigned';
        const tierClass = tier === 'official' ? 'verified' : tier === 'unsigned' ? 'unsigned' : tier === 'failed' ? 'tampered' : '';
        const tierLabel = tier === 'official' ? '\u2713' : tier === 'unsigned' ? '?' : tier === 'failed' ? '\u2717' : '';
        const deps = p.missing_deps?.length ? ` \u26A0 deps` : '';
        return `<span class="status-plugin-chip ${p.loaded ? 'loaded' : p.enabled ? 'enabled' : 'disabled'} ${tierClass}"
            title="${esc(tier)}${deps}">${esc(p.name)}${p.version ? ' v' + esc(p.version) : ''}${deps}</span>`;
    }).join('');

    // Tool names (collapsible)
    const toolNames = sess.tool_names || [];

    // Mind scopes
    const allScopes = mind.scopes || [];
    const memScopes = mind.memory_scopes || {};
    const knowledgeScopes = mind.knowledge_scopes || {};
    const peopleScopes = mind.people_by_scope || {};

    el.innerHTML = `
        <div class="status-dashboard">
            <!-- Row 1: Identity + Session -->
            <div class="status-row">
                <div class="status-card status-identity">
                    <div class="status-card-title">Sapphire v${esc(ident.app_version || '?')}${branch}</div>
                    <div class="status-meta">
                        Python ${esc(ident.python_version || '?')} &middot; ${esc(env)} &middot; Uptime: ${upH}h ${upM}m
                        ${ident.hostname ? ` &middot; ${esc(ident.hostname)}` : ''}
                    </div>
                    <div class="status-meta" style="margin-top:4px">
                        ${esc(ident.datetime || '')} &middot; ${esc(ident.timezone || '')}
                        ${sess.user_timezone ? ` &middot; App TZ: ${esc(sess.user_timezone)}` : ''}
                        ${update.available ? ` &middot; <span style="color:#ffa726">\u2B06 Update available: v${esc(update.latest_version)}</span>` : ''}
                    </div>
                </div>
                <div class="status-card">
                    <div class="status-card-title">Active Session</div>
                    <div class="status-grid">
                        ${field('Chat', sess.chat)}
                        ${field('Prompt', sess.prompt)}
                        ${field('Persona', sess.persona || 'none')}
                        ${field('LLM', `${sess.llm_primary || 'auto'}${sess.llm_model ? ' (' + sess.llm_model + ')' : ''}`)}
                        ${field('Toolset', `${sess.toolset || '?'} (${sess.function_count || 0} tools)`)}
                        ${field('Parallel / Iters', `${sess.parallel_tool_calls || 1} parallel, ${sess.max_iterations || 10} max iterations`)}
                        ${field('Memory', sess.memory_scope || 'default')}
                        ${field('Knowledge', sess.knowledge_scope || 'default')}
                        ${field('Theme', sess.theme || 'default')}
                    </div>
                </div>
            </div>

            <!-- Row 2: Services + Audio + Task Engine -->
            <div class="status-row">
                <div class="status-card">
                    <div class="status-card-title">Services</div>
                    <div class="status-service-list">
                        <div class="status-svc-row">${dot(svc.tts?.enabled)} TTS: ${esc(svc.tts?.provider || 'off')}${svc.tts?.voice ? ' (' + esc(svc.tts.voice) + ')' : ''}</div>
                        <div class="status-svc-row">${dot(svc.stt?.enabled)} STT: ${esc(svc.stt?.provider || 'off')}</div>
                        <div class="status-svc-row">${dot(svc.wakeword?.enabled)} Wakeword${svc.wakeword?.model ? ': ' + esc(svc.wakeword.model) : ''}</div>
                        <div class="status-svc-row">${dot(svc.embeddings?.enabled)} Embeddings: ${esc(svc.embeddings?.provider || 'off')}</div>
                        <div class="status-svc-row">${dot(svc.socks?.enabled)} SOCKS Proxy${svc.socks?.enabled ? (svc.socks.has_credentials ? ' (creds set)' : ' (no creds)') : ''}</div>
                        ${Object.entries(daemons).map(([k,v]) =>
                            `<div class="status-svc-row">${dot(v === 'running')} ${esc(k)}: ${esc(v)}</div>`
                        ).join('')}
                    </div>
                    <div class="status-grid" style="margin-top:10px; border-top:1px solid var(--border); padding-top:8px">
                        ${field('Audio In', audio.input || 'default')}
                        ${field('Audio Out', audio.output || 'default')}
                    </div>
                </div>
                <div class="status-card">
                    <div class="status-card-title">Task Engine</div>
                    <div class="status-grid">
                        ${field('Total', tasks.total)}
                        ${field('Enabled', tasks.enabled)}
                        ${field('Running', tasks.running)}
                    </div>
                    <div class="status-task-types">
                        ${tasks.tasks ? `<span class="status-type-chip">Tasks: ${tasks.tasks}</span>` : ''}
                        ${tasks.heartbeats ? `<span class="status-type-chip">Heartbeats: ${tasks.heartbeats}</span>` : ''}
                        ${tasks.daemons ? `<span class="status-type-chip">Daemons: ${tasks.daemons}</span>` : ''}
                        ${tasks.webhooks ? `<span class="status-type-chip">Webhooks: ${tasks.webhooks}</span>` : ''}
                    </div>
                    ${backup.count !== undefined ? `
                    <div class="status-grid" style="margin-top:10px; border-top:1px solid var(--border); padding-top:8px">
                        <div class="status-card-title" style="font-size:var(--font-sm)">Backups</div>
                        ${field('Count', backup.count)}
                        ${backup.latest ? field('Latest', backup.latest_date || backup.latest) : ''}
                        ${backup.latest_size ? field('Size', (backup.latest_size / 1024 / 1024).toFixed(1) + ' MB') : ''}
                    </div>` : ''}
                </div>
            </div>

            <!-- Row 3: Providers + Metrics -->
            <div class="status-row">
                <div class="status-card">
                    <div class="status-card-title">LLM Providers</div>
                    ${providers.length ? `
                    <table class="status-table">
                        <thead><tr><th>Provider</th><th>Status</th><th>Key</th><th>Type</th></tr></thead>
                        <tbody>
                            ${providers.map(p => `
                                <tr>
                                    <td>${esc(p.name || p.key)}</td>
                                    <td>${dot(p.enabled)} ${p.enabled ? 'on' : 'off'}</td>
                                    <td>${p.has_key ? '<span style="color:var(--color-success,#4caf50)">set</span>' : '<span style="color:var(--text-muted)">-</span>'}</td>
                                    <td>${p.is_local ? 'local' : 'cloud'}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>` : '<div class="status-meta">No providers configured</div>'}
                </div>
                ${metrics.total_tokens ? `
                <div class="status-card">
                    <div class="status-card-title">Token Usage (7 days)</div>
                    <div class="status-grid">
                        ${field('Total tokens', (metrics.total_tokens || 0).toLocaleString())}
                        ${field('API calls', metrics.total_calls || 0)}
                        ${metrics.by_provider ? Object.entries(metrics.by_provider).map(([k,v]) =>
                            field(k, (v.tokens || 0).toLocaleString() + ' tok, ' + (v.calls || 0) + ' calls')
                        ).join('') : ''}
                    </div>
                </div>` : ''}
            </div>

            <!-- Row 4: Plugins -->
            <div class="status-row">
                <div class="status-card" style="grid-column: 1 / -1">
                    <div class="status-card-title">Plugins (${plugins.filter(p => p.loaded).length} loaded / ${plugins.length} total)</div>
                    <div class="status-plugin-grid">${pluginChips}</div>
                </div>
            </div>

            <!-- Row 5: Tools (collapsible) -->
            <div class="status-row">
                <div class="status-card" style="grid-column: 1 / -1">
                    <details>
                        <summary class="status-card-title" style="cursor:pointer;user-select:none">
                            Enabled Tools (${toolNames.length})
                        </summary>
                        <div class="status-tool-grid">
                            ${toolNames.map(t => `<span class="status-type-chip">${esc(t)}</span>`).join('')}
                        </div>
                    </details>
                </div>
            </div>

            <!-- Row 6: Mind -->
            <div class="status-row">
                <div class="status-card" style="grid-column: 1 / -1">
                    <div class="status-card-title">Mind</div>
                    <div class="status-grid">
                        ${field('Scopes', allScopes.length ? allScopes.join(', ') : 'none')}
                        ${field('Memories', `${mind.memories || 0} total`)}
                        ${Object.keys(memScopes).length ? field('  by scope', Object.entries(memScopes).map(([k,v]) => `${k}: ${v}`).join(', ')) : ''}
                        ${field('People', `${mind.people || 0} total`)}
                        ${Object.keys(peopleScopes).length ? field('  by scope', Object.entries(peopleScopes).map(([k,v]) => `${k}: ${v}`).join(', ')) : ''}
                        ${field('Knowledge', `${mind.knowledge_total || 0} entries`)}
                        ${Object.keys(knowledgeScopes).length ? field('  by scope', Object.entries(knowledgeScopes).map(([k,v]) => `${k}: ${v}`).join(', ')) : ''}
                    </div>
                </div>
            </div>

            <!-- Diagnostics -->
            <div class="status-row">
                <div class="status-card" style="grid-column: 1 / -1">
                    <button class="status-copy-btn" id="status-copy-diag">Copy Diagnostics</button>
                    <div class="status-meta" style="margin-top:8px">One-click copy for Discord support — includes everything above</div>
                </div>
            </div>
        </div>

        <style>
            .status-dashboard { display: flex; flex-direction: column; gap: 16px; }
            .status-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
            .status-card {
                background: var(--bg-secondary, #1a1a2e); border: 1px solid var(--border, #333);
                border-radius: 10px; padding: 16px;
            }
            .status-identity { border-color: var(--accent, #4a9eff); }
            .status-card-title { font-weight: 600; margin-bottom: 10px; color: var(--text); }
            .status-meta { color: var(--text-muted); font-size: var(--font-sm); }
            .status-grid { display: flex; flex-direction: column; gap: 4px; font-size: var(--font-sm); }
            .status-field { display: flex; gap: 8px; }
            .status-field-label { color: var(--text-muted); min-width: 110px; }
            .status-field-value { color: var(--text); }
            .status-service-list { display: flex; flex-direction: column; gap: 6px; font-size: var(--font-sm); }
            .status-svc-row { display: flex; align-items: center; color: var(--text); white-space: nowrap; }
            .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; flex-shrink: 0; }
            .status-dot.on { background: #4caf50; }
            .status-dot.off { background: #666; }
            .status-task-types { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
            .status-type-chip { font-size: var(--font-xs); padding: 2px 8px; border-radius: 10px; background: var(--bg, #111); color: var(--text-muted); }
            .status-table { width: 100%; border-collapse: collapse; font-size: var(--font-sm); }
            .status-table th { text-align: left; color: var(--text-muted); padding: 4px 8px; border-bottom: 1px solid var(--border); }
            .status-table td { padding: 4px 8px; color: var(--text); }
            .status-plugin-grid { display: flex; flex-wrap: wrap; gap: 6px; }
            .status-plugin-chip {
                font-size: var(--font-xs); padding: 3px 8px; border-radius: 12px;
                border: 1px solid var(--border);
            }
            .status-plugin-chip.loaded { color: var(--text); border-color: var(--color-success, #4caf50); }
            .status-plugin-chip.verified { border-color: var(--color-success, #4caf50); }
            .status-plugin-chip.unsigned { border-color: var(--border); }
            .status-plugin-chip.tampered { border-color: #ef5350; color: #ef5350; }
            .status-plugin-chip.enabled { color: var(--text-muted); }
            .status-plugin-chip.disabled { color: var(--text-muted); opacity: 0.5; }
            .status-tool-grid { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
            .status-copy-btn {
                background: var(--bg); border: 1px solid var(--border); color: var(--text);
                padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: var(--font-sm); width: 100%;
            }
            .status-copy-btn:hover { border-color: var(--accent); }
            .status-loading { color: var(--text-muted); text-align: center; padding: 40px; }
        </style>
    `;

    // Copy diagnostics — everything
    el.querySelector('#status-copy-diag')?.addEventListener('click', () => {
        const lines = [
            `Sapphire v${ident.app_version}${branch} | Python ${ident.python_version} | ${env}`,
            `${ident.datetime} | ${ident.timezone}${sess.user_timezone ? ' | App TZ: ' + sess.user_timezone : ''}`,
            `Uptime: ${upH}h ${upM}m | Host: ${ident.hostname || 'unknown'}`,
            update.available ? `UPDATE AVAILABLE: v${update.latest_version}` : '',
            ``,
            `=== Session ===`,
            `Chat: ${sess.chat} | Prompt: ${sess.prompt} | Persona: ${sess.persona || 'none'}`,
            `LLM: ${sess.llm_primary} (${sess.llm_model || 'default'})`,
            `Toolset: ${sess.toolset} (${sess.function_count} tools) | Parallel: ${sess.parallel_tool_calls || 1} | Max Iters: ${sess.max_iterations || 10}`,
            `Theme: ${sess.theme || 'default'}`,
            `Scopes: memory=${sess.memory_scope || 'default'}, knowledge=${sess.knowledge_scope || 'default'}`,
            ``,
            `=== Services ===`,
            `TTS: ${svc.tts?.provider || 'off'}${svc.tts?.voice ? ' (' + svc.tts.voice + ')' : ''} | STT: ${svc.stt?.provider || 'off'}`,
            `Wakeword: ${svc.wakeword?.enabled ? 'ON' : 'OFF'} | Embeddings: ${svc.embeddings?.provider || 'off'} | SOCKS: ${svc.socks?.enabled ? 'ON' : 'OFF'}`,
            `Audio In: ${audio.input || 'default'} | Audio Out: ${audio.output || 'default'}`,
            Object.keys(daemons).length ? `Daemons: ${Object.entries(daemons).map(([k,v]) => `${k}: ${v}`).join(', ')}` : '',
            ``,
            `=== Tasks ===`,
            `${tasks.tasks || 0} tasks, ${tasks.heartbeats || 0} heartbeats, ${tasks.daemons || 0} daemons, ${tasks.webhooks || 0} webhooks (${tasks.running || 0} running)`,
            ``,
            `=== Plugins (${plugins.filter(p => p.loaded).length} loaded / ${plugins.length} total) ===`,
            ...plugins.map(p => {
                const tier = p.verify_tier || 'unsigned';
                const status = p.loaded ? 'loaded' : (p.enabled ? 'enabled' : 'disabled');
                const deps = p.missing_deps?.length ? ` [MISSING: ${p.missing_deps.join(', ')}]` : '';
                return `  ${p.name}${p.version ? ' v' + p.version : ''}: ${status} (${tier})${deps}`;
            }),
            ``,
            `=== Providers ===`,
            ...providers.map(p => `  ${p.name || p.key}: ${p.enabled ? 'on' : 'off'}, ${p.has_key ? 'key set' : 'no key'}${p.is_local ? ' (local)' : ''}`),
            ``,
            `=== Tools (${toolNames.length}) ===`,
            toolNames.join(', '),
            ``,
            `=== Backups ===`,
            backup.count !== undefined ? `${backup.count} backups${backup.latest ? ', latest: ' + (backup.latest_date || backup.latest) : ''}` : 'unavailable',
            ``,
            `=== Mind ===`,
            `Scopes: ${allScopes.join(', ') || 'none'}`,
            `Memories: ${mind.memories || 0} total${Object.keys(memScopes).length ? ' (' + Object.entries(memScopes).map(([k,v]) => `${k}: ${v}`).join(', ') + ')' : ''}`,
            `People: ${mind.people || 0} total${Object.keys(peopleScopes).length ? ' (' + Object.entries(peopleScopes).map(([k,v]) => `${k}: ${v}`).join(', ') + ')' : ''}`,
            `Knowledge: ${mind.knowledge_total || 0} entries${Object.keys(knowledgeScopes).length ? ' (' + Object.entries(knowledgeScopes).map(([k,v]) => `${k}: ${v}`).join(', ') + ')' : ''}`,
            ``,
            metrics.total_tokens ? `=== Tokens (7d) ===\n${(metrics.total_tokens || 0).toLocaleString()} total, ${metrics.total_calls || 0} calls` : '',
        ].filter(Boolean);

        navigator.clipboard.writeText(lines.join('\n'));
        const btn = el.querySelector('#status-copy-diag');
        btn.textContent = 'Copied!';
        setTimeout(() => btn.textContent = 'Copy Diagnostics', 2000);
    });
}

function esc(s) { return String(s || '').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function dot(on) { return `<span class="status-dot ${on ? 'on' : 'off'}"></span>`; }
function field(label, value) {
    return `<div class="status-field"><span class="status-field-label">${esc(label)}</span><span class="status-field-value">${esc(value)}</span></div>`;
}
