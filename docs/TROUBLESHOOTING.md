# Troubleshooting

## Startup Issues

**"Connection refused" or "No LLM endpoints available"**
- LLM server not running. Start LM Studio/llama-server first.
- Wrong port in settings. Default expects `http://127.0.0.1:1234/v1`
- Check LM Studio has "Start Server" enabled and "Allow LAN" if needed.

**"bcrypt module not available"**
```bash
pip install bcrypt
```

**"Failed to load module" warnings on startup**
- Usually harmless. Missing optional dependencies for unused features.
- If a specific feature is broken, check logs for the actual error.

## Web UI Issues

**Blank page or "Unauthorized"**
- Clear browser cookies for localhost:8073
- Try incognito window
- Delete `~/.config/sapphire/secret_key` and restart the app

**Certificate warning every time**
- Expected with self-signed certs. Click through it.
- For proper certs, use a reverse proxy (nginx/caddy) with Let's Encrypt.

**UI loads but chat doesn't respond**
- Check browser console (F12) for errors
- Verify LLM server is responding: `curl http://127.0.0.1:1234/v1/models`

## Audio Issues

**Recorder does not detect when to stop (webcam mic)**
- Change your Recorder Background Percentile in Wakeword settings higher
- This is usually VAD voice activity detection thinking your BG noise is speech
- Lapel and headsets mics may be ~10-20, but with webcam or other weak mics, use ~40

**No TTS audio output**
- Verify `TTS_ENABLED: true` in settings
- Check TTS server started: `grep "kokoro" user/logs/`
- Test system audio: `aplay /usr/share/sounds/alsa/Front_Center.wav`
- Check PulseAudio/PipeWire is running

**STT not transcribing**
- First run downloads models (can take minutes)
- Check `STT_ENABLED: true`
- For GPU: verify CUDA is working (`nvidia-smi`)
- Try CPU mode: set `FASTER_WHISPER_DEVICE: "cpu"`

**Wake word not triggering**
- Install Mycroft Precise engine (see README)
- Raise `WAKE_WORD_SENSITIVITY` (default 0.46, try 0.55)
- Check mic permissions and levels
- Test mic: `arecord -d 3 test.wav && aplay test.wav`

## Tool/Function Issues

**"No executor found for function"**
- Function exists in toolset but Python file missing or has errors
- Check `functions/` directory for the module
- Look for import errors in logs

**Web search returns no results**
- Rate limited by DuckDuckGo. Wait and retry.
- If using SOCKS proxy, verify it's working (see SOCKS.md)

**Memory functions fail**
- Memory engine not running. It starts with main process.
- Check for socket errors: `ls -la /tmp/sapphire_memory.sock`

## Performance Issues

**Slow responses**
- LLM is the bottleneck. Try smaller model or faster hardware.
- Reduce `LLM_MAX_HISTORY` to send less context.
- Disable tools (`ability: none`) for faster pure chat.

**High memory usage**
- Large LLM models need RAM. Check LM Studio memory settings.
- STT with base Whisper models uses ~2GB.
- TTS (Kokoro) uses ~2-3GB.

## Reset Everything

Nuclear option - fresh start:

```bash
# Stop Sapphire
pkill -f "python main.py"

# Remove user data (keeps code)
rm -rf user/
rm ~/.config/sapphire/secret_key

# Restart
python main.py
```

You'll need to re-run setup and reconfigure settings.