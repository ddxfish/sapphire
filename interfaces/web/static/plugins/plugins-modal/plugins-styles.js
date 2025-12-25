// plugins-styles.js - CSS for plugins modal

export function injectStyles() {
  if (document.getElementById('plugins-modal-styles')) return;
  
  const style = document.createElement('style');
  style.id = 'plugins-modal-styles';
  style.textContent = `
    .plugins-modal-overlay {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0, 0, 0, 0.6);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 1000;
      opacity: 0;
      transition: opacity 0.2s ease;
    }
    
    .plugins-modal-overlay.active {
      opacity: 1;
    }
    
    .plugins-modal {
      background: var(--bg-secondary);
      border-radius: 12px;
      width: 90%;
      max-width: 600px;
      max-height: 80vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
      transform: scale(0.95);
      transition: transform 0.2s ease;
    }
    
    .plugins-modal-overlay.active .plugins-modal {
      transform: scale(1);
    }
    
    .plugins-modal-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px 20px;
      border-bottom: 1px solid var(--border);
    }
    
    .plugins-modal-header h2 {
      margin: 0;
      font-size: 18px;
      color: var(--text);
    }
    
    .plugins-modal-header .close-btn {
      background: none;
      border: none;
      font-size: 24px;
      color: var(--text-muted);
      cursor: pointer;
      padding: 0;
      line-height: 1;
    }
    
    .plugins-modal-header .close-btn:hover {
      color: var(--text);
    }
    
    .plugins-modal-tabs {
      display: flex;
      gap: 4px;
      padding: 12px 12px 0 12px;
      background: var(--bg);
      overflow-x: auto;
      overflow-y: hidden;
      border-bottom: 1px solid var(--border);
      min-height: 52px;
      max-height: 52px;
    }
    
    .plugins-modal-tabs .tab-btn {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 8px 16px;
      background: var(--bg-tertiary);
      border: 1px solid var(--border);
      border-bottom: none;
      border-radius: var(--radius-md, 8px) var(--radius-md, 8px) 0 0;
      color: var(--text-tertiary);
      cursor: pointer;
      font-size: var(--font-base, 14px);
      white-space: nowrap;
      transition: all var(--transition-normal, 0.15s);
    }
    
    .plugins-modal-tabs .tab-btn:hover {
      background: var(--bg-hover);
      color: var(--text-bright);
    }
    
    .plugins-modal-tabs .tab-btn:focus {
      outline: none;
      box-shadow: 0 0 0 2px var(--focus-ring);
    }
    
    .plugins-modal-tabs .tab-btn.active {
      background: var(--bg-secondary);
      color: var(--text-bright);
      border-color: var(--border-light);
      border-bottom: 1px solid var(--bg-secondary);
      margin-bottom: -1px;
    }
    
    .plugins-modal-tabs .tab-icon {
      font-size: var(--font-lg, 16px);
    }
    
    .plugins-modal-tabs .tab-label {
      font-weight: 500;
    }
    
    .plugins-modal-content {
      flex: 1;
      overflow-y: auto;
      padding: 20px;
    }
    
    .plugins-tab-content {
      display: none;
    }
    
    .plugins-tab-content.active {
      display: block;
    }
    
    .plugins-help-tip {
      background: var(--bg-tertiary);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 16px;
      font-size: 13px;
      color: var(--text-muted);
    }
    
    .plugins-help-tip::before {
      content: 'ℹ️ ';
    }
    
    /* Plugin list styles */
    .plugin-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    
    .plugin-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 12px 16px;
      background: var(--bg-tertiary);
      border-radius: 8px;
      border: 1px solid var(--border);
    }
    
    .plugin-item.locked {
      opacity: 0.7;
    }
    
    .plugin-item-info {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    
    .plugin-item-title {
      font-weight: 500;
      color: var(--text);
      font-size: 14px;
    }
    
    .plugin-item-name {
      font-size: 12px;
      color: var(--text-muted);
      font-family: monospace;
    }
    
    .plugin-item-toggle {
      position: relative;
      width: 44px;
      height: 24px;
      background: var(--bg-primary);
      border-radius: 12px;
      border: 1px solid var(--border);
      cursor: pointer;
      transition: background 0.2s ease;
    }
    
    .plugin-item-toggle.enabled {
      background: var(--accent-blue);
      border-color: var(--accent-blue);
    }
    
    .plugin-item-toggle.locked {
      cursor: not-allowed;
      opacity: 0.5;
    }
    
    .plugin-item-toggle::after {
      content: '';
      position: absolute;
      top: 2px;
      left: 2px;
      width: 18px;
      height: 18px;
      background: white;
      border-radius: 50%;
      transition: transform 0.2s ease;
    }
    
    .plugin-item-toggle.enabled::after {
      transform: translateX(20px);
    }
    
    .plugins-modal-footer {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      padding: 16px 20px;
      border-top: 1px solid var(--border);
    }
    
    .plugins-modal-footer .btn {
      padding: 8px 16px;
      border-radius: 6px;
      font-size: 14px;
      cursor: pointer;
      border: none;
    }
    
    .plugins-modal-footer .btn-secondary {
      background: var(--bg-tertiary);
      color: var(--text);
      border: 1px solid var(--border);
    }
    
    .plugins-modal-footer .btn-secondary:hover {
      background: var(--bg-hover);
    }
    
    .plugins-modal-footer .btn-primary {
      background: var(--accent-blue);
      color: white;
    }
    
    .plugins-modal-footer .btn-primary:hover {
      filter: brightness(1.1);
    }
    
    .plugins-reload-notice {
      background: var(--accent-blue-light, rgba(74, 158, 255, 0.1));
      border: 1px solid var(--accent-blue);
      border-radius: 8px;
      padding: 12px;
      margin-top: 16px;
      font-size: 13px;
      color: var(--text);
      display: none;
    }
    
    .plugins-reload-notice.visible {
      display: block;
    }
    
    .plugins-reload-notice button {
      margin-left: 8px;
      padding: 4px 12px;
      background: var(--accent-blue);
      color: white;
      border: none;
      border-radius: 4px;
      cursor: pointer;
      font-size: 12px;
    }
  `;
  
  document.head.appendChild(style);
}