// styles.js - CSS injection for Prompt Manager plugin
// Modal styles now in shared.css

export function injectStyles() {
  if (document.getElementById('pm-styles')) return;
  
  const style = document.createElement('style');
  style.id = 'pm-styles';
  style.textContent = `
    .prompt-manager-plugin {
      background: var(--bg-secondary);
      border-radius: var(--radius-lg);
      padding: 12px;
      margin-top: 5px;
    }
    
    .pm-controls {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 8px;
    }
    
    .pm-controls select {
      flex: 1 1 100%;
      padding: 4px 8px;
      padding-right: 30px;
      font-size: var(--font-sm);
      font-family: var(--font-mono);
    }
    
    .pm-control-buttons {
      display: flex;
      gap: 4px;
      width: 100%;
    }
    
    .pm-editor {
      background: transparent;
      padding: 0;
      margin-bottom: 0;
      min-height: 200px;
      max-height: 400px;
      overflow-y: auto;
    }
    
    .pm-placeholder, .pm-error, .pm-notice {
      color: var(--text-muted);
      font-style: italic;
      text-align: center;
      padding: 20px;
    }
    
    .pm-error { color: var(--error-text); }
    
    .pm-notice {
      font-size: var(--font-sm);
      padding: 10px;
      margin-top: 10px;
      background: var(--warning-light);
      border-radius: var(--radius-sm);
    }
    
    #pm-content {
      width: 100%;
      min-height: 250px;
      background: var(--plugin-prompt-bg);
      border: none;
      color: var(--text-light);
      padding: 0;
      border-radius: 0;
      font-family: var(--font-mono);
      font-size: var(--font-sm);
      resize: vertical;
    }
    
    #pm-content:focus {
      background: var(--plugin-prompt-bg-focus);
      outline: none;
      box-shadow: 0 0 0 3px var(--focus-ring);
    }
    
    .pm-component {
      margin-bottom: 4px;
    }
    
    .pm-component label {
      display: block;
      color: var(--text-muted);
      font-size: var(--font-sm);
      margin-bottom: 2px;
    }
    
    .pm-component select {
      padding: 3px 8px;
      padding-right: 30px;
      font-size: var(--font-sm);
      font-family: var(--font-mono);
      border: none;
    }

    .pm-component-row {
      display: flex;
      gap: 4px;
      align-items: center;
    }
    
    .pm-component-row select {
      flex: 1;
      min-width: 0;
    }
    
    .pm-component select:hover,
    .pm-component select:focus {
      background-color: var(--bg-hover);
    }
    
    .pm-component select:focus {
      box-shadow: 0 0 0 2px var(--focus-ring);
    }
    
    .pm-selected-items {
      flex: 1;
      padding: 3px;
      background: var(--input-bg);
      border: none;
      border-radius: var(--radius-sm);
      color: var(--text-light);
      font-size: var(--font-sm);
      font-family: var(--font-mono);
      min-height: 20px;
    }
  `;
  document.head.appendChild(style);
}