// /static/shared/danger-confirm.js - Danger confirmation modal for risky integrations
// Reusable gate: single or double "type I UNDERSTAND" confirmation
// Usage: const confirmed = await showDangerConfirm({ title, warnings, ... })

import { escapeHtml, setupModalClose } from './modal.js';

/**
 * Show a danger confirmation modal that requires typing a phrase to proceed.
 * @param {Object} opts
 * @param {string} opts.title - e.g. "Enable SSH — Remote Command Execution"
 * @param {string[]} opts.warnings - Bullet points of what this enables
 * @param {string} [opts.detail] - Optional paragraph below warnings
 * @param {string} [opts.confirmPhrase='I UNDERSTAND'] - What the user must type
 * @param {string} [opts.buttonLabel='Confirm'] - Action button text
 * @param {boolean} [opts.doubleConfirm=false] - If true, shows a second stage
 * @param {string} [opts.stage2Title] - Override title for stage 2
 * @param {string[]} [opts.stage2Warnings] - Override warnings for stage 2
 * @returns {Promise<boolean>} - true if confirmed, false if cancelled
 */
export function showDangerConfirm({
    title,
    warnings = [],
    detail = '',
    confirmPhrase = 'I UNDERSTAND',
    buttonLabel = 'Confirm',
    doubleConfirm = false,
    stage2Title = '',
    stage2Warnings = [],
}) {
    return new Promise(resolve => {
        const resolved = { done: false };
        _showStage({
            title,
            warnings,
            detail,
            confirmPhrase,
            buttonLabel: doubleConfirm ? 'Continue' : buttonLabel,
            isDanger: false,
            onConfirm: () => {
                if (!doubleConfirm) {
                    resolved.done = true;
                    resolve(true);
                    return;
                }
                _showStage({
                    title: stage2Title || '\u26A0 Final Confirmation',
                    warnings: stage2Warnings.length ? stage2Warnings : warnings,
                    detail: '',
                    confirmPhrase,
                    buttonLabel,
                    isDanger: true,
                    onConfirm: () => { resolved.done = true; resolve(true); },
                    onCancel: () => { if (!resolved.done) resolve(false); },
                });
            },
            onCancel: () => { if (!resolved.done) resolve(false); },
        });
    });
}

/**
 * Show a non-blocking warning banner (no confirmation required).
 * For repeat actions after first acknowledgment.
 * @param {HTMLElement} container - Where to prepend the banner
 * @param {string} message - Warning text
 * @returns {HTMLElement} - The banner element
 */
export function showDangerBanner(container, message) {
    container.querySelector('.danger-banner')?.remove();
    const banner = document.createElement('div');
    banner.className = 'danger-banner';
    banner.style.cssText = `
        padding: 10px 14px; margin-bottom: 12px;
        background: rgba(255,152,0,0.08); border: 1px solid var(--warning-border);
        border-radius: var(--radius-sm); color: var(--warning-text);
        font-size: 0.85em; line-height: 1.5;
    `;
    banner.innerHTML = `\u26A0\uFE0F ${escapeHtml(message)}`;
    container.prepend(banner);
    return banner;
}

// -- internal --

function _showStage({ title, warnings, detail, confirmPhrase, buttonLabel, isDanger, onConfirm, onCancel }) {
    document.querySelector('.danger-confirm-overlay')?.remove();

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay danger-confirm-overlay';

    const borderStyle = isDanger ? `border: 2px solid var(--danger-red);` : `border: 1px solid var(--warning-border);`;
    const headerBg = isDanger
        ? `background: rgba(211,47,47,0.1); border-bottom: 1px solid var(--danger-red);`
        : `background: rgba(255,152,0,0.08); border-bottom: 1px solid var(--warning-border);`;
    const titleColor = isDanger ? `color: var(--danger-red);` : `color: var(--warning-text);`;
    const iconColor = isDanger ? 'color: var(--danger-red)' : 'color: var(--warning)';
    const btnBg = isDanger
        ? `background: var(--danger); border-color: var(--danger);`
        : `background: var(--warning); border-color: var(--warning); color: #000;`;

    const warningsHtml = warnings.map(w =>
        `<li style="margin:6px 0;color:var(--text-secondary);line-height:1.5">${escapeHtml(w)}</li>`
    ).join('');

    const detailHtml = detail
        ? `<p style="margin:12px 0 0;color:var(--text-muted);font-size:0.9em;line-height:1.5">${escapeHtml(detail)}</p>`
        : '';

    overlay.innerHTML = `
        <div class="modal-base" style="${borderStyle} max-width:480px;">
            <div style="${headerBg} padding:16px 20px; display:flex; align-items:center; justify-content:space-between; border-radius: var(--radius-lg) var(--radius-lg) 0 0;">
                <h3 style="margin:0; font-size:1.1em; ${titleColor}">
                    <span style="${iconColor}">\u26A0</span> ${escapeHtml(title)}
                </h3>
                <button class="close-btn dc-close" style="background:none;border:none;color:var(--text-muted);font-size:1.4em;cursor:pointer;padding:0 4px;">&times;</button>
            </div>
            <div style="padding:20px;">
                <ul style="margin:0 0 16px; padding-left:20px; list-style:disc;">
                    ${warningsHtml}
                </ul>
                ${detailHtml}
                <div style="margin-top:16px;">
                    <label style="display:block; margin-bottom:6px; font-size:0.85em; color:var(--text-muted);">
                        Type <strong style="${titleColor}">${escapeHtml(confirmPhrase)}</strong> to continue
                    </label>
                    <input class="dc-input" type="text" autocomplete="off" spellcheck="false"
                        placeholder="${escapeHtml(confirmPhrase)}"
                        style="width:100%; padding:10px 12px; background:var(--input-bg);
                            border:${isDanger ? '2px' : '1px'} solid ${isDanger ? 'var(--danger-red)' : 'var(--border-light)'};
                            border-radius:var(--radius-sm); color:var(--text-light); font-size:1em;">
                </div>
            </div>
            <div style="padding:12px 20px; border-top:1px solid var(--border); display:flex; justify-content:flex-end; gap:8px;">
                <button class="btn btn-secondary dc-cancel">Cancel</button>
                <button class="btn dc-confirm" disabled
                    style="${btnBg} opacity:0.4; pointer-events:none; color:#fff; font-weight:600;">
                    ${escapeHtml(buttonLabel)}
                </button>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add('active'));

    const input = overlay.querySelector('.dc-input');
    const confirmBtn = overlay.querySelector('.dc-confirm');

    let cleanupEsc;
    const close = () => {
        if (cleanupEsc) cleanupEsc();
        overlay.classList.remove('active');
        setTimeout(() => overlay.remove(), 300);
    };
    cleanupEsc = setupModalClose(overlay, () => { close(); onCancel(); });

    input.addEventListener('input', () => {
        const valid = input.value.trim().toUpperCase() === confirmPhrase.toUpperCase();
        confirmBtn.style.opacity = valid ? '1' : '0.4';
        confirmBtn.style.pointerEvents = valid ? 'auto' : 'none';
        confirmBtn.disabled = !valid;
    });

    confirmBtn.addEventListener('click', () => {
        if (input.value.trim().toUpperCase() !== confirmPhrase.toUpperCase()) return;
        close();
        onConfirm();
    });

    overlay.querySelector('.dc-close').addEventListener('click', () => { close(); onCancel(); });
    overlay.querySelector('.dc-cancel').addEventListener('click', () => { close(); onCancel(); });
    // click-outside + ESC handled by setupModalClose above

    setTimeout(() => input.focus(), 50);
}
