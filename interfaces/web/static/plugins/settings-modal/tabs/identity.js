// tabs/identity.js - Identity tab with name settings and avatar uploads
import settingsAPI from '../settings-api.js';
import { showToast } from '../../../shared/toast.js';

export default {
  id: 'identity',
  name: 'Identity',
  icon: '👤',
  description: 'User and AI identity settings',
  keys: ['DEFAULT_USERNAME', 'DEFAULT_AI_NAME', 'AVATARS_IN_CHAT'],

  render(modal) {
    const avatarSection = this.renderAvatarSection(modal);
    return `
      <div class="settings-list">
        ${modal.renderCategorySettings(this.keys)}
      </div>
      <div style="margin-bottom: 20px;"></div>
      ${avatarSection}
    `;
  },

  renderAvatarSection(modal) {
    const userPath = modal.avatarPaths?.user || '/static/users/user.webp';
    const assistantPath = modal.avatarPaths?.assistant || '/static/users/assistant.webp';
    const ts = Date.now();
    
    return `
      <div class="avatar-upload-section">
        <div class="avatar-column">
          <h4>User Avatar</h4>
          <img src="${userPath}?t=${ts}" alt="User" class="avatar-preview" id="user-avatar-preview" 
               onerror="this.src='/static/users/user.webp'">
          <input type="file" id="user-avatar-input" accept=".png,.jpg,.jpeg,.gif,.webp" hidden>
          <button class="btn btn-secondary" id="user-avatar-btn">Choose File</button>
          <span class="avatar-hint">PNG, JPG, GIF, WEBP • Max 4MB</span>
        </div>
        <div class="avatar-column">
          <h4>Assistant Avatar</h4>
          <img src="${assistantPath}?t=${ts}" alt="Assistant" class="avatar-preview" id="assistant-avatar-preview"
               onerror="this.src='/static/users/assistant.webp'">
          <input type="file" id="assistant-avatar-input" accept=".png,.jpg,.jpeg,.gif,.webp" hidden>
          <button class="btn btn-secondary" id="assistant-avatar-btn">Choose File</button>
          <span class="avatar-hint">PNG, JPG, GIF, WEBP • Max 4MB</span>
        </div>
      </div>
    `;
  },

  attachListeners(modal, contentEl) {
    const userBtn = contentEl.querySelector('#user-avatar-btn');
    const userInput = contentEl.querySelector('#user-avatar-input');
    const assistantBtn = contentEl.querySelector('#assistant-avatar-btn');
    const assistantInput = contentEl.querySelector('#assistant-avatar-input');
    
    if (userBtn && userInput) {
      userBtn.addEventListener('click', () => userInput.click());
      userInput.addEventListener('change', (e) => this.handleAvatarUpload(modal, 'user', e.target.files[0]));
    }
    
    if (assistantBtn && assistantInput) {
      assistantBtn.addEventListener('click', () => assistantInput.click());
      assistantInput.addEventListener('change', (e) => this.handleAvatarUpload(modal, 'assistant', e.target.files[0]));
    }
  },

  async handleAvatarUpload(modal, role, file) {
    if (!file) return;
    
    const checkResult = await settingsAPI.checkAvatar(role).catch(() => ({ exists: false }));
    if (checkResult.exists) {
      if (!confirm(`Replace existing ${role} avatar?`)) {
        const input = modal.modal.querySelector(`#${role}-avatar-input`);
        if (input) input.value = '';
        return;
      }
    }
    
    const btn = modal.modal.querySelector(`#${role}-avatar-btn`);
    const originalText = btn?.textContent;
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Uploading...';
    }
    
    try {
      const result = await settingsAPI.uploadAvatar(role, file);
      
      const preview = modal.modal.querySelector(`#${role}-avatar-preview`);
      if (preview && result.path) {
        preview.src = `${result.path}?t=${Date.now()}`;
        modal.avatarPaths[role] = result.path;
      }
      
      showToast(`${role.charAt(0).toUpperCase() + role.slice(1)} avatar updated`, 'success');
    } catch (e) {
      console.error('Avatar upload failed:', e);
      showToast(`Upload failed: ${e.message}`, 'error');
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = originalText;
      }
      const input = modal.modal.querySelector(`#${role}-avatar-input`);
      if (input) input.value = '';
    }
  }
};