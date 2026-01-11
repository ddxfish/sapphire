// setup-wizard.js - Setup wizard modal orchestrator

import { injectSetupStyles } from './setup-styles.js';
import { getSettings, getWizardStep, setWizardStep } from './setup-api.js';
import voiceTab from './tabs/voice.js';
import audioTab from './tabs/audio.js';
import llmTab from './tabs/llm.js';
import identityTab from './tabs/identity.js';

const TABS = [voiceTab, audioTab, llmTab, identityTab];
const STEP_NAMES = ['Voice', 'Audio', 'AI Brain', 'Identity'];

class SetupWizard {
  constructor() {
    this.modal = null;
    this.settings = {};
    this.currentStep = 0;
    this.completedStep = 0;
  }

  async open(forceShow = false) {
    injectSetupStyles();

    // Load current settings and wizard state
    try {
      this.settings = await getSettings();
      this.completedStep = await getWizardStep();
    } catch (e) {
      console.warn('Failed to load wizard state:', e);
      this.settings = {};
      this.completedStep = 0;
    }

    // If wizard is complete and not forced, don't show
    if (this.completedStep >= 4 && !forceShow) {
      console.log('Setup wizard already completed');
      return;
    }

    // Start at first incomplete step
    this.currentStep = Math.min(this.completedStep, 3);

    this.render();
    this.attachEventListeners();
  }

  render() {
    this.modal = document.createElement('div');
    this.modal.className = 'setup-wizard-overlay';
    this.modal.innerHTML = `
      <div class="setup-wizard">
        <div class="setup-wizard-header">
          <h2>💎 Welcome to Sapphire</h2>
        </div>

        <div class="setup-steps">
          ${this.renderStepIndicators()}
        </div>

        <div class="setup-wizard-content">
          ${TABS.map((tab, idx) => `
            <div class="setup-tab ${idx === this.currentStep ? 'active' : ''}" data-step="${idx}">
              <!-- Content loaded dynamically -->
            </div>
          `).join('')}
        </div>

        <div class="setup-wizard-footer">
          <button class="btn btn-secondary" id="setup-back" ${this.currentStep === 0 ? 'style="visibility:hidden"' : ''}>
            ← Back
          </button>
          <span class="footer-hint">Step ${this.currentStep + 1} of ${TABS.length}</span>
          <button class="btn ${this.currentStep === TABS.length - 1 ? 'btn-success' : 'btn-primary'}" id="setup-next">
            ${this.currentStep === TABS.length - 1 ? 'Finish Setup ✓' : 'Next →'}
          </button>
        </div>
      </div>
    `;

    document.body.appendChild(this.modal);

    // Load current tab content
    this.loadTabContent(this.currentStep);

    // Animate in
    requestAnimationFrame(() => {
      this.modal.classList.add('active');
    });
  }

  renderStepIndicators() {
    return TABS.map((tab, idx) => {
      const isCompleted = idx < this.completedStep;
      const isActive = idx === this.currentStep;
      
      return `
        <div class="setup-step ${isActive ? 'active' : ''} ${isCompleted ? 'completed' : ''}" data-step="${idx}">
          <span class="step-num">${isCompleted ? '✓' : idx + 1}</span>
          <span class="step-label">${STEP_NAMES[idx]}</span>
        </div>
      `;
    }).join('');
  }

  async loadTabContent(stepIdx) {
    const tab = TABS[stepIdx];
    const container = this.modal.querySelector(`.setup-tab[data-step="${stepIdx}"]`);
    
    if (!container) return;

    // Render tab content
    const html = await tab.render(this.settings);
    container.innerHTML = html;

    // Attach tab-specific listeners
    if (tab.attachListeners) {
      tab.attachListeners(container, this.settings, (updates) => {
        Object.assign(this.settings, updates);
      });
    }
  }

  attachEventListeners() {
    // Back button
    this.modal.querySelector('#setup-back').addEventListener('click', () => {
      this.goToStep(this.currentStep - 1);
    });

    // Next button
    this.modal.querySelector('#setup-next').addEventListener('click', () => {
      this.handleNext();
    });

    // Step indicators (allow clicking on completed steps)
    this.modal.querySelectorAll('.setup-step').forEach(step => {
      step.addEventListener('click', () => {
        const targetStep = parseInt(step.dataset.step);
        // Can only go to completed steps or current step
        if (targetStep <= this.completedStep) {
          this.goToStep(targetStep);
        }
      });
    });

    // Close on overlay click (with warning)
    this.modal.addEventListener('click', (e) => {
      if (e.target === this.modal) {
        this.confirmClose();
      }
    });

    // ESC key
    this.escHandler = (e) => {
      if (e.key === 'Escape') {
        this.confirmClose();
      }
    };
    document.addEventListener('keydown', this.escHandler);
  }

  async handleNext() {
    const tab = TABS[this.currentStep];
    
    // Validate current step
    if (tab.validate) {
      const result = tab.validate(this.settings);
      if (!result.valid) {
        this.showValidationError(result.message);
        return;
      }
    }

    // Mark current step as completed
    const newCompleted = Math.max(this.completedStep, this.currentStep + 1);
    if (newCompleted > this.completedStep) {
      this.completedStep = newCompleted;
      await setWizardStep(this.completedStep);
    }

    // Go to next step or finish
    if (this.currentStep < TABS.length - 1) {
      this.goToStep(this.currentStep + 1);
    } else {
      this.finish();
    }
  }

  goToStep(stepIdx) {
    if (stepIdx < 0 || stepIdx >= TABS.length) return;

    // Hide current tab
    const currentTab = this.modal.querySelector(`.setup-tab[data-step="${this.currentStep}"]`);
    if (currentTab) currentTab.classList.remove('active');

    // Update step
    this.currentStep = stepIdx;

    // Show new tab
    const newTab = this.modal.querySelector(`.setup-tab[data-step="${stepIdx}"]`);
    if (newTab) {
      newTab.classList.add('active');
      this.loadTabContent(stepIdx);
    }

    // Update step indicators
    this.modal.querySelectorAll('.setup-step').forEach((el, idx) => {
      el.classList.remove('active');
      if (idx === this.currentStep) el.classList.add('active');
      if (idx < this.completedStep) {
        el.classList.add('completed');
        el.querySelector('.step-num').textContent = '✓';
      }
    });

    // Update footer
    const backBtn = this.modal.querySelector('#setup-back');
    const nextBtn = this.modal.querySelector('#setup-next');
    const hint = this.modal.querySelector('.footer-hint');

    backBtn.style.visibility = stepIdx === 0 ? 'hidden' : 'visible';
    hint.textContent = `Step ${stepIdx + 1} of ${TABS.length}`;

    if (stepIdx === TABS.length - 1) {
      nextBtn.textContent = 'Finish Setup ✓';
      nextBtn.className = 'btn btn-success';
    } else {
      nextBtn.textContent = 'Next →';
      nextBtn.className = 'btn btn-primary';
    }
  }

  showValidationError(message) {
    // Simple alert for now - could be a toast
    alert(message);
  }

  confirmClose() {
    if (this.completedStep >= 4) {
      this.close();
      return;
    }

    const confirmed = confirm(
      'Setup is not complete yet.\n\n' +
      'You can always access these settings later from the Settings menu (⚙️).\n\n' +
      'Close the setup wizard?'
    );

    if (confirmed) {
      this.close();
    }
  }

  async finish() {
    // Mark as complete
    await setWizardStep(4);
    this.completedStep = 4;

    // Show success message with celebration
    const content = this.modal.querySelector('.setup-wizard-content');
    content.innerHTML = `
      <div class="success-screen">
        <div class="celebration">
          <div class="sparkle s1">✦</div>
          <div class="sparkle s2">✦</div>
          <div class="sparkle s3">✦</div>
          <div class="sparkle s4">✦</div>
          <div class="sparkle s5">✦</div>
          <div class="sparkle s6">✦</div>
          <div class="success-icon">✓</div>
        </div>
        <h3>You're all set!</h3>
        <p>Sapphire is ready to chat.</p>
      </div>
    `;

    // Hide navigation
    this.modal.querySelector('.setup-wizard-footer').innerHTML = `
      <div></div>
      <button class="btn btn-primary" id="setup-done">Start Chatting →</button>
    `;

    this.modal.querySelector('#setup-done').addEventListener('click', () => {
      this.close();
    });

    // Auto-close after 3 seconds
    setTimeout(() => this.close(), 3000);
  }

  close() {
    if (!this.modal) return; // Already closed
    
    document.removeEventListener('keydown', this.escHandler);
    
    this.modal.classList.remove('active');
    
    setTimeout(() => {
      if (this.modal) {
        this.modal.remove();
        this.modal = null;
      }
    }, 300);
  }
}

// Export singleton instance
export const setupWizard = new SetupWizard();
export default setupWizard;