// setup-api.js - Setup wizard API calls

import { fetchWithTimeout } from '../../shared/fetch.js';

/**
 * Check which optional packages are installed.
 * Returns status for TTS, STT, and Wakeword.
 */
export async function checkPackages() {
  const data = await fetchWithTimeout('/api/setup/check-packages');
  return data.packages || {};
}

/**
 * Get current wizard step (0-3).
 */
export async function getWizardStep() {
  const data = await fetchWithTimeout('/api/setup/wizard-step');
  return data.step || 0;
}

/**
 * Set wizard step.
 * @param {number} step - 0=not started, 1=voice done, 2=audio done, 3=complete
 */
export async function setWizardStep(step) {
  const res = await fetch('/api/setup/wizard-step', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ step })
  });
  if (!res.ok) throw new Error('Failed to save wizard progress');
  return await res.json();
}

/**
 * Update a setting value.
 */
export async function updateSetting(key, value) {
  const res = await fetch('/api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ [key]: value })
  });
  if (!res.ok) throw new Error(`Failed to update ${key}`);
  return await res.json();
}

/**
 * Get all current settings.
 */
export async function getSettings() {
  const data = await fetchWithTimeout('/api/settings');
  return data.settings || {};
}