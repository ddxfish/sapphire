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