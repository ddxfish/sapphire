// tabs/appearance.js - Theme and visual settings tab

export default {
  id: 'appearance',
  name: 'Visual',
  icon: '🎨',
  description: 'Theme and visual settings',
  keys: [], // Theme is localStorage, not backend config

  render(modal) {
    const themeOptions = modal.availableThemes.map(theme => {
      const selected = theme === modal.currentTheme ? 'selected' : '';
      const displayName = theme.charAt(0).toUpperCase() + theme.slice(1);
      return `<option value="${theme}" ${selected}>${displayName}</option>`;
    }).join('');

    return `
      <div class="appearance-container">
        <div class="appearance-controls">
          <div class="setting-row">
            <div class="setting-label">
              <label for="theme-select">Theme</label>
              <div class="help-text-short">Choose your visual theme</div>
            </div>
            <div class="setting-input">
              <select id="theme-select">${themeOptions}</select>
            </div>
          </div>
        </div>
        
        <div class="theme-preview">
          <div class="preview-titlebar">
            <span class="preview-dots">
              <span class="dot red"></span>
              <span class="dot yellow"></span>
              <span class="dot green"></span>
            </span>
            <span class="preview-title">Preview</span>
          </div>
          <div class="preview-content">
            <p class="preview-text">Sample text showing how content appears with this theme.</p>
            
            <div class="preview-accordion">
              <div class="preview-accordion-header">
                <span class="preview-toggle">▼</span>
                <span>Accordion Section</span>
              </div>
              <div class="preview-accordion-body">Collapsed content area</div>
            </div>
            
            <div class="preview-buttons">
              <span class="preview-btn primary">Primary</span>
              <span class="preview-btn secondary">Secondary</span>
              <span class="preview-btn danger">Danger</span>
            </div>
            
            <div class="preview-messages">
              <div class="preview-bubble user">User message bubble</div>
              <div class="preview-bubble assistant">Assistant response bubble</div>
            </div>
            
            <div class="preview-input">
              <span class="preview-input-box">Type message...</span>
              <span class="preview-send">Send</span>
            </div>
          </div>
        </div>
      </div>
    `;
  },

  attachListeners(modal, contentEl) {
    const themeSelect = contentEl.querySelector('#theme-select');
    if (themeSelect) {
      themeSelect.addEventListener('change', (e) => this.handleThemeChange(modal, e.target.value));
    }
  },

  handleThemeChange(modal, themeName) {
    modal.currentTheme = themeName;
    document.documentElement.setAttribute('data-theme', themeName);
    this.loadThemeCSS(themeName);
  },

  loadThemeCSS(themeName) {
    const existingLink = document.getElementById('theme-stylesheet');
    const href = `/static/themes/${themeName}.css`;
    
    if (existingLink) {
      existingLink.href = href;
    } else {
      const link = document.createElement('link');
      link.id = 'theme-stylesheet';
      link.rel = 'stylesheet';
      link.href = href;
      document.head.appendChild(link);
    }
  }
};