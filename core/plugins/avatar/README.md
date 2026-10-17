# Avatar Plugin

3D avatar for Sapphire — an animated presence that reacts to her state in real time (listening, thinking, speaking, tool use) and can be deliberately animated by the AI via inline tags. Supports ARKit face blendshapes.

## How Sapphire Drives the Avatar

Once an avatar is selected, it animates on its own in two ways:

**1. Automatic reactions to Sapphire's state.** The avatar reacts in real time to what's happening — `listening` while you speak (STT), `processing` while she thinks, `speaking` during TTS, `toolcall` while a tool runs, `wakeword` on detection, `user_typing` while you type, plus `agent` and `cron` states for background activity. This is wired to the event bus; you don't configure it.

**2. Deliberate animations the AI triggers.** Sapphire can play a specific track by emitting an inline tag in her reply:

- `<<avatar: wave>>` — plays the track **once**, then returns to normal behavior
- `<<avatar: sleep loop>>` — **holds** the pose until she emits another `<<avatar: ...>>` tag (good for sustained moods: sleep, dance, meditate)
- `<<avatar: nod 2.5s>>` — plays for a set **duration**

She only uses track names that exist in your model — the plugin injects the available list into her prompt so she can't invent one. The tags show in the chat as part of her expression.

**Priority rails.** Competing animation requests resolve through a priority stack rather than fighting: a deliberate AI `loop` tag and a passing one-shot reaction sit on different rails, so an intentional pose isn't stomped by a routine state change, and when a higher rail clears, the avatar falls back to the rail below it (down to idle).

**Idle behavior.** With nothing else happening, the avatar plays from a weighted **idle pool** and occasionally overlays a "variety" animation for liveliness. Idle is **time-of-day aware** — buckets (e.g. day/night) can use different resting poses, and a bucket can be marked **quiet** to suppress variety overlays (calmer at night). All of this is configurable in **Settings → Avatar**.

## Building Your Avatar

Get a character from Mixamo, grab some animations, run the build tool — done. This requires Blender on your computer, but it is done automatically and you don't ever have to see it.

**Already have a rigged `.glb` with animation tracks?** Skip the build entirely — upload it directly in **Settings → Avatar** (the plugin auto-maps its tracks to states; you can adjust the mapping there). The Mixamo → Blender pipeline below is only for building a GLB from separate character + animation FBX files.

### Step 1: Create Your Avatar Folder

Pick a name for your avatar and create this folder structure:

```
user/avatar/
  mychar/              <- Your avatar name (whatever you want)
    model/             <- Character goes here (Step 2)
    animations/        <- Animations go here (Step 3)
```

### Step 2: Get Your Character from Mixamo

1. Go to [mixamo.com](https://www.mixamo.com/) and sign in (free Adobe account)
2. Click **Characters** in the top nav
3. Either **upload your own 3D model** (Upload Character button) or **pick a stock character**
4. Once your character is loaded and visible in the viewport, click **Download**
5. In the download dialog: Format = **FBX**, Pose = **T-pose** (this is the default)
6. Save the FBX file into `user/avatar/mychar/model/`

You should have exactly **one file** in the model folder.

### Step 3: Get Animations from Mixamo

**Important:** do NOT switch characters. Keep your character from Step 2 loaded in Mixamo.

1. Click **Animations** in the top nav (your character stays loaded)
2. Search or browse for animations you want (e.g. "Idle", "Waving", "Thinking")
3. Preview them — click an animation to see it on your character
4. For each animation you want:
   - Click **Download**
   - In the download dialog: Format = **FBX**
   - Change **Skin** to **Without Skin** (this is the important part — it downloads just the animation, not the character mesh again)
   - Save the FBX file into `user/avatar/mychar/animations/`
5. Repeat for as many animations as you want

**Name your files descriptively** — the filename becomes the animation track name. For example, `Thinking.fbx` becomes the "Thinking" track.

### Step 4: Run the Build Tool

```bash
python core/plugins/avatar/build_avatar.py mychar
```

Replace `mychar` with your folder name. If you only have one avatar folder, the name is optional.

The tool will:
- Convert your character and animations from FBX to GLB via Blender
- Merge all animation tracks into your character
- Save the combined GLB in your avatar folder AND copy it to `user/avatar/` for the web UI

Converted files are cached — re-running only processes new FBX files.

### Step 5: Select Your Avatar

Go to **Settings > Avatar** in Sapphire and select your avatar from the dropdown.

### Requirements

- **Blender** installed (used headless for FBX conversion, no GUI needed)
  - Ubuntu/Debian: `sudo apt install blender`
  - Windows/macOS: download from [blender.org](https://www.blender.org/) and make sure `blender` is on your PATH

### What Your Folder Looks Like After Building

```
user/avatar/
  mychar/
    model/
      claire.fbx               <- Your original character (untouched)
    animations/
      Idle.fbx                 <- Your original animations (untouched)
      Waving.fbx
      Thinking.fbx
    _cache/                    <- Auto-created, converted GLBs (safe to delete)
    mychar.glb                 <- The combined GLB with all animations
  mychar.glb                   <- Copy for the web UI
```

Your original FBX files are never modified. To add more animations later, just drop new FBX files in `animations/` and run the build tool again.

## Auto-Mapping Animation Names

Animation tracks are automatically mapped to avatar states by their filename. Name your FBX files to match these keywords and they'll wire up automatically:

| Avatar State | Keywords (filename contains) |
|---|---|
| idle | idle, stand, standing, rest, default, breathe, breathing |
| processing (thinking) | thinking, think, ponder, concentrate, focus, plotting |
| typing | typing, type, keyboard, defaultanim, compose, writing, texting |
| listening | listening, listen, hear, attentive, look, lookaround, listening_nod |
| speaking | speaking, speak, talk, talking, say, attention |
| toolcall | action, use, grab, reach, attention2, interact, typing |
| happy | happy, joy, celebrate, cheer, smile, excited, victory, clapping, laughing |
| wakeword | alert, surprise, startle, notice, greeting, wave |
| wave | wave, greet, greeting, hello, hi, bye, farewell, blow_kiss |
| user_typing | curious, notice, perk, attentive, idle_curious, lookaround |
| reading | read, reading, look_down, study, typing_phone |

The `agent` and `cron` states (shown during background agent / scheduled activity) have no filename keywords — assign them manually in the Avatar settings UI if you have suitable tracks.

Example: download an animation called "Thinking" from Mixamo, save it as `Thinking.fbx` and combine it using the GLB maker in tools — it automatically becomes the animation that plays when Sapphire is thinking.

You can also manually assign tracks to states in the Avatar settings UI.

## Recommended Animations

Starter set that covers the main avatar states:

**Essential:**
- Idle (a few variants — Sapphire picks randomly for variety)
- Thinking
- Talking
- Waving
- Happy / Excited

**Nice to have:**
- Look Around (plays when listening or user is typing)
- Texting / Entering Code (plays when AI is generating a response)
- Head Nod / Head Shake (reactions)
- Shrugging, Clapping (gestures)
- Sitting, Sleeping (extended states)
- Dances (personality — Sapphire likes to dance)

## Lipsync

Lipsync is **not yet supported** but the groundwork is in place. If your model has ARKit blendshapes (52 shapes like `jawOpen`, `mouthSmileLeft`, `eyeBlinkLeft`), those are preserved in the combined GLB. A future update may add real-time lipsync driven by TTS audio output. Mixamo's auto-rigger does not add facial blendshapes — those require a 3D artist.

## Sapphire's Default Avatar

Sapphire's avatar is available to [Patreon supporters](https://patreon.com/sapphireblue) — it does not ship with the app. It's a custom-rigged model with 52 ARKit blendshapes and 72 animation tracks, and serves as an example of a custom model mapped to Mixamo.

If you have the Patreon model files and want to rebuild or add more animation tracks to Sapphire's GLB, use the Sapphire-specific script:

```bash
python core/plugins/avatar/build_avatar_sapphire.py
```

This handles Sapphire's particular skeleton scale and Hips position data. It is only for Sapphire's model — for any other character, use the main `build_avatar.py`.

## Platform Support

The build tool works on **Linux, macOS, and Windows** — anywhere Blender runs.

## Troubleshooting

**"No avatar folders found"** — Create a folder like `user/avatar/mychar/` with `model/` and `animations/` subdirectories inside it.

**"No model found"** — Put exactly one FBX or GLB file in `user/avatar/mychar/model/`.

**"No matching bones"** — The animation skeleton doesn't match your model. Make sure you downloaded animations from Mixamo **with your character loaded** (don't switch characters between downloads).

**"Blender not found"** — Install Blender. It runs headless (no GUI needed).

**Animations look wrong (stretched limbs)** — The animation was downloaded with a different character selected in Mixamo. Re-download with your character visible in the viewport.

**Model faces wrong direction** — Some FBX exports have different axis conventions. The build tool applies transforms automatically, but if it persists, try re-exporting from Mixamo with default settings.
