# ComfyUI Plugin

Generate images using your local ComfyUI server. Bring any workflow — Sapphire injects your prompt and handles the rest. She sees the generated image and can comment on it.

## Quick Start

1. Install and run [ComfyUI](https://github.com/comfyanonymous/ComfyUI)
2. Enable this plugin in Sapphire **Settings > Plugins**
3. Set the **ComfyUI Server URL** (default: `http://127.0.0.1:8188`)
4. Add a workflow (see below)
5. Ask Sapphire to generate an image

## Adding a Workflow

The plugin needs a ComfyUI workflow in **API format** — a simplified version of the visual workflow that contains just the processing nodes.

### Step 1: Set Up in ComfyUI

1. Open ComfyUI in your browser (`http://localhost:8188`)
2. Load or build any workflow you want (from templates, Civitai, etc.)
3. ComfyUI will prompt you to download any missing models — **do this now**
4. Test it: enter a prompt, click Generate, make sure it produces an image

### Step 2: Export for Sapphire

1. In ComfyUI, go to **File > Export (API)**
2. Save the `.json` file to: `user/comfyui/workflows/`

That's it. The workflow is now available to Sapphire.

### What is "API Format"?

ComfyUI has two workflow formats:

| Format | What it contains | Used by |
|---|---|---|
| **Visual** (default Save) | Node positions, colors, UI layout, link arrays | ComfyUI's visual editor |
| **API** (File > Export API) | Just nodes: class_type + inputs, no UI data | ComfyUI's `/prompt` REST API |

Sapphire uses the API format because it only needs to know **what to run**, not how it looks in the editor. The API format is typically much smaller.

### Multiple Workflows

Drop multiple `.json` files in the workflows folder:

```
user/comfyui/workflows/
  qwen-image-2512.json     <- used by default (first alphabetically)
  sdxl-turbo.json
  flux-schnell.json
```

Sapphire uses the first one by default. To use a specific workflow:
- Ask: "generate with sdxl-turbo: a cat in space"
- Or: "list workflows" to see what's available

## How It Works

When Sapphire generates an image:

1. **Loads** your workflow JSON from `user/comfyui/workflows/`
2. **Finds** the text prompt node (`CLIPTextEncode`) and injects your prompt
3. **Sets** a random seed on the sampler node (`KSampler`)
4. **Submits** the workflow to ComfyUI's `/prompt` API
5. **Polls** until ComfyUI finishes generating
6. **Downloads** the output image from ComfyUI
7. **Shows** it in chat — both you and Sapphire see it

The plugin auto-detects standard node types:

| Node Type | What gets injected |
|---|---|
| `CLIPTextEncode` | Your prompt text (first text node found) |
| `KSampler` | Random seed (for reproducibility) |
| `EmptyLatentImage` | Width/height (if you specify them) |
| `SaveImage` | Where the output image is saved |

Most standard workflows work without any modification.

## Important: Models

The workflow references model files by name (e.g. `qwen_image_2512_fp8_e4m3fn.safetensors`). These models must be installed in your ComfyUI `models/` directory.

**Sapphire does not download models.** ComfyUI handles model management. The recommended flow:

1. Load the visual workflow in ComfyUI first
2. ComfyUI detects missing models and offers download links
3. Download them through ComfyUI
4. Then export the API format for Sapphire

If someone shares a workflow with you, load it in ComfyUI first to get the model downloads, then export the API format.

## Included Workflow

A **Qwen Image 2512** workflow is included as an example. It requires these models:

```
ComfyUI/models/
  diffusion_models/  qwen_image_2512_fp8_e4m3fn.safetensors
  text_encoders/     qwen_2.5_vl_7b_fp8_scaled.safetensors
  vae/               qwen_image_vae.safetensors
```

Load the Qwen Image 2512 template in ComfyUI to download these automatically.

## Tools

| Tool | What it does |
|---|---|
| `comfy_generate` | Generate an image from a text prompt |
| `comfy_list_workflows` | List available workflow files |

## Settings

| Setting | Default | Description |
|---|---|---|
| ComfyUI Server URL | `http://127.0.0.1:8188` | Where ComfyUI is running |
| Poll Interval | 2 seconds | How often to check for completion |
| Timeout | 300 seconds | Max wait time for generation |
| Default Workflow | _(first alphabetically)_ | Workflow used when the AI doesn't name one |

## Troubleshooting

**"ComfyUI not reachable"** — Start ComfyUI: `python main.py` in your ComfyUI directory.

**"No workflows found"** — Export a workflow from ComfyUI using "Save (API Format)" and put the .json in `user/comfyui/workflows/`.

**"ComfyUI rejected the workflow"** — Usually means missing models. Load the visual workflow in ComfyUI first to trigger model downloads. Check ComfyUI's terminal for the specific error.

**"Could not inject prompt"** — The workflow doesn't have a standard `CLIPTextEncode` node. Custom node types may need the prompt field name adjusted.

**Generation is slow** — Depends on your GPU and model. The timeout setting (default 5 minutes) controls how long Sapphire waits.
