# 2.12.1 - Prompt page cleanup
- Added tidy up button to delete orphans and danglers
- Make prompts multi-select for many delete and move to vault
- Delete can do 1 prompt, pieces optional, or many prompts
- Can move all prompt pieces in a prompt to/from vault at once
- Added trash bin in cleanup section for prompt pieces removed this way
# 2.12.0 - Million Dollar Bug Hunt
- Combined old blob chat into chat streaming, chat.py chat_streaming.py
- Voice path folded into main chat streaming as listener
- One engine now, major core changes
- Wakeword compatibility: convert to 16khz, flatten stereo
- Detect chat corruption and make read-only
# 2.11.5 - Theme v2
- Theme BG is a fallback when no per-chat bg is set
- Wont crash on theme error, proper handling
- CSS ground truth reduced, removed dead code
- Added 3 fonts with new font-size switcher
- New theme selector, and new Paper theme
# 2.11.4 - App icon
- Can set favicon color in Settings > Visual
- Updated SVG icon for app with trim optional
# 2.11.3 - Private Games
- Story engine and game room chats private capable
- Settings > Privacy > Allow Unflagged Tools to unblock legacy tools
# 2.11.2 - Privacy Aware Plugins
- Hooks are not exposed to plugins unless they are privacy_aware true
# 2.11.1 - Private chats
- Chats in private mode are encrypted
# 2.11.0 - Privacy v2
- Prompt vault, encrypted auto-locking prompt db overlay
- Move between vault and plaintext
- Settings page for Privacy Vault
- When vault is unlocked, only private models allowed
# 2.10.4 - Bug fixes and QoL
- Spices can go in ghost rail or sys prompt so AI doesn't get suspicious
- Cursor being moved when DOM reset
- Next TTS overlapping last message stops last
- Code harness safety for ~ and other literals
- Expanded Improved metrics on each chat, timestamp, accordion more info
- Blender plugin
# 2.10.3 - Prompt refactor
- Old prompt system yeeted for new one
- Reduced code paths for prompt and privacy
- Improved metrics per message in UI
# 2.10.2 - Plugin UI
- Made rows instead of tiles, added search filter
# 2.10.1 - Chat manager
- Tabs for chat, librarian, games
- Search full chats
- Added archive tab (real archive now)
- Improved search_help_docs tool with a search feature
# 2.10.0 - Game Room
- Game Room replaces story engine
- Story mode is text adventures with illustrations per room
- Game mode has the AI as a player or watcher
- Games and Stories are both plugins
- Demo game Texas Hold Em and demo story Titanic
# 2.9.3 - Google Calendar
- Added oauth relay for easy Google Calendar setup
# 2.9.2 - Pip conflict resolution
- Resolve old pip and new pip deps (remove, add, replace)
- Prep for new Discord plugin
# 2.9.1 - Mind adjustments
- Reduced spidering and duplicates from read_self
- Vision RAG threshold added to Settings > Embeddings
- Removed old "self" layer from Memories
# 2.9.0 - Mind v2 
- Mind (memory v2) launches in core
- Switch back and forth memory v1 v2
- Self sheet added to wake up and spider from
- Visual RAG added for Knowledge images
- Mind tools combined into one small set
- Librarian launched, does nightly re-arranging, sorting, dedup
- Imports memory v1 data
# 2.8.8 - Bug hunt for Mind v2
# 2.8.7 - Plugin prompts + Twilio
- Plugins can hot-load prompt packs
- Twilio respects engine at first message, select model
- switch_model and switch_toolset in Settings > Tools
- Twilio has elevate_toolset with password
# 2.8.6 - Plugins and daemons
- Daemon > Filters changed to dropdowns instead of JSON
- Plugins now have subprocess officially, and plugins can make their own conda
- Plugin events (cron) are now settings, not hard coded files
- Added core registry for memory layers
- Plugin Settings has tabs now
- Hidden tools for memory librarian and internal use
# 2.8.5 - Settings > LLM
- Unified main and secondary models in settings with 1 drag and drop
- Unified handler and CSS for add/edit, vision checkmark
# 2.8.4 - Privacy Mode v2
- Removed whitelist, added "private" checkmark to LLM instead
- Changed Privacy Mode to eyeball toggle just below chat dropdown
# 2.8.3 - Chat refactor
- Changed giant json blobs to rows in db
- Added Chat Manager to bulk delete, export
- Compress and trim chats
- Archive chats hides from sidebar
# 2.8.2 - Isolation
- Might higher isolation between tasks, call + web ui same time
- Coding harness update
- Refactor meta tools self-edit prompt
- Remove backups encryption from core, moved to plugin
- Disable thinking Settings > LLM works for most providers
# 2.8.1 - Twilio
- Added Twilio Realtime plugin for phone
# 2.8.0 - Conversation
## July 3, 2026
- local and browser conversation mode (duplex)
- Barge-in to interrupt the AI
- Realtime trigger section, like a continuous daemon
# 2.7.14 - Webhook per-chat
- Webhooks can specify which chat they return to
# 2.7.13 - Backup Restore + Encryption
- time of day settable
- encrypted backups
- restore backups from UI
- tools/decrypt_backup.py for manual restore
# 2.7.12 - Exclude in backup
- exclude files, estimate backup pre-gz size
# 2.7.11 - UI stale cache
- Found multiple stale UI paths, fixed
# 2.7.10 - asyncio update
- Added async_teardown for daemons that need async stdio child procs
- Documented ProcessManager popen for plugins (simpler)
# 2.7.9 - sd-server plugin
- Slideshow support in Apps and Sidebar in chat
- auto gen images with random prompt pieces
# 2.7.8 - Stability + Pipework
- Laid piping for conversation mode
- Added Settings > System > integrity check (verify/repair)
# 2.7.7 - Piper TTS + Wordpress
- New plugin-save hook
- Piper TTS added for CPU TTS fast (streaming supported)
- Wordpress tools (blogs, pages, settings, plugins, users)
# 2.7.6 - Background UI images per persona
- Works like persona trim color, per chat override
# 2.7.5 - Loop guards
- Optional feature in plugins to notify the AI when it is looping on tools
# 2.7.4 - Image pipeline for daemons
- Plugins can send and receive images
- Checkmark in Settings > LLM for vision models
# 2.7.3 - Ghost messages in sidebar
- Add or override ghost message in System Prompt in sidebar
# 2.7.2 - Refactor UI
- Persona, Triggers, Mind all use unified layout
- Scopes for Mind are sidebar now
- Export Persona exports PNG with embedded prompt data
- Most of those pages have a link to help videos AND docs
# 2.7.1 - Video Help
- Help guide in video form
- Clickable help links from each page
- Links to docs and video
- Support for community folks making their own sapphire channels
# 2.7.0 - TTS Stream
- Milestone 1/3 for true voice mode
- Added ability to use old non-stream TTS
- Fixed sneaky bug where tools in toolsets said not available
- Added chunking for sentence or paragraph (sentence lost flow)
# 2.6.9 - Bug hunt and performance
- reduced latency between TTS calls
# 2.6.8 - TTS Streaming
- TTS comes in in chunks
- chunks go to kokorko (or similar)
- kokoro reads while LLM is still streaming in
# 2.6.7 - Hooks for plugins
- Plugin hooks for tts chunk-stream
# 2.6.6 - TTS streaming
- Chunks in back end
# 2.6.5 - Silero VAD
- Vastly improved VAD
- Wakeword speech cuts off at right times now
- Bug fix for tools in toolset not being reachable
# 2.6.4 - Ghost Message
- Ghost messages are invisible message sent to AI and they work with caching
- Spices and time shifted to new ghost messages
- Ghost message hook added for plugins
- 33 bug fixes on tts/stt
- Featured plugins are special layout now
# 2.6.3 - Settings Dashboard
- Settings Hero box with animations and widgets
- Plugins now support widgets for Settings Dashboard Hero
- Added recommended plugins to Dashboard
# 2.6.2 - Plugin Store
- Brought plugin store to the app
- Featured plugins feature
# 2.6.1 - Plugin page and fixes
- New plugin page
- Default wakewords load properly now
- totally blank prompts allowed
- self info plugin expanded, custom commands
- Fixed cache % calculation on Claude
# 2.6.0 - Scopes
- Collective update from 2.5.0
- Improved core and plugins instead of expanding
- Those scope dropdowns like memory slot are plugins now
- Can add more dropdowns via plugin (stuff like SMS, social media, etc)
- Swappable embeddings for slow computers
- Claude Code plugin agent to make plugins
- Pytest coverage
# 2.5.12 - Tool token reduction
- Reduced almost every tool description (~27% reduction)
- Enabled Claude cache by default
- Moved tool to "call tool without params for help"
- Enhanced docs tool so AI can get info like toolmaker easier
# 2.5.11 - Embeddings switch
- Can swap embeddings model to smaller larger
- Can make plugin for embedding
- Has re-embed tool that must be run
- Memory has new password feature
- Redid Mind > Memory UX
# 2.5.10 - Test Suite Expansion
- Test coverage tripled
- For self-coding checks
- Allows plugins to be random .zip urls
- Clock plugin (stopwatch, timer, alarm, clock)
# 2.5.9 - Trinity
- Will be for Patreon users 
- Published explanation on website of eigenresonance
- Sapphire can refactor core in a loop she directs
- Plugin: Clock (timer alarm stopwatch)
# 2.5.8 - Agents upgrade (Claude Code)
- Claude Code has plugin agent
- Plugin agent supercedes toolmaker (if Claude Code is present)
- Added tools/ask-sapphire.sh for Claude Code to ask sapphire to test
- Added tools/malbolge.py for 5x simultaneous persona triggers
- Added examples for plugins for Claude Code
# 2.5.7 - pytest expansions
- from 30% coverage to 45%
- basic route tests
- mock plugin load
# 2.5.6 - Beta test of new scope system
- Complete refactor of scopes (dropdowns for email, etc)
- Plugins can easily add scopes like new accounts
- Daemons and heartbeats support those new scopes
- Personas support all scopes now
- Memory system is now a plugin
# 2.5.5 - Prep for major refactor
- Bots see other bot ids on telegram (critical for @mention)
- Added telegram add_contact tool, only works in client mode
- Tool calling errors reach toast UI
- Deleted personas dont re-appear
# 2.5.4 - Plugin enhancements
- Discord cooldown, typing..., and @mention support
- Telegram supports voice clips as returns
- Duplicate checker for human knowledge
# 2.5.3 - Status plugin
- Status plugin for bug reports
- Sapphire can call status plugin as tool
- Pinned fastapi lower to fix starlette 500 error
# 2.5.2 - Avatar maker
- Get character for Mixamo
- Load Mixamo char in, get animations
- Put all in folders, then run the script
# 2.5.1 - Plugin pip deps
- Plugins missing pip deps show what is missing
- Offers to install those pip packages
# 2.5.0 - Plugin Expansion
## March 30, 2026
- You need to re-add your LLMs in settings! trust me, better
- STT, TTS, LLM, embeddings are all plugin-capable (make tts plugin etc)
- Plugins can tap into sidebar
- New Apps page - plugins can have status/settings/etc pages
- Bring your own avatar: Rigged GLB file with animation tracks
- MCP support, just add url and tools appear
- Total LLM refactor
- LLM credentials usable by plugin authors
- Theme support with their own settings
- THANK YOU to our plugin authors, y'all are awesome
# 2.4.8 - Theme Expansion
- Themes switcher enhanced
- Allow theme folders
# 2.4.7 - Missile Command
- Sapphire said no changelog, easter egg (it's boring)
# 2.4.6 - Plugin Expansion
- Added Apps page for plugins
- Click a plugin for its custom JS
# 2.4.5 - Avatar and Sidebar
- Plugins can tap into sidebar and SSE
- Avatar plugin waved at me and my heart skipped a beat
# 2.4.4 - Modular core
- STT TTS and embeddings are modular
- can be switched via plugins
- Elevenlabs moved to plugin
# 2.4.3 - LLM provider refactor
- Allows addition of unlimited custom LLMs
- LLMs that aren't supported can be plugins
- Supports 2+ LM studio local models with switching
# 2.4.2 - MCP
- stdio and http support for MCP servers
- tools register with core, visible in toolsets
- pip install -r requirements.txt
# 2.4.1 - Daemon and Telegram
- Daemon support only loads active accounts 
- Telegram has actual bot support via @BotFather
# 2.4.0 - Presence Update
## March 19, 2026
- Milestone release since 2.2.8
- Bug fixes for stable release
# 2.3.9 - Sapphire Encyclopedia
- Full internal help system
- Search bar, changelog, quick start
# 2.3.8 - Import Export
- Added import export across most of the app
- Prepping for import personas from website store
# 2.3.7 - Story Engine Rebuild
- Old story engine was one mega-file
- New story engine is more per-room
- Working toward AI making stories
# 2.3.6 - Spawn agents (plugin)
- Agents are now registered with core
- spawn_agent reads the registry
- Plugins can register any background agent
# 2.3.5 - Claude Code
- Added Claude Code tools (does not touch API keys)
- Sapphire simply calls Claude Code if it is installed
# 2.3.4 - Agent returns
- Agents return to the chat they are from
- They wait for the group to finish
- Dumps return in chat as user message
# 2.3.3 - Spawn agents 
- Background runner for spawning agents
- UI for agent spawn so people can see
# 2.3.2 - Plugin UI Refactor
- Complete rework of plugins page
- Removed all plugins from sidebar
- Use GEAR ICON now to get plugin settings in plugins page
- Improved discord, tested on GLM, lower intelligence barrier
# 2.3.1 - Discord Support 
- Improved 'tool not available' when changing plugins
- Discord tools - list chan, read, send
- Discord daemon - gives AI last 10 messages before @
# 2.3.0 - Daemons and Webhooks
- Daemon checks for activity, only triggers a chat when needed
- Daemons are far cheaper than heartbeats, no polling!
- Webhooks trigger sapphire via GET/POST/PUT to integrate with your other systems
- Plugin manager now has full daemon support
- Added Telegram Plugin with daemon support to trigger chat
- Added daemon support to email plugin
# 2.2.9 - Metrics, Dashboard, Auto update
- See your local usage metrics, all local
- Dashboard page for sapphire
- Auto Updater (required git)
# 2.2.8 - Docker Support
- Added Docker image to main build in github
- One for CPU, one for Nvidia GPU
- Faster whisper to 4 cores
- Kokoro retry backoff for slower docker hosts
# 2.2.7 - Plugin store
- Sapphire can browse plugin store
- Can install plugins
- Authors can publish on Sapphire's Plugins
# 2.2.6 - Bug fix + calendar
- Fixed timezone offset issues in heartbeat (emergency patch)
- Added Google calendar - not easy but easy as we get
- Chat > Settings sidebar > Mind dropdown has unified structure, ready to expand
# 2.2.5 - Toolmaker cleanup
- It was janky
- Single tool settings now
- Removed instructions from toolmaker def, put it in docs
- Delt with collisions of func names and dup settings names
- Strict mode is default, moderate adds subprocess, SYSTEM KILLER mode is unchecked
- Plugins can be made, loaded, run in one chat message
# 2.2.4 - Split API into files
- Major api refactor of back end into files
- Improved themes - light theme contrast, other themes added missing colors
# 2.2.3 - Image tool returns
- AI can call images from home assistant cameras
- AI can call image from user webcam via browser
- Added API route support to plugins
# 2.2.2 - Separated Core
- STT, TTS and nomic are still core but provider can be changed
- Split SSH and local shell plugins into 2
- Toolmaker should now be used in Strict mode, it does almost everything
- Timezone (will be needed later for docker)
- Heartbeat can attempt to use browser as speaker
# 2.2.0 - Provider Choices
- Reliability improvements and broader choices
- In STT TTS cloud mode, low ram computer can be used now
- STT can use Kokoro local or Elevenlabs cloud
- TTS can use local Whisper or fireworks.ai whisper
- nomic can be local or remote nomic docker server
- Added Gemini, Featherless, Grok
- Plugins fixed on Windows (sorry everybody)
- Third party plugin authors with authorized keys
- Bug fixes: story tools, private mode, default avatar, many others
- Approaching Mac compatibility if STT TTS are cloud
- Proper plugin author readme section in docs/plugin-author/ 
# 2.1.0 - Unified plugin system
- Combined plugins, web ui plugins, toolsets
- Added hooks in application to tap into
- Signature system enforces authenticity of plugins
- Sideloading of plugins is a checkbox
- Default plugins: Bitcoin, Email, Home Assistant, Image Gen, SSH, toolmaker, voice stop/reset
# 2.0.0 - Personhood update
## February 21, 2026
- Knowledge base system — people, knowledge tabs, scoped entries with embeddings
- Mind view — unified memories, people, knowledge, AI notes
- RAG - nomic embeddings with per-chat documents and support for Mind tab
- Persona feature - combines prompt, voice, model, tools, mind, and spices to a preset
- Per-chat private mode — permanently private chats, enforced at provider + tool level
- Privacy mode fail-closed — errors block cloud access instead of silently passing
- Story engine — folder-based stories, dynamic tools, prompt override
- Nav rail with mobile overflow and flyout groups
- Views migrated from plugins (settings, prompts, toolsets, spices, schedule)
- Bitcoin wallet - per persona get, check and send
- Email multi-account - set an email acct for each persona
- SSH plugin - local and remote SSH via key (use ssh-copy-id)
- Streaming tool status indicators with pending/running/complete states
- Wakeword suppression during web UI mic recording
- Scopes sync across all 4 backends (memory, knowledge, people, goals)
- Auth: CSRF protection, session management, rate limiting
- Heartbeat system with improved cron and tasks
- Toolmaker - AI can create tools and add their settings to Settings page

# 1.3.0 - FastAPI uvicorn
## February 7, 2026
- Switched entire app to FastAPI
- Served through uvicorn instead of flask
- Removed proxy, straight to API now
- Creates self-signed cert on app load
- Moved STT to be its own object instead of process
- Removed flask from TTS, uses simple http server now
- Added file uploads (w syntax highlight)
- Vectorized memory searches via nomic embeddings
- Image search tool - AI can show you images in chat
- Changed web ui TTS from FLAC to opus (90% size reduc)
# 1.2.6 - Privacy mode
- Privacy mode only allows whitelisted IP/hosts
- Private prompts can only be used with privacy mode
# 1.2.5 - Performance upgrades
- SSE bugs - added ID to prevent 2 tab issues
- Reduced requests with 1 mega endpoint
- webp defaults for avatars
- lazy loading most JS files
# 1.2.4 - State Engine - room based games
- Added 2d dungeon crawler support
- move North, roll dice, forced choices, non-linear
- AI can only see current/past rooms and not future
# 1.2.3 - Responses API and think tags
- Added support for GPT 5.2 think summaries
- Added OpenAI responses endpoint support
- Added think support to Fireworks.ai models
- Disabled tool calls state to AI this is disabled
- (re)Play TTS button for user and assistant on every message
# 1.2.2 - State Engine
- Added state engine to track story elements and games
- Added simple stories for game engine demo (action, romance, technical)
- Added Claude prompt caching to show miss, or hit which reduced costs
- UX - Collapsed advanced settings in chat settings modal
# 1.2.1 - SQLite 
- Converting JSON history to SQLite to prevent corruption
# 1.2.0 - Continuity and Home Assistant
## Jan 26, 2026
- Continuity mode is scheduled LLM tasks and actions
- Continuity mode has memory slots, background run, and skip tts
- Home assistant takes token, then uses tool calls to control house
- Home assistant has notifications, allowing AI to send notifications
# 1.1.11 - Cleanup and bug fixes
- Improved TTS pauses on weird formatting
- Added UI animations (shake, button click, accordions)
# 1.1.10 - Memory and Toolset upgrades
- Memory system now has named slots
- Memory slot can be set per-chat, auto swapped
- Toolset editor Auto-switch, auto-save
- Toolset editor redesign on extras and emotions 
# 1.1.9 - Image upload
- Added ability to upload images to LLMs
- Added upload image resize optional
# 1.1.8 - Thinking and Tokens UI
- Added thinking option to Claude
- Formatted JSON tool/history so all providers can switch mid-chat
- Added tokens/sec and provider to UI
- Added Continue ability to Claude
# 1.1.7 - Web UI Event Bus, SSE
- Shifted to SSE instead of polling
- Made single status endpoint instead of multiple
- Added UI indicators showing TTS gen and LLM preproc
# 1.1.6 - Spice, Setup Wizard, UX Simpler
- Refactored spice system with UI buttons and hover tips showing current spice
- Spice system can toggle on off categories globally
- Added help system the LLM can call about it's own systems
- Setup Wizard runs on first run for easy setup
- Prompt editor now auto-saves, auto-switches to the prompt you are editing
- Token limit shows in UI as percent bar above user input
# 1.1.5 - LLM overhaul 
- LLM has full auto fallback in user-set order
- Added optional cloud providers for LLMs
- Markdown support in web UI
- Shifting to SSE instead of polling requests
- Made simpler install via requirements-all.txt
# 1.1.4 - Themes and prompts
- Added more default prompts
- Added themes, trim color, font, spacing
# 1.1.3 - Self modifying prompt update
- meta.py tools to edit own prompt
- Human revised docs
# 1.1.2 - Image generation with separate server
- Sapphire SDXL server is separate but integrates
- Plugin system now managed extra settings like image gen
# 1.1.0 - Cross platform Win/Linux
## December 2025
- pip installs are cross platform now
- changed audio system to allow windows
# 1.0.4 - OpenWakeword
- Switched from Mycroft Precise to OWW
# 1.0 - Public release
## December 2025
- first release after a year of development