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
