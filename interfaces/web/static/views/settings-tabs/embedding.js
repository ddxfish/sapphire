// settings-tabs/embedding.js - Embedding provider settings
import { renderProviderTab, attachProviderListeners } from '../../shared/provider-selector.js';

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
        let html = renderProviderTab(tabConfig, ctx);
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

    attachListeners(ctx, el) {
        attachProviderListeners(tabConfig, ctx, el, this);

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
                const res = await fetch('/api/embedding/test', { method: 'POST' });
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
