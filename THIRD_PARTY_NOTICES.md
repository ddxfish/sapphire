# Third-party notices

Sapphire is under the GNU AGPL v3 ([LICENSE](LICENSE)). It also carries, downloads or builds on the work below, each under its own license.

## In this repository

| What | Where | License |
|---|---|---|
| Highlight.js 11.9.0 + Atom One Dark theme | `interfaces/web/static/vendor/highlight*` | BSD 3-Clause |
| SortableJS 1.15.2 | `interfaces/web/static/vendor/sortable.min.js` | MIT |
| esptool-js 0.7.0 (pako 2.1.0 inlined) | `interfaces/web/static/vendor/esptool-js.bundle.js` | Apache 2.0; pako MIT + zlib |

Full texts: [`interfaces/web/static/vendor/THIRD-PARTY-LICENSES.txt`](interfaces/web/static/vendor/THIRD-PARTY-LICENSES.txt).

**`core/wakeword/models/hey_sapphire.onnx`**, Sapphire's own wake word, was trained with a community openWakeWord training notebook. Training data of that kind can include sets under non-commercial licenses (openWakeWord's ACAV100M features are CC BY-NC-SA 4.0). For a commercial product, train your own word in the Wakeword Maker plugin, which labels each dataset's license.

The persona pictures, user pictures and theme textures are AI-generated or our own.

## Downloaded when used, from their own publishers

Nothing below is in this repository. Each is fetched on first use into your own data folders, under its own terms:

| What | Used for | License |
|---|---|---|
| Kokoro-82M and its voices | speech | Apache 2.0 |
| Whisper models (via faster-whisper) | speech to text | MIT |
| Silero VAD | voice activity | MIT |
| nomic-embed-text-v1.5, nomic-embed-vision-v1.5 | memory search, Mind Palace | Apache 2.0 |
| CLIP ViT-B/32 | picture vibes | MIT |
| openWakeWord feature models (melspectrogram, embedding) | wake word | Apache 2.0 |
| openWakeWord's own words (alexa, hey_jarvis, hey_mycroft, hey_rhasspy, timer, weather) | wake word | **CC BY-NC-SA 4.0: not for commercial use** |
| Piper voices (Piper plugin) | speech | per voice: HFC Female and Lessac are **non-commercial**; Kristin, LJSpeech public domain; Kathleen CC0 ([model cards](https://huggingface.co/rhasspy/piper-voices)) |
| twitter-roberta-base-sentiment-latest (Discord plugin) | mood | CC BY 4.0 |
| GeoNames | places | CC BY 4.0 (an ATTRIBUTION.txt is written beside the data) |
| Google Fonts, Dancing Script | UI fonts | SIL Open Font License 1.1 |
| three.js (via esm.sh) | 3D avatar | MIT |
| Z-Image-Turbo GGUF, FLUX VAE, Qwen3-4B (sd-server plugin) | pictures | Apache 2.0 |
| Wakeword Maker datasets | training wake words | per dataset, shown before each download (two are non-commercial) |

## Python packages

Installed by pip from `requirements*.txt`; all are compatible with the AGPL. Among them, ebooklib (AGPL 3.0), esptool (GPL 2.0+), piper-tts (GPL 3.0+), espeak-ng (GPL 3.0, used by Kokoro and Piper) and mutagen (GPL 2.0+) are copyleft; pillow-heif's wheels include libheif and libde265 (LGPL 3.0) and x265 (GPL 2.0+). NVIDIA's CUDA libraries are under NVIDIA's own license.

The Docker image (`ghcr.io/ddxfish/sapphire`) contains these packages and the Kokoro, Whisper and Nomic text models listed above. The source of each package is at its PyPI page or project repository; the GPU image is built on NVIDIA's CUDA base image, under NVIDIA's container license.

## Satellite firmware

The ESP32 firmware is its own repository, under the AGPL v3 with an additional permission for Espressif's binary-only parts. Each firmware release carries a `THIRD-PARTY-NOTICES.txt` with every component's license.
