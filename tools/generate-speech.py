#!/usr/bin/env python3
"""generate-speech.py - turn text into a Sapphire-voiced audio file via the running app.

Hits POST /api/tts/preview on the local Sapphire (Kokoro stays loaded, so it's fast),
authenticating with a Bearer API token. Saves native ogg/opus; transcodes to mp3 via
ffmpeg when the output path ends in .mp3.

  python tools/generate-speech.py "Hi, I'm Sapphire." -o intro.mp3
  python tools/generate-speech.py --file intro.txt --voice af_heart -o intro.ogg

API key resolution (first hit wins):
  --api-key  >  $SAPPHIRE_API_KEY  >  ~/.sapphire_api_key
Mint a token in Sapphire: Settings > System > API Keys.
"""
import argparse
import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from shutil import which

DEFAULT_URL = os.environ.get("SAPPHIRE_URL", "https://localhost:8073")


def resolve_key(cli_key):
    """--api-key > $SAPPHIRE_API_KEY > ~/.sapphire_api_key. Returns None if none found."""
    if cli_key:
        return cli_key.strip()
    env = os.environ.get("SAPPHIRE_API_KEY")
    if env:
        return env.strip()
    f = Path.home() / ".sapphire_api_key"
    try:
        if f.exists():
            return f.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None


def have_ffmpeg():
    return which("ffmpeg") is not None


def transcode_to_mp3(audio_bytes, out_path):
    """ogg/opus bytes -> mp3 file via ffmpeg. Raises on failure."""
    p = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", "pipe:0",
         "-codec:a", "libmp3lame", "-q:a", "2", str(out_path)],
        input=audio_bytes, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "ignore")[:200] or "ffmpeg failed")


def main():
    ap = argparse.ArgumentParser(
        description="Generate a Sapphire-voiced audio file from text.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("text", nargs="?", help="Text to speak (or use --file).")
    ap.add_argument("--file", help="Read text from a file instead of the positional arg.")
    ap.add_argument("-o", "--out", default="speech.ogg",
                    help="Output path. .mp3 transcodes via ffmpeg (default: speech.ogg).")
    ap.add_argument("--voice", help="Voice override (e.g. af_heart).")
    ap.add_argument("--speed", type=float, default=1.2,
                    help="Speed multiplier (default: 1.2).")
    ap.add_argument("--pitch", type=float, default=0.96,
                    help="Pitch, <1 lower / >1 higher (default: 0.96, Sapphire's usual).")
    ap.add_argument("--url", default=DEFAULT_URL,
                    help=f"Sapphire base URL (default: {DEFAULT_URL}).")
    ap.add_argument("--api-key",
                    help="Bearer token (else $SAPPHIRE_API_KEY or ~/.sapphire_api_key).")
    args = ap.parse_args()

    # --- text ---
    if args.file:
        try:
            text = Path(args.file).read_text(encoding="utf-8")
        except Exception as e:
            sys.exit(f"ERROR: could not read --file {args.file}: {e}")
    elif args.text:
        text = args.text
    else:
        sys.exit("ERROR: provide text as an argument or via --file.")
    text = text.strip()
    if not text:
        sys.exit("ERROR: text is empty.")

    # --- key ---
    key = resolve_key(args.api_key)
    if not key:
        sys.exit("ERROR: no API key. Pass --api-key, set $SAPPHIRE_API_KEY, or write "
                 "~/.sapphire_api_key.\n       Mint one in Sapphire: Settings > System > API Keys.")

    # --- request ---
    payload = {"text": text, "speed": args.speed, "pitch": args.pitch}
    if args.voice:
        payload["voice"] = args.voice

    ctx = ssl.create_default_context()  # local app uses a self-signed cert
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(
        args.url.rstrip("/") + "/api/tts/preview",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
            audio = resp.read()
            ctype = resp.headers.get("Content-Type", "audio/ogg")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail", "")
        except Exception:
            pass
        hints = {401: "bad or expired API key", 413: "text too long",
                 503: "TTS disabled or generation failed"}
        sys.exit(f"ERROR: server returned {e.code} ({hints.get(e.code, 'request failed')}). {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"ERROR: could not reach Sapphire at {args.url}: {e.reason}\n"
                 f"       Is the app running?")

    if not audio:
        sys.exit("ERROR: empty audio response.")

    # --- save (transcode to mp3 if asked and not already mp3) ---
    out = Path(args.out)
    native_is_mp3 = "mpeg" in ctype or "mp3" in ctype
    if out.suffix.lower() == ".mp3" and not native_is_mp3:
        if have_ffmpeg():
            try:
                transcode_to_mp3(audio, out)
                print(f"Wrote {out} ({out.stat().st_size} bytes, mp3 via ffmpeg)")
                return
            except Exception as e:
                alt = out.with_suffix(".ogg")
                alt.write_bytes(audio)
                sys.exit(f"WARN: ffmpeg transcode failed ({e}); saved native audio to {alt}.")
        else:
            alt = out.with_suffix(".ogg")
            alt.write_bytes(audio)
            print(f"NOTE: ffmpeg not found - saved native ogg to {alt} "
                  f"(install ffmpeg for mp3 output).")
            return

    out.write_bytes(audio)
    print(f"Wrote {out} ({out.stat().st_size} bytes, {ctype})")


if __name__ == "__main__":
    main()
