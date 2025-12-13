// backup-styles.js - CSS injection for Backup modal
// Uses shared.css variables - no custom colors

export function injectStyles() {
  if (document.getElementById('backup-modal-styles')) return;

  const style = document.createElement('style');
  style.id = 'backup-modal-styles';
  style.textContent = `
    /* Backup Modal Overlay */
    .backup-modal-overlay {
      position: fixed;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      background: var(--overlay-bg);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 10000;
      opacity: 0;
      transition: opacity var(--transition-slow);
    }

    .backup-modal-overlay.active {
      opacity: 1;
    }

    /* Modal Container */
    .backup-modal {
      background: var(--bg-secondary);
      border: 1px solid var(--border-light);
      border-radius: var(--radius-lg);
      width: 90%;
      max-width: 600px;
      max-height: 85vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 8px 32px var(--shadow-heavy);
      transform: scale(0.9);
      transition: transform var(--transition-slow);
    }

    .backup-modal-overlay.active .backup-modal {
      transform: scale(1);
    }

    /* Header */
    .backup-modal-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px 20px;
      border-bottom: 1px solid var(--border);
    }

    .backup-modal-header h2 {
      margin: 0;
      font-size: var(--font-xl);
      color: var(--text-bright);
      font-weight: 600;
    }

    /* Content */
    .backup-modal-content {
      flex: 1;
      overflow-y: auto;
      padding: 16px 20px;
    }

    /* Stats bar */
    .backup-stats {
      display: flex;
      gap: 20px;
      padding: 10px 14px;
      background: var(--bg-tertiary);
      border-radius: var(--radius-md);
      margin-bottom: 16px;
      font-size: var(--font-md);
      color: var(--text-secondary);
    }

    /* Settings section */
    .backup-settings-section {
      margin-bottom: 16px;
    }

    .backup-settings-section h3 {
      margin: 0 0 10px 0;
      font-size: var(--font-md);
      color: var(--text-light);
      font-weight: 600;
    }

    .backup-settings-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
    }

    .backup-settings-grid label {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }

    .backup-settings-grid label span {
      font-size: var(--font-sm);
      color: var(--text-muted);
    }

    .backup-settings-grid input {
      width: 100%;
      padding: 8px;
      background: var(--input-bg);
      border: 1px solid var(--input-border);
      border-radius: var(--radius-sm);
      color: var(--text);
      font-size: var(--font-md);
      text-align: center;
    }

    .backup-settings-grid input:focus {
      outline: none;
      border-color: var(--input-focus-border);
      box-shadow: 0 0 0 3px var(--focus-ring);
    }

    /* Action section */
    .backup-action-section {
      margin-bottom: 16px;
      text-align: center;
    }

    .backup-action-section .btn {
      min-width: 150px;
    }

    /* Backup lists */
    .backup-lists {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .backup-section {
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      overflow: hidden;
    }

    .backup-section-header {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 10px 12px;
      background: var(--bg-tertiary);
      cursor: pointer;
      transition: background var(--transition-fast);
    }

    .backup-section-header:hover {
      background: var(--bg-hover);
    }

    .backup-section-title {
      flex: 1;
      font-size: var(--font-md);
      color: var(--text);
      font-weight: 500;
    }

    .backup-section-count {
      font-size: var(--font-sm);
      color: var(--text-muted);
      padding: 2px 8px;
      background: var(--bg);
      border-radius: var(--radius-sm);
    }

    .backup-section-content {
      max-height: 200px;
      overflow-y: auto;
      transition: max-height var(--transition-slow), padding var(--transition-slow);
    }

    .backup-section-content.collapsed {
      max-height: 0;
      overflow: hidden;
    }

    .backup-empty {
      padding: 12px;
      text-align: center;
      color: var(--text-muted);
      font-size: var(--font-sm);
    }

    .backup-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 12px;
      border-top: 1px solid var(--border);
    }

    .backup-item:hover {
      background: var(--bg-hover);
    }

    .backup-item-info {
      display: flex;
      gap: 12px;
      font-size: var(--font-sm);
    }

    .backup-item-date {
      color: var(--text);
    }

    .backup-item-size {
      color: var(--text-muted);
    }

    .backup-item-actions {
      display: flex;
      gap: 6px;
    }

    /* Footer */
    .backup-modal-footer {
      display: flex;
      justify-content: flex-end;
      gap: 10px;
      padding: 16px 20px;
      border-top: 1px solid var(--border);
    }

    /* Responsive */
    @media (max-width: 500px) {
      .backup-settings-grid {
        grid-template-columns: repeat(2, 1fr);
      }
    }
  `;

  document.head.appendChild(style);
}