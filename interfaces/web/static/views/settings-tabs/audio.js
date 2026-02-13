// settings-tabs/audio.js - Audio device selection and testing
// Delegates to shared/audio-devices.js for device management
import { populateDeviceSelects, attachAudioDeviceListeners } from '../../shared/audio-devices.js';

export default {
    id: 'audio',
    name: 'Audio',
    icon: '\uD83C\uDFA7',
    description: 'Audio device selection and testing',
    advancedKeys: ['AUDIO_SAMPLE_RATES', 'AUDIO_BLOCKSIZE_FALLBACKS', 'AUDIO_PREFERRED_DEVICES_LINUX', 'AUDIO_PREFERRED_DEVICES_WINDOWS'],

    render(ctx) {
        return `
            <div class="audio-section">
                <h4>Input Device (Microphone)</h4>
                <div style="display:flex;gap:8px;margin-bottom:8px">
                    <select id="audio-input-select" style="flex:1;padding:6px 8px;background:var(--input-bg);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-size:var(--font-sm)">
                        <option value="auto">Auto-detect</option>
                    </select>
                    <button class="btn-sm" data-test="input">\uD83C\uDF99 Test</button>
                </div>
                <div class="test-result" data-result="input"></div>
            </div>
            <div class="audio-section">
                <h4>Output Device (Speakers)</h4>
                <div style="display:flex;gap:8px;margin-bottom:8px">
                    <select id="audio-output-select" style="flex:1;padding:6px 8px;background:var(--input-bg);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-size:var(--font-sm)">
                        <option value="auto">System default</option>
                    </select>
                    <button class="btn-sm" data-test="output">\uD83D\uDD0A Test</button>
                </div>
                <div class="test-result" data-result="output"></div>
            </div>
            ${ctx.renderAccordion('audio-adv', this.advancedKeys)}
        `;
    },

    async attachListeners(ctx, el) {
        try { await populateDeviceSelects(el); } catch {}
        attachAudioDeviceListeners(el, {
            onInputChange: v => { ctx.settings.AUDIO_INPUT_DEVICE = v; ctx.markChanged('AUDIO_INPUT_DEVICE', v); },
            onOutputChange: v => { ctx.settings.AUDIO_OUTPUT_DEVICE = v; ctx.markChanged('AUDIO_OUTPUT_DEVICE', v); }
        });
        ctx.attachAccordionListeners(el);
    }
};
