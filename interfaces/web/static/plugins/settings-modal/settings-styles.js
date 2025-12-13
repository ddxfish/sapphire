// styles.js - CSS injection for Settings Modal plugin
// Uses shared.css for buttons, defines plugin-specific styles

export function injectStyles() {
  if (document.getElementById('settings-modal-styles')) return;
  
  const style = document.createElement('style');
  style.id = 'settings-modal-styles';
  style.textContent = `
    /* Settings Modal Overlay */
    .settings-modal-overlay {
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

    .settings-modal-overlay.active {
      opacity: 1;
    }

    /* Settings Modal Container */
    .settings-modal {
      background: var(--bg-secondary);
      border: 1px solid var(--border-light);
      border-radius: var(--radius-lg);
      width: 90%;
      max-width: none;
      height: 90vh;
      max-height: 90vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 8px 32px var(--shadow-heavy);
      transform: scale(0.9);
      transition: transform var(--transition-slow);
    }

    .settings-modal-overlay.active .settings-modal {
      transform: scale(1);
    }

    /* Header */
    .settings-modal-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px 20px;
      border-bottom: 1px solid var(--border);
    }

    .settings-modal-header h2 {
      margin: 0;
      font-size: var(--font-2xl);
      color: var(--text-bright);
      font-weight: 600;
    }

    /* Tabs */
    .settings-modal-tabs {
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

    .tab-btn {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 8px 16px;
      background: var(--bg-tertiary);
      border: 1px solid var(--border);
      border-bottom: none;
      border-radius: var(--radius-md) var(--radius-md) 0 0;
      color: var(--text-tertiary);
      cursor: pointer;
      font-size: var(--font-base);
      white-space: nowrap;
      transition: all var(--transition-normal);
    }

    .tab-btn:hover {
      background: var(--bg-hover);
      color: var(--text-bright);
    }

    .tab-btn:focus {
      outline: none;
      box-shadow: 0 0 0 2px var(--focus-ring);
    }

    .tab-btn.active {
      background: var(--bg-secondary);
      color: var(--text-bright);
      border-color: var(--border-light);
      border-bottom: 1px solid var(--bg-secondary);
      margin-bottom: -1px;
    }

    .tab-icon { font-size: var(--font-lg); }
    .tab-label { font-weight: 500; }

    /* Content Area */
    .settings-modal-content {
      flex: 1;
      overflow-y: auto;
      padding: 20px;
    }

    .tab-content {
      display: none;
    }

    .tab-content.active {
      display: block;
      animation: settingsFadeIn var(--transition-normal);
    }

    @keyframes settingsFadeIn {
      from { opacity: 0; transform: translateY(-10px); }
      to { opacity: 1; transform: translateY(0); }
    }

    .tab-header {
      margin-bottom: 20px;
      padding-bottom: 12px;
      border-bottom: 1px solid var(--border);
    }

    .tab-header h3 {
      margin: 0 0 8px 0;
      font-size: var(--font-xl);
      color: var(--text-bright);
    }

    .tab-header p {
      margin: 0;
      font-size: var(--font-base);
      color: var(--text-muted);
    }

    /* Settings List */
    .settings-list {
      display: flex;
      flex-direction: column;
      gap: 16px;
    }

    .setting-row {
      display: grid;
      grid-template-columns: 1fr 2fr auto;
      gap: 16px;
      align-items: center;
      padding: 12px;
      background: var(--bg-tertiary);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      transition: all var(--transition-normal);
    }

    .setting-row:hover {
      background: var(--bg-hover);
      border-color: var(--border-light);
    }

    .setting-row.overridden {
      border-left: 3px solid var(--accent-blue);
    }

    .setting-row.modified {
      border-left: 3px solid var(--accent-orange);
    }

    /* Setting Label */
    .setting-label {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }

    .label-with-help {
      display: flex;
      align-items: center;
      gap: 6px;
    }

    .setting-label label {
      font-size: var(--font-base);
      font-weight: 500;
      color: var(--text-light);
      cursor: pointer;
    }

    .help-icon {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 16px;
      height: 16px;
      background: var(--accent-blue-light);
      border: 1px solid var(--accent-blue-border);
      border-radius: 50%;
      color: var(--accent-blue);
      font-size: var(--font-sm);
      font-weight: bold;
      cursor: help;
      transition: all var(--transition-normal);
      flex-shrink: 0;
    }

    .help-icon:hover {
      background: var(--accent-blue-light);
      border-color: var(--accent-blue);
      transform: scale(1.1);
    }

    .help-text-short {
      font-size: var(--font-sm);
      color: var(--text-muted);
      line-height: 1.4;
      font-style: italic;
    }

    .override-badge {
      display: inline-block;
      padding: 2px 8px;
      background: var(--accent-blue);
      color: var(--text-bright);
      font-size: var(--font-xs);
      font-weight: 600;
      border-radius: var(--radius-sm);
      text-transform: uppercase;
    }

    /* Setting Input */
    .setting-input {
      display: flex;
      align-items: center;
    }

    .setting-input input[type="text"],
    .setting-input input[type="number"],
    .setting-input textarea {
      width: 100%;
      padding: 8px 12px;
      background: var(--input-bg);
      border: 1px solid var(--input-border);
      border-radius: var(--radius-sm);
      color: var(--text-bright);
      font-size: var(--font-base);
      font-family: var(--font-mono);
      transition: border-color var(--transition-normal), background-color var(--transition-normal), box-shadow var(--transition-normal);
    }

    .setting-input input:focus,
    .setting-input textarea:focus {
      outline: none;
      border-color: var(--input-focus-border);
      background: var(--input-focus-bg);
      box-shadow: 0 0 0 3px var(--focus-ring);
    }

    .setting-input .json-input {
      font-size: var(--font-sm);
      resize: vertical;
      min-height: 80px;
    }

    /* Checkbox */
    .setting-input .checkbox-container {
      display: flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
    }

    .setting-input .checkbox-container input[type="checkbox"] {
      width: 18px;
      height: 18px;
      cursor: pointer;
    }

    .setting-input .checkbox-container input[type="checkbox"]:focus {
      outline: none;
      box-shadow: 0 0 0 2px var(--focus-ring);
    }

    .setting-input .checkbox-label {
      font-size: var(--font-base);
      color: var(--text-tertiary);
      user-select: none;
    }

    /* Setting Actions */
    .setting-actions {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    /* =============================================================================
       IDENTITY TAB - Avatar Upload
       ============================================================================= */
    
    .avatar-upload-section {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 32px;
      margin-bottom: 24px;
      padding: 20px;
      background: var(--bg-tertiary);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
    }

    .avatar-column {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 12px;
    }

    .avatar-column h4 {
      margin: 0;
      font-size: var(--font-md);
      color: var(--text-light);
      font-weight: 600;
    }

    .avatar-preview {
      width: 100px;
      height: 100px;
      border-radius: 50%;
      object-fit: cover;
      border: 3px solid var(--border-light);
      background: var(--bg-secondary);
      transition: border-color var(--transition-normal);
    }

    .avatar-preview:hover {
      border-color: var(--accent-blue);
    }

    .avatar-hint {
      font-size: var(--font-xs);
      color: var(--text-muted);
      text-align: center;
    }

    @media (max-width: 768px) {
      .avatar-upload-section {
        grid-template-columns: 1fr;
        gap: 24px;
      }
    }

    /* =============================================================================
       APPEARANCE TAB - Theme Preview
       ============================================================================= */
    
    .appearance-container {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 24px;
      align-items: start;
    }

    .appearance-controls {
      display: flex;
      flex-direction: column;
      gap: 16px;
    }

    .appearance-controls .setting-row {
      grid-template-columns: 1fr 1fr;
    }

    /* Theme Preview Panel */
    .theme-preview {
      background: var(--bg);
      border: 1px solid var(--border-light);
      border-radius: var(--radius-lg);
      overflow: hidden;
      box-shadow: 0 4px 12px var(--shadow-medium);
    }

    .preview-titlebar {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 8px 12px;
      background: var(--bg-secondary);
      border-bottom: 1px solid var(--border);
    }

    .preview-dots {
      display: flex;
      gap: 6px;
    }

    .preview-dots .dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
    }

    .preview-dots .dot.red { background: #ff5f56; }
    .preview-dots .dot.yellow { background: #ffbd2e; }
    .preview-dots .dot.green { background: #27ca40; }

    .preview-title {
      font-size: var(--font-sm);
      color: var(--text-muted);
      font-weight: 500;
    }

    .preview-content {
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .preview-text {
      margin: 0;
      font-size: var(--font-sm);
      color: var(--text);
      line-height: 1.5;
    }

    .preview-accordion {
      border: 1px solid var(--border-light);
      border-radius: var(--radius-sm);
      overflow: hidden;
    }

    .preview-accordion-header {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 8px 10px;
      background: var(--bg-tertiary);
      font-size: var(--font-sm);
      color: var(--text-secondary);
    }

    .preview-toggle {
      font-size: var(--font-xs);
      color: var(--text-dim);
    }

    .preview-accordion-body {
      padding: 8px 10px;
      font-size: var(--font-xs);
      color: var(--text-muted);
      background: var(--bg-secondary);
    }

    .preview-buttons {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }

    .preview-btn {
      padding: 6px 12px;
      border-radius: var(--radius-sm);
      font-size: var(--font-xs);
      font-weight: 500;
    }

    .preview-btn.primary {
      background: var(--primary);
      color: var(--text-bright);
    }

    .preview-btn.secondary {
      background: var(--bg-tertiary);
      color: var(--text);
      border: 1px solid var(--border-light);
    }

    .preview-btn.danger {
      background: var(--error-light);
      color: var(--error-text);
      border: 1px solid var(--error-border);
    }

    .preview-messages {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .preview-bubble {
      padding: 8px 12px;
      border-radius: var(--radius-lg);
      font-size: var(--font-xs);
      max-width: 80%;
    }

    .preview-bubble.user {
      background: var(--user-bg);
      color: var(--text);
      align-self: flex-end;
    }

    .preview-bubble.assistant {
      background: var(--assistant-bg);
      color: var(--text);
      align-self: flex-start;
    }

    .preview-input {
      display: flex;
      gap: 8px;
      align-items: center;
    }

    .preview-input-box {
      flex: 1;
      padding: 8px 12px;
      background: var(--input-bg);
      border: 1px solid var(--input-border);
      border-radius: var(--radius-md);
      font-size: var(--font-xs);
      color: var(--placeholder-color);
    }

    .preview-send {
      padding: 8px 14px;
      background: var(--primary);
      color: var(--text-bright);
      border-radius: var(--radius-md);
      font-size: var(--font-xs);
      font-weight: 500;
    }

    /* System Tab - Danger Zone */
    .system-tab-content {
      padding: 20px 0;
    }

    .system-tab-content .settings-list {
      margin-bottom: 32px;
    }

    .system-danger-zone {
      background: var(--error-subtle);
      border: 2px solid var(--error-border);
      border-radius: var(--radius-lg);
      padding: 24px;
      text-align: center;
    }

    .system-danger-zone h4 {
      margin: 0 0 12px 0;
      font-size: var(--font-xl);
      color: var(--error-text);
      font-weight: 600;
    }

    .system-danger-zone p {
      margin: 0 0 20px 0;
      font-size: var(--font-md);
      color: var(--text-tertiary);
    }

    .system-danger-zone .warning-text {
      margin: 16px 0 0 0;
      font-size: var(--font-sm);
      color: var(--text-muted);
      font-style: italic;
    }

    /* Tier Badges */
    .tier-badge {
      padding: 4px 10px;
      border-radius: var(--radius-sm);
      font-size: var(--font-sm);
      font-weight: 600;
      white-space: nowrap;
    }

    .tier-green {
      background: var(--success-light);
      color: var(--success-text);
      border: 1px solid var(--success-border);
    }

    .tier-yellow {
      background: var(--warning-light);
      color: var(--warning-text);
      border: 1px solid var(--warning-border);
    }

    .tier-red {
      background: var(--error-light);
      color: var(--error-text);
      border: 1px solid var(--error-border);
    }

    /* Icon buttons in settings */
    .settings-modal .btn-icon {
      background: var(--bg-hover);
    }

    .settings-modal .btn-icon:hover {
      background: var(--border-hover);
      border-color: var(--text-dim);
    }

    /* Footer */
    .settings-modal-footer {
      display: flex;
      gap: 12px;
      justify-content: flex-end;
      padding: 16px 20px;
      border-top: 1px solid var(--border);
      background: var(--bg);
    }

    /* Color overrides for settings modal buttons */
    .settings-modal-footer .btn-primary {
      background: var(--accent-blue);
      border-color: var(--accent-blue);
    }

    .settings-modal-footer .btn-primary:hover {
      background: var(--accent-blue-hover);
    }

    .settings-modal-footer .btn-secondary {
      background: var(--bg-hover);
      border-color: var(--border-light);
    }

    .settings-modal-footer .btn-secondary:hover {
      background: var(--border-hover);
      border-color: var(--text-dim);
    }

    /* Scrollbar Styling */
    .settings-modal-content::-webkit-scrollbar,
    .settings-modal-tabs::-webkit-scrollbar {
      width: 8px;
      height: 8px;
    }

    .settings-modal-content::-webkit-scrollbar-track,
    .settings-modal-tabs::-webkit-scrollbar-track {
      background: var(--scrollbar-track);
    }

    .settings-modal-content::-webkit-scrollbar-thumb,
    .settings-modal-tabs::-webkit-scrollbar-thumb {
      background: var(--scrollbar-thumb);
      border-radius: var(--radius-sm);
    }

    .settings-modal-content::-webkit-scrollbar-thumb:hover,
    .settings-modal-tabs::-webkit-scrollbar-thumb:hover {
      background: var(--scrollbar-thumb-hover);
    }

    /* Responsive */
    @media (max-width: 768px) {
      .settings-modal {
        width: 95%;
        max-height: 90vh;
      }

      .setting-row {
        grid-template-columns: 1fr;
        gap: 12px;
      }

      .settings-modal-tabs {
        padding: 8px 8px 0 8px;
      }

      .tab-btn {
        padding: 6px 12px;
        font-size: var(--font-sm);
      }

      .tab-label {
        display: none;
      }

      .tab-icon {
        font-size: var(--font-xl);
      }

      .appearance-container {
        grid-template-columns: 1fr;
      }
    }

    /* Help Popup */
    .help-popup-overlay {
      position: fixed;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      background: var(--overlay-medium);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 10001;
      opacity: 0;
      transition: opacity var(--transition-slow);
    }

    .help-popup-overlay.active {
      opacity: 1;
    }

    .help-popup {
      background: var(--bg-tertiary);
      border: 1px solid var(--accent-blue);
      border-radius: var(--radius-lg);
      width: 90%;
      max-width: 500px;
      max-height: 70vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 8px 32px var(--accent-blue-light);
      transform: scale(0.9);
      transition: transform var(--transition-slow);
    }

    .help-popup-overlay.active .help-popup {
      transform: scale(1);
    }

    .help-popup-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px 20px;
      border-bottom: 1px solid var(--border-light);
      background: var(--accent-blue-light);
    }

    .help-popup-header h3 {
      margin: 0;
      font-size: var(--font-lg);
      color: var(--accent-blue);
      font-weight: 600;
    }

    .help-popup-close {
      background: none;
      border: none;
      color: var(--text-muted);
      font-size: var(--font-2xl);
      cursor: pointer;
      padding: 0;
      width: 28px;
      height: 28px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: var(--radius-sm);
      transition: all var(--transition-normal);
    }

    .help-popup-close:hover {
      background: var(--bg-hover);
      color: var(--text-bright);
    }

    .help-popup-close:focus {
      outline: none;
      box-shadow: 0 0 0 2px var(--focus-ring);
    }

    .help-popup-content {
      padding: 20px;
      overflow-y: auto;
      flex: 1;
    }

    .help-long {
      margin: 0 0 16px 0;
      font-size: var(--font-md);
      color: var(--text-light);
      line-height: 1.6;
    }

    .help-short-label {
      margin: 0;
      padding: 12px;
      background: var(--accent-blue-light);
      border-left: 3px solid var(--accent-blue);
      border-radius: var(--radius-sm);
      font-size: var(--font-base);
      color: var(--text-tertiary);
      line-height: 1.5;
    }

    .help-short-label strong {
      color: var(--accent-blue);
    }
  `;
  document.head.appendChild(style);
}