"""ComfyUI image generation — submit custom workflows to a local ComfyUI server.

Users drop their workflow JSON (API format) into user/comfyui/workflows/.
The tool loads the workflow, injects the prompt text, and submits to ComfyUI.
"""

import base64
import io
import json
import logging
import random
import time
from pathlib import Path

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = "\U0001F3A8"

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
WORKFLOW_DIR = PROJECT_ROOT / "user" / "comfyui" / "workflows"

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "comfy_generate",
            "description": (
                "Generate an image using ComfyUI with your local workflow. "
                "Describe what you want to see in detail. The image appears in chat for both you and the user. "
                "Optionally specify a workflow name if multiple are available, width, height, and seed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Detailed image description. Be specific about subject, style, lighting, composition."
                    },
                    "workflow": {
                        "type": "string",
                        "description": "Workflow filename (without .json). Omit to use the default workflow."
                    },
                    "width": {
                        "type": "integer",
                        "description": "Image width in pixels (default: from workflow)"
                    },
                    "height": {
                        "type": "integer",
                        "description": "Image height in pixels (default: from workflow)"
                    },
                    "seed": {
                        "type": "integer",
                        "description": "Seed for reproducibility. Omit for random."
                    }
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "comfy_list_workflows",
            "description": "List available ComfyUI workflows. Use this to see what workflows are installed before generating.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }
]


def _get_settings():
    try:
        from core.plugin_loader import plugin_loader
        return plugin_loader.get_plugin_settings("comfyui") or {}
    except Exception:
        return {}


def _get_workflows():
    """List available workflow files."""
    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(WORKFLOW_DIR.glob("*.json"))


def _load_workflow(name=None):
    """Load a workflow JSON. Priority: explicit name > settings default > first alphabetically."""
    workflows = _get_workflows()
    if not workflows:
        return None, "No workflows found. Put a ComfyUI API-format workflow JSON in user/comfyui/workflows/"

    if name:
        # Find by name (with or without .json)
        target = WORKFLOW_DIR / f"{name}.json"
        if not target.exists():
            target = WORKFLOW_DIR / name
        if not target.exists():
            available = ", ".join(w.stem for w in workflows)
            return None, f"Workflow '{name}' not found. Available: {available}"
        return json.loads(target.read_text(encoding="utf-8")), None

    # Check settings for a default
    settings = _get_settings()
    default_name = settings.get("default_workflow", "").strip()
    if default_name:
        target = WORKFLOW_DIR / f"{default_name}.json"
        if target.exists():
            return json.loads(target.read_text(encoding="utf-8")), None

    # Fall back to first alphabetically
    return json.loads(workflows[0].read_text(encoding="utf-8")), None


def _available_workflow_names():
    """Get list of workflow names for tool descriptions."""
    return [w.stem for w in _get_workflows()]


def _inject_prompt(workflow, prompt_text, seed=None, width=None, height=None):
    """Inject prompt text, seed, width, height into a workflow.

    Scans all nodes for common patterns:
    - CLIPTextEncode with a 'text' input → inject prompt (first match = positive prompt)
    - KSampler/SamplerCustom with 'seed' → inject seed
    - EmptyLatentImage with 'width'/'height' → inject dimensions
    - Any node with a 'text' widget that looks like a prompt field
    """
    prompt_injected = False
    seed_injected = False
    size_injected = False

    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type", "")
        inputs = node.get("inputs", {})

        # Prompt injection — CLIPTextEncode or similar text nodes
        if not prompt_injected:
            if class_type in ("CLIPTextEncode", "CLIPTextEncodeFlux"):
                if "text" in inputs:
                    # Only inject into nodes that have non-empty text or are the first text encoder
                    existing = inputs.get("text", "")
                    if isinstance(existing, str):  # Not a link
                        inputs["text"] = prompt_text
                        prompt_injected = True
                        logger.info(f"[COMFY] Injected prompt into node {node_id} ({class_type})")

        # Seed injection
        if seed is not None and not seed_injected:
            if class_type in ("KSampler", "KSamplerAdvanced", "SamplerCustom", "SamplerCustomAdvanced"):
                if "seed" in inputs and isinstance(inputs["seed"], (int, float)):
                    inputs["seed"] = seed
                    seed_injected = True
                    logger.info(f"[COMFY] Injected seed {seed} into node {node_id}")

        # Size injection
        if (width or height) and not size_injected:
            if class_type in ("EmptyLatentImage", "EmptySD3LatentImage"):
                if width and "width" in inputs:
                    inputs["width"] = width
                if height and "height" in inputs:
                    inputs["height"] = height
                size_injected = True
                logger.info(f"[COMFY] Injected size {width}x{height} into node {node_id}")

    # Fallback: if no CLIPTextEncode found, scan for any 'text' string field
    if not prompt_injected:
        for node_id, node in workflow.items():
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs", {})
            if "text" in inputs and isinstance(inputs["text"], str):
                inputs["text"] = prompt_text
                prompt_injected = True
                logger.info(f"[COMFY] Injected prompt into node {node_id} (fallback)")
                break

    if not prompt_injected:
        logger.warning("[COMFY] Could not find a text node to inject prompt into")

    return prompt_injected


def execute(function_name, arguments, config=None, plugin_settings=None, credentials=None):
    if function_name == "comfy_list_workflows":
        return _exec_list_workflows()
    elif function_name == "comfy_generate":
        return _exec_generate(arguments, plugin_settings)
    return f"Unknown function: {function_name}", False


def _exec_list_workflows():
    workflows = _get_workflows()
    if not workflows:
        return "No workflows installed. Put ComfyUI API-format .json files in user/comfyui/workflows/", True

    settings = _get_settings()
    default_name = settings.get("default_workflow", "").strip()

    # Determine which is actually the default
    if default_name and (WORKFLOW_DIR / f"{default_name}.json").exists():
        active_default = default_name
    else:
        active_default = workflows[0].stem

    lines = ["Available workflows:"]
    for w in workflows:
        size_kb = w.stat().st_size // 1024
        is_default = " (default)" if w.stem == active_default else ""
        lines.append(f"  {w.stem}{is_default} ({size_kb}KB)")
    lines.append(f"\nUse: comfy_generate(prompt='...', workflow='{workflows[0].stem}') to specify one.")
    return "\n".join(lines), True


def _exec_generate(arguments, plugin_settings=None):
    import requests
    from core import net

    prompt_text = arguments.get("prompt", "").strip()
    if not prompt_text:
        return "No prompt provided", False

    workflow_name = arguments.get("workflow")
    seed = arguments.get("seed") or random.randint(1, 2**31)
    width = arguments.get("width")
    height = arguments.get("height")

    settings = plugin_settings or _get_settings()
    comfy_url = settings.get("comfy_url", "http://127.0.0.1:8188")
    poll_interval = int(settings.get("poll_interval", 2))
    timeout = int(settings.get("timeout", 300))

    # Load workflow
    workflow, err = _load_workflow(workflow_name)
    if err:
        return err, False

    # Strip non-node keys (comments, metadata) — ComfyUI expects only node dicts
    workflow = {k: v for k, v in workflow.items() if isinstance(v, dict) and "class_type" in v}

    if not workflow:
        return "Workflow has no valid nodes — make sure you exported in API format", False

    # Inject prompt, seed, dimensions
    injected = _inject_prompt(workflow, prompt_text, seed=seed, width=width, height=height)
    if not injected:
        return "Could not inject prompt into workflow — no compatible text node found", False

    logger.info(f"[COMFY] Generating: {prompt_text[:60]}... (seed={seed})")

    try:
        # Check if ComfyUI is running
        try:
            net.get(f"{comfy_url}/system_stats", timeout=3)
        except Exception:
            return f"ComfyUI not reachable at {comfy_url}. Make sure it's running.", False

        # Submit workflow
        resp = net.post(
            f"{comfy_url}/prompt",
            json={"prompt": workflow},
            timeout=10,
        )
        if resp.status_code != 200:
            error_text = resp.text[:300]
            return f"ComfyUI rejected the workflow: {error_text}", False

        data = resp.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            return "ComfyUI returned no prompt_id", False

        logger.info(f"[COMFY] Queued prompt {prompt_id}")

        # Poll for completion
        elapsed = 0
        output_images = None

        while elapsed < timeout:
            time.sleep(poll_interval)
            elapsed += poll_interval

            hist_resp = net.get(f"{comfy_url}/history/{prompt_id}", timeout=10)
            if hist_resp.status_code != 200:
                continue

            history = hist_resp.json()
            if prompt_id not in history:
                continue

            entry = history[prompt_id]
            status = entry.get("status", {})

            if status.get("status_str") == "error":
                msgs = status.get("messages", [])
                error_text = str(msgs) if msgs else "Unknown error"
                return f"ComfyUI generation failed: {error_text}", False

            if status.get("completed", False) or entry.get("outputs"):
                output_images = entry.get("outputs", {})
                break

        if output_images is None:
            return f"ComfyUI timed out after {timeout}s", False

        # Find output image
        image_data = None
        for node_id, node_output in output_images.items():
            images = node_output.get("images", [])
            if images:
                img_info = images[0]
                filename = img_info.get("filename")
                subfolder = img_info.get("subfolder", "")
                img_type = img_info.get("type", "output")

                params = {"filename": filename, "type": img_type}
                if subfolder:
                    params["subfolder"] = subfolder
                img_resp = net.get(f"{comfy_url}/view", params=params, timeout=30)
                if img_resp.status_code == 200:
                    image_data = img_resp.content
                    break

        if not image_data:
            return "Generation completed but no image found in output", False

        # Resize for chat if huge
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(image_data))
            max_px = 2160
            if max(img.size) > max_px:
                ratio = max_px / max(img.size)
                img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            image_data = buf.getvalue()
        except Exception:
            pass

        b64 = base64.b64encode(image_data).decode()

        wf_label = workflow_name or _get_workflows()[0].stem if _get_workflows() else "unknown"
        summary = (
            f"Generated image with workflow '{wf_label}', seed={seed}\n"
            f"Prompt: {prompt_text[:200]}"
        )

        return {
            "text": summary,
            "images": [{"data": b64, "media_type": "image/jpeg"}]
        }, True

    except Exception as e:
        logger.error(f"[COMFY] Generation failed: {e}", exc_info=True)
        return f"ComfyUI generation error: {e}", False
