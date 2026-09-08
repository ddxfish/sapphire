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
    assert "const softRender = deferWhileEditing(() => container, () => render());" in src
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
    assert "const restore = _focusStash || snapFocus(m);" in rm and "restore();" in rm
    assert "oldSearch" not in src, "the dead inline capture came back"


def test_trigger_editor_keeps_partial_rows_and_never_rebuilds_under_the_cursor():
    src = _src("shared/trigger-editor/trigger-event.js")
    assert "_readFilterRows(modal, '#ed-filter-rows', { partial: true })" in src
    assert "if (!editableFocused(rows)) {" in src
    assert "else if (partial && (key || val)) filter[key || ''] = val || '';" in src
    assert re.search(r"if \(!editableFocused\(modal\.querySelector\('#ed-task-fields'\)\)\) _renderTaskFields\(modal\);", src)
