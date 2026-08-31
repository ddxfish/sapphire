// settings-tabs/stt.js - Speech-to-text provider settings + VAD tuning
import { renderProviderTab, attachProviderListeners, mergeRegistryProviders } from '../../shared/provider-selector.js';

let _mergedConfig = null;

const tabConfig = {
    providerKey: 'STT_PROVIDER',
    disabledMessage: 'Speech-to-text is disabled. Select a provider above to enable voice input.',

    providers: {
        none: {
            label: 'Disabled',
            essentialKeys: [],
            advancedKeys: []
        },
        faster_whisper: {
            label: 'Local (Faster Whisper)',
            is_local: true,
            essentialKeys: ['STT_MODEL_SIZE'],
            advancedKeys: [
                'FASTER_WHISPER_DEVICE', 'FASTER_WHISPER_CUDA_DEVICE', 'FASTER_WHISPER_COMPUTE_TYPE',
                'FASTER_WHISPER_BEAM_SIZE', 'FASTER_WHISPER_NUM_WORKERS', 'FASTER_WHISPER_VAD_FILTER'
            ]
        },
        fireworks_whisper: {
            label: 'Fireworks Whisper',
            is_local: false,
            essentialKeys: ['STT_FIREWORKS_API_KEY', 'STT_FIREWORKS_MODEL'],
            advancedKeys: []
        }
    },

    // Always-visible common keys — capture-loop concerns shared by every
    // recording, agnostic of which VAD backend is active.
    commonKeys: window.__managed
        ? ['STT_LANGUAGE']
        : ['STT_LANGUAGE', 'RECORDER_SILENCE_DURATION', 'RECORDER_MAX_SECONDS'],
    commonAdvancedKeys: window.__managed
        ? []
        : ['RECORDER_SPEECH_DURATION', 'RECORDER_NO_SPEECH_TIMEOUT', 'RECORDER_BEEP_WAIT_TIME']
};

// ── VAD section render ───────────────────────────────────────────────────────
// Schema-matched controls: STT_VAD_ENABLED is a BOOLEAN setting bound to a
// native checkbox via data-key. No translation layer. The generic settings
// change-handler path captures the boolean directly. No manual markChanged.
// No type coercion in parseValue. No polling-overrides-user-state. The bug
// class is structurally eliminated.

function renderVADSection(ctx) {
    if (window.__managed) return '';   // hosted/Docker — no local mic

    // Render Silero enable checkbox via the canonical renderInput path so
    // pendingChanges and the generic change handler work natively.
    const sileroToggleHtml = ctx.renderFields(['STT_VAD_ENABLED']);

    const testButtonHtml = `
        <div class="settings-field" style="margin-top:16px;padding-top:12px;border-top:1px solid rgba(255,255,255,0.08)">
            <button id="vad-test-btn" type="button" style="padding:6px 14px">Test my voice (5s)</button>
            <div id="vad-test-result" style="margin-top:10px;font-family:monospace;font-size:0.9em;opacity:0.85"></div>
        </div>`;

    const sileroFields = ctx.renderFields(['STT_VAD_SPEECH_THRESHOLD']);

    const classicKeys = [
        'RECORDER_BACKGROUND_PERCENTILE',
        'RECORDER_SILENCE_THRESHOLD',
        'RECORDER_NOISE_MULTIPLIER',
        'RECORDER_LEVEL_HISTORY_SIZE',
    ];

    return `
        <div style="margin-top:28px;padding-top:8px;border-top:2px solid rgba(255,255,255,0.12)">
            <h3 style="margin:8px 0 12px 0">Voice Activity Detection
                <span id="vad-status-badge" style="margin-left:12px;font-size:0.7em;opacity:0.8;font-weight:normal">checking…</span>
            </h3>
            <div class="settings-accordion" data-accordion="stt-silero">
                <div class="settings-accordion-header" data-accordion-toggle="stt-silero">
                    <span class="accordion-arrow">▼</span>
                    <h4>Silero VAD</h4>
                </div>
                <div class="settings-accordion-body" data-accordion-body="stt-silero">
                    ${sileroToggleHtml}
                    ${sileroFields}
                    ${testButtonHtml}
                </div>
            </div>
            ${ctx.renderAccordion('stt-classic', classicKeys, 'Classic VAD (fallback engine — used when Silero is off or unavailable)')}
        </div>
    `;
}

// ── Status badge polling ─────────────────────────────────────────────────────
// Updates ONLY the badge text/color — never touches any input or checkbox.
// The user's unsaved state lives in pendingChanges + the DOM; polling reads
// server state and reports it visually, nothing more.

async function updateVadStatusBadge(el) {
    const badge = el.querySelector('#vad-status-badge');
    if (!badge) return;
    try {
        const resp = await fetch('/api/stt/vad-status', { credentials: 'same-origin' });
        const data = await resp.json();
        let label, color;
        if (data.state === 'ready') { label = '✓ Silero ready'; color = '#5fd17a'; }
        else if (data.state === 'pending') { label = '⟳ downloading…'; color = '#e0c068'; }
        else { label = `✗ unavailable: ${data.reason || 'unknown'}`; color = '#e07060'; }
        badge.textContent = label;
        badge.style.color = color;
    } catch (e) {
        badge.textContent = '? status check failed';
        badge.style.color = '#e07060';
    }
}

function attachVadTestListener(el) {
    const btn = el.querySelector('#vad-test-btn');
    const resultEl = el.querySelector('#vad-test-result');
    if (!btn || !resultEl) return;
    btn.addEventListener('click', async () => {
        btn.disabled = true;
        const originalLabel = btn.textContent;
        btn.textContent = 'Recording 5s — speak now…';
        resultEl.textContent = '';
        try {
            const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
            const resp = await fetch('/api/stt/vad-test', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
                body: JSON.stringify({ duration_s: 5.0 })
            });
            const data = await resp.json();
            if (!data.ok) {
                resultEl.innerHTML = `<span style="color:#e07060">✗ ${data.error || 'test failed'}</span>`;
            } else {
                const colorMap = { comfortable: '#5fd17a', marginal: '#e0c068', too_high: '#e07060' };
                const color = colorMap[data.verdict] || '#fff';
                resultEl.innerHTML = `
                    <div>peak <strong style="color:${color}">${data.max_prob}</strong>
                         · mean ${data.mean_prob}
                         · ${data.num_chunks_scored} chunks scored
                         · audio amp ${data.peak_amp}</div>
                    <div style="margin-top:6px;color:${color}">${data.suggestion}</div>`;
            }
        } catch (e) {
            resultEl.innerHTML = `<span style="color:#e07060">✗ test request failed: ${e.message}</span>`;
        } finally {
            btn.disabled = false;
            btn.textContent = originalLabel;
        }
    });
}

export default {
    id: 'stt',
    name: 'STT',
    icon: '🎤',
    description: 'Speech-to-text engine and voice detection',

    render(ctx) {
        const cfg = _mergedConfig || tabConfig;
        return renderProviderTab(cfg, ctx) + renderVADSection(ctx);
    },

    async attachListeners(ctx, el) {
        {
            _mergedConfig = await mergeRegistryProviders(tabConfig);
            if (Object.keys(_mergedConfig.providers).length > Object.keys(tabConfig.providers).length) {
                const body = el.querySelector('.settings-tab-body') || el;
                body.innerHTML = this.render(ctx);
                if (ctx.attachAccordionListeners) ctx.attachAccordionListeners(el);
            }
        }
        const cfg = _mergedConfig || tabConfig;
        attachProviderListeners(cfg, ctx, el);

        // VAD wiring — badge polling + test button only. The checkbox is
        // bound by the generic data-key path, no manual handlers here.
        updateVadStatusBadge(el);
        attachVadTestListener(el);
        // Timer guard on el (the PERSISTENT #settings-content container): the
        // old cleanup was a body-wide MutationObserver waiting for el to leave
        // the DOM — but renderTabContent only ever innerHTML-refills el, so
        // the guard could never fire and every render stacked one interval +
        // one document-subtree observer (GC pressure scaling with chat churn).
        // Same shape as attachGenericListeners' _genericBound guard.
        // (negspace N19, 2026-08-31)
        if (el._vadTimer) clearInterval(el._vadTimer);
        el._vadTimer = setInterval(() => {
            if (!el.querySelector('#vad-status-badge')) {
                clearInterval(el._vadTimer);
                el._vadTimer = null;
                return;
            }
            updateVadStatusBadge(el);
        }, 5000);
    }
};
