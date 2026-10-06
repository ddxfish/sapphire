# Sapphire Features

## Core
### Chat
SSE streaming
Tool call loop
Per-chat loadout
Ghost messages
Thinking blocks
Image upload vision
CLIP describe fallback
File attachments
RAG docs per-chat
### History
SQLite message rows
Edit regen delete
Trim compress
Import export
Content search
### LLM
Claude OpenAI Gemini
LMStudio custom OpenAI-compat
Plugin LLM providers
Auto fallback order
Per-chat model
Prompt caching
Token metrics
### TTS
Kokoro local
Sentence streaming
Per-chat voice/pitch/speed
Plugin providers
### STT
faster-whisper local
Fireworks cloud
Silero VAD
Hallucination filter
### Wakeword
openWakeWord
Custom models
Hot swap
### Conversation
Hands-free speech
Barge-in
Browser phone WebSocket
### Prompts
Assembled pieces
Monolith
Self-edit tools
Persona loadout presets
Persona PNG cards
Spice prompt randomizer
### Tools
Toolsets per-chat
Web search fetch
Wikipedia research
Web image view
Notepad
Net checks
schedule_task
set_scene set_motion
set_voice
list_tools
search_help_docs
switch_model switch_toolset
### Scopes
Per-chat data scopes
Global overlay
### Triggers
Heartbeat
Cron tasks
Daemons event listeners
Webhooks
Realtime rules
Background agents
### Devices
Computer vol/screen/power
Satellites Pi ESP32
Satellite mic/speaker/light/cam
Other Sapphire instances
Presence tracking
Dual-wake arbiter
Speak all devices
### Privacy
Vault passphrase
Encrypted private chats
Local-only providers
Voice privacy gate
SOCKS5 proxy
LAN/WAN split
### Safety
Local backup rotation
Backup folder choice
Sealed backups option
One-click restore
Login API tokens
Encrypted credentials
Core integrity check
### UI
Web UI :8073
Themes fonts
Scenes motions
Dashboard widgets
Mind view
Chat manager
### System
Settings hot reload
Auto-updater
Docker
REST API
MCP server

## Plugins
### Mind
Memory embeddings FTS
Keyed memories
Knowledge tabs
People contacts
Goals subtasks journal
Mind Palace layers
Identity self sheet
Entity cards
Librarian passes
Library docs images
### Voice
Twilio VOIP
ElevenLabs TTS
gTTS TTS
Piper local TTS
Voice commands
TTS captions
Wakeword maker
### Comms
Email IMAP/SMTP
Discord bot
Telegram bot/client
Google Calendar
### Build
Claude Code
Coding harness
Toolmaker custom tools
GitHub repos issues
SSH remote commands
MCP client
WordPress admin
### Media
ComfyUI images
SD server images
Blender bpy control
Webcam
Screenshot
MIDI synth music
3D avatar
### Life
Home Assistant
Clock timers alarms
Bitcoin wallet
### Games
Game Room host
Story packs
Texas Hold'em
### System
Status dashboard
Plugin store
Remembrance offsite backups
Widget samples

## Plugin API
### Capabilities
tools
hooks
voice_commands
routes HTTP
settings UI
web scripts
app full page
sidebar_accordion
widgets dashboard
themes
motions
providers TTS/STT/LLM/embed
daemon event sources
schedule cron
scopes
devices drivers
games
prompts packs
memory_layers
services conda env
### Hooks
post_stt pre_chat post_chat
prompt_inject ghost_inject post_llm
tools_filter pre_execute post_execute
pre_tts post_tts on_wake
tts_stream_start tts_stream_end
tts_chunk_text tts_chunk_audio
chat_renamed chat_deleted chat_cleared
chat_vaulted provider_switched plugins_ready
### Runtime
Plugin state KV
Chat-scoped state
Reply handlers
Dynamic tool descriptions
Cadence unprompted turns
Perception next-turn frames
Tools attach files
privacy_aware flag
Plugin signing ed25519
Live toggle
user/plugins dir
Store publishing

## Reference for AI

Sapphire: self-hosted AI companion app, the system you run in. Local-first, private, voice-first, plugin-extensible. github.com/ddxfish/sapphire
Has: chat, LLMs, TTS, STT, wakeword, phone, memory, tools, triggers, agents, devices, plugins.
Feature list: search_help_docs(doc_name='self', full=true)
Search docs: search_help_docs(query='tts')
Read doc: search_help_docs(doc_name='voice')
Build: toolmaker (tools), plugin-author/ai-reference (plugins)
