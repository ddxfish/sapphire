# Image Generation

Sapphire can generate images using SDXL via a separate server. The AI describes scenes, and character descriptions stay consistent across images.

![Image Generation Settings](screenshots/image-generation.png)

## Setup

1. **Run the image server** - Follow instructions at [ddxfish/sapphire-image-api](https://github.com/ddxfish/sapphire-image-api)
2. **Connect Sapphire** - Gear icon → Plugins → Image Generation
3. **Enter your server URL** (e.g., `http://localhost:5153`)
4. **Click Test** to verify connection

## Settings

### Character Descriptions

The AI writes prompts using "me" (itself) and "you" (the human). These get replaced with physical descriptions you define, keeping characters consistent across all generated images.

**Example:** AI writes "me and you walking in the park" → becomes "A woman with long brown hair and you walking in the park"

### Generation Defaults

| Setting | Description |
|---------|-------------|
| Width/Height | Image dimensions (256-2048) |
| Steps | More steps = better quality, slower (1-100) |
| CFG Scale | How closely to follow the prompt (1-20) |
| Scheduler | Sampling algorithm (dpm++_2m_karras recommended) |

### Other Options

- **Negative Prompt** - Things to avoid (ugly, blurry, etc.)
- **Static Keywords** - Always appended to prompts (e.g., "wide shot")

## Usage

Once configured, the AI can use the `generate_scene_image` tool automatically when describing scenes. Images appear inline in the chat.