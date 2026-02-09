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

**403 Forbidden**
- Try http:// and https://
- Delete cookies for this site
- Test in private browsing window
- Delete secret key `~/.config/sapphire/secret_key` 
- Restart Sapphire app

**Blank page or "Unauthorized"**
- Clear browser cookies for localhost:8073
- Try incognito window
- Delete `~/.config/sapphire/secret_key` and restart the app

**Certificate warning on first visit**
- Expected with self-signed certs. Accept once (Advanced → Proceed) and the browser remembers it.
- Cert is persistent (valid 10 years), stored in `user/ssl/`. Not regenerated on restart.

**UI loads but chat doesn't respond**
- Check browser console (F12) for errors
- Verify LLM server is responding: `curl http://127.0.0.1:1234/v1/models`

## Audio Issues

**Sample rate detection error on speakers**
- Cheap USB speakers may not support certain sample rates, so choose "auto detect" or "default" 
- Auto detect will route through OS audio defaults, which can resample the audio to be compatible

**Default audio seems to not work via TTS**
- Gear icon > App Settings > Audio - then change your device to auto-detect
- Selecting specific audio devices like default or a specific mic can work too
- Auto-detect sometimes fails test due to it being open, but it may actually work so try it

**Wakeword recorder does not detect when to stop recording (webcam mic)**
- Change your Recorder Background Percentile in STT settings higher
- This is VAD voice activity detection thinking your BG noise is speech so it keeps recording
- Lapel/lav and headsets mics may be ~10-20, but with webcam or other weak mics, raise to ~40

**No TTS audio output**
- Verify TTS is enabled in Gear icon > App Settings > TTS
- Check TTS server started: `grep "kokoro" user/logs/`
- Test system audio: `aplay /usr/share/sounds/alsa/Front_Center.wav`
- Check PulseAudio/PipeWire is running

**STT not transcribing**
- Check STT is enabled in Gear icon > App Settings > STT
- For GPU: verify CUDA is working (`nvidia-smi`)
- Try CPU mode: set faster whisper device to cpu in settings
- Turn up your mic volume to 70% or 100%
- Check your OS/system default mic - it tries to read from this
- If Web UI, check browser mic permissions AND windows mic permissions

**Wake word not triggering**
- Check which wakeword you are using in settings
- Make sure you pip installed requirements-wakeword.txt
- Check wakeword is enabled in settings, reboot app after
- Turn your mic volume up to 70-100%
- Set system mic to the mic you want wakeword on
- Try using Hey Mycroft as a wakeword instead of Hey Sapphire
- Reduce sensitivity threshold to 0.5
- Test a different mic

## Prompt issues
**If you broke your default prompts**
- Gear icon > App Settings > System tab 
- You can reset all prompts to default, or merge the defaults back into yours

## LLM issues
**LM Studio (simple) test failing**
- Open LM studio, click Developer in lower left to show advanced options, click green Developer tab, toggle server on, load a model
- Go back to Sapphire: Gear icon > App Settings > LLM > LM Studio > test button

**Anthropic Claude not responding**
- conda activate sapphire && pip install anthropic
- Check API key (some are for Claude Code only)
- Put new API key in Gear icon > App Settings > LLM > Claude

**No thinking/reasoning visible**
- Not all models support thinking. Check provider supports it.
- Claude: Enable "Extended Thinking" in LLM settings
- GPT-5.x: Uses Responses API, set reasoning_summary to "detailed"
- Fireworks: Only works with thinking-enabled models (Qwen3-Thinking, Kimi-K2-Thinking)
- Local models via LM Studio: May need specific model that outputs `<think>` tags

**Thinking breaks when switching providers**
- Thinking should transfer between chats 
- Note, models cannot see their past think tags in some cases (Claude)
- Check if the model you are on supports thinking

**Claude prompt caching not working (always MISS)**
- Spice changes system prompt every turn — disable if caching matters
- Datetime injection also breaks cache
- "State vars in prompt" breaks cache (changes on state updates)
- Check logs for `[CACHE] Dynamic content detected - tools only, system prompt not cached`

**Claude caching enabled but costs seem high**
- First request is always a MISS (writing to cache costs 25% more)
- Cache expires after TTL (5m default, can set to 1h)
- If prompts change often, cache never gets reused

## Tool/Function Issues

**"No executor found for function"**
- Function exists in toolset but Python file missing or has errors
- Check `functions/` directory for the module
- Look for import errors in logs

**Web search returns no results**
- Rate limited by DuckDuckGo. Wait and retry.
- If using SOCKS proxy, verify it's working (see SOCKS.md)
- Enable verbose tool debugging in settings for more logging

## Continuity Issues

**Task not running at scheduled time**
- Check enabled toggle is on (green) in the Tasks tab
- Verify cron syntax is correct (minute hour day month weekday)
- Check cooldown hasn't blocked it (see Activity tab for "skipped - cooldown")
- Low chance % may have rolled unfavorably (see Activity tab for "skipped - chance")

**"Invalid cron schedule" error**
- Cron format: `minute hour day month weekday`
- Use `*` for any, `*/N` for every N, `1-5` for ranges
- Example: `0 9 * * *` = 9:00 AM daily
- Weekday: 0 or 7 = Sunday, 1-6 = Mon-Sat

**Task runs but no TTS audio**
- Check "Enable TTS" is checked in task editor
- Verify TTS is working for regular chat first
- Background tasks still use TTS if enabled

**croniter not installed**
- Run: `pip install croniter`
- Continuity requires this package for cron parsing

## Home Assistant Issues

**Connection test failing**
- Verify URL includes port (e.g., `http://192.168.1.50:8123`)
- Check Home Assistant is running and accessible from this machine
- Try the URL in a browser first

**"401 Unauthorized" error**
- Token is invalid or expired
- Create a new Long-Lived Access Token in HA profile
- Make sure you copied the full token (~180+ characters)

**Token shows "too short" warning**
- HA tokens are typically 180+ characters
- If shorter, you may have copied it incorrectly
- Create a new token and copy the entire string

**Entity not found**
- Check exact spelling (entity_id or friendly name)
- Entity may be blacklisted - check blacklist patterns
- Use `ha_list_lights_and_switches` to see available entities

**Notifications not sending**
- Find your service in HA: Developer Tools → Actions → search "notify"
- Enter just the service name without "notify." prefix
- Example: `mobile_app_pixel_7` not `notify.mobile_app_pixel_7`
- Make sure HA companion app is installed on your phone

**HA tools not available**
- Add Home Assistant functions to your active toolset
- Check Settings → Plugins → Home Assistant is configured
- Test connection before trying to use tools

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