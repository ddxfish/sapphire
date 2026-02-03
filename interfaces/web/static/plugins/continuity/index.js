// index.js - Continuity plugin entry point
// Supports both eager and lazy loading modes

import ContinuityModal from './continuity-modal.js';
import { injectStyles } from './continuity-styles.js';

const ContinuityPlugin = {
  name: 'continuity',
  modal: null,

  helpText: `
    <h4>Continuity - Scheduled AI Tasks</h4>
    <p>Wake Sapphire on a schedule to run autonomous tasks.</p>

    <h5>Task Options</h5>
    <ul>
      <li><strong>Schedule:</strong> Cron expression (minute hour day month weekday)</li>
      <li><strong>Chance:</strong> Probability (1-100%) task will actually run</li>
      <li><strong>Cooldown:</strong> Minimum minutes between runs</li>
      <li><strong>Iterations:</strong> How many back-and-forth messages per run</li>
    </ul>

    <h5>Chat Modes</h5>
    <ul>
      <li><strong>Dated:</strong> Creates a new chat each run (default)</li>
      <li><strong>Single:</strong> One persistent chat per task</li>
      <li><strong>Fixed:</strong> Use a specific existing chat</li>
    </ul>

    <h5>Cron Examples</h5>
    <pre>
0 9 * * *      = 9:00 AM daily
0 9 * * 1-5    = 9:00 AM weekdays
*/30 * * * *   = Every 30 minutes
0 */4 * * *    = Every 4 hours
0 8,12,18 * * * = 8 AM, noon, 6 PM
    </pre>
  `,

  async init(container) {
    injectStyles();

    // Check if loaded lazily (menu button already exists)
    const existingBtn = document.querySelector('[data-plugin="continuity"][data-lazy="true"]');
    if (existingBtn) {
      console.log('✔ Continuity plugin initialized (lazy)');
      return;
    }

    // Eager mode - register menu item ourselves
    if (window.pluginLoader) {
      const menuBtn = window.pluginLoader.registerIcon(this);
      if (menuBtn) {
        menuBtn.textContent = '⏰ Continuity';
        menuBtn.addEventListener('click', () => this.openModal());
      }
    }

    console.log('✔ Continuity plugin initialized');
  },

  // Called by lazy loader when menu item is clicked
  onTrigger() {
    this.openModal();
  },

  openModal() {
    if (this.modal) return;

    this.modal = new ContinuityModal();
    this.modal.onCloseCallback = () => {
      this.modal = null;
    };
    this.modal.open();
  },

  destroy() {
    if (this.modal) {
      this.modal.close();
      this.modal = null;
    }
  }
};

export default ContinuityPlugin;