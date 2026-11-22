# ComfyUI Plugin

Generate images and video with your own ComfyUI workflows. ComfyUI can run on this machine or on another one. Sapphire writes the prompt into your workflow, runs it, and shows the result in chat. She sees the image (or three frames of a video) and can comment on it.

## Quick Start

1. Install and run [ComfyUI](https://github.com/comfyanonymous/ComfyUI).
2. Enable this plugin in Sapphire: **Settings > Plugins**.
3. Open **Settings > Plugins > ComfyUI**. Set the ComfyUI address and press **Test**.
4. Add a workflow (below).
5. Ask Sapphire to generate an image.

No workflow ships with the plugin. Models change too fast for a shipped one to stay useful, and yours are better.

## Adding a Workflow

Get the workflow working in ComfyUI first: load it, let ComfyUI download its models, and run it once. Then use one of three ways in.

| Way | How |
|---|---|
| **Import last ComfyUI run** | Press the button in the panel and give it a name. Sapphire takes the last run that worked, exactly as ComfyUI executed it. Nothing to export. |
| **Upload a workflow file** | In ComfyUI: **File > Export (API)**. Upload that `.json` in the panel. |
| **Upload a picture** | Any PNG that ComfyUI's Save Image node saved carries its workflow. Upload the PNG. |

ComfyUI's normal **Save** writes its editor format, which only ComfyUI's own page can run. The panel says so if you upload one. Use Export (API) or Import last run instead.

Workflows are kept in `user/comfyui/workflows/`. You can also drop API-format `.json` files there by hand.

Adding a workflow under a name that is already there asks before it replaces the old one. The description stays; a prompt node you picked does not, because node numbers belong to the old graph.

## ComfyUI on Another Machine

1. On the ComfyUI machine, start it with `--listen` (for example `python main.py --listen`). Without it, ComfyUI answers only its own machine.
2. In the panel, set the address to that machine: `http://192.168.1.20:8188`. Press **Test**; it answers with the ComfyUI version and GPU.

Workflows stay with Sapphire. Only the models live with ComfyUI.

### Moving a workflow between machines

Press **Download** on a workflow, carry the file to the other Sapphire, and **Upload** it there. Its description and settings travel inside the file.

Then press **Check**. It compares the workflow with that ComfyUI's own lists and names what is missing:

- a model file, and the `models/` folder it belongs in
- a custom node that is not installed there

A model that is the same file under another path is matched by itself: a Windows `flux\dev.safetensors` runs on a Linux `flux/dev.safetensors`, and the same file kept in another subfolder is found by name.

## What Sapphire Sets

| In your workflow | What she sets |
|---|---|
| The positive prompt | Found by following the main sampler's `positive` link back to where the text is typed, through guiders, ControlNet and image-to-video nodes, and string nodes. A negative prompt is never overwritten. A face detailer or tiled upscaler keeps its own prompt |
| Every seed | A random seed, or the one she names. Also a seed typed in a number node and wired in |
| The size | Width and height, on the node that sets the size at the start of the sampler's chain. A workflow that upscales on the way (hires fix) keeps its own size |
| A Load Image node | The picture she passes, for workflows that edit or animate one. A reference image (a ControlNet pose, an IPAdapter style, a mask) is left alone |

Only the part of the workflow that reaches a save or preview node counts. A leftover branch that ComfyUI would not run is ignored.

When a workflow has no place for something she asked for, or two places would both fit, the tool says so in its answer instead of guessing in silence. The panel shows **Her prompt goes into** and **Her input image goes into** wherever there is a choice, and what you pick there wins.

Give each workflow a description in the panel. She reads it when she chooses one.

## Video and Audio

A workflow with a Save Video node works the same way. The clip is kept in `user/comfyui/outputs/` and plays in chat, with a download button. Sapphire is shown three frames of it (start, middle, end); this needs `ffmpeg` on the Sapphire machine. Without ffmpeg the clip still reaches you, and she is told she can't see it.

Clips are never deleted by themselves. The panel shows how much space they take and has a **Delete all** button. Clips are plain files: unlike chat images, they are not stored inside a private chat's vault.

Video takes longer than an image, so it has its own wait.

## Tools

| Tool | What it does |
|---|---|
| `comfy_generate` | Run a workflow: `prompt`, and optionally `workflow`, `image`, `width`, `height`, `seed` |
| `comfy_list_workflows` | List the workflows: what each makes, its size, whether it takes an input image, your description |

## Settings

| Setting | Default | Description |
|---|---|---|
| ComfyUI address | `http://127.0.0.1:8188` | Where ComfyUI runs |
| Default workflow | the first in the list | Used when she names none |
| Image wait | 300 seconds | The longest to wait for an image |
| Video wait | 1200 seconds | The longest to wait for video or audio |

When a wait runs out, Sapphire takes her job out of ComfyUI's queue, and stops it only when it is the one running. (A ComfyUI from before 2025 stops whatever is running.)

Pressing Stop in the chat does not reach ComfyUI yet: the job runs to its end or to the wait.

## Privacy

- Everything goes to the ComfyUI address you set and nowhere else. The one exception: in an ordinary chat she may pass a web address as the input image, and Sapphire fetches that picture.
- **Private chats.** The tool works in a private chat only while nothing leaves your network. If the ComfyUI address is out on the internet, it refuses. The input image has to be one that is already here; a web address is refused.
- **What stays on the ComfyUI machine.** ComfyUI keeps the prompts in its history, writes them into the pictures it saves, and keeps every input image in its input folder. ComfyUI has no login. Started with `--listen`, anyone on your network can read these, including what came from a private chat.
- **Clips** made in a private chat are plain files in `user/comfyui/outputs/` (see above).

## Troubleshooting

**"Nothing answers at ..."** ComfyUI is not running, the address is wrong, or (on another machine) it was started without `--listen` or a firewall blocks the port.

**"the model file '...' isn't there"** That ComfyUI does not have the model. Put it in the folder the message names, or load the workflow in ComfyUI and let it download.

**"it has no '...' node"** The workflow uses a custom node that ComfyUI does not have. Install it with ComfyUI Manager.

**"Can't tell where the prompt goes"** Pick the prompt node for that workflow in the panel.

**"takes no input image"** The workflow has no Load Image node, or its only one feeds a ControlNet or similar. If that one is meant for her picture, pick it under **Her input image goes into**.

**"ComfyUI took longer than ..."** Raise the image or video wait in the panel.
