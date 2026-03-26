// settings-tabs/tts.js - Text-to-speech provider settings
import { renderProviderTab, attachProviderListeners, mergeRegistryProviders } from '../../shared/provider-selector.js';

let _mergedConfig = null;

const tabConfig = {
    providerKey: 'TTS_PROVIDER',
    disabledMessage: 'Text-to-speech is disabled. Select a provider above to enable voice output.',

    providers: {
        none: {
            label: 'Disabled',
            essentialKeys: [],
            advancedKeys: []
        },
        kokoro: {
            label: 'Local (Kokoro)',
            essentialKeys: [],
            advancedKeys: [
                'TTS_SERVER_HOST', 'TTS_SERVER_PORT',
                'TTS_PRIMARY_SERVER', 'TTS_FALLBACK_SERVER', 'TTS_FALLBACK_TIMEOUT'
            ]
        }
    },

    commonKeys: [],
    commonAdvancedKeys: []
};

export default {
    id: 'tts',
    name: 'TTS',
    icon: '\uD83D\uDD0A',
    description: 'Text-to-speech engine configuration',

    render(ctx) {
        const cfg = _mergedConfig || tabConfig;
        let html = renderProviderTab(cfg, ctx);
        html += `
            <div class="settings-grid" style="margin-top: 1rem;">
                <div class="setting-row full-width">
                    <button id="tts-test-btn" class="btn btn-secondary" style="width: auto;">
                        Test TTS
                    </button>
                    <span id="tts-test-result" style="margin-left: 0.75rem; font-size: var(--font-sm);"></span>
                </div>
            </div>`;
        return html;
    },

    async attachListeners(ctx, el) {
        // Always re-fetch plugin providers (plugins may have been toggled)
        {
            _mergedConfig = await mergeRegistryProviders(tabConfig);
            // Re-render dropdown if new providers were added
            if (Object.keys(_mergedConfig.providers).length > Object.keys(tabConfig.providers).length) {
                const body = el.querySelector('.settings-tab-body') || el;
                body.innerHTML = this.render(ctx);
                if (ctx.attachAccordionListeners) ctx.attachAccordionListeners(el);
            }
        }
        const cfg = _mergedConfig || tabConfig;
        attachProviderListeners(cfg, ctx, el, this);

        const btn = el.querySelector('#tts-test-btn');
        const result = el.querySelector('#tts-test-result');
        if (btn) btn.addEventListener('click', async () => {
            btn.disabled = true;
            btn.textContent = 'Testing...';
            result.textContent = '';
            result.style.color = '';
            try {
                const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                const res = await fetch('/api/tts/test', { method: 'POST', headers: { 'X-CSRF-Token': csrf } });
                if (!res.ok) throw new Error(`Server error (${res.status})`);
                const data = await res.json();
                if (data.success) {
                    result.style.color = 'var(--color-success, #4caf50)';
                    result.textContent = `${data.provider} — ${data.ms}ms`;
                } else {
                    result.style.color = 'var(--color-error, #f44336)';
                    result.textContent = data.error || 'Test failed';
                }
            } catch (e) {
                result.style.color = 'var(--color-error, #f44336)';
                result.textContent = `Error: ${e.message}`;
            }
            btn.disabled = false;
            btn.textContent = 'Test TTS';
        });
    }
};
