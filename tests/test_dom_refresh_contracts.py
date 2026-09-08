"""Source tripwires for the DOM-refresh surgical wave (2026-09-08; record:
tmp/dom-refresh-hunt-plan.md). One bug class — a container rebuilt while the
user is inside it (edit reverted / focus stolen / scroll reset) — fixed at
~30 sites with ONE primitive (shared/dom-guard.js). These read the shipped
JS/Python as text and assert the load-bearing shapes are still there, so a
revert or a well-meaning "simplification" can't quietly bring a site back.
Behavior lives in tests/js/dom-guard.test.mjs (the primitive) and
tests/test_mind_origin.py (the server leg); the rest is the click-list.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "interfaces" / "web" / "static"


def _src(rel):
    return (STATIC / rel).read_text(encoding="utf-8")


def _between(src, start, end):
    i = src.index(start)
    j = src.index(end, i)
    return src[i:j]


# ── the primitive ──

def test_primitive_exports_the_five_moves_and_is_the_house_pattern():
    g = _src("shared/dom-guard.js")
    for name in ("editableFocused", "deferWhileEditing", "snapScroll", "snapFocus", "focusUnlessEditing"):
        assert f"export function {name}" in g, name
    # one document-level focusout drains every deferred refresh
    assert "document.addEventListener('focusout'" in g
    importers = [p for p in STATIC.rglob("*.js") if "dom-guard.js'" in p.read_text(encoding="utf-8")]
    assert len(importers) >= 20, f"only {len(importers)} files import dom-guard — a site was reverted?"


# ── seeded case 1: the composer focus ──

def test_composer_focus_honors_a_cursor_elsewhere():
    src = _src("handlers/send-handlers.js")
    assert "focusUnlessEditing(input);" in src
    assert "        input.focus();\n        setProc(false);" not in src


# ── the sidebar wipe (CRIT): seven doors go through the soft refresh ──

def test_sidebar_bus_doors_are_soft_and_foreign_guarded():
    src = _src("views/chat.js")
    assert "const softSidebar = deferWhileEditing(" in src
    assert "busy: () => !!saveTimer || _saveInFlight > 0" in src
    bus = _between(src, "eventBus.on('settings_changed'", "// Accordion behavior")
    assert bus.count("softSidebar()") >= 8, bus.count("softSidebar()")
    assert "loadSidebar()" not in bus.replace("() => loadSidebar()", ""), "a bus door calls loadSidebar() bare"
    assert "if (s.llm_primary && !foreign) {" in src
    # the save reports in-flight and kicks the held refresh when it lands
    save = _between(src, "async function saveSettings(", "function collectSettings(")
    assert "_saveInFlight++;" in save and "_saveInFlight--;" in save and "softSidebar.kick();" in save
    # user-driven paint after a switch stays direct (the cursor is no veto)
    assert "(e) => loadSidebar(e.detail?.settings || null, e.detail?.chat || null)" in src


# ── seeded case 2 stays closed: the edit hold is still the one gate ──

def test_transcript_edit_hold_still_guards_the_only_render_path():
    chat = _src("chat.js")
    assert "document.querySelector('#chat-container .message.editing')" in chat
    callers = [p for p in STATIC.rglob("*.js") if "ui.renderHistory(" in p.read_text(encoding="utf-8")]
    assert [p.name for p in callers] == ["chat.js"], callers


# ── renderHistory no longer force-snaps on background refreshes ──

def test_render_history_carries_scroll_and_never_forces():
    ui = _src("ui.js")
    body = _between(ui, "export const renderHistory", "export const showStatus")
    assert "const keepTop = sticky ? null" in body
    assert "scrollToBottomIfSticky(true)" not in body
    cm = _src("features/chat-manager.js")
    assert cm.count("ui.forceScrollToBottom();") == 4, "switch / delete / clear / import land at the bottom"


# ── settings family ──

def test_provider_tabs_gate_their_second_paint():
    for tab in ("stt", "tts", "embedding"):
        src = _src(f"views/settings-tabs/{tab}.js")
        assert "if (ctx.isTabActive?.(this.id) === false) return;" in src, tab
        assert "&& !editableFocused(el)) {" in src, tab
        assert "dom-guard.js" in src, tab


def test_settings_shell_refresh_flushes_carries_and_binds_nav_once():
    src = _src("views/settings.js")
    rt = _between(src, "async refreshTab() {", "// For async completions")
    assert "flushCurrentInputs();" in rt and "snapScroll(" in rt and "restoreScroll();" in rt
    assert "let _shellNavBound = false;" in src
    assert "if (!_shellNavBound) container.addEventListener('settings-navigate'" in src
    rs = _between(src, "refreshSidebar() {", "// ── Generic Field Renderer")
    assert "renderSidebarItems(tabs)" in rs and "renderMobileItems(tabs)" in rs
    assert "render();" not in rs, "refreshSidebar went back to a whole-view render"


def test_scope_selects_keep_a_live_pick():
    src = _src("shared/scope-dropdowns.js")
    assert "e.target.dataset.touched = '1'" in src
    assert "const current = (sel.dataset.touched && sel.value)" in src


def test_prompt_monolith_preview_never_writes_over_a_live_edit():
    src = _src("views/prompts.js")
    assert "if (!promptSaveInFlight && !(mono && document.activeElement === mono)) {" in src


def test_persona_rename_repaint_is_deferred_and_roster_scroll_carried():
    src = _src("views/personas.js")
    assert ("const softRender = deferWhileEditing(() => container, () => render(),\n"
            "    { busy: () => !!saveTimer || _saveInFlight > 0 });") in src
    rename = _between(src, "if (data.name && data.name !== selectedName) {", "} else {")
    assert "softRender();" in rename and "render();" not in rename.replace("softRender();", "")
    assert "snapScroll(container, ['.panel-list-items', '.view-body'])" in src
    for v in ("toolsets", "spices"):
        assert "snapScroll(container, ['.panel-list-items', '.view-body'])" in _src(f"views/{v}.js"), v


# ── palace / mind ──

def test_mind_funnel_has_the_focus_guard():
    src = _src("shared/mind-common.js")
    assert "const soft = deferWhileEditing(document, onChange);" in src
    fn = _between(src, "export function subscribeMindDomain", "\n}\n")
    assert "soft();" in fn and "onChange();" not in fn


def test_self_sheet_poll_leg_is_held_like_the_sse_leg():
    src = _src("views/palace/self.js")
    assert "const softSheet = deferWhileEditing(() => container, () => renderSheet(), {" in src
    leg = _between(src, "'Librarian pass finished'", "} else {")
    assert "softSheet();" in leg and "renderSheet();" not in leg


def test_palace_lists_carry_focus_caret_and_scroll():
    for v in ("memories", "entities", "goals", "knowledge", "layer"):
        src = _src(f"views/palace/{v}.js")
        assert "const restoreFocus = snapFocus(el), restoreScroll = snapScroll(el);" in src, v
        assert "restoreScroll();\n    restoreFocus();" in src, v
        assert "document.activeElement === document.body" not in src, f"{v}: caret-to-end hack came back"


def test_classic_mind_views_and_knowledge_accordions_carry_state():
    for v in ("views/memories.js", "views/people.js", "views/goals.js", "views/chat-manage.js"):
        src = _src(v)
        assert "snapScroll(" in src and "restoreScroll();" in src, v
    mk = _src("shared/mind-knowledge.js")
    assert "details.mind-accordion[open] .mind-tab-entries" in mk
    assert "if (det) det.open = true;" in mk


def test_palace_self_echo_is_stamped_on_both_sides():
    assert "'X-Session-ID': sessionId" in _src("views/palace/common.js")
    me = (ROOT / "core" / "mind_events.py").read_text(encoding="utf-8")
    assert '"origin": session_origin.get()' in me
    api = (ROOT / "core" / "api_fastapi.py").read_text(encoding="utf-8")
    assert "session_origin.set(request.headers.get('X-Session-ID'))" in api


# ── store + trigger editor ──

def test_store_search_capture_runs_before_the_loading_wipe():
    src = _src("views/store.js")
    rl = _between(src, "async function renderList() {", "// Two parallel fetches")
    assert rl.index("_focusStash = snapFocus(main)") < rl.index("store-loading")
    rm = _between(src, "function renderMain(html) {", "\n}\n")
    assert "const restore = (editableFocused(m) ? snapFocus(m) : _focusStash) || (() => {});" in rm
    assert "restore();" in rm
    assert "oldSearch" not in src, "the dead inline capture came back"
    # pre-push hunt D2#5: the box survives the Loading wipe while the user types,
    # and repaints with the LIVE text (keystrokes typed during the RTT)
    assert "if (!editableFocused(main)) main.innerHTML = '<div class=\"store-loading\">Loading...</div>';" in rl
    assert "const liveQ = main.querySelector('.store-search')?.value ?? state.q;" in src
    assert 'value="${_esc(liveQ)}"' in src


def test_trigger_editor_keeps_partial_rows_and_never_rebuilds_under_the_cursor():
    src = _src("shared/trigger-editor/trigger-event.js")
    assert "_readFilterRows(modal, '#ed-filter-rows', { partial: true })" in src
    assert "if (!editableFocused(rows)) {" in src
    # pre-push hunt D2#8: partial rows are ORDERED pairs so two keyless rows
    # don't collapse onto one '' key; the save path still gets an object
    assert "else if (partial && (key || val)) rows.push([key || '', val || '']);" in src
    assert "return partial ? rows : filter;" in src
    assert "Array.isArray(filter) ? filter.slice() : Object.entries(filter || {})" in src
    assert re.search(r"if \(!editableFocused\(modal\.querySelector\('#ed-task-fields'\)\)\) _renderTaskFields\(modal\);", src)


# ── pre-push scout round, fix wave (2026-09-08; record tmp/scout-round-20260908-prepush.md) ──

def test_image_loads_follow_only_while_sticky():
    """D3-B1/B2 (found by two scouts): image onload FORCED the bottom and re-armed
    sticky through two owners the sticky-v2 work never reached."""
    assert "forceScrollToBottom" not in _src("core/events.js")
    assert "ui.followIfSticky()" in _src("core/events.js")
    assert "export const followIfSticky = () => scrollToBottomIfSticky();" in _src("ui.js")
    imgs = _src("ui-images.js")
    assert "scheduleScrollAfterImages(scrollCallback, true)" not in imgs
    assert imgs.count("scheduleScrollAfterImages(scrollCallback);") == 2


def test_scroll_intent_guards_short_chats_and_unreachable_clamps():
    ui = _src("ui.js")
    assert "const scrollable = () => el.scrollHeight > el.clientHeight + 1;" in ui
    assert "if (e.deltaY < 0 && scrollable()) unstick();" in ui
    assert "y > touchY + 4 && scrollable()) unstick();" in ui
    assert "if (top < _prevTop - 1 && h >= _prevHeight && _prevTop <= max + 1) unstick();" in ui


def test_remote_chat_switch_gates_same_chat_and_lands_at_the_bottom():
    m = _src("main.js")
    h = _between(m, "eventBus.Events.CHAT_SWITCHED", "eventBus.Events.CHAT_CREATED")
    assert "sel.value !== name" in h, "same-chat re-activate is the 8th sidebar door (D2#3)"
    assert h.index("await refresh(false);") < h.index("ui.forceScrollToBottom();") < h.index("await updateScene();")


def test_persona_soft_render_holds_while_a_save_is_pending():
    p = _src("views/personas.js")
    assert "{ busy: () => !!saveTimer || _saveInFlight > 0 }" in p
    assert "saveTimer = null;" in p, "the hold reads saveTimer — it must be nulled at fire or it holds forever"
    assert "_saveInFlight++;" in p and "_saveInFlight--;" in p and "softRender.kick();" in p


def test_provider_tabs_pass_the_module_and_repaint_only_on_change():
    for tab in ("stt", "tts", "embedding"):
        s = _src(f"views/settings-tabs/{tab}.js")
        assert "attachProviderListeners(cfg, ctx, el, this);" in s, tab
        assert "const painted = Object.keys((_mergedConfig || tabConfig).providers).join();" in s, tab
        assert "Object.keys(_mergedConfig.providers).join() !== painted" in s, tab


def test_dictated_turns_do_not_refocus_the_composer():
    s = _src("handlers/send-handlers.js")
    assert "export async function handleSend({ refocus = true } = {})" in s
    assert "if (refocus) focusUnlessEditing(input);" in s
    assert "await handleSend({ refocus: false });" in s


def test_sidebar_load_rearms_the_hold_after_its_fetches():
    c = _src("views/chat.js")
    ls = _between(c, "async function loadSidebar(", "const settings = settingsResp.status")
    assert "if (!useOverride && (editableFocused(container.querySelector('.chat-sidebar'))" in ls
    assert "softSidebar();\n            return;" in ls


def test_sse_reader_treats_a_foreign_cancel_as_cancelled():
    a = _src("api.js")
    rt = _between(a, "const _readTurn = async", "const _streamTurn = async")
    assert "if (data.cancelled && !data.type) {" in rt
    assert "return onError(new Error('Cancelled'));" in rt
    assert "if (data.type === 'tts_stream_start') tailId = data.stream_id || tailId;" in rt
    assert "dispatch('tts_stream_end', {})" not in rt, "belts must name the tail's stream_id (E1#6)"


def test_modal_cannot_close_while_an_async_save_runs():
    m = _src("shared/modal.js")
    assert "if (busy) return;" in m and "busy = true;" in m and "busy = false;" in m


def test_snapfocus_restores_without_scrolling():
    assert "el.focus({ preventScroll: true });" in _src("shared/dom-guard.js")


def test_prompt_preview_binds_the_name_across_its_await():
    p = _src("views/prompts.js")
    rp = _between(p, "async function refreshPreview()", "const previewEl")
    assert "const name = selected;" in rp and "if (fresh && name === selected) {" in rp
