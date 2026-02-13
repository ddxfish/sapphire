// settings-tabs/identity.js - User/AI identity and avatars
import { uploadAvatar } from '../../shared/settings-api.js';
import * as ui from '../../ui.js';

export default {
    id: 'identity',
    name: 'Identity',
    icon: '\uD83D\uDC64',
    description: 'User and AI identity settings',
    keys: ['DEFAULT_USERNAME', 'DEFAULT_AI_NAME', 'AVATARS_IN_CHAT'],

    render(ctx) {
        const userPath = ctx.avatarPaths?.user || '/static/users/user.webp';
        const assistantPath = ctx.avatarPaths?.assistant || '/static/users/assistant.webp';
        const ts = Date.now();

        return `
            ${ctx.renderFields(this.keys)}
            <div class="avatar-section">
                <div class="avatar-col">
                    <h4>User Avatar</h4>
                    <img src="${userPath}?t=${ts}" class="avatar-preview" id="user-avatar-img"
                         onerror="this.src='/static/users/user.webp'">
                    <input type="file" id="user-avatar-file" accept=".png,.jpg,.jpeg,.gif,.webp" hidden>
                    <button class="btn-sm" id="user-avatar-btn">Choose File</button>
                </div>
                <div class="avatar-col">
                    <h4>Assistant Avatar</h4>
                    <img src="${assistantPath}?t=${ts}" class="avatar-preview" id="asst-avatar-img"
                         onerror="this.src='/static/users/assistant.webp'">
                    <input type="file" id="asst-avatar-file" accept=".png,.jpg,.jpeg,.gif,.webp" hidden>
                    <button class="btn-sm" id="asst-avatar-btn">Choose File</button>
                </div>
            </div>
        `;
    },

    attachListeners(ctx, el) {
        this.setupUpload(ctx, el, 'user', '#user-avatar-btn', '#user-avatar-file', '#user-avatar-img');
        this.setupUpload(ctx, el, 'assistant', '#asst-avatar-btn', '#asst-avatar-file', '#asst-avatar-img');
    },

    setupUpload(ctx, el, role, btnSel, fileSel, imgSel) {
        const btn = el.querySelector(btnSel);
        const file = el.querySelector(fileSel);
        if (!btn || !file) return;

        btn.addEventListener('click', () => file.click());
        file.addEventListener('change', async e => {
            const f = e.target.files[0];
            if (!f) return;
            btn.disabled = true;
            btn.textContent = 'Uploading...';
            try {
                const result = await uploadAvatar(role, f);
                const img = el.querySelector(imgSel);
                if (img && result.path) {
                    img.src = `${result.path}?t=${Date.now()}`;
                    ctx.avatarPaths[role] = result.path;
                }
                ui.showToast(`${role} avatar updated`, 'success');
            } catch (err) {
                ui.showToast(`Upload failed: ${err.message}`, 'error');
            } finally {
                btn.disabled = false;
                btn.textContent = 'Choose File';
                file.value = '';
            }
        });
    }
};
