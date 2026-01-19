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
- pip installs are cross platform now
- changed audio system to allow windows
# 1.0.4 - OpenWakeword
- Switched from Mycroft Precise to OWW
# 1.0 - Public release
- first release after a year of development