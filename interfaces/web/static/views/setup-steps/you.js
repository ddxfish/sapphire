// setup-steps/you.js - your name and your time zone.
// The name never saves as the literal default (an unedited field used to overwrite
// a real saved name with "Human Protagonist", 2026-04-30); the zone comes from the
// browser when the stored one is UTC or empty, and is saved only then.
import { escapeHtml as esc } from '../../shared/modal.js';

const DEFAULT_NAME = 'Human Protagonist';
const isDefaultZone = z => !z || z === 'UTC' || z === 'Etc/UTC';

export default w => ({
    id: 'you', title: 'You',

    render(body) {
        const ctx = w.host.createCtx();
        const name = ctx.getValue('DEFAULT_USERNAME') || '';
        const stored = ctx.getValue('USER_TIMEZONE') || '';
        let browser = 'UTC';
        try { browser = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch {}
        const tz = isDefaultZone(stored) ? browser : stored;
        let zones = [];
        try { zones = Intl.supportedValuesOf('timeZone'); } catch { zones = [tz]; }
        if (!zones.includes(tz)) zones = [tz, ...zones];
        body.innerHTML = `
            <p class="setup-lead">So she knows who she is talking to, and what time it is where you are.</p>
            <div class="settings-grid">
                <div class="setting-row">
                    <div class="setting-label"><label for="setup-name">Your name</label>
                        <div class="setting-help">What she calls you.</div></div>
                    <div class="setting-input"><input type="text" id="setup-name" autocomplete="off"
                        value="${esc(name === DEFAULT_NAME ? '' : name)}" placeholder="What should Sapphire call you?"></div>
                </div>
                <div class="setting-row">
                    <div class="setting-label"><label for="setup-tz">Time zone</label>
                        <div class="setting-help">${isDefaultZone(stored) ? 'From your browser.' : 'As saved.'}</div></div>
                    <div class="setting-input"><select id="setup-tz">${zones.map(z =>
                        `<option value="${esc(z)}"${z === tz ? ' selected' : ''}>${esc(z.replace(/_/g, ' '))}</option>`).join('')}</select></div>
                </div>
            </div>`;
        if (isDefaultZone(stored) && browser !== 'UTC' && stored !== browser) ctx.markChanged('USER_TIMEZONE', browser);
        body.querySelector('#setup-tz').addEventListener('change', e => ctx.markChanged('USER_TIMEZONE', e.target.value));
    },

    async leave(body, dir) {
        // the name field has no data-key on purpose (the generic flush would save
        // whatever it holds): read it here, with the guard
        const ctx = w.host.createCtx();
        const v = body.querySelector('#setup-name')?.value.trim() || '';
        if (v && v !== DEFAULT_NAME && v !== (ctx.settings.DEFAULT_USERNAME || '')) ctx.markChanged('DEFAULT_USERNAME', v);
        else delete w.host.pendingChanges.DEFAULT_USERNAME;
        const r = await w.save();
        if (r === false && dir > 0) return false;
        if (r) {
            // the nav rail's initial is painted at boot only
            const { refreshProfileBadge } = await import('../../features/user-profile.js');
            refreshProfileBadge();
        }
    },
});
