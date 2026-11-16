// setup-steps/you.js - your name and your time zone.
// The name never saves as the literal default (an unedited field used to overwrite
// a real saved name with "Human Protagonist", 2026-04-30); the zone comes from the
// browser when the stored one is UTC or empty on a first setup, and is saved only then.
// The two fields are also the foot of the Easy step (setup-steps/easy.js), so they
// are functions here: renderYou paints them, stageName stages the name.
import { escapeHtml as esc } from '../../shared/modal.js';

const DEFAULT_NAME = 'Human Protagonist';
const isDefaultZone = z => !z || z === 'UTC' || z === 'Etc/UTC';

export function renderYou(w, el) {
    const ctx = w.host.createCtx();
    const name = ctx.getValue('DEFAULT_USERNAME') || '';
    const stored = ctx.settings.USER_TIMEZONE || '';
    const picked = w.host.pendingChanges.USER_TIMEZONE;       // staged on an earlier paint of this visit
    let browser = 'UTC';
    try { browser = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch {}
    // The browser's zone stands in for a zone never set, on a first setup only:
    // a finished install's UTC is as much a choice as any other (hunt 2026-11-15).
    const unset = isDefaultZone(stored) && !w.state?.complete;
    const fromBrowser = unset && (!picked || picked === browser);
    const tz = picked || (fromBrowser ? browser : stored || 'UTC');
    let zones = [];
    try { zones = Intl.supportedValuesOf('timeZone'); } catch { zones = [tz]; }
    if (!zones.includes(tz)) zones = [tz, ...zones];
    el.innerHTML = `
        <div class="settings-grid">
            <div class="setting-row">
                <div class="setting-label"><label for="setup-name">Your name</label>
                    <div class="setting-help">What she calls you.</div></div>
                <div class="setting-input"><input type="text" id="setup-name" autocomplete="off"
                    value="${esc(name === DEFAULT_NAME ? '' : name)}" placeholder="What should Sapphire call you?"></div>
            </div>
            <div class="setting-row">
                <div class="setting-label"><label for="setup-tz">Time zone</label>
                    <div class="setting-help">${fromBrowser ? 'From your browser.' : picked ? 'Your pick.' : 'As saved.'}</div></div>
                <div class="setting-input"><select id="setup-tz">${zones.map(z =>
                    `<option value="${esc(z)}"${z === tz ? ' selected' : ''}>${esc(z.replace(/_/g, ' '))}</option>`).join('')}</select></div>
            </div>
        </div>`;
    if (fromBrowser && !picked && browser !== 'UTC' && stored !== browser) ctx.markChanged('USER_TIMEZONE', browser);
    el.querySelector('#setup-tz').addEventListener('change', e => ctx.markChanged('USER_TIMEZONE', e.target.value));
}

// the name field has no data-key on purpose (the generic flush would save
// whatever it holds): read it here, with the guard
export function stageName(w, el) {
    const ctx = w.host.createCtx();
    const v = el.querySelector('#setup-name')?.value.trim() || '';
    if (v && v !== DEFAULT_NAME && v !== (ctx.settings.DEFAULT_USERNAME || '')) ctx.markChanged('DEFAULT_USERNAME', v);
    else delete w.host.pendingChanges.DEFAULT_USERNAME;
}

// the nav rail's initial is painted at boot only
export async function refreshBadge() {
    const { refreshProfileBadge } = await import('../../features/user-profile.js');
    refreshProfileBadge();
}

export default w => ({
    id: 'you', title: 'You',

    render(body) {
        body.innerHTML = `
            <p class="setup-lead">So she knows who she is talking to, and what time it is where you are.</p>
            <div class="you-fields"></div>`;
        renderYou(w, body.querySelector('.you-fields'));
    },

    async leave(body, dir) {
        stageName(w, body);
        const r = await w.save();
        if (r === false && dir > 0) return false;
        if (r) refreshBadge();
    },
});
