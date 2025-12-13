// styles.js - CSS injection for Ability Manager plugin
// Uses shared.css accordion system

export function injectStyles() {
  if (document.getElementById('am-styles')) return;
  
  const style = document.createElement('style');
  style.id = 'am-styles';
  style.textContent = `
    .ability-manager-plugin {
      padding: 8px;
    }
    
    .am-controls {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 10px;
    }
    
    .am-controls select {
      flex: 1 1 100%;
      font-family: var(--font-mono);
    }
    
    .am-control-buttons {
      display: flex;
      gap: 4px;
      width: 100%;
    }
    
    .am-status {
      color: var(--text-muted);
      font-size: var(--font-sm);
      text-align: center;
      padding: 6px;
      margin-bottom: 8px;
      background: var(--bg-inset);
      border-radius: var(--radius-sm);
    }
    
    .am-status strong {
      color: var(--text-light);
    }
    
    .am-functions {
      max-height: 300px;
      overflow-y: auto;
      background: var(--bg-inset);
      border-radius: var(--radius-sm);
      padding: 4px;
    }
    
    /* Module header customizations (extends .accordion-header) */
    .am-module .accordion-header {
      background: var(--plugin-ability-bg);
    }
    
    .am-module .accordion-header:hover {
      background: var(--plugin-ability-bg-hover);
    }
    
    /* Function items */
    .am-function {
      display: flex;
      flex-direction: column;
      gap: 2px;
      padding: 4px 4px 4px 8px;
      color: var(--text-secondary);
      font-size: var(--font-sm);
      transition: background var(--transition-normal);
      border-radius: var(--radius-sm);
    }
    
    .am-function:hover {
      background: var(--highlight-subtle);
    }
    
    .am-function-label {
      display: flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
    }
    
    .am-function input[type="checkbox"] {
      cursor: pointer;
      flex-shrink: 0;
    }
    
    .am-function input[type="checkbox"]:focus {
      outline: none;
      box-shadow: 0 0 0 2px var(--focus-ring);
    }
    
    .am-function-name {
      font-family: var(--font-mono);
      color: var(--text-bright);
    }
    
    .am-function-desc {
      color: var(--text-muted);
      font-size: var(--font-xs);
      padding-left: 24px;
      line-height: 1.3;
    }
    
    .am-placeholder {
      color: var(--text-muted);
      font-style: italic;
      text-align: center;
      padding: 20px;
    }
  `;
  document.head.appendChild(style);
}