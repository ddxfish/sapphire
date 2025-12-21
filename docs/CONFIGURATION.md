# Configuration

Run the [Installation](https://github.com/ddxfish/sapphire/blob/main/docs/INSTALLATION.md) and open Sapphire before you configure. 

![Settings Manager](screenshots/settings-manager.png)

## LLM

Sapphire does not come with an AI model or AI server. You choose your own and run it locally, it's easy. If you don't know what this means, get LM Studio, put it in developer mode. Then download Qwen3 8B (Q4_K_M) in LM studio downloads, go to dev tab and enable API toggle. Sapphire by default will try to use LM Studio if it has an LLM loaded. For power users: Sapphire is OpenAI compliant.

<img src="screenshots/llm-settings.png" alt="LLM Settings" width="75%">

---

## Now make your own persona

Each chat can have completely different personas, voices, and capabilities. Switch between them instantly.

<table>
<tr>
<td width="33%">

[![Settings Manager](screenshots/settings-manager.png)](screenshots/settings-manager.png)

</td>
<td>

### Make the Settings Yours
- Gear icon → App Settings 
- Change names and avatars
- Enable TTS, STT, and Wakeword if desired
- Pick your wake word and raise Recorder Background Percentile if you have webcam mic

</td>
</tr>
<tr>
<td width="33%">

[![Prompt Editor](screenshots/prompt-editor-assembled.png)](screenshots/prompt-editor-assembled.png)

</td>
<td>

### Make the Prompt Yours
- Open the Prompt editor in the sidebar, click **+**
- Choose **Assembled** (more customizable) and name it
- Click **+** next to sections to create new ones:
  - **Persona** - Who the AI is. (You are William AI, a smart coder who...)
  - **Relationship** - Who you are to the AI (I am Jackie, your human boss that...)
  - **Location** - Story location - (You are in a forest where...)
  - **Goals** - AI Goals - (Your goals are to cheer up your user by...)
  - **Format** - Story Format - (3 paragraphs of dialog, narration and inner thoughts...)
  - **Scenario** - World Events - (Dinosaurs just invaded the mainland and...)
  - **Extras** - Optional - Swap multiple in: (sapphire-aware, your hobbies, uncensored)
  - **Emotions** - Optional - Multiple emotions: happy, curious, loved
- Save with the disk icon

Note: Write prompt from first person. You should refer to yourself as "I" in prompts, refer to your AI as "You".

</td>
</tr>
<tr>
<td width="33%">

[![Chat Settings](screenshots/per-chat-settings.png)](screenshots/per-chat-settings.png)

</td>
<td>

### Set up your default chat settings 
- Open the default chat (upper left), click **... → Chat Settings**
- Select your preferred prompt
- Choose which tools the AI can use
- Set TTS voice, pitch, speed
- **SPICE** adds randomness to replies
- **Inject Date** lets the AI know the current date
- **Custom text** is always included in addition to system prompt
- Click **Set as Default** then **Save**

Note: Set as Default is for all future chats. Save is for this chat only. Each chat has its own settings.

</td>
</tr>
<tr>
<td width="33%">

[![Personality Switcher](screenshots/chat-personality-switcher.png)](screenshots/chat-personality-switcher.png)

</td>
<td>

### Make Multiple Personas
- Click **...** next to any chat name → **New chat**
- Configure that chat differently via **... → Chat Settings**
- Each chat maintains its own prompt, voice, and tool configuration
- So change the voice, toolset, prompt etc and save it

</td>
</tr>
</table>

---

## Advanced Personalization

### Custom Plugins
Keyword-triggered extensions. Feed [PLUGINS.md](docs/PLUGINS.md) to an AI and drop the output in `user/plugins/`. Can run on keywords, in background, or on schedule.

### Custom Tools
AI-callable functions. Simpler than plugins- they are one file in `user/functions/`. Control your devices, check services, simulate capabilities like email/text or even crazy sims like hire a lawyer or deceive(). Feed [TOOLS.md](docs/TOOLS.md) to an AI to generate them.

### Custom Wake Word
Drop ONNX models in `user/wakeword/models/`. I trained "Hey Sapphire" in ~2 hours with synthetic data. [Community wakewords](https://github.com/fwartner/home-assistant-wakewords-collection) available.

### Custom Web UI Plugins
Plugin system works in Web UI but will reset plugins.json on app update.