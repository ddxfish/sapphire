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
    assert "if (!w.state?.consent) await w.putState({ consent: true });" in c    # the server stamps the time
    assert 'return false;' in c.split("Tick \"I understand\"")[1]
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
    wz = _src('shared/wizards.js')
    assert 'export function register(w) {' in wz and 'export function list(except = null) {' in wz


def test_setup_css_hides_the_rail_only_in_setup_mode():
    css = (STATIC / 'style.css').read_text(encoding='utf-8')
    assert 'body.setup-mode #nav-rail { display: none; }' in css
    assert 'body.setup-mode #app-content { margin-left: 0; margin-bottom: 0; width: 100%; height: 100%; }' in css
