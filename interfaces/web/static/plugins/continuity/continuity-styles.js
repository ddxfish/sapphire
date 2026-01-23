// continuity-styles.js - CSS-in-JS for continuity plugin

export function injectStyles() {
  if (document.getElementById('continuity-styles')) return;
  
  const style = document.createElement('style');
  style.id = 'continuity-styles';
  style.textContent = `
    .continuity-modal {
      position: fixed;
      inset: 0;
      z-index: 1000;
      display: flex;
      align-items: center;
      justify-content: center;
      background: rgba(0, 0, 0, 0.6);
      backdrop-filter: blur(4px);
    }

    .continuity-panel {
      background: var(--bg-secondary);
      border: 1px solid var(--border);
      border-radius: 12px;
      width: 90%;
      max-width: 900px;
      max-height: 85vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
    }

    .continuity-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px 20px;
      border-bottom: 1px solid var(--border);
    }

    .continuity-header h2 {
      margin: 0;
      font-size: 18px;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .continuity-header .status-badge {
      font-size: 11px;
      padding: 3px 8px;
      border-radius: 10px;
      background: var(--success-bg, rgba(34, 197, 94, 0.15));
      color: var(--success, #22c55e);
    }

    .continuity-header .status-badge.stopped {
      background: var(--error-bg, rgba(239, 68, 68, 0.15));
      color: var(--error, #ef4444);
    }

    .continuity-close {
      background: none;
      border: none;
      font-size: 24px;
      color: var(--text-muted);
      cursor: pointer;
      padding: 4px 8px;
      border-radius: 4px;
      transition: all var(--transition-fast);
    }

    .continuity-close:hover {
      background: var(--bg-hover);
      color: var(--text);
    }

    .continuity-tabs {
      display: flex;
      border-bottom: 1px solid var(--border);
      padding: 0 20px;
    }

    .continuity-tab {
      padding: 12px 16px;
      background: none;
      border: none;
      color: var(--text-muted);
      cursor: pointer;
      font-size: 14px;
      border-bottom: 2px solid transparent;
      margin-bottom: -1px;
      transition: all var(--transition-fast);
    }

    .continuity-tab:hover {
      color: var(--text);
    }

    .continuity-tab.active {
      color: var(--trim);
      border-bottom-color: var(--trim);
    }

    .continuity-content {
      flex: 1;
      overflow-y: auto;
      padding: 20px;
    }

    /* Tasks List */
    .continuity-tasks {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .continuity-task-card {
      background: var(--bg-tertiary);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
      display: flex;
      align-items: center;
      gap: 16px;
      transition: all var(--transition-fast);
    }

    .continuity-task-card:hover {
      border-color: var(--trim-border);
    }

    .continuity-task-toggle {
      width: 42px;
      height: 24px;
      background: var(--bg-hover);
      border: none;
      border-radius: 12px;
      cursor: pointer;
      position: relative;
      flex-shrink: 0;
      transition: background var(--transition-fast);
    }

    .continuity-task-toggle::after {
      content: '';
      position: absolute;
      top: 3px;
      left: 3px;
      width: 18px;
      height: 18px;
      background: var(--text-muted);
      border-radius: 50%;
      transition: all var(--transition-fast);
    }

    .continuity-task-toggle.enabled {
      background: var(--trim);
    }

    .continuity-task-toggle.enabled::after {
      left: 21px;
      background: white;
    }

    .continuity-task-info {
      flex: 1;
      min-width: 0;
    }

    .continuity-task-name {
      font-weight: 600;
      color: var(--text);
      margin-bottom: 4px;
    }

    .continuity-task-schedule {
      font-size: 12px;
      color: var(--text-muted);
      font-family: monospace;
    }

    .continuity-task-meta {
      font-size: 11px;
      color: var(--text-muted);
      margin-top: 4px;
    }

    .continuity-task-actions {
      display: flex;
      gap: 8px;
    }

    .continuity-task-actions button {
      padding: 6px 10px;
      border: 1px solid var(--border);
      border-radius: 4px;
      background: var(--bg-secondary);
      color: var(--text);
      cursor: pointer;
      font-size: 12px;
      transition: all var(--transition-fast);
    }

    .continuity-task-actions button:hover {
      background: var(--bg-hover);
      border-color: var(--trim-border);
    }

    .continuity-task-actions button.run {
      background: var(--trim-light);
      border-color: var(--trim-border);
    }

    .continuity-task-actions button.run:hover {
      background: var(--trim);
      color: white;
    }

    .continuity-task-actions button.delete:hover {
      background: var(--error-bg, rgba(239, 68, 68, 0.15));
      border-color: var(--error, #ef4444);
      color: var(--error, #ef4444);
    }

    /* Add Task Button */
    .continuity-add-btn {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      padding: 12px;
      border: 2px dashed var(--border);
      border-radius: 8px;
      background: none;
      color: var(--text-muted);
      cursor: pointer;
      font-size: 14px;
      transition: all var(--transition-fast);
    }

    .continuity-add-btn:hover {
      border-color: var(--trim);
      color: var(--trim);
      background: var(--trim-light);
    }

    /* Timeline */
    .continuity-timeline {
      position: relative;
      padding-left: 24px;
    }

    .continuity-timeline::before {
      content: '';
      position: absolute;
      left: 8px;
      top: 0;
      bottom: 0;
      width: 2px;
      background: var(--border);
    }

    .continuity-timeline-item {
      position: relative;
      padding: 12px 0;
      padding-left: 24px;
    }

    .continuity-timeline-item::before {
      content: '';
      position: absolute;
      left: -20px;
      top: 18px;
      width: 10px;
      height: 10px;
      background: var(--trim);
      border-radius: 50%;
      border: 2px solid var(--bg-secondary);
    }

    .continuity-timeline-time {
      font-size: 12px;
      color: var(--text-muted);
      font-family: monospace;
    }

    .continuity-timeline-task {
      font-weight: 500;
      color: var(--text);
      margin-top: 4px;
    }

    .continuity-timeline-chance {
      font-size: 11px;
      color: var(--text-muted);
    }

    /* Activity Log */
    .continuity-activity {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .continuity-activity-item {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 10px 12px;
      background: var(--bg-tertiary);
      border-radius: 6px;
      font-size: 13px;
    }

    .continuity-activity-status {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      flex-shrink: 0;
    }

    .continuity-activity-status.complete { background: var(--success, #22c55e); }
    .continuity-activity-status.started { background: var(--trim); }
    .continuity-activity-status.skipped { background: var(--warning, #f59e0b); }
    .continuity-activity-status.error { background: var(--error, #ef4444); }

    .continuity-activity-time {
      font-size: 11px;
      color: var(--text-muted);
      font-family: monospace;
      white-space: nowrap;
    }

    .continuity-activity-name {
      flex: 1;
      color: var(--text);
    }

    .continuity-activity-detail {
      font-size: 11px;
      color: var(--text-muted);
    }

    /* Empty States */
    .continuity-empty {
      text-align: center;
      padding: 40px;
      color: var(--text-muted);
    }

    .continuity-empty-icon {
      font-size: 48px;
      margin-bottom: 16px;
      opacity: 0.5;
    }

    /* Editor Modal */
    .continuity-editor-overlay {
      position: fixed;
      inset: 0;
      z-index: 1001;
      display: flex;
      align-items: center;
      justify-content: center;
      background: rgba(0, 0, 0, 0.5);
    }

    .continuity-editor {
      background: var(--bg-secondary);
      border: 1px solid var(--border);
      border-radius: 12px;
      width: 90%;
      max-width: 600px;
      max-height: 85vh;
      overflow-y: auto;
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
    }

    .continuity-editor-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px 20px;
      border-bottom: 1px solid var(--border);
      position: sticky;
      top: 0;
      background: var(--bg-secondary);
    }

    .continuity-editor-header h3 {
      margin: 0;
      font-size: 16px;
      color: var(--text);
    }

    .continuity-editor-body {
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }

    .continuity-field {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .continuity-field label {
      font-size: 13px;
      color: var(--text);
      font-weight: 500;
    }

    .continuity-field input,
    .continuity-field select,
    .continuity-field textarea {
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg-tertiary);
      color: var(--text);
      font-size: 14px;
      transition: border-color var(--transition-fast);
    }

    .continuity-field input:focus,
    .continuity-field select:focus,
    .continuity-field textarea:focus {
      outline: none;
      border-color: var(--trim);
    }

    .continuity-field textarea {
      resize: vertical;
      min-height: 80px;
      font-family: inherit;
    }

    .continuity-field-hint {
      font-size: 11px;
      color: var(--text-muted);
    }

    .continuity-field-row {
      display: flex;
      gap: 16px;
    }

    .continuity-field-row .continuity-field {
      flex: 1;
    }

    .continuity-editor-footer {
      display: flex;
      justify-content: flex-end;
      gap: 12px;
      padding: 16px 20px;
      border-top: 1px solid var(--border);
      position: sticky;
      bottom: 0;
      background: var(--bg-secondary);
    }

    .continuity-editor-footer button {
      padding: 10px 20px;
      border-radius: 6px;
      font-size: 14px;
      cursor: pointer;
      transition: all var(--transition-fast);
    }

    .continuity-editor-footer .cancel-btn {
      background: var(--bg-tertiary);
      border: 1px solid var(--border);
      color: var(--text);
    }

    .continuity-editor-footer .cancel-btn:hover {
      background: var(--bg-hover);
    }

    .continuity-editor-footer .save-btn {
      background: var(--trim);
      border: none;
      color: white;
    }

    .continuity-editor-footer .save-btn:hover {
      filter: brightness(1.1);
    }

    /* Checkbox field */
    .continuity-checkbox {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .continuity-checkbox input[type="checkbox"] {
      width: 18px;
      height: 18px;
      accent-color: var(--trim);
    }

    .continuity-checkbox label {
      font-weight: normal;
    }
  `;
  document.head.appendChild(style);
}