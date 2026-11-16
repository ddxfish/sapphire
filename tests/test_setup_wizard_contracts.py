"""The setup wizard's wiring (2026-10-22, tmp/setup-wizard-plan.md wave 3), pinned as
text: the #setup view, the boot gate, finishing without a reload, and the seams the
wizard shares with the Settings page."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'interfaces' / 'web' / 'static'


def _src(rel):
    return (STATIC / rel).read_text(encoding='utf-8')


def test_main_registers_the_view_and_gates_the_router_on_init_data():
    m = _src('main.js')
    assert "    setup:    `./views/setup.js${_v}`," in m
    assert "const needSetup = initData?.setup?.complete === false;" in m
    assert "initRouter(needSetup ? 'setup' : 'chat');" in m
    assert "if (needSetup) switchView('setup');" in m
    assert "import { registerView, initRouter, getCurrentView, switchView } from './core/router.js';" in m
    # the old wizard's auto-show is gone with its int step
    assert 'wizard_step' not in m and 'core-ui/setup-wizard' not in m


def test_full_resync_replaces_the_reload_and_both_handlers_use_it():
    m = _src('main.js')
    assert 'export async function fullResync() {' in m
    body = m.split('export async function fullResync() {')[1].split('\n}\n')[0]
    for call in ('await refreshInitData();', 'await populateChatDropdown();', 'await refresh(false);',
                 'await updateScene();', 'await resyncTrueSpeech();', 'refreshProfileBadge();',
                 'resetProviderMeta();', 'syncNavWithPlugins();'):
        assert call in body, call
    assert m.count('        await fullResync();\n') == 2          # SERVER_RESTARTED and BUS_CONNECTED
    assert 'async function resyncTrueSpeech() {' in m


def test_found_in_passing_fixes_in_main():
    m = _src('main.js')
    assert 'api.fetchChatList' not in m and 'fetchChatList(undefined, { slim: true })' in m
    assert "if (data?.key === 'LLM_PROVIDERS' || data?.key === 'LLM_CUSTOM_PROVIDERS') resetProviderMeta();" in m
    # the rail follows a plugin toggle both ways, from one place
    assert "if (p && btn) btn.style.display = p.enabled ? '' : 'none';" in m
    assert "document.addEventListener('sapphire:plugin_toggled', () => { refreshInitData(); syncNavWithPlugins(); });" in m
    assert 'reloadPluginScripts' not in m
    p = _src('views/settings-tabs/plugins.js')
    assert 'PLUGIN_NAV_MAP' not in p


def test_badge_and_provider_meta_are_repaintable():
    assert 'export async function refreshProfileBadge() {' in _src('features/user-profile.js')
    s = _src('features/scene.js')
    assert 'export function resetProviderMeta() {' in s and '_providerMetaPending = null;' in s.split('resetProviderMeta')[1]


def test_setup_view_contract():
    v = _src('views/setup.js')
    assert "document.body.classList.add('setup-mode');" in v and "document.body.classList.remove('setup-mode');" in v
    assert "wizards.register({" in v and "open: () => switchView('setup')," in v
    assert "await w.putState({ complete: true, step: '' });" in v
    assert "const main = await import('../main.js');" in v and 'await main.fullResync();' in v
    assert "switchView('chat');" in v
    assert 'wizard: true,' in v                                   # tabs may hide their General sections
    assert "isTabActive: id => !!stepper?.current()?.tabs?.some(t => t.id === id)," in v
    assert "container.addEventListener('settings-navigate'" in v
    assert 'notify.onChange(paintDownloads)' in v


def test_steps_host_the_real_tabs():
    v = _src('views/setup.js')
    assert "brain: tabStep('brain', 'Brain', llmTab)," in v and "audio: tabStep('audio', 'Audio', audioTab)," in v
    voice = _src('views/setup-steps/voice.js')
    assert "import ttsTab from '../settings-tabs/tts.js';" in voice and "import sttTab from '../settings-tabs/stt.js';" in voice
    assert "import wakewordTab from '../settings-tabs/wakeword.js';" in voice
    look = _src('views/setup-steps/look.js')
    assert "import appearanceTab from '../settings-tabs/appearance.js';" in look
    llm = _src('views/settings-tabs/llm.js')
    assert "${ctx.wizard ? '' : `<div style=\"margin-top:24px\">" in llm


def test_voice_step_is_cards_plus_one_fold():
    # 2026-10-24: the voice step was overwhelming. Three sections, each the provider
    # cards, everything else under ONE Advanced fold; the tabs paint that shape under
    # ctx.wizard (one module, two densities). Silero rides inside the ears' fold.
    ps = _src('shared/provider-selector.js')
    assert "export function renderProviderCompact(tabConfig, ctx, { fold = '', after = '' } = {})" in ps
    assert '<details class="setup-adv"><summary>' in ps
    for tab in ('tts', 'stt'):
        src = _src(f'views/settings-tabs/{tab}.js')
        assert 'if (ctx.wizard)' in src and 'renderProviderCompact(cfg, ctx, {' in src
    assert 'fold: renderVADFold(ctx)' in _src('views/settings-tabs/stt.js')
    ww = _src('views/settings-tabs/wakeword.js')
    assert 'name="ww-pick"' in ww and "stage(ctx, 'WAKE_WORD_ENABLED', !!v);" in ww
    voice = _src('views/setup-steps/voice.js')
    assert 'renderFields' not in voice and 'Memory model' not in voice     # the step paints headings only


def test_consent_and_name_guards():
    c = _src('views/setup-steps/consent.js')
    assert "try { await w.putState({ consent: true }); }" in c                   # the server stamps the time
    # one button agrees and moves on (2026-11-15); a failed stamp stays on the step (hunt 19)
    assert 'ownNext: true' in c and 'id="setup-understand"' in c and 'type="checkbox"' not in c
    assert "btn.disabled = false; return; }" in c and c.index("btn.disabled = false; return; }") < c.index('w.next();')
    y = _src('views/setup-steps/you.js')
    assert "const DEFAULT_NAME = 'Human Protagonist';" in y
    assert "if (v && v !== DEFAULT_NAME && v !== (ctx.settings.DEFAULT_USERNAME || '')) ctx.markChanged('DEFAULT_USERNAME', v);" in y


def test_look_step_is_theme_font_background_and_one_fold():
    a = _src('views/settings-tabs/appearance.js')
    assert "import { renderFold } from '../../shared/provider-selector.js';" in a
    assert "if (ctx.wizard) {" in a and "renderFold(themePanel + title('Motion', false) + motion + options)" in a
    # Settings keeps the full layout from the same module
    assert "${colors}${themePanel}${type}${title('Background &amp; Motion')}${scene}${motion}${options}" in a
    look = _src('views/setup-steps/look.js')
    assert 'Her look' in look
    assert "w.host.mount(appearanceTab, body.querySelector('[data-tab=\"appearance\"] .setup-sub-body'));" in look


def test_core_ui_class_is_gone():
    """Wave 4 (2026-10-25): the old core-ui setup wizard and the static plugin
    list it rode on are retired; every plugin is the loader's."""
    assert not (ROOT / 'interfaces/web/static/core-ui').exists()
    for rel, static in (('core/plugin_loader.py', False), ('core/routes/plugins.py', False),
                        ('views/settings.js', True), ('core/events.js', True)):
        src = _src(rel) if static else (ROOT / rel).read_text(encoding='utf-8')
        assert 'core-ui' not in src and 'STATIC_PLUGINS_JSON' not in src and 'sapphireSetupWizard' not in src, rel
    assert 'RETIRED_PLUGIN_NAMES = frozenset({"setup-wizard"})' in (ROOT / 'core/plugin_loader.py').read_text(encoding='utf-8')


def test_next_step_every_door_finishes_first():
    n = _src('views/setup-steps/next.js')
    assert "import { burst } from '../../shared/confetti.js';" in n and 'burst();' in n
    assert 'href=' not in n                                           # no plain links: a door marks setup complete
    assert "if (b.dataset.door) w.finishTo(DOORS[b.dataset.door]);" in n
    assert 'data-door="chat"' in n and "prompts: { view: 'prompts' }" in n and "devices: { tab: 'devices' }" in n
    v = _src('views/setup.js')
    assert "async function finish(_step, to = {}) {" in v and "finishTo: to => finish(null, to)," in v
    assert "else if (to.tab) w.navigate(to.tab);" in v and "else switchView(to.view || 'chat');" in v


def test_featured_step_toggles_live_and_installs_first():
    p = _src('views/setup-steps/plugins.js')
    assert "const ok = await ensureExtra(`plugin:${p.name}`);" in p
    assert "if (p.enabled === on) return;" in p                     # the route toggles: only when it differs
    assert "for (const other of group) await flip(other, false);" in p
    assert 'data-setup' not in p and 'navigate' not in p             # nothing on the step leaves the wizard
    assert "${p.legacy ? ' legacy' : ''}" in p and '<span class="feat-tag">Legacy</span>' in p
    # on by default but not running for want of packages: an Install button, the install reloads it
    assert "const stalled = p => p.enabled && !p.loaded && !!needs(p);" in p
    assert 'data-install="${esc(p.name)}">Install</button>' in p and "if (await ensureExtra(`plugin:${p.name}`)) await load();" in p


def test_settings_system_button_opens_the_view():
    s = _src('views/settings-tabs/system.js')
    assert "import('../../core/router.js').then(r => r.switchView('setup'));" in s
    assert 'sapphireSetupWizard' not in s


def test_stepper_and_registry_exports():
    st = _src('shared/stepper.js')
    assert 'export function createStepper({ el, steps, onShow, onFinish, onExit, exitLabel' in st
    assert "if (step.kind === 'gate') {" in st and 'if (ok === false) return;' in st
    assert "right = step.ownNext ? '' : btn('next', 'Next →', 'btn-primary');" in st   # a step with its own continue
    wz = _src('shared/wizards.js')
    assert 'export function register(w) {' in wz and 'export function list(except = null) {' in wz


def test_setup_css_hides_the_rail_only_in_setup_mode():
    css = (STATIC / 'style.css').read_text(encoding='utf-8')
    assert 'body.setup-mode #nav-rail { display: none; }' in css
    assert 'body.setup-mode #app-content { margin-left: 0; margin-bottom: 0; width: 100%; height: 100%; }' in css


def test_the_kind_of_setup_decides_the_steps():
    # 2026-11-15, tmp/easy-mode-plan.md: welcome -> Easy or Advanced -> that kind's steps
    v = _src('views/setup.js')
    assert "const DEFAULT_STEPS = ['consent', 'mode'];" in v
    assert "await w.putState({ mode: kind });" in v and "await build(ids[ids.indexOf('mode') + 1]);" in v
    assert "if (agreedBefore) ids = ids.filter(id => id !== 'consent');" in v        # agreed on an earlier visit: not asked again
    assert "agreedBefore = !!state.consent;" in v                                    # the visit that showed it keeps it in the count
    # a visit starts with nothing staged: an earlier visit's unsaved picks must not ride its first save
    assert "for (const k of Object.keys(host.pendingChanges)) delete host.pendingChanges[k];" in v.split('async show()')[1]
    assert "await build(state.complete ? 'mode' : state.step);" in v                 # a second run picks the kind again
    m = _src('views/setup-steps/mode.js')
    assert 'ownNext: true' in m and 'await w.setMode(card.dataset.mode);' in m


def test_easy_step_stages_the_picks_and_unfolds_the_real_tabs():
    e = _src('views/setup-steps/easy.js')
    assert "const TABS = { brain: llmTab, voice: ttsTab, ears: sttTab, wake: wakewordTab };" in e
    assert "kind: 'gate'" in e                                                   # Finish is the only way on
    assert "if (c.settings[k] !== v) c.markChanged(k, v);" in e                  # only what differs is staged
    assert "w.host.mount(TABS[id], fold);" in e and "w.host.unmount(TABS[id], fold);" in e
    assert "const r = await w.save({ wake_providers: true });" in e
    back = e.split("if (dir < 0) {")[1].split('return;')[0]
    assert 'dropPending();' in back                                              # going back drops the picks
    assert back.index('setFold(id, false)') < back.index('open.clear()')         # and each open tab gets its detach first
    assert "if (opts.wake_providers) body.wake_providers = true;" in _src('shared/settings-api.js')
    assert "wake_providers: opts?.wake_providers === true" in _src('shared/settings-host.js')


def test_easy_second_run_offers_and_its_tests_install_nothing_by_themselves():
    e = _src('views/setup-steps/easy.js')
    assert "for (const r of plan.rerun ? [] : plan.rows) {" in e                 # a second run stages nothing
    assert 'data-use="${id}">Use it</button>' in e                               # the pick is one click away
    # nothing of the row's staged: what is set, with the pick offered (a second run, or a switch that failed)
    assert "if (keys.every(k => c.getValue(k) === c.settings[k])) {" in e and "offer: r };" in e
    # the tests: this computer's speakers and microphone, only where it has them
    assert "'/api/setup/easy/say'" in e and "fetch('/api/setup/easy/listen'" in e
    assert "const testable = id => id === 'brain' || (!!plan?.sound && (id === 'voice' ? plan.speakers !== false : plan.mic !== false));" in e
    assert "if (key && !busy && key in w.host.pendingChanges && !(which in waiting)) {" in e    # "Set up now" only for a staged pick
    assert "if (!closed && hearing === take)" in e                               # a stream cut short says so
    assert "if (el && el._html !== html)" in e                                   # the Brain poll never replaces a button mid-click
    assert "if (brain) armBrain(); else loadBrain(false);" in e                  # the whole look once per visit, not per repaint
    assert e.count('await saveStaged()') == 2                                    # Finish and "Set up now": one save
    assert 'abort' not in e.split('async function listenTest')[1].split('async function setUpNow')[0].replace('Never aborted', '')
    assert "if (hearing !== take) return;       // the person went back" in e    # a repaint keeps the take, going back drops it


def test_the_stepper_owns_busy_while_it_moves_and_a_repaint_keeps_its_place():
    """hunt 2026-11-15: a save's own busy(false) re-enabled Finish while the finish
    was still running; the scroll reset aimed at an element that does not scroll."""
    s = _src('shared/stepper.js')
    assert "busy = !!b || moving;" in s
    assert "moving = true;" in s and "finally { moving = false; setBusy(false); }" in s
    assert "if (!again) scroller.scrollTop = 0;" in s and "if (again) scroller.scrollTop = at;" in s
    assert "scroller: container.querySelector('.setup-view')" in _src('views/setup.js')


def test_the_page_follows_the_warning_before_the_microphone_test_listens():
    """Krem 2026-11-15: nobody near the Sapphire computer is listened to quietly. The server
    says so and beeps three times (core/setup_easy.py _warn); the Ears row glows yellow while
    it does and red while the microphone is open."""
    e = _src('views/setup-steps/easy.js')
    assert "if (ev.warn) { glow('warning');" in e and "else if (ev.count) { glow('warning');" in e
    assert "else if (ev.mic) { glow('listening');" in e and "else if (ev.clear) { glow(''); show(false);" in e
    test = e.split('async function listenTest')[1].split('const TESTS')[0]
    assert "glow('');" in test.split('} finally {')[1]                               # however the test ends, the glow goes
    css = _src('style.css')
    assert '.easy-row.warning' in css and '.easy-row.listening' in css and 'red-alert' not in css
    py = (ROOT / 'core' / 'setup_easy.py').read_text(encoding='utf-8')
    assert py.index("_warn(system, emit)") < py.index("with sd.InputStream(")         # the warning, then the microphone
    assert 'tkinter' not in py and 'notify-send' not in py                           # no screen flash, no alarm: a standard warning


def test_every_easy_row_has_a_light_and_a_test_of_its_own():
    """Krem 2026-11-15: a Test beside each Change (her LLM says hello, her voice speaks, her
    ears hear, the wake word waits for its phrase), a light on every row, no section at the bottom."""
    e = _src('views/setup-steps/easy.js')
    assert "const TESTS = { brain: helloTest, voice: sayTest, ears: listenTest, wake: wakeTest };" in e
    for route in ("'/api/setup/easy/hello'", "'/api/setup/easy/wake'", "'/api/setup/easy/status'"):
        assert route in e
    assert 'data-test="${id}"' in e and 'data-try=' not in e and 'Try it' not in e
    assert "if (Object.keys(r.settings).some(k => c.getValue(k) !== c.settings[k])) return ['idle', ''];" in e   # staged: grey until Finish
    assert "if (testing.has(id)) return;" in e                                    # one test per row at a time
    css = _src('style.css')
    assert '.easy-dot.failed' in css and '.easy-dot.idle' in css and '.easy-try-row' not in css
