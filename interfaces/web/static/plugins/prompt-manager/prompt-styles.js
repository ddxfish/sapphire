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
    
    /* Edit Definitions Modal */
    .edit-def-list {
      display: flex;
      flex-direction: column;
      gap: 12px;
      max-height: 450px;
      overflow-y: auto;
      padding-right: 8px;
    }
    
    .edit-def-item {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    
    .edit-def-item label {
      color: var(--text-light);
      font-size: var(--font-sm);
      font-weight: 600;
      font-family: var(--font-mono);
    }
    
    .edit-def-item textarea {
      width: 100%;
      padding: 8px;
      background: var(--input-bg);
      border: 1px solid var(--input-border);
      border-radius: var(--radius-md);
      color: var(--text);
      font-size: var(--font-sm);
      font-family: var(--font-mono);
      resize: vertical;
      min-height: 60px;
      transition: border-color var(--transition-normal), box-shadow var(--transition-normal);
    }
    
    .edit-def-item textarea:focus {
      outline: none;
      border-color: var(--input-focus-border);
      box-shadow: 0 0 0 2px var(--focus-ring);
    }

    /* Combined Edit Modal (extras/emotions) */
    .combined-edit-list {
      display: flex;
      flex-direction: column;
      gap: 10px;
      max-height: 500px;
      overflow-y: auto;
      padding-right: 4px;
    }

    .combined-edit-row {
      display: flex;
      align-items: flex-start;
      gap: 10px;
      padding: 10px;
      background: var(--bg-secondary);
      border-radius: var(--radius-md);
    }

    .combined-edit-check {
      width: 18px;
      height: 18px;
      cursor: pointer;
      flex-shrink: 0;
      margin-top: 2px;
    }

    .combined-edit-content {
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }

    .combined-edit-label {
      color: var(--text-light);
      font-size: var(--font-sm);
      font-weight: 600;
      font-family: var(--font-mono);
    }

    .combined-edit-textarea {
      width: 100%;
      padding: 8px;
      background: var(--input-bg);
      border: 1px solid var(--input-border);
      border-radius: var(--radius-sm);
      color: var(--text);
      font-size: var(--font-sm);
      font-family: var(--font-mono);
      resize: vertical;
      min-height: 80px;
    }

    .combined-edit-textarea:focus {
      outline: none;
      border-color: var(--input-focus-border);
      box-shadow: 0 0 0 2px var(--focus-ring);
    }

    .combined-edit-delete {
      padding: 4px 8px;
      background: transparent;
      border: 1px solid var(--danger, #dc3545);
      border-radius: var(--radius-sm);
      color: var(--danger, #dc3545);
      cursor: pointer;
      font-size: var(--font-sm);
      font-weight: bold;
      flex-shrink: 0;
      transition: all var(--transition-fast);
    }

    .combined-edit-delete:hover {
      background: var(--danger, #dc3545);
      color: white;
    }
  `;
  document.head.appendChild(style);
}