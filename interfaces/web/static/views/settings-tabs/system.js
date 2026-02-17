// settings-tabs/system.js - System settings and danger zone
import { resetAllSettings, resetPrompts, mergeUpdates, resetChatDefaults } from '../../shared/settings-api.js';
import * as ui from '../../ui.js';
import { updateScene } from '../../features/scene.js';

export default {
    id: 'system',
    name: 'System',
    icon: '\u26A1',
    description: 'System settings and danger zone',
    essentialKeys: ['PLUGINS_ENABLED', 'WEB_UI_SSL_ADHOC'],
    advancedKeys: ['WEB_UI_HOST', 'WEB_UI_PORT'],

    render(ctx) {
        return `
            ${ctx.renderFields(this.essentialKeys)}
            ${ctx.renderAccordion('sys-adv', this.advancedKeys)}

            <div class="system-tools" style="margin:20px 0;padding:16px;border:1px solid var(--border);border-radius:var(--radius)">
                <h4 style="margin:0 0 10px;font-size:var(--font-sm)">Tools</h4>
                <button class="btn-primary" id="sys-setup-wizard">Run Setup Wizard</button>
                <p class="text-muted" style="font-size:var(--font-xs);margin:4px 0 0">Configure LLM, audio, voice, and identity settings step by step.</p>
            </div>

            <div class="danger-zone">
                <h4>Danger Zone</h4>
                <div class="danger-section">
                    <h5>Settings</h5>
                    <button class="btn-sm danger" id="dz-reset-all">Reset All Settings</button>
                    <p class="text-muted" style="font-size:var(--font-xs);margin:4px 0 0">Reverts everything to defaults. Requires restart.</p>
                </div>
                <div class="danger-section">
                    <h5>Prompts & Personas</h5>
                    <button class="btn-primary" id="dz-merge-updates" style="margin-bottom:6px">Import App Updates</button>
                    <p class="text-muted" style="font-size:var(--font-xs);margin:0 0 10px">Adds new prompts and personas from updates without touching your stuff. Backs up first.</p>
                    <button class="btn-sm danger" id="dz-reset-prompts">Reset Prompts to Defaults</button>
                    <p class="text-muted" style="font-size:var(--font-xs);margin:4px 0 0">Overwrites all prompt files with factory versions. Creates backup first.</p>
                </div>
                <div class="danger-section">
                    <h5>Chat Defaults</h5>
                    <button class="btn-sm danger" id="dz-reset-chat">Reset Chat Defaults</button>
                </div>
            </div>
        `;
    },

    attachListeners(ctx, el) {
        el.querySelector('#sys-setup-wizard')?.addEventListener('click', () => {
            if (window.sapphireSetupWizard) {
                window.sapphireSetupWizard.open(true);
            } else {
                ui.showToast('Setup wizard plugin not loaded', 'error');
            }
        });

        el.querySelector('#dz-reset-all')?.addEventListener('click', async () => {
            if (!confirm('Reset ALL settings to defaults?')) return;
            const t = prompt('Type RESET to confirm:');
            if (t?.toUpperCase() !== 'RESET') return;
            try {
                await resetAllSettings();
                ui.showToast('All settings reset. Restart to apply.', 'success');
                ctx.refreshTab();
            } catch { ui.showToast('Failed', 'error'); }
        });

        el.querySelector('#dz-reset-prompts')?.addEventListener('click', async () => {
            if (!confirm('Reset ALL prompts to factory defaults?')) return;
            const t = prompt('Type RESET to confirm:');
            if (t?.toUpperCase() !== 'RESET') return;
            try {
                await resetPrompts();
                ui.showToast('Prompts reset', 'success');
                updateScene();
            } catch { ui.showToast('Failed', 'error'); }
        });

        el.querySelector('#dz-merge-updates')?.addEventListener('click', async () => {
            if (!confirm('Import new prompts and personas from app updates?\n\nYour existing content is untouched. A backup is created first.')) return;
            try {
                const result = await mergeUpdates();
                const a = result.added || {};
                const total = (a.components||0) + (a.presets||0) + (a.monoliths||0) + (a.spice_categories||0) + (a.personas||0);
                if (total === 0) {
                    ui.showToast('Already up to date', 'info');
                } else {
                    const parts = [];
                    if (a.components) parts.push(`${a.components} components`);
                    if (a.presets) parts.push(`${a.presets} presets`);
                    if (a.monoliths) parts.push(`${a.monoliths} monoliths`);
                    if (a.spice_categories) parts.push(`${a.spice_categories} spice categories`);
                    if (a.personas) parts.push(`${a.personas} personas`);
                    ui.showToast(`Added ${parts.join(', ')}`, 'success');
                }
                updateScene();
                window.dispatchEvent(new CustomEvent('prompts-changed'));
            } catch { ui.showToast('Import failed', 'error'); }
        });

        el.querySelector('#dz-reset-chat')?.addEventListener('click', async () => {
            if (!confirm('Reset chat defaults?')) return;
            const t = prompt('Type RESET to confirm:');
            if (t?.toUpperCase() !== 'RESET') return;
            try {
                await resetChatDefaults();
                ui.showToast('Chat defaults reset', 'success');
            } catch { ui.showToast('Failed', 'error'); }
        });
    }
};
