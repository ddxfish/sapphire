# Google Translate TTS

Free text-to-speech using Google Translate's TTS endpoint. No API key, no account, no config.

## Setup

1. Install: `pip install gTTS`
2. Enable the plugin in **Settings > Plugins**
3. Select "Google Translate (Free)" in **Settings > TTS**
4. Test TTS

## Limitations

- English only (can be extended to other languages)
- Robotic quality — this is Google Translate, not a neural voice
- Speed control limited to normal and slow
- Requires internet connection
- MP3 output format

## When to use

- Testing that TTS works without configuring API keys
- Quick demo setups
- Environments where Kokoro is too heavy and ElevenLabs costs money
