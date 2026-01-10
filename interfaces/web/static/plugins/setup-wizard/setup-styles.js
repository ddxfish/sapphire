// setup-styles.js - CSS for setup wizard

export function injectSetupStyles() {
  if (document.getElementById('setup-wizard-styles')) return;

  const style = document.createElement('style');
  style.id = 'setup-wizard-styles';
  style.textContent = `
    /* ========================================
       Setup Wizard Modal Overlay
       ======================================== */
    .setup-wizard-overlay {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.85);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 10000;
      opacity: 0;
      transition: opacity 0.3s ease;
      backdrop-filter: blur(4px);
    }
    .setup-wizard-overlay.active {
      opacity: 1;
    }

    /* ========================================
       Main Modal Container
       ======================================== */
    .setup-wizard {
      background: var(--surface-primary, #1e1e2e);
      border-radius: 16px;
      width: 95%;
      max-width: 640px;
      max-height: 90vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 25px 80px rgba(0, 0, 0, 0.5);
      transform: translateY(20px);
      transition: transform 0.3s ease;
      overflow: hidden;
    }
    .setup-wizard-overlay.active .setup-wizard {
      transform: translateY(0);
    }

    /* ========================================
       Header
       ======================================== */
    .setup-wizard-header {
      padding: 24px 28px 16px;
      border-bottom: 1px solid var(--border-color, #333);
      text-align: center;
    }
    .setup-wizard-header h2 {
      margin: 0 0 8px;
      font-size: 1.75rem;
      font-weight: 600;
      color: var(--text-primary, #fff);
    }
    .setup-wizard-header .subtitle {
      margin: 0;
      font-size: 0.95rem;
      color: var(--text-secondary, #888);
      line-height: 1.5;
    }

    /* ========================================
       Step Indicators
       ======================================== */
    .setup-steps {
      display: flex;
      justify-content: center;
      gap: 8px;
      padding: 16px 28px;
      background: var(--surface-secondary, #252535);
    }
    .setup-step {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 16px;
      border-radius: 20px;
      font-size: 0.85rem;
      color: var(--text-secondary, #888);
      background: transparent;
      transition: all 0.2s ease;
    }
    .setup-step.active {
      background: var(--accent-color, #4a9eff);
      color: #fff;
    }
    .setup-step.completed {
      color: var(--accent-green, #5cb85c);
    }
    .setup-step .step-num {
      width: 24px;
      height: 24px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 600;
      font-size: 0.8rem;
      background: var(--surface-tertiary, #333);
    }
    .setup-step.active .step-num {
      background: rgba(255,255,255,0.2);
    }
    .setup-step.completed .step-num {
      background: var(--accent-green, #5cb85c);
      color: #fff;
    }

    /* ========================================
       Content Area
       ======================================== */
    .setup-wizard-content {
      flex: 1;
      overflow-y: auto;
      padding: 24px 28px;
    }
    .setup-tab {
      display: none;
    }
    .setup-tab.active {
      display: block;
      animation: fadeIn 0.3s ease;
    }
    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(10px); }
      to { opacity: 1; transform: translateY(0); }
    }

    /* Tab headers */
    .setup-tab-header {
      text-align: center;
      margin-bottom: 24px;
    }
    .setup-tab-header h3 {
      margin: 0 0 8px;
      font-size: 1.3rem;
      color: var(--text-primary, #fff);
    }
    .setup-tab-header p {
      margin: 0;
      color: var(--text-secondary, #888);
      font-size: 0.9rem;
    }

    /* ========================================
       Feature Cards (Voice tab)
       ======================================== */
    .feature-card {
      background: var(--surface-secondary, #252535);
      border: 2px solid var(--border-color, #333);
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 16px;
      transition: all 0.2s ease;
    }
    .feature-card.enabled {
      border-color: var(--accent-green, #5cb85c);
      background: rgba(92, 184, 92, 0.1);
    }
    .feature-card-header {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 12px;
    }
    .feature-icon {
      font-size: 1.8rem;
    }
    .feature-info {
      flex: 1;
    }
    .feature-info h4 {
      margin: 0 0 4px;
      font-size: 1.1rem;
      color: var(--text-primary, #fff);
    }
    .feature-info p {
      margin: 0;
      font-size: 0.85rem;
      color: var(--text-secondary, #888);
    }

    /* Toggle switch */
    .feature-toggle {
      position: relative;
      width: 52px;
      height: 28px;
    }
    .feature-toggle input {
      opacity: 0;
      width: 0;
      height: 0;
    }
    .feature-toggle .slider {
      position: absolute;
      inset: 0;
      background: var(--surface-tertiary, #333);
      border-radius: 28px;
      cursor: pointer;
      transition: 0.3s;
    }
    .feature-toggle .slider:before {
      content: '';
      position: absolute;
      width: 22px;
      height: 22px;
      left: 3px;
      bottom: 3px;
      background: #fff;
      border-radius: 50%;
      transition: 0.3s;
    }
    .feature-toggle input:checked + .slider {
      background: var(--accent-green, #5cb85c);
    }
    .feature-toggle input:checked + .slider:before {
      transform: translateX(24px);
    }

    /* Package status */
    .package-status {
      margin-top: 12px;
      padding: 12px;
      border-radius: 8px;
      font-size: 0.85rem;
    }
    .package-status.installed {
      background: rgba(92, 184, 92, 0.15);
      color: var(--accent-green, #5cb85c);
    }
    .package-status.not-installed {
      background: rgba(240, 173, 78, 0.15);
      color: var(--accent-yellow, #f0ad4e);
    }
    .package-status.checking {
      background: rgba(74, 158, 255, 0.1);
      color: var(--text-secondary, #888);
    }
    .package-status.checking .spinner {
      display: inline-block;
      animation: spin 1s linear infinite;
    }
    @keyframes spin {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }
    .pip-command {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 8px;
      padding: 8px 12px;
      background: var(--surface-tertiary, #1a1a2a);
      border-radius: 6px;
      font-family: monospace;
      font-size: 0.8rem;
    }
    .pip-command code {
      flex: 1;
      color: var(--text-primary, #fff);
    }
    .pip-command .copy-btn {
      padding: 4px 8px;
      font-size: 0.75rem;
      background: var(--accent-color, #4a9eff);
      color: #fff;
      border: none;
      border-radius: 4px;
      cursor: pointer;
    }
    .pip-command .copy-btn:hover {
      opacity: 0.9;
    }

    /* Help tip */
    .help-tip {
      display: flex;
      align-items: flex-start;
      gap: 10px;
      padding: 12px 16px;
      background: rgba(74, 158, 255, 0.1);
      border-left: 3px solid var(--accent-color, #4a9eff);
      border-radius: 0 8px 8px 0;
      margin: 16px 0;
      font-size: 0.85rem;
      color: var(--text-secondary, #aaa);
    }
    .help-tip .tip-icon {
      font-size: 1.1rem;
    }

    /* ========================================
       Audio Devices (Audio tab)
       ======================================== */
    .audio-section {
      margin-bottom: 24px;
    }
    .audio-section h4 {
      margin: 0 0 8px;
      font-size: 1rem;
      color: var(--text-primary, #fff);
    }
    .audio-section > p {
      margin: 0 0 12px;
      font-size: 0.85rem;
      color: var(--text-secondary, #888);
    }
    .device-row {
      display: flex;
      gap: 12px;
      align-items: center;
    }
    .device-select {
      flex: 1;
      padding: 10px 14px;
      background: var(--surface-secondary, #252535);
      border: 1px solid var(--border-color, #333);
      border-radius: 8px;
      color: var(--text-primary, #fff);
      font-size: 0.9rem;
    }
    .test-result {
      margin-top: 8px;
      font-size: 0.85rem;
      min-height: 20px;
    }
    .test-result.success { color: var(--accent-green, #5cb85c); }
    .test-result.warning { color: var(--accent-yellow, #f0ad4e); }
    .test-result.error { color: var(--accent-red, #d9534f); }

    .level-meter-container {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-top: 8px;
    }
    .level-meter {
      flex: 1;
      height: 8px;
      background: var(--surface-tertiary, #333);
      border-radius: 4px;
      overflow: hidden;
    }
    .level-bar {
      height: 100%;
      width: 0%;
      background: var(--accent-green, #5cb85c);
      transition: width 0.1s;
    }
    .level-value {
      font-size: 0.8rem;
      color: var(--text-secondary, #888);
      min-width: 35px;
    }

    /* ========================================
       LLM Providers (LLM tab)
       ======================================== */
    .llm-intro {
      text-align: center;
      margin-bottom: 20px;
    }
    .llm-intro p {
      color: var(--text-secondary, #888);
      font-size: 0.9rem;
      line-height: 1.5;
    }

    .provider-simple-card {
      background: var(--surface-secondary, #252535);
      border: 2px solid var(--border-color, #333);
      border-radius: 12px;
      padding: 16px 20px;
      margin-bottom: 12px;
      cursor: pointer;
      transition: all 0.2s ease;
    }
    .provider-simple-card:hover {
      border-color: var(--accent-color, #4a9eff);
    }
    .provider-simple-card.selected {
      border-color: var(--accent-green, #5cb85c);
      background: rgba(92, 184, 92, 0.1);
    }
    .provider-simple-header {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .provider-simple-header .icon {
      font-size: 1.5rem;
    }
    .provider-simple-header .info {
      flex: 1;
    }
    .provider-simple-header .info h4 {
      margin: 0 0 2px;
      font-size: 1rem;
      color: var(--text-primary, #fff);
    }
    .provider-simple-header .info p {
      margin: 0;
      font-size: 0.8rem;
      color: var(--text-secondary, #888);
    }
    .provider-simple-header .check {
      font-size: 1.2rem;
      color: var(--accent-green, #5cb85c);
      opacity: 0;
      transition: opacity 0.2s;
    }
    .provider-simple-card.selected .check {
      opacity: 1;
    }

    .provider-config {
      margin-top: 16px;
      padding-top: 16px;
      border-top: 1px solid var(--border-color, #333);
      display: none;
    }
    .provider-simple-card.selected .provider-config {
      display: block;
      animation: fadeIn 0.2s ease;
    }
    .config-field {
      margin-bottom: 12px;
    }
    .config-field label {
      display: block;
      margin-bottom: 6px;
      font-size: 0.85rem;
      color: var(--text-secondary, #888);
    }
    .config-field input,
    .config-field select {
      width: 100%;
      padding: 10px 12px;
      background: var(--surface-tertiary, #1a1a2a);
      border: 1px solid var(--border-color, #333);
      border-radius: 6px;
      color: var(--text-primary, #fff);
      font-size: 0.9rem;
    }
    .config-field input:focus,
    .config-field select:focus {
      outline: none;
      border-color: var(--accent-color, #4a9eff);
    }
    .config-field .hint {
      margin-top: 4px;
      font-size: 0.75rem;
      color: var(--text-tertiary, #666);
    }

    .test-connection-row {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-top: 16px;
    }
    .test-connection-result {
      flex: 1;
      font-size: 0.85rem;
    }

    /* ========================================
       Footer Navigation
       ======================================== */
    .setup-wizard-footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 16px 28px;
      border-top: 1px solid var(--border-color, #333);
      background: var(--surface-secondary, #252535);
    }
    .setup-wizard-footer .btn {
      padding: 10px 24px;
      border-radius: 8px;
      font-size: 0.95rem;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.2s ease;
      border: none;
    }
    .setup-wizard-footer .btn-secondary {
      background: var(--surface-tertiary, #333);
      color: var(--text-secondary, #888);
    }
    .setup-wizard-footer .btn-secondary:hover {
      background: var(--surface-primary, #444);
      color: var(--text-primary, #fff);
    }
    .setup-wizard-footer .btn-primary {
      background: var(--accent-color, #4a9eff);
      color: #fff;
    }
    .setup-wizard-footer .btn-primary:hover {
      background: #3a8eef;
    }
    .setup-wizard-footer .btn-success {
      background: var(--accent-green, #5cb85c);
      color: #fff;
    }
    .setup-wizard-footer .btn:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
    .footer-hint {
      font-size: 0.8rem;
      color: var(--text-tertiary, #666);
    }

    /* ========================================
       Responsive
       ======================================== */
    @media (max-width: 600px) {
      .setup-wizard {
        max-height: 95vh;
        border-radius: 12px;
      }
      .setup-wizard-header {
        padding: 16px 20px 12px;
      }
      .setup-wizard-content {
        padding: 16px 20px;
      }
      .setup-steps {
        padding: 12px 16px;
        gap: 4px;
      }
      .setup-step {
        padding: 6px 10px;
        font-size: 0.8rem;
      }
      .setup-step .step-label {
        display: none;
      }
    }
  `;

  document.head.appendChild(style);
}