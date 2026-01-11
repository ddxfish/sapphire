// tabs/identity.js - User and AI identity setup

import { updateSetting } from '../setup-api.js';

export default {
  id: 'identity',
  name: 'Identity',
  icon: '👤',

  render(settings) {
    const userName = settings.DEFAULT_USERNAME || 'Human Protagonist';
    const aiName = settings.DEFAULT_AI_NAME || 'Sapphire';

    return `
      <div class="identity-section">
        <div class="identity-field">
          <label for="setup-user-name">Your Name</label>
          <input type="text" id="setup-user-name" class="identity-input" 
                 value="${userName}" placeholder="What should Sapphire call you?">
        </div>

        <div class="identity-field">
          <label for="setup-ai-name">AI Name</label>
          <input type="text" id="setup-ai-name" class="identity-input" 
                 value="${aiName}" placeholder="Name your AI assistant">
        </div>
      </div>
    `;
  },

  attachListeners(container, settings, updateSettings) {
    const userInput = container.querySelector('#setup-user-name');
    const aiInput = container.querySelector('#setup-ai-name');

    // Debounced save on blur or enter
    const saveField = async (key, input) => {
      const value = input.value.trim();
      if (!value) return;
      
      try {
        await updateSetting(key, value);
        settings[key] = value;
      } catch (err) {
        console.error(`Failed to save ${key}:`, err);
      }
    };

    userInput?.addEventListener('blur', () => saveField('DEFAULT_USERNAME', userInput));
    userInput?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        saveField('DEFAULT_USERNAME', userInput);
        aiInput?.focus();
      }
    });

    aiInput?.addEventListener('blur', () => saveField('DEFAULT_AI_NAME', aiInput));
    aiInput?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        saveField('DEFAULT_AI_NAME', aiInput);
      }
    });
  },

  validate(settings) {
    return { valid: true };
  }
};