// index.js - Setup wizard plugin registration

import { setupWizard } from './setup-wizard.js';
import { getWizardStep } from './setup-api.js';

/**
 * Setup Wizard Plugin
 * 
 * Provides a beginner-friendly setup flow for configuring:
 * - Voice features (TTS, STT, Wake Word)
 * - Audio devices
 * - LLM providers
 * 
 * Shows automatically on first launch until completed.
 */

let hasCheckedAutoShow = false;

export default {
  name: 'setup-wizard',
  title: 'Setup Wizard',
  version: '1.0.0',
  
  // Don't show in sidebar - only accessed via auto-show or settings menu
  showInSidebar: false,

  async init() {
    // Check if we should auto-show the wizard
    if (!hasCheckedAutoShow) {
      hasCheckedAutoShow = true;
      await this.checkAutoShow();
    }

    // Register global access for settings menu
    window.sapphireSetupWizard = {
      open: (force = false) => setupWizard.open(force),
      isComplete: async () => {
        const step = await getWizardStep();
        return step >= 3;
      }
    };
  },

  async checkAutoShow() {
    try {
      const step = await getWizardStep();
      
      // If wizard hasn't been completed, show it
      if (step < 3) {
        // Small delay to let the main UI render first
        setTimeout(() => {
          setupWizard.open();
        }, 500);
      }
    } catch (e) {
      console.warn('Could not check wizard state:', e);
      // Don't auto-show on error - user can access via settings
    }
  },

  // For manual trigger from UI
  openWizard(force = false) {
    setupWizard.open(force);
  },

  // Cleanup
  destroy() {
    delete window.sapphireSetupWizard;
  }
};