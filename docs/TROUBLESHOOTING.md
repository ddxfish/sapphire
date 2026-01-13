<!-- AI_INCLUDE_FULL: Common issues and fixes for audio, LLM, web UI, and performance -->
# Troubleshooting

## Startup Issues

**"Connection refused" or "No LLM endpoints available"**
- LLM server not running. Start LM Studio/llama-server first.
- Wrong port in settings. Default expects `http://127.0.0.1:1234/v1`
- Check LM Studio has "Start Server" enabled and "Allow LAN" if needed.

**"Failed to load module" warnings on startup**
- Usually harmless to core functionality. Missing optional dependencies for optional features.
- If a specific feature is broken, check logs for the actual error.

## Web UI Issues

**Blank page or "Unauthorized"**
- Clear browser cookies for localhost:8073
- Try incognito window
- Delete `~/.config/sapphire/secret_key` and restart the app

**Certificate warning every app restart**
- Expected with self-signed certs. SSL is enabled.

**UI loads but chat doesn't respond**
- Check browser console (F12) for errors
- Verify LLM server is responding: `curl http://127.0.0.1:1234/v1/models`

## Audio Issues

**Wakeword recorder does not detect when to stop (webcam mic)**
- Change your Recorder Background Percentile in Wakeword settings higher
- This is VAD voice activity detection thinking your BG noise is speech so it keeps recording
- Lapel/lav and headsets mics may be ~10-20, but with webcam or other weak mics, raise to ~40

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
- Turn up your mic volume to 70%
- Check your system default mic - it tries to read from this
- If Web UI, check browser mic permissions AND windows mic permissions

**Wake word not triggering**
- Check which wakeword you are using in settings
- Make sure you pip installed requirements-wakeword.txt
- Check wakeword is enabled in settings, reboot app after
- Turn your mic volume up
- Set system mic to what you use
- Try using Hey Mycroft
- Reduce sensitivity threshold to 0.5

## Tool/Function Issues

**"No executor found for function"**
- Function exists in toolset but Python file missing or has errors
- Check `functions/` directory for the module
- Look for import errors in logs

**Web search returns no results**
- Rate limited by DuckDuckGo. Wait and retry.
- If using SOCKS proxy, verify it's working (see SOCKS.md)
- Enable verbose tool debugging in settings for more logging

## Performance Issues

**Slow responses**
- LLM is the bottleneck. Use a 4B or smaller model to test
- Reduce `LLM_MAX_HISTORY` to send less context, it gets slower over time
- Kokoro is slow(er) on my i5-8250u. Nvidia is way faster, or faster CPU too. 

**High memory usage**
- Large LLM models need RAM. 4B model needs ~7GB after KV cache.
- Use quantized models in Q4_K_M to reduce memory
- STT with base Whisper models uses ~2-3GB.
- TTS (Kokoro) uses ~2-3GB.

### Troubleshoot Nvidia 5000 series on Linux
Try Sapphire first. Most won't need this. Only do this if STT and TTS are not using your GPU. It's a nightly build of torch with cuda 12.8 that may work better with the Linux open-kernel drivers if you get stuck. Don't use this if you don't need it.

```bash
pip install torch torchaudio --index-url https://download.pytorch.org/whl/nightly/cu128
```

## Reset Everything (Delete data)

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