# 2.0.0 - Knowledge, Privacy, and Polish
## February 21, 2026
- Knowledge base system — people, knowledge tabs, scoped entries with embeddings
- Mind view — unified memories, people, knowledge, AI notes
- Per-chat private mode — permanently private chats, enforced at provider + tool level
- Privacy mode fail-closed — errors block cloud access instead of silently passing
- Story engine — folder-based stories, dynamic tools, prompt override
- Nav rail with mobile overflow and flyout groups
- Views migrated from plugins (settings, prompts, toolsets, spices, schedule)
- Bitcoin wallet, email multi-account, SSH plugins
- Streaming tool status indicators with pending/running/complete states
- Wakeword suppression during web UI mic recording
- Scopes sync across all 4 backends (memory, knowledge, people, goals)
- Auth: CSRF protection, session management, rate limiting
- Bug hunt: 17 issues identified, 15 fixed, 2 deferred
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