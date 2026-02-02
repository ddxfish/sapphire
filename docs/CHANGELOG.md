# 1.2.4 - State Engine upgrades
- SE bugs - UI and UX

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