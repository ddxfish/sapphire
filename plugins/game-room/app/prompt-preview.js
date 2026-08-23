// Prompt preview (2026-08-23) — EXACTLY what she gets, from core's own
// assembly (/api/chats/{chat}/prompt-preview): persona + custom context +
// spice + every plugin prompt_inject (surface-filtered: avatar etc.) and
// the real per-turn ghost envelope (the story state block rides inside
// it). Shared by the story room's 👁 modal and the gear's State tab. The
// old client-side stitch silently omitted plugin injections — Krem caught
// avatar instructions she was getting unseen in story mode.

function csrf() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

function esc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export async function fetchPromptPreview(chat) {
    const res = await fetch(`/api/chats/${encodeURIComponent(chat)}/prompt-preview`,
                            { headers: { 'X-CSRF-Token': csrf() } });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
    return data;
}

export function promptPreviewHtml(data) {
    const pre = 'white-space:pre-wrap;background:var(--bg-secondary,#161b26);border:1px solid var(--border,#333);border-radius:8px;padding:12px;font-size:var(--font-sm,12.5px);line-height:1.5;overflow:auto;margin:6px 0 14px';
    const tools = data.tools || [];
    return `
        <div style="color:var(--text-muted);font-size:var(--font-sm,12.5px);margin-bottom:10px">
            Core's own assembly for the next turn on <b>${esc(data.chat || '')}</b>
            (surface: ${esc(data.surface || 'chat')}) — persona, custom context, spice,
            every plugin injection, then the per-turn envelope.</div>
        <b>System prompt (${(data.system_prompt || '').length.toLocaleString()} chars)</b>
        <div style="${pre}">${esc(data.system_prompt || '(empty)')}</div>
        <b>Per-turn envelope (ghost rail — sent right before your message, never saved)</b>
        <div style="${pre}">${esc(data.ghost || '(empty — nothing rides this turn)')}</div>
        <b>Tools offered (${tools.length})</b>
        <div style="${pre}">${tools.length ? esc(tools.join(', ')) : '(none)'}</div>`;
}
