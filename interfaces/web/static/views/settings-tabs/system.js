// settings-tabs/system.js - System settings and danger zone
import { resetAllSettings, resetPrompts, mergePrompts, resetChatDefaults } from '../../shared/settings-api.js';
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

            <div class="danger-zone">
                <h4>Danger Zone</h4>
                <div class="danger-section">
                    <h5>Settings</h5>
                    <button class="btn-sm danger" id="dz-reset-all">Reset All Settings</button>
                    <p class="text-muted" style="font-size:var(--font-xs);margin:4px 0 0">Reverts everything to defaults. Requires restart.</p>
                </div>
                <div class="danger-section">
                    <h5>Prompts</h5>
                    <div style="display:flex;gap:8px">
                        <button class="btn-sm danger" id="dz-reset-prompts">Reset Prompts</button>
                        <button class="btn-sm danger" id="dz-merge-prompts">Merge Factory</button>
                    </div>
                    <p class="text-muted" style="font-size:var(--font-xs);margin:4px 0 0">Reset: overwrites all. Merge: factory overwrites conflicts, your additions kept.</p>
                </div>
                <div class="danger-section">
                    <h5>Chat Defaults</h5>
                    <button class="btn-sm danger" id="dz-reset-chat">Reset Chat Defaults</button>
                </div>
            </div>
        `;
    },

    attachListeners(ctx, el) {
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

        el.querySelector('#dz-merge-prompts')?.addEventListener('click', async () => {
            if (!confirm('Merge factory defaults? Conflicts will use factory values.')) return;
            const t = prompt('Type MERGE to confirm:');
            if (t?.toUpperCase() !== 'MERGE') return;
            try {
                await mergePrompts();
                ui.showToast('Factory defaults merged', 'success');
                updateScene();
            } catch { ui.showToast('Failed', 'error'); }
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
