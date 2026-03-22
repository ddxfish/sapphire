// trigger-editor/editor.js - Unified modal shell for all trigger types
// Composes trigger section (cron, event) + AI config section
import { fetchAIConfigData, renderAIConfig, wireAIConfig, readAIConfig } from './ai-config.js';
import { renderCronTrigger, wireCronTrigger, readCronTrigger } from './trigger-cron.js';
import { renderEventTrigger, wireEventTrigger, readEventTrigger } from './trigger-event.js';
import { setupModalClose } from '../modal.js';

const EMOJI_PICKS = [
    '\u2764\uFE0F', '\uD83E\uDE77', '\uD83E\uDDE1', '\uD83D\uDC9B', '\uD83D\uDC9A', '\uD83D\uDC99', '\uD83D\uDC9C',
    '\uD83D\uDDA4', '\uD83E\uDD0D', '\uD83E\uDE76', '\uD83D\uDC96', '\uD83D\uDC9D', '\uD83D\uDC93', '\uD83D\uDC97', '\uD83D\uDC95',
    '\uD83D\uDD25', '\u26A1', '\uD83C\uDF19', '\u2728', '\uD83D\uDCAB', '\uD83C\uDF1F', '\u2600\uFE0F', '\uD83C\uDF08',
    '\uD83C\uDF0A', '\uD83C\uDF3F', '\uD83C\uDF38', '\uD83C\uDF40', '\uD83E\uDD8B', '\uD83D\uDD4A\uFE0F', '\uD83E\uDEA9',
    '\uD83E\uDDE0', '\uD83D\uDC41\uFE0F', '\uD83D\uDEE1\uFE0F', '\uD83D\uDD2E', '\uD83C\uDF00', '\uD83D\uDCA1',
    '\uD83D\uDD27', '\u2699\uFE0F', '\uD83D\uDCE1', '\uD83D\uDEF8',
    '\uD83D\uDC8E', '\uD83C\uDFAF', '\uD83D\uDD14', '\uD83C\uDFB5', '\uD83D\uDD11', '\uD83D\uDEE1\uFE0F',
    '\uD83D\uDDDD\uFE0F', '\uD83C\uDFF9', '\uD83E\uDEAC', '\uD83D\uDCBF'
];

const TYPE_CONFIG = {
    task:      { label: 'Task', blurb: 'Tasks run at a scheduled time \u2014 an alarm that triggers Sapphire to do something specific.', trigger: 'cron', hasEmoji: false },
    heartbeat: { label: 'Heartbeat', blurb: 'Heartbeats wake Sapphire up on a rhythm to check on things. She remembers what she found last time.', trigger: 'cron', hasEmoji: true },
    daemon:    { label: 'Daemon', blurb: 'Daemons respond to external events \u2014 a Discord message, a sensor alert, or any event source plugin.', trigger: 'event', hasEmoji: true },
    webhook:   { label: 'Webhook', blurb: 'Webhooks listen for HTTP requests and trigger Sapphire when called from external services.', trigger: 'event', hasEmoji: false },
};

/**
 * Open the unified trigger editor modal
 * @param {Object|null} task - Existing task to edit, or null for new
 * @param {string} type - 'task' | 'heartbeat' | 'daemon' | 'webhook'
 * @param {Object} callbacks - { onSave, onDelete, onDataReload }
 */
export async function openTriggerEditor(task, type, callbacks = {}) {
    document.querySelector('.sched-editor-overlay')?.remove();

    const config = TYPE_CONFIG[type];
    if (!config) { console.error('Unknown trigger type:', type); return; }

    const isEdit = !!task;
    const t = task || {};

    // Set defaults for new items
    if (!isEdit) {
        if (type === 'heartbeat') { t.tts_enabled = false; t.emoji = t.emoji || '\u2764\uFE0F'; }
        if (type === 'daemon') { t.emoji = t.emoji || '\uD83D\uDCE1'; }
    }

    // Fetch AI config data
    const aiData = await fetchAIConfigData();

    // Build trigger section
    const triggerHTML = config.trigger === 'cron'
        ? renderCronTrigger(t, { isHeartbeat: type === 'heartbeat' })
        : renderEventTrigger(t, { type });

    // Build AI config section
    const aiHTML = renderAIConfig(t, aiData, { isHeartbeat: type === 'heartbeat' });

    // Build modal
    const modal = document.createElement('div');
    modal.className = 'sched-editor-overlay';
    modal.innerHTML = `
        <div class="sched-editor">
            <div class="sched-editor-header">
                ${config.hasEmoji ? `
                <div class="sched-hb-emoji-wrap" id="sched-hb-emoji-wrap">
                    <span class="sched-hb-emoji-btn" id="sched-hb-emoji-btn" title="Pick emoji">${t.emoji || '\u2764\uFE0F'}</span>
                </div>` : ''}
                <h3>${isEdit ? 'Edit' : 'New'} ${config.label}</h3>
                <div style="flex:1"></div>
                ${isEdit ? `<button class="btn-sm danger" id="ed-delete" style="margin-right:8px">Delete</button>` : ''}
                <button class="btn-icon" data-action="close">&times;</button>
            </div>
            <div class="sched-editor-body">
                <p class="sched-editor-blurb">${config.blurb}</p>

                <div class="sched-field">
                    <label>${config.label} Name</label>
                    <input type="text" id="ed-name" value="${_esc(t.name || '')}" placeholder="${_placeholderForType(type)}">
                </div>
                <div class="sched-field">
                    <label>${_messageLabel(type)} <span class="help-tip" data-tip="${_messageTip(type)}">?</span></label>
                    <textarea id="ed-message" rows="2" placeholder="${_messageHintForType(type)}">${_esc(t.initial_message || '')}</textarea>
                </div>

                ${triggerHTML}
                ${config.trigger === 'event' ? '<hr class="sched-divider" style="margin-top:20px">' : ''}
                ${aiHTML}
            </div>
            <div class="sched-editor-footer">
                <button class="btn-sm" data-action="close">Cancel</button>
                <button class="btn-primary" id="ed-save">${isEdit ? 'Save' : 'Create ' + config.label}</button>
            </div>
        </div>
    `;

    document.body.appendChild(modal);

    // Tooltips
    const tipEl = document.createElement('div');
    tipEl.className = 'help-tip-popup';
    document.body.appendChild(tipEl);
    modal.addEventListener('mouseover', e => {
        const tip = e.target.closest('.help-tip');
        if (!tip?.dataset.tip) return;
        tipEl.textContent = tip.dataset.tip;
        tipEl.style.display = 'block';
        const r = tip.getBoundingClientRect();
        tipEl.style.left = (r.left + r.width / 2) + 'px';
        tipEl.style.top = (r.top - 6) + 'px';
    });
    modal.addEventListener('mouseout', e => {
        if (e.target.closest('.help-tip') && !e.target.closest('.help-tip').contains(e.relatedTarget))
            tipEl.style.display = 'none';
    });

    // Close
    const close = () => { modal.remove(); tipEl.remove(); };
    setupModalClose(modal, close);
    modal.querySelectorAll('[data-action="close"]').forEach(b => b.addEventListener('click', close));

    // Delete
    if (isEdit && callbacks.onDelete) {
        modal.querySelector('#ed-delete')?.addEventListener('click', async () => {
            if (!confirm(`Delete "${t.name}"?`)) return;
            await callbacks.onDelete(t.id);
            close();
        });
    }

    // Emoji picker
    if (config.hasEmoji) {
        _wireEmojiPicker(modal);
    }

    // Wire trigger section
    let getCurrentCron = null;
    if (config.trigger === 'cron') {
        getCurrentCron = wireCronTrigger(modal, { isHeartbeat: type === 'heartbeat' });
    } else {
        wireEventTrigger(modal, { type, triggerConfig: t.trigger_config });
    }

    // Wire AI config
    wireAIConfig(modal, t, aiData);

    // Save
    modal.querySelector('#ed-save')?.addEventListener('click', async () => {
        const name = modal.querySelector('#ed-name')?.value?.trim();
        if (!name) { alert('Name is required'); return; }

        const aiConfig = readAIConfig(modal);

        let triggerConfig;
        if (config.trigger === 'cron') {
            triggerConfig = readCronTrigger(modal, getCurrentCron);
            if (!triggerConfig.schedule) { alert('Schedule is required'); return; }
        } else {
            triggerConfig = readEventTrigger(modal);
        }

        const selectedEmoji = modal.querySelector('#sched-hb-emoji-btn')?.textContent?.trim();
        const data = {
            name,
            type,
            initial_message: modal.querySelector('#ed-message')?.value?.trim() || '',
            heartbeat: type === 'heartbeat',  // backward compat
            emoji: selectedEmoji || t.emoji || '',
            ...triggerConfig,
            ...aiConfig,
        };

        if (callbacks.onSave) {
            const success = await callbacks.onSave(isEdit ? t.id : null, data);
            if (success !== false) close();
        }
    });
}

// ── Private helpers ──

function _wireEmojiPicker(modal) {
    const emojiBtn = modal.querySelector('#sched-hb-emoji-btn');
    emojiBtn?.addEventListener('click', e => {
        e.stopPropagation();
        const wrap = modal.querySelector('#sched-hb-emoji-wrap');
        if (!wrap) return;
        wrap.querySelector('.sched-hb-emoji-picker')?.remove();
        const picker = document.createElement('div');
        picker.className = 'sched-hb-emoji-picker';
        picker.innerHTML = `<div class="sched-hb-emoji-grid">${EMOJI_PICKS.map(em =>
            `<button class="sched-hb-emoji-opt" data-emoji="${em}">${em}</button>`
        ).join('')}</div>`;
        wrap.appendChild(picker);
        picker.addEventListener('click', ev => {
            const opt = ev.target.closest('.sched-hb-emoji-opt');
            if (!opt) return;
            emojiBtn.textContent = opt.dataset.emoji;
            picker.remove();
        });
        const closePicker = ev => {
            if (!picker.contains(ev.target) && ev.target !== emojiBtn) {
                picker.remove();
                document.removeEventListener('click', closePicker);
            }
        };
        setTimeout(() => document.addEventListener('click', closePicker), 0);
    });
}

function _placeholderForType(type) {
    switch (type) {
        case 'heartbeat': return 'System Health Check';
        case 'daemon': return 'Discord Responder';
        case 'webhook': return 'Deploy Notifier';
        default: return 'Morning Greeting';
    }
}

function _messageLabel(type) {
    switch (type) {
        case 'heartbeat': return 'What should Sapphire check?';
        case 'daemon': return 'Instructions';
        case 'webhook': return 'Instructions';
        default: return 'What should Sapphire do?';
    }
}

function _messageTip(type) {
    switch (type) {
        case 'daemon': return 'Prepended before each incoming event. Tell Sapphire how to handle these messages \u2014 tone, format, what to focus on. The actual event data follows automatically.';
        case 'webhook': return 'Prepended before each incoming webhook payload. Tell Sapphire what to do with the data. The raw payload follows automatically.';
        case 'heartbeat': return 'What the AI receives when this fires. Be specific \u2014 the AI only knows what you tell it here.';
        default: return 'What the AI receives when this fires. Be specific \u2014 the AI only knows what you tell it here.';
    }
}

function _messageHintForType(type) {
    switch (type) {
        case 'heartbeat': return 'Check my emails and calendar. If anything needs attention, tell me. Otherwise just say all clear.';
        case 'daemon': return 'Respond to Discord messages casually. Keep it short. Use emoji if it feels natural.';
        case 'webhook': return 'Summarize the incoming data and notify me if anything needs attention.';
        default: return 'Write a brief daily summary of what happened today.';
    }
}

function _esc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
