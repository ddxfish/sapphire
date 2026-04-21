// settings-tabs/embedding.js - Embedding provider settings
import { renderProviderTab, attachProviderListeners, mergeRegistryProviders } from '../../shared/provider-selector.js';
import { on as onBusEvent, Events as BusEvents } from '../../core/event-bus.js';

// Merged config cache — populated by attachListeners on first render so the
// dropdown includes plugin-registered embedders (e.g. embedder-minilm). Mirrors
// the tts.js / stt.js pattern. Without this, a plugin-registered EMBEDDING_PROVIDER
// value doesn't map to any hardcoded entry and the dropdown falsely shows
// "Disabled" even though the backend has the plugin active. 2026-04-21.
let _mergedConfig = null;

async function loadIntegrityStatus(el) {
    // Passive status: fetch how stored vectors are stamped vs the active
    // provider, surface any "orphaned" counts prominently. Pairs with the
    // save-time confirm in settings.js (which uses the same endpoint).
    const box = el.querySelector('#embedding-integrity');
    const reembedBtn = el.querySelector('#embedding-reembed-btn');
    if (!box) return;
    try {
        const res = await fetch('/api/embedding/integrity');
        if (!res.ok) {
            box.innerHTML = 'Could not check stored-vector integrity.';
            return;
        }
        const report = await res.json();
        const active = report.active || {};
        const t = report.tables || {};
        const mem = t.memories || {};
        const know = t.knowledge_entries || {};
        const people = t.people || {};
        const matching = (mem.matching_active || 0) + (know.matching_active || 0) + (people.matching_active || 0);
        const legacy = (mem.legacy_unstamped || 0) + (know.legacy_unstamped || 0) + (people.legacy_unstamped || 0);
        const other = (mem.other_stamps || 0) + (know.other_stamps || 0) + (people.other_stamps || 0);
        const orphaned = legacy + other;
        const total = matching + orphaned;
        if (total === 0) {
            box.innerHTML = `No stored vectors yet. Saving memories will use <code>${active.provider || 'none'}</code>.`;
            if (reembedBtn) reembedBtn.disabled = true;
            return;
        }
        const rows = [
            `<b>Stored vectors:</b> ${total.toLocaleString()} total`,
            `&nbsp;&nbsp;• ${matching.toLocaleString()} stamped with current provider (<code>${active.provider || 'none'}</code>)`,
        ];
        if (legacy > 0) {
            rows.push(
                `&nbsp;&nbsp;• <span style="color:var(--color-warning,#f59e0b)">${legacy.toLocaleString()} legacy rows with no provenance</span> — invisible to vector search until re-embedded`
            );
        }
        if (other > 0) {
            rows.push(
                `&nbsp;&nbsp;• <span style="color:var(--color-warning,#f59e0b)">${other.toLocaleString()} rows stamped with a different provider</span> — invisible until re-embedded`
            );
        }
        box.innerHTML = rows.join('<br>');
        // Re-embed button enabled iff there's something orphaned to process.
        if (reembedBtn) reembedBtn.disabled = orphaned === 0;
    } catch (e) {
        box.innerHTML = `Integrity check failed: ${e.message}`;
    }
}

function _renderReembedProgress(el, status) {
    const progressEl = el.querySelector('#embedding-reembed-progress');
    const btn = el.querySelector('#embedding-reembed-btn');
    const cancelBtn = el.querySelector('#embedding-reembed-cancel');
    if (!progressEl || !btn || !cancelBtn) return;
    if (!status || !status.running) {
        // Final or idle state
        btn.disabled = false;
        btn.textContent = 'Re-embed orphaned vectors';
        cancelBtn.style.display = 'none';
        if (status && status.started_at && status.finished_at) {
            const total = status.total || 0;
            const done = status.done || 0;
            const errors = status.errors || 0;
            if (status.current_table === 'cancelled') {
                progressEl.textContent = `Cancelled after ${done}/${total} rows.`;
                progressEl.style.color = 'var(--color-warning,#f59e0b)';
            } else if (status.last_error) {
                progressEl.textContent = `Finished with error: ${status.last_error}`;
                progressEl.style.color = 'var(--color-error,#f44336)';
            } else {
                progressEl.textContent = `Done: ${done} vectors re-embedded` + (errors ? ` (${errors} errors)` : '');
                progressEl.style.color = 'var(--color-success,#4caf50)';
            }
        } else {
            progressEl.textContent = '';
        }
        // Refresh integrity readout so counts reflect the result
        const container = btn.closest('.settings-grid')?.parentElement || btn.ownerDocument;
        if (container && container.querySelector) loadIntegrityStatus(container);
        return;
    }
    btn.disabled = true;
    btn.textContent = 'Re-embedding…';
    cancelBtn.style.display = 'inline-block';
    const pct = status.total > 0 ? Math.round(100 * status.done / status.total) : 0;
    const tbl = status.current_table ? ` [${status.current_table}]` : '';
    progressEl.style.color = '';
    progressEl.textContent = `${status.done}/${status.total} (${pct}%)${tbl}`;
}

let _reembedUnsubscribe = null;

function wireReembedControls(el) {
    const btn = el.querySelector('#embedding-reembed-btn');
    const cancelBtn = el.querySelector('#embedding-reembed-cancel');
    if (!btn) return;

    btn.addEventListener('click', async () => {
        if (!confirm('Re-embed all orphaned vectors using the current provider? This may take a while depending on how many rows are affected.')) return;
        try {
            const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
            const res = await fetch('/api/embedding/reembed', { method: 'POST', headers: { 'X-CSRF-Token': csrf } });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${res.status}`);
            }
            // Immediate optimistic state; SSE events will update progress
            _renderReembedProgress(el, { running: true, total: 0, done: 0 });
        } catch (e) {
            const p = el.querySelector('#embedding-reembed-progress');
            if (p) { p.textContent = `Start failed: ${e.message}`; p.style.color = 'var(--color-error,#f44336)'; }
        }
    });

    cancelBtn?.addEventListener('click', async () => {
        try {
            const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
            await fetch('/api/embedding/reembed/cancel', { method: 'POST', headers: { 'X-CSRF-Token': csrf } });
        } catch {}
    });

    // Fetch current status once on mount (in case a re-embed was already running)
    fetch('/api/embedding/reembed/status').then(r => r.ok ? r.json() : null).then(s => {
        if (s) _renderReembedProgress(el, s);
    }).catch(() => {});

    // Subscribe to SSE progress. Unsubscribe on next wire to avoid stacking.
    if (_reembedUnsubscribe) { try { _reembedUnsubscribe(); } catch {} }
    _reembedUnsubscribe = onBusEvent(BusEvents.REEMBED_PROGRESS, (data) => {
        _renderReembedProgress(el, data);
    });
}

const tabConfig = {
    providerKey: 'EMBEDDING_PROVIDER',
    disabledMessage: 'Embeddings disabled. Memory and knowledge search will use text matching only.',

    providers: {
        none: {
            label: 'Disabled',
            essentialKeys: [],
            advancedKeys: []
        },
        local: {
            label: 'Local (Nomic ONNX)',
            essentialKeys: [],
            advancedKeys: []
        },
        api: {
            label: 'Remote (Nomic API)',
            essentialKeys: ['EMBEDDING_API_URL'],
            advancedKeys: ['EMBEDDING_API_KEY']
        },
        sapphire_router: {
            label: 'Sapphire Router',
            essentialKeys: ['SAPPHIRE_ROUTER_URL', 'SAPPHIRE_ROUTER_TENANT_ID'],
            advancedKeys: []
        }
    },

    commonKeys: [],
    commonAdvancedKeys: []
};

export default {
    id: 'embedding',
    name: 'Embedding',
    icon: '\uD83E\uDDF2',
    description: 'Vector embedding engine for memory and knowledge search',

    render(ctx) {
        const cfg = _mergedConfig || tabConfig;
        let html = renderProviderTab(cfg, ctx);
        // Test button — shown for all providers except disabled
        html += `
            <div class="settings-grid" style="margin-top: 1rem;">
                <div class="setting-row full-width">
                    <button id="embedding-test-btn" class="btn btn-secondary" style="width: auto;">
                        Test Embedding
                    </button>
                    <span id="embedding-test-result" style="margin-left: 0.75rem; font-size: var(--font-sm);"></span>
                </div>
            </div>
            <div class="settings-grid" style="margin-top: 1rem;">
                <div class="setting-row full-width">
                    <div id="embedding-integrity" class="setting-help" style="padding:8px 12px;border-radius:6px;background:var(--color-bg-alt,#222);font-size:var(--font-sm)">
                        Checking stored-vector integrity…
                    </div>
                </div>
                <div class="setting-row full-width" style="margin-top: 8px;">
                    <button id="embedding-reembed-btn" class="btn btn-secondary" style="width: auto;" disabled>
                        Re-embed orphaned vectors
                    </button>
                    <button id="embedding-reembed-cancel" class="btn btn-secondary" style="width: auto; margin-left: 8px; display:none;">
                        Cancel
                    </button>
                    <span id="embedding-reembed-progress" style="margin-left: 0.75rem; font-size: var(--font-sm);"></span>
                </div>
            </div>
            <div class="settings-grid" style="margin-top: 1.5rem;">
                <div class="setting-row" data-key="MEMORY_DEDUP_THRESHOLD">
                    <div class="setting-label">
                        <div class="setting-label-row">
                            <label>Memory Dedup Threshold</label>
                            <span class="help-icon" title="Cosine similarity threshold for duplicate memory detection (0.70–0.99). Higher values require closer matches. 0.92 is a good default.">?</span>
                        </div>
                    </div>
                    <div class="setting-input">
                        <input type="number" id="setting-MEMORY_DEDUP_THRESHOLD" data-key="MEMORY_DEDUP_THRESHOLD"
                            value="${ctx.settings.MEMORY_DEDUP_THRESHOLD ?? 0.92}" step="0.01" min="0.70" max="0.99">
                    </div>
                </div>
            </div>`;
        return html;
    },

    async attachListeners(ctx, el) {
        // Re-fetch plugin providers each time (plugins may have been toggled
        // since last visit). If the merge pulls in new keys, re-render so the
        // dropdown reflects them before we attach any listeners.
        _mergedConfig = await mergeRegistryProviders(tabConfig);
        if (Object.keys(_mergedConfig.providers).length > Object.keys(tabConfig.providers).length) {
            const body = el.querySelector('.settings-tab-body') || el;
            body.innerHTML = this.render(ctx);
            if (ctx.attachAccordionListeners) ctx.attachAccordionListeners(el);
        }
        const cfg = _mergedConfig || tabConfig;
        attachProviderListeners(cfg, ctx, el, this);
        loadIntegrityStatus(el);
        wireReembedControls(el);

        // Set placeholder on URL field after render
        const urlInput = el.querySelector('[data-key="EMBEDDING_API_URL"]');
        if (urlInput) urlInput.placeholder = 'http://your-server:8080/v1/embeddings';

        // Test button
        const btn = el.querySelector('#embedding-test-btn');
        const result = el.querySelector('#embedding-test-result');
        if (btn) btn.addEventListener('click', async () => {
            btn.disabled = true;
            btn.textContent = 'Testing...';
            result.textContent = '';
            result.style.color = '';
            try {
                const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                const res = await fetch('/api/embedding/test', { method: 'POST', headers: { 'X-CSRF-Token': csrf } });
                if (!res.ok) throw new Error(`Server error (${res.status})`);
                const data = await res.json();
                if (data.success) {
                    result.style.color = 'var(--color-success, #4caf50)';
                    result.textContent = `${data.provider} — ${data.dimensions}d vector in ${data.ms}ms`;
                } else {
                    result.style.color = 'var(--color-error, #f44336)';
                    result.textContent = data.error || 'Test failed';
                }
            } catch (e) {
                result.style.color = 'var(--color-error, #f44336)';
                result.textContent = `Error: ${e.message}`;
            }
            btn.disabled = false;
            btn.textContent = 'Test Embedding';
        });
    }
};
