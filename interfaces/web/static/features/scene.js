// features/scene.js - Scene state, prompt display, functions display
import * as api from '../api.js';
import { getElements, setTtsEnabled } from '../core/state.js';

export async function updateScene() {
    try {
        const status = await api.fetchSystemStatus();
        
        if (status?.tts_enabled !== undefined) {
            setTtsEnabled(status.tts_enabled);
            const volumeRow = document.querySelector('.sidebar-row-3');
            if (volumeRow) volumeRow.style.display = status.tts_enabled ? '' : 'none';
        }
        
        updatePrompt(status?.prompt, status?.prompt_name, status?.prompt_char_count);
        updateFuncs(status?.functions, status?.ability);
    } catch {}
}

function updatePrompt(state, promptName, charCount) {
    const { promptPill } = getElements();
    if (!promptPill) return;
    
    const textEl = promptPill.querySelector('.pill-text');
    const tooltipEl = promptPill.querySelector('.pill-tooltip');
    
    // Format char count (2400 -> 2.4k)
    const formatCount = (n) => n >= 1000 ? (n / 1000).toFixed(1) + 'k' : n;
    const displayName = promptName || 'Unknown';
    const displayCount = charCount !== undefined ? ` (${formatCount(charCount)})` : '';
    
    textEl.textContent = `${displayName}${displayCount}`;
    
    // Build tooltip from state
    if (state) {
        const parts = [];
        ['location', 'persona', 'goals', 'scenario', 'relationship', 'format'].forEach(k => {
            const v = state[k];
            if (v && v !== 'default' && v !== 'none') parts.push(`${k}: ${v}`);
        });
        if (state.extras?.length > 0) parts.push(`extras: ${state.extras.join(', ')}`);
        if (state.emotions?.length > 0) parts.push(`emotions: ${state.emotions.join(', ')}`);
        tooltipEl.textContent = parts.length ? parts.join('\n') : 'Monolith prompt';
    } else {
        tooltipEl.textContent = '';
    }
}

function updateFuncs(funcs, ability) {
    const { abilityPill } = getElements();
    if (!abilityPill) return;
    
    const textEl = abilityPill.querySelector('.pill-text');
    const tooltipEl = abilityPill.querySelector('.pill-tooltip');
    
    if (!funcs || funcs.length === 0) {
        textEl.textContent = 'None (0)';
        tooltipEl.textContent = 'No functions enabled';
        abilityPill.classList.remove('warning');
        return;
    }
    
    const name = ability?.name || 'Custom';
    const count = ability?.function_count || funcs.length;
    const expected = ability?.expected_count;
    
    textEl.textContent = `${name} (${count})`;
    
    // Warning state if missing functions
    if (expected && count < expected) {
        abilityPill.classList.add('warning');
        tooltipEl.textContent = `⚠️ ${count}/${expected} functions\n${funcs.join(', ')}`;
    } else {
        abilityPill.classList.remove('warning');
        tooltipEl.textContent = funcs.join(', ');
    }
}