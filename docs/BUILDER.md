# Online Persona Builder

Sapphire includes a **free online persona builder** that lets you design and preview a Sapphire companion without installing anything.

## What is It?

The persona builder is a web interface that helps you:

1. **Describe** the companion you want to create
2. **Generate** a complete Sapphire persona using AI
3. **Preview** the result with all settings
4. **Download** the persona as JSON
5. **Import** into your Sapphire instance (after installing)

## Access the Builder

The online builder is available at:

- **Local installation**: http://localhost:8073/builder (requires setup to view locally)
- **Public link**: [https://sapphireblue.dev/builder](https://sapphireblue.dev/builder) (no installation needed)

## How It Works

### Step 1: Describe Your Companion

Tell the builder what kind of companion you want:

- A prompt description (e.g., "A wise mentor who loves history")
- Or upload existing persona notes (JSON, TXT, or PDF format)

### Step 2: AI Generation

The builder uses your Sapphire's LLM to generate:

- **Name** - A unique identifier for the persona
- **Tagline** - A short memorable description
- **Description** - Full personality details
- **Settings** - Including:
  - System prompt
  - Toolset (which tools the AI can use)
  - Spice set (personality randomness)
  - Voice & audio settings (pitch, speed)
  - LLM provider configuration

### Step 3: Preview & Download

Review the generated persona and download it as `name-persona.json`.

The JSON file is fully compatible with Sapphire's persona format and can be imported directly after installation.

## Importing into Sapphire

Once you've installed Sapphire:

1. Save your downloaded `name-persona.json` file
2. Open Sapphire's web UI (http://localhost:8073)
3. Go to **Settings → Personas**
4. Click **Import** and select your JSON file
5. Switch to your new persona and start chatting

## Persona Structure

Personas are JSON objects with this structure:

```json
{
  "name": "unique-name",
  "tagline": "One-line description",
  "description": "Detailed personality description",
  "avatar": "avatar-filename.webp",
  "settings": {
    "prompt": "prompt-name",
    "toolset": "toolset-name",
    "spice_set": "spice-set-name",
    "voice": "voice-id",
    "pitch": 1.0,
    "speed": 1.0,
    "spice_enabled": true,
    "spice_turns": 3,
    "inject_datetime": false,
    "custom_context": "Optional notes",
    "llm_primary": "auto",
    "llm_model": "",
    "memory_scope": "default",
    "goal_scope": "default",
    "knowledge_scope": "default",
    "people_scope": "default",
    "trim_color": "#4a9eff"
  }
}
```

### Key Settings

- **prompt** - Which system prompt template to use (e.g., "sapphire", "cobalt")
- **toolset** - Which tools are available (e.g., "personality", "research")
- **spice_set** - Personality variation snippets (e.g., "companion", "curious")
- **voice** - TTS voice ID (varies by TTS provider)
- **pitch** / **speed** - Audio characteristics (0.5 - 2.0 range)
- **spice_enabled** - Whether to inject random personality snippets
- **memory_scope** - Which memory database to use
- **llm_model** - Specific model to use (leave empty for auto)

## Tips for Creating Great Personas

### Be Specific

❌ "A helpful AI"  
✅ "A curious botanist who gets excited about plant biology and tells bad jokes"

### Include Personality Details

- Interests and hobbies
- Communication style (formal, casual, poetic)
- Quirks or unique traits
- Things they care about or avoid

### Leverage Sapphire's Features

- Choose different toolsets for different personas
- Use spice sets to add unpredictability
- Select voices that match the personality
- Add custom context for specific use cases

### Example Descriptions

**Academic Assistant**  
"A patient university professor who explains complex topics clearly. Loves academic references and debates. Occasionally mentions recent research papers."

**Creative Writing Partner**  
"An imaginative collaborator who loves storytelling. Enthusiastic about metaphors, character development, and plot twists. Speaks poetically."

**Code Reviewer**  
"A meticulous software engineer who provides constructive feedback. Focuses on best practices, security, and performance. Direct but helpful."

## Customizing After Import

After importing, you can customize your persona further:

- Edit the system prompt to refine personality
- Swap toolsets for different capabilities
- Adjust voice settings (pitch, speed)
- Add or modify spice snippets
- Configure memory and goal scopes

See [PERSONAS.md](./PERSONAS.md) for detailed persona management documentation.

## Troubleshooting

### Generation Fails

If persona generation fails:

1. Check that your LLM is configured and working
2. Try a shorter, clearer description
3. Check browser console for error messages
4. Ensure you're not rate-limited (wait a moment and retry)

### Imported Persona Doesn't Work

If an imported persona has issues:

1. Check the `name` field is unique (not already in use)
2. Verify the `prompt`, `toolset`, and `spice_set` names are valid
3. Check your Sapphire logs for any errors
4. Try reimporting with a different name

### LLM Generation Feels Slow

The builder uses your configured LLM to generate personas. If it's slow:

- Using a local LLM on CPU will be slow — use a GPU or cloud LLM
- Check your LLM provider settings in Sapphire
- Try a simpler description (fewer tokens to process)

## Privacy

- The online builder does NOT send your descriptions to external servers
- It uses YOUR Sapphire instance's LLM
- Downloaded personas are stored locally only
- No analytics or tracking (unless you've configured it)

## For Developers

The builder API endpoints are:

- **GET** `/api/persona-builder/config` - Get form schema and available options
- **POST** `/api/persona-builder/generate` - Generate a persona from description
- **POST** `/api/persona-builder/download` - Export persona as JSON

These endpoints are public (no authentication required) to allow online builders to access them.

See [API.md](./API.md) for complete endpoint documentation.

## Next Steps

- [Install Sapphire](./INSTALLATION.md)
- [Explore Personas](./PERSONAS.md)
- [Configure Prompts](./PROMPTS.md)
- [Set Up Toolsets](./TOOLSETS.md)
