# Browser

Sapphire's own Chromium, driven by Playwright. Pages come back as text (the
accessibility tree) with a `[ref=eN]` on every control; `browser_act` uses it.

- Tools: `browser_open`, `browser_look` (fresh read, wait, part, screenshot),
  `browser_act`, `browser_tabs`, `browser_console`, `browser_js`.
- Lanes: her turns use the `sapphire` profile, MCP callers (tick the tools under
  Settings > MCP Server) get `mcp-<persona>`. Profiles live in `user/browser/`.
- Guard rails (on by default) hold her lane only. All her traffic goes through
  a small proxy inside the plugin that checks every connection's address, so
  redirects and a page's WebSockets can't reach the local network either.
  Self-login is off by default.
- Install: the plugin card installs `playwright`. Chromium (about 200 MB to
  download, ~370 MB on disk) downloads by itself on the first browser call, into
  Playwright's cache (`~/.cache/ms-playwright`, `%LOCALAPPDATA%\ms-playwright`),
  outside Sapphire's folder. On Linux, missing system libraries raise a
  notification with the one `sudo` command to run.
- Browse together: Apps > Browser shows her tab live (a Chromium screencast) and
  your clicks, typing and paste go into it. The same app opens beside the chat
  on the Chat page's stage (the 🖥 button in the sidebar header), and she can
  open it herself with `open_stage`. Private chats never have a stage.
  "Clear browser profile" wipes her logins, site data, history and cache.
  A viewer in a hidden tab lets go, so her Chromium can idle out. If the page
  moved under her (you navigated, or another turn did), her next click on an
  old ref is refused and she gets the page again with fresh refs.
- SOCKS: when Sapphire's SOCKS proxy is on (Settings > Network), every page
  leaves through it, so her browsing has its own exit, not this machine's IP.
  Fail-closed: a proxy that's down or refuses is reported, never dialed around.
  This machine and the LAN stay direct. With remote DNS on, names go to the
  proxy unresolved; her railed lane does that only when the proxy is out on
  the internet (it can't reach your LAN then), else she resolves names here so
  the rails can check them. Switching SOCKS or the rails hangs up open
  connections, so nothing keeps the old route.
- Search engines wall off automated browsers (captchas, empty results): she
  searches with a web search tool and asks you to solve a captcha in the viewer.
- Windows and Linux; headless works on a server (watch it through the viewer).
- Backups skip `user/browser/` (live session cookies).

## A person's own browser

With the extension in `extension/` loaded into their Brave or Chrome and a
**Browser** device added (Settings > Devices), her own turns can also work in
the tabs that person shares. User guide: docs/DEVICES.md, "Your own browser".

- The extension is a thin pipe: `chrome.debugger` on the tabs she takes, one
  WebSocket to `/ws/plugin/browser/pipe`. The socket lives in a hidden page
  (`pipe.html`): an extension's worker cannot reach a Sapphire whose
  self-signed certificate was accepted in a tab, a page can. Each connection
  carries a one-time proof made with the pairing code (core's device door,
  `core/devices/door.py`); the code itself never crosses the wire.
- Pairing: the user presses the extension's button on her page. That lends
  the extension that one tab (`activeTab`), and it sends one DOM event there,
  `sapphire-browser-bridge`, with a JSON string: `{v: 1, ask: 'hello'}` (is
  this a Sapphire? `preventDefault` = yes) or `{v: 1, ask: 'pair', code,
  browser}`. `web/main.js` answers: it asks the user, and posts the code to
  `routes/pair.py` (`POST /api/plugin/browser/pair`, `{code, name}`), which
  adds the Browser device, or answers `already` for a browser that is one.
  `{v: 1, ask: 'unpair', id}` says this browser dropped its pairing (the
  code's public half): her page offers to remove the device (`POST unpair`).
  Everything about pairing but that one event lives here, so it can change
  without a new extension (a store review). Pasting the code into Add Device
  still works.
- `bridge.py` is a relay: Playwright attaches to it over loopback
  (`connect_over_cdp`) as if it were a browser. Browser-level commands are
  answered there, a tab's commands go down the pipe. So the reader, the refs
  and the six tools are the same code as for her own Chromium. The lane is
  `ext-<device id>`.
- `browser_tabs` lists their tabs as `<device>:N`. A switch moves that chat's
  hand (`engine.hand(chat)`); every read from there starts with whose browser
  it is. Only a turn a person started reaches a person's browser
  (`engine.turn`, the rule the Bitcoin plugin's sends use): not an agent, a
  turn a message from outside started, or an MCP caller.
- A scheduled task's run reaches it only while that device's `tasks` field is
  on ("Scheduled tasks may use this browser", off by default):
  `engine.may(who, device)`, asked on every call. Core says what a run is in
  `tool_context['task']` = `{id, type, event, n}`; only type `task` or
  `heartbeat` with `event` False counts, so a run a message or a webhook set
  off never does. `turn()` answers `('task', id, run)` for it, and that is the
  key of the run's hand: each run starts in her own browser.
- Calls on a person's lane run one at a time (`engine.using`): it has one tab
  in hand.
- The debugger goes on a tab only when she takes it, and comes off when the
  lane idles out, the pipe drops, or they press Cancel on the browser's
  "is debugging" bar. Playwright's usual restyling of a page (forced light
  colours, its fonts) is swallowed by the relay: their pages look as they did.
- Rails there are always on (`engine.guarded`), and there is no proxy of ours
  in that browser's path, so the address rule is checked twice: by the
  extension (`worker.js` `addressWhy`: never-shared sites, local addresses,
  private windows, an allowlist of DevTools commands; it also cancels a
  download that starts within 5 s of her acting, since an extension's debugger
  cannot refuse downloads) and here
  (`engine.person_blocked`: local addresses resolved on this machine, her own
  UI's origin unless self-login is on). `tools/browser.py` `_gate` asks both
  before every read, picture and action. A frame from an off-limits site is
  let go by the extension (never-shared, local) or the relay (her own UI)
  before Playwright hears of it. The Network domain is never enabled there:
  its events carry cookies and posted forms.
- Tests: `tests/test_browser_bridge.py` loads the real extension into a
  throwaway Chromium. No browser of yours is ever touched by a test.
- Not built: Firefox (it has no debugger API for extensions), a Web Store
  listing, MCP callers.
