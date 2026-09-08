// shared/mind-common.js - Shared helpers + tab config for the Mind section's
// five sibling views (memories / people / knowledge / ai-knowledge / goals).
// Factored out of the old monolithic views/mind.js.
import { on as onBusEvent, Events as BusEvents } from '../core/event-bus.js';
import { deferWhileEditing } from './dom-guard.js';

// Tab strip config (consumed by section-tabs/section-header). Tab ids === view ids.
export const MIND_TABS = [
    { id: 'memories', label: 'Memories', icon: '\u{1F9E0}' },
    { id: 'people', label: 'People', icon: '\u{1F465}' },
    { id: 'knowledge', label: 'Human Knowledge', icon: '\u{1F4DA}' },
    { id: 'ai-knowledge', label: 'AI Knowledge', icon: '\u{1F916}' },
    { id: 'goals', label: 'Goals', icon: '\u{1F3AF}' },
];

export function csrfHeaders(extra = {}) {
    const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
    return { 'X-CSRF-Token': token, ...extra };
}

export function escHtml(s) {
    if (!s) return '';
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

export function escAttr(s) {
    if (!s) return '';
    return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export function timeAgo(ts) {
    if (!ts) return '';
    try {
        const diff = Date.now() - new Date(ts).getTime();
        const mins = Math.floor(diff / 60000);
        if (mins < 1) return 'just now';
        if (mins < 60) return `${mins}m ago`;
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return `${hrs}h ago`;
        const days = Math.floor(hrs / 24);
        if (days < 14) return `${days}d ago`;
        return `${Math.floor(days / 7)}w ago`;
    } catch { return ''; }
}

// Resolve the active chat's scope for a given domain's chat-setting key
// (memory_scope / knowledge_scope / people_scope / goal_scope). 'none'/empty →
// 'default' (so the view still shows something). null = couldn't determine
// (caller keeps its current scope). Was mind.js `_scopeForActiveChatTab`.
export async function scopeForChatTab(scopeKey) {
    if (!scopeKey) return null;
    try {
        const resp = await fetch('/api/status');
        if (!resp.ok) return null;
        const data = await resp.json();
        const raw = (data.chat_settings || {})[scopeKey];
        if (!raw || raw === 'none') return 'default';
        return raw;
    } catch {
        return null;
    }
}

// Per-view SSE: re-render when MIND_CHANGED fires for THIS domain + current scope,
// while the view is visible. Returns an unsubscribe fn (call it on hide()).
export function subscribeMindDomain(domain, getScope, isVisible, onChange) {
    // Focus guard (DOM-refresh hunt 2026-09-08): Sapphire saves memories in
    // the background mid-turn, so this fires while the user may be typing in
    // a filter box or an inline edit — nine of ten subscribers rebuilt their
    // view under the cursor. Held while any editable has focus; catches up on
    // focusout. One guard here covers every subscriber.
    const soft = deferWhileEditing(document, onChange);
    return onBusEvent(BusEvents.MIND_CHANGED, (data) => {
        if (!data || !isVisible()) return;
        if (data.domain !== domain) return;
        if (data.scope !== getScope()) return;
        soft();
    });
}
