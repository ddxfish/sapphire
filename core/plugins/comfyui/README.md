# ComfyUI Plugin

Generate images and video with your own ComfyUI workflows. ComfyUI can run on this machine or on another one. Sapphire writes the prompt into your workflow, runs it, and shows the result in chat. She sees the image (or three frames of a video) and can comment on it.

## Quick Start

1. Install and run [ComfyUI](https://github.com/comfyanonymous/ComfyUI).
2. Enable this plugin in Sapphire: **Settings > Plugins**.
3. Open **Settings > Plugins > ComfyUI**. Set the ComfyUI address and press **Test**.
4. Add a workflow (below).
5. Ask Sapphire to generate an image.

No workflow ships with the plugin. Models change too fast for a shipped one to stay useful, and yours are better.

## The ComfyUI Page

**Apps > ComfyUI** runs a workflow by hand, with no AI in between.

- **Left:** pick a workflow, type the prompt, choose an input picture when the workflow takes one (or several when it has room; click a picture to remove it), pick a shape, and set the workflow's own settings (the same extras Sapphire is shown, with the same limits). An empty setting keeps the workflow's own value.
- **Right:** the result, large. Under it: a download button, the seed, and **Use as input picture**, which sends a still straight into a workflow that animates or edits one.
- **Stop** takes the job out of ComfyUI.
- The strip at the bottom is what was made last, by you or by Sapphire.

A run keeps going if you leave the page, and the page picks it up when you come back. What the page makes is kept in `user/comfyui/outputs/` with Sapphire's clips; the panel's **Delete all** clears both. Ctrl+Enter in the prompt starts a run.

## Adding a Workflow

Get the workflow working in ComfyUI first: load it, let ComfyUI download its models, and run it once. Then use one of three ways in.

| Way | How |
|---|---|
| **Import last ComfyUI run** | Press the button in the panel and give it a name. Sapphire takes the last run that worked, exactly as ComfyUI executed it. Nothing to export. |
| **Upload a workflow file** | In ComfyUI: **File > Export (API)**. Upload that `.json` in the panel. |
| **Upload a picture** | Any PNG that ComfyUI's Save Image node saved carries its workflow. Upload the PNG. |

ComfyUI's normal **Save** writes its editor format, which only ComfyUI's own page can run. The panel says so if you upload one. Use Export (API) or Import last run instead.

Workflows are kept in `user/comfyui/workflows/`. You can also drop API-format `.json` files there by hand.

Adding a workflow under a name that is already there asks before it replaces the old one. The description stays; a prompt node you picked and what you changed about its extras do not, because node numbers belong to the old graph.

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
| The shape | Square, portrait or landscape (see below). Never a number of pixels: every size in the workflow keeps the pixel count you gave it |
| A Load Image node | The picture she passes, for workflows that edit or animate one. A reference image (a ControlNet pose, an IPAdapter style, a mask) is left alone. A workflow with room for several pictures (an edit model that takes a person and a piece of clothing) gets them in the order the model reads them (the first is the main picture). A slot nobody filled is unplugged, so the picture saved in the workflow file does not ride along; where a workflow can't run with that slot empty, the saved picture goes in and her result starts by saying so |

### The shape of the picture

She does not choose pixels. The workflow keeps the pixel count you tuned it to, and only its shape changes:

- **With an input image**, the result takes the shape of that image (the first one, when there are several), as near as the workflow can make it (a workflow that picks from a list of ratios gets the nearest one). A square portrait animated by a widescreen workflow would otherwise be cropped, and the face redrawn when it comes back into frame.
- **She can name a shape instead**: `square`, `portrait` or `landscape` (it wins over the image's shape). A workflow that is already landscape keeps its own ratio, and `portrait` turns it on its side. A square workflow becomes 3:2 for a still and 16:9 for video.
- **With neither**, the workflow runs as you saved it.

The shape is set wherever the workflow sets its size: width and height typed on a node, typed in number nodes wired to it, or a ratio list such as ComfyUI's Resolution Selector (she gets the nearest ratio on the list). A workflow with a hires pass gets the shape on both passes. A ControlNet picture is not reshaped: ComfyUI crops it to fit.

A workflow with no size the tool can set shows **shape not settable** in the panel and in her list. Naming a shape for it is refused before anything runs. The tool measures what came out and tells her the truth about it: the real size, and whether her picture kept its shape or was cropped to the nearest the workflow makes. A clip can only be measured with `ffmpeg` on the Sapphire machine.

The old `width` and `height` arguments are gone. A call that still passes them is answered with the shape word to use instead.

### Extras: the workflow's own settings

Most of the time she just names a workflow and it runs as you saved it. When she wants more (a longer clip, more steps), she passes **extras**: `extras={"Duration": 8}`. Her list of workflows shows what each one offers, the value it came with, and how far it goes:

```
ltx-video: video, takes an input image, landscape by default
    extras: Frame Rate 24 (6 to 96), Duration 5 (1 to 20), Enable Prompt Enhance off
```

Extras are found in the workflow by themselves:

- **A number or switch node you gave a title.** ComfyUI's own templates are built this way (Duration, Frame Rate, a text-to-video switch). To offer her a setting of your own, wire a titled Int, Float or Boolean node to it.
- **The inputs every model has**, typed on a node: steps, CFG or guidance, frames, frame rate, how many images, and denoise on a workflow that edits a picture.

Seeds, sizes and the prompt are never extras: the tool sets those itself. A face detailer or upscaler keeps its own steps. A CFG of 1 (a distilled model) and a full denoise are left alone. So are the steps of two samplers that share one run (a high-noise and a low-noise model handing over at a set step): changing them would move the hand-over.

A number may go from a quarter to four times the value the workflow came with, and never past what the input itself can take (150 steps, 4 images). She is shown at most twelve extras per workflow. She can call one by the start of its name (`duration` for "Duration (seconds)") when only one starts that way. In the panel, each workflow lists its extras under **She can also set**: untick one to hide it from her, type another name, or set your own lowest and highest. A name she gets wrong or a value outside the range is answered before anything runs, with what she can set.

The limits are per setting. Four times the duration at four times the frame rate is sixteen times the work, so narrow them on a heavy workflow.

Only the part of the workflow that reaches a save or preview node counts. A leftover branch that ComfyUI would not run is ignored.

When a workflow has no place for something she asked for, or two places would both fit, the tool says so in its answer instead of guessing in silence. A workflow that takes an input picture, run without one, uses the picture it was saved with: her result starts by saying so. A workflow with a text-to-video switch takes no picture while that switch is on.

ComfyUI sometimes runs a workflow without one of its viewer nodes (a compare or preview node it could not check). When nothing that is saved depends on that node, the run goes ahead and Sapphire's log notes it. The panel shows **Her prompt goes into** and **Her input image goes into** wherever there is a choice, and what you pick there wins.

Give each workflow a description in the panel. She reads it when she chooses one.

## Video and Audio

A workflow with a Save Video node works the same way. The clip is kept in `user/comfyui/outputs/` and plays in chat, with a download button. Sapphire is shown three frames of it (start, middle, end); this needs `ffmpeg` on the Sapphire machine (with `ffmpeg` but no `ffprobe`, the first frame only). Without ffmpeg the clip still reaches you, and she is told she can't see it.

Clips are never deleted by themselves. The panel shows how much space they take and has a **Delete all** button. Clips are plain files: unlike chat images, they are not stored inside a private chat's vault.

Video takes longer than an image, so it has its own wait.

## Tools

| Tool | What it does |
|---|---|
| `comfy_generate` | Run a workflow: `prompt`, and optionally `workflow`, `image`, `shape`, `seed`, `extras` |
| `comfy_list_workflows` | List the workflows: what each makes, its shape, whether it takes an input image, your description, its extras |

## Settings

| Setting | Default | Description |
|---|---|---|
| ComfyUI address | `http://127.0.0.1:8188` | Where ComfyUI runs |
| Default workflow | the first in the list | Used when she names none |
| Image wait | 300 seconds | The longest ComfyUI may work on an image |
| Video wait | 1200 seconds | The longest ComfyUI may work on video or audio |
| Include these in backups | off | Whether what ComfyUI made goes into Sapphire's backups |

The wait counts the time ComfyUI works on the job, not the time it waits its turn behind another one (her video, your still). When a wait runs out, Sapphire takes her job out of ComfyUI's queue, and stops it only when it is the one running. (A ComfyUI from before 2025 stops whatever is running.)

Pressing Stop in the chat does not reach ComfyUI yet: the job runs to its end or to the wait.

### Backups

What ComfyUI makes (`user/comfyui/outputs/`) is **left out of Sapphire's backups** unless you switch on **Include these in backups**. One video can be larger than the rest of a Sapphire backup, and every nightly backup would carry all of them.

While they are left out, a restore does not bring them back, and the chats that showed them have empty players. To keep them safe, copy `user/comfyui/outputs/` somewhere yourself. The settings panel and the ComfyUI page both show how many files are kept, their size, and whether they are in backups. Sapphire is told the same when she makes a clip. Your workflows are always backed up.

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

**The video player's own menu has no Download.** On purpose: the browser's own download fails on Sapphire's self-signed certificate. Use the download button under the clip.

**"takes no input image"** The workflow has no Load Image node, or its only one feeds a ControlNet or similar. If that one is meant for her picture, pick it under **Her input image goes into**.

**"ComfyUI took longer than ..."** Raise the image or video wait in the panel.
