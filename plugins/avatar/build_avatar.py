#!/usr/bin/env python3
"""Build a combined avatar GLB from a Mixamo character + Mixamo animations.

Usage:
    python plugins/avatar/build_avatar.py [name]

    name    Avatar folder name under user/avatar/ (optional — auto-detects if only one)

Directory structure:
    user/avatar/
      anita/                  <- Avatar name (you create this)
        model/                <- Your Mixamo character FBX (one file)
          claire.fbx
        animations/           <- Mixamo animation FBX files (Without Skin)
          Idle.fbx
          Waving.fbx
          ...

Output:
    user/avatar/anita/anita.glb       <- Combined GLB (source kept intact)
    user/avatar/anita.glb             <- Copy for the web UI to load

How to get files:
    1. Go to mixamo.com, sign in (free Adobe account)
    2. Upload your model or pick a stock character
    3. Download character as FBX (T-pose)
    4. With same character loaded, browse Animations
    5. For each: set Skin = "Without Skin", download as FBX
    6. Put character FBX in user/avatar/yourname/model/
    7. Put animation FBX files in user/avatar/yourname/animations/
    8. Run: python plugins/avatar/build_avatar.py yourname
"""

import json
import struct
import subprocess
import sys
import shutil
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
AVATAR_DIR = PROJECT_ROOT / "user" / "avatar"

# Windows cmd: cp1252 raises on the unicode glyphs and VT processing is
# off by default \u2014 plain ASCII with no ANSI there.
_WIN = sys.platform == "win32"
GREEN = "" if _WIN else "\033[92m"
YELLOW = "" if _WIN else "\033[93m"
RED = "" if _WIN else "\033[91m"
CYAN = "" if _WIN else "\033[96m"
DIM = "" if _WIN else "\033[2m"
RESET = "" if _WIN else "\033[0m"
CHECK = f"{GREEN}{'+' if _WIN else '\u2713'}{RESET}"
CROSS = f"{RED}{'x' if _WIN else '\u2717'}{RESET}"
ARROW = f"{CYAN}{'>' if _WIN else '\u25b6'}{RESET}"


def log(msg, color=""):
    print(f"  {color}{msg}{RESET}")


def read_glb(path):
    with open(path, "rb") as f:
        magic, version, length = struct.unpack("<III", f.read(12))
        if magic != 0x46546C67:
            raise ValueError(f"Not a GLB file: {path}")
        chunk_len, chunk_type = struct.unpack("<II", f.read(8))
        gltf = json.loads(f.read(chunk_len).decode("utf-8"))
        remaining = length - 12 - 8 - chunk_len
        bin_chunk = b""
        if remaining > 8:
            bin_len, bin_type = struct.unpack("<II", f.read(8))
            bin_chunk = f.read(bin_len)
    return gltf, bin_chunk


def write_glb(gltf, bin_chunk, path):
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_pad = (4 - len(json_bytes) % 4) % 4
    json_bytes += b" " * json_pad
    bin_pad = (4 - len(bin_chunk) % 4) % 4
    bin_chunk += b"\x00" * bin_pad
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_chunk)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(json_bytes), 0x4E4F534A))
        f.write(json_bytes)
        f.write(struct.pack("<II", len(bin_chunk), 0x004E4942))
        f.write(bin_chunk)


def convert_fbx_to_glb(fbx_path, glb_path):
    """Convert FBX to GLB using Blender headless."""
    script = '''
import bpy, sys
argv = sys.argv[sys.argv.index("--") + 1:]
bpy.ops.wm.read_homefile(use_empty=True)
bpy.ops.import_scene.fbx(filepath=argv[0])
for obj in bpy.context.scene.objects:
    if obj.type == 'ARMATURE':
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        obj.select_set(False)
bpy.ops.export_scene.gltf(filepath=argv[1], export_format='GLB',
    export_animations=True, export_skins=True, export_morph=True, export_apply=False)
'''
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(script)
        script_path = f.name
    try:
        import os
        env = dict(os.environ)
        # On Linux, strip conda so Blender uses system Python with numpy
        if sys.platform != "win32":
            for k in ("CONDA_PREFIX", "CONDA_DEFAULT_ENV", "CONDA_EXE"):
                env.pop(k, None)
            env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        result = subprocess.run(
            ["blender", "--background", "--factory-startup", "--python", script_path,
             "--", str(fbx_path), str(glb_path)],
            capture_output=True, text=True, timeout=120, env=env,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    finally:
        Path(script_path).unlink(missing_ok=True)


def _clean_bone_name(name):
    return name.replace("mixamorig:", "").replace("Armature|", "")


def merge_animations(base_path, anim_glbs, output_path):
    """Merge animation GLBs into base model. Rotation-only."""
    base_gltf, base_bin = read_glb(base_path)
    result_bin = bytearray(base_bin)

    base_name_to_idx = {}
    for j in base_gltf["skins"][0]["joints"]:
        raw = base_gltf["nodes"][j]["name"]
        base_name_to_idx[raw] = j
        base_name_to_idx[_clean_bone_name(raw)] = j

    base_gltf["animations"] = []
    added = []

    for glb_path in anim_glbs:
        anim_gltf, anim_bin = read_glb(str(glb_path))

        remap = {}
        for i, node in enumerate(anim_gltf.get("nodes", [])):
            name = node.get("name", "")
            clean = _clean_bone_name(name)
            if name in base_name_to_idx:
                remap[i] = base_name_to_idx[name]
            elif clean in base_name_to_idx:
                remap[i] = base_name_to_idx[clean]

        if not remap:
            log(f"{CROSS} {glb_path.stem}: no matching bones, skipped")
            continue

        for anim in anim_gltf.get("animations", []):
            max_time = 0
            for s in anim.get("samplers", []):
                ai = s.get("input")
                if ai is not None and ai < len(anim_gltf.get("accessors", [])):
                    acc = anim_gltf["accessors"][ai]
                    if "max" in acc:
                        max_time = max(max_time, acc["max"][0])
            if max_time < 0.1:
                continue

            keep_samplers = set()
            for ch in anim.get("channels", []):
                t = ch.get("target", {})
                if t.get("path") == "rotation" and t.get("node") in remap:
                    keep_samplers.add(ch.get("sampler"))

            if not keep_samplers:
                continue

            kept_acc = set()
            for si in keep_samplers:
                s = anim["samplers"][si]
                if "input" in s: kept_acc.add(s["input"])
                if "output" in s: kept_acc.add(s["output"])

            kept_bv = set()
            for ai in kept_acc:
                if ai < len(anim_gltf.get("accessors", [])):
                    bv = anim_gltf["accessors"][ai].get("bufferView")
                    if bv is not None: kept_bv.add(bv)

            bv_remap = {}
            for old_bv in sorted(kept_bv):
                bv = dict(anim_gltf["bufferViews"][old_bv])
                new_off = len(result_bin)
                result_bin.extend(anim_bin[bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]])
                new_bv = dict(bv); new_bv["buffer"] = 0; new_bv["byteOffset"] = new_off
                base_gltf.setdefault("bufferViews", []).append(new_bv)
                bv_remap[old_bv] = len(base_gltf["bufferViews"]) - 1

            acc_remap = {}
            for old_ai in sorted(kept_acc):
                if old_ai >= len(anim_gltf.get("accessors", [])): continue
                acc = dict(anim_gltf["accessors"][old_ai])
                obv = acc.get("bufferView")
                if obv is not None and obv in bv_remap: acc["bufferView"] = bv_remap[obv]
                base_gltf.setdefault("accessors", []).append(acc)
                acc_remap[old_ai] = len(base_gltf["accessors"]) - 1

            new_samplers = []
            sampler_remap = {}
            for old_si in sorted(keep_samplers):
                s = anim["samplers"][old_si]
                ns = {}
                if "input" in s and s["input"] in acc_remap: ns["input"] = acc_remap[s["input"]]
                if "output" in s and s["output"] in acc_remap: ns["output"] = acc_remap[s["output"]]
                if "interpolation" in s: ns["interpolation"] = s["interpolation"]
                if "input" in ns and "output" in ns:
                    sampler_remap[old_si] = len(new_samplers)
                    new_samplers.append(ns)

            new_channels = []
            for ch in anim.get("channels", []):
                old_s = ch.get("sampler")
                if old_s not in sampler_remap: continue
                t = ch.get("target", {})
                if t.get("path") != "rotation": continue
                nn = remap.get(t.get("node"))
                if nn is None: continue
                new_channels.append({"sampler": sampler_remap[old_s], "target": {"node": nn, "path": "rotation"}})

            if new_channels and new_samplers:
                track_name = glb_path.stem.replace(".anim", "")
                existing = {a["name"] for a in base_gltf["animations"]}
                if track_name in existing:
                    c = 2
                    while f"{track_name}_{c}" in existing: c += 1
                    track_name = f"{track_name}_{c}"
                base_gltf["animations"].append({"name": track_name, "samplers": new_samplers, "channels": new_channels})
                added.append({"name": track_name, "duration": round(max_time, 1)})

    if base_gltf.get("buffers"):
        base_gltf["buffers"][0]["byteLength"] = len(result_bin)

    write_glb(base_gltf, bytes(result_bin), output_path)
    return added


def find_avatar_dir(name=None):
    """Find the avatar directory to build."""
    if name:
        d = AVATAR_DIR / name
        if not d.exists():
            print(f"  {CROSS} Avatar folder not found: user/avatar/{name}/")
            sys.exit(1)
        return d, name

    # Auto-detect: look for folders with model/ and animations/ subdirs
    candidates = []
    for child in sorted(AVATAR_DIR.iterdir()):
        if not child.is_dir():
            continue
        if (child / "model").is_dir() or (child / "animations").is_dir():
            candidates.append(child)

    if not candidates:
        print(f"  {CROSS} No avatar folders found in user/avatar/")
        print(f"  {DIM}Create a folder like user/avatar/mychar/ with model/ and animations/ inside.{RESET}")
        sys.exit(1)
    if len(candidates) == 1:
        return candidates[0], candidates[0].name
    print(f"  {CROSS} Multiple avatar folders found. Specify which one:")
    for c in candidates:
        print(f"    python plugins/avatar/build_avatar.py {c.name}")
    sys.exit(1)


def main():
    print(f"\n{CYAN}{'=' * 50}")
    print(f"  Sapphire Avatar Builder")
    print(f"{'=' * 50}{RESET}\n")

    # Parse args
    name = sys.argv[1] if len(sys.argv) > 1 else None
    avatar_dir, avatar_name = find_avatar_dir(name)
    model_dir = avatar_dir / "model"
    anim_dir = avatar_dir / "animations"
    cache_dir = avatar_dir / "_cache"

    print(f"  {ARROW} Building avatar: {CYAN}{avatar_name}{RESET}\n")

    # === Gate 1: Directories ===
    model_dir.mkdir(exist_ok=True)
    anim_dir.mkdir(exist_ok=True)

    # === Gate 2: Find base model ===
    models = [f for f in model_dir.iterdir()
              if f.is_file() and f.suffix.lower() in (".glb", ".fbx")]
    if not models:
        print(f"  {CROSS} No model found in user/avatar/{avatar_name}/model/")
        print(f"  {DIM}Put your Mixamo character FBX there.{RESET}")
        sys.exit(1)
    if len(models) > 1:
        print(f"  {CROSS} Multiple model files — put only one.")
        for m in models: print(f"    {m.name}")
        sys.exit(1)

    base_model = models[0]

    # Convert FBX to GLB if needed
    if base_model.suffix.lower() == ".fbx":
        if not shutil.which("blender"):
            print(f"  {CROSS} Blender not found — needed for FBX conversion.")
            sys.exit(1)
        cache_dir.mkdir(exist_ok=True)
        glb_model = cache_dir / f"{base_model.stem}.glb"
        if not glb_model.exists():
            print(f"  {ARROW} Converting model FBX -> GLB via Blender...")
            ok = convert_fbx_to_glb(base_model, glb_model)
            if not ok or not glb_model.exists():
                print(f"  {CROSS} Model conversion failed.")
                sys.exit(1)
            print(f"  {CHECK} Converted {base_model.name}")
        base_model = glb_model

    base_gltf, _ = read_glb(base_model)
    skins = base_gltf.get("skins", [])
    joint_count = len(skins[0].get("joints", [])) if skins else 0
    morph_count = 0
    for mesh in base_gltf.get("meshes", []):
        morph_count = max(morph_count, len(mesh.get("extras", {}).get("targetNames", [])))

    print(f"  {CHECK} Model: {models[0].name}")
    print(f"    {DIM}{joint_count} bones, {morph_count} blendshapes{RESET}")

    # === Gate 3: Find animations ===
    fbx_files = sorted(f for f in anim_dir.iterdir() if f.suffix.lower() == ".fbx")
    glb_files = sorted(f for f in anim_dir.iterdir() if f.suffix.lower() == ".glb")
    total = len(fbx_files) + len(glb_files)

    if total == 0:
        print(f"\n  {CROSS} No animations in user/avatar/{avatar_name}/animations/")
        print(f"  {DIM}Download Mixamo animations (FBX, Without Skin) and put them there.{RESET}")
        sys.exit(1)

    print(f"\n  {CHECK} Found {total} animation files ({len(fbx_files)} FBX, {len(glb_files)} GLB)")

    # === Gate 4: Convert FBX -> GLB ===
    all_anim_glbs = list(glb_files)

    if fbx_files:
        if not shutil.which("blender"):
            print(f"\n  {CROSS} Blender not found — needed for FBX conversion.")
            sys.exit(1)
        cache_dir.mkdir(exist_ok=True)
        print(f"\n  {ARROW} Converting animations via Blender...")

        for fbx in fbx_files:
            glb_out = cache_dir / f"{fbx.stem}.anim.glb"
            if glb_out.exists():
                log(f"{CHECK} {fbx.stem} (cached)", DIM)
                all_anim_glbs.append(glb_out)
                continue
            log(f"{YELLOW}  converting {fbx.stem}...{RESET}", "")
            ok = convert_fbx_to_glb(fbx, glb_out)
            if ok and glb_out.exists():
                log(f"{CHECK} {fbx.stem}")
                all_anim_glbs.append(glb_out)
            else:
                log(f"{CROSS} {fbx.stem} — failed", RED)

    # === Gate 5: Merge ===
    output_path = avatar_dir / f"{avatar_name}.glb"

    print(f"\n  {ARROW} Merging {len(all_anim_glbs)} animations...")

    added = merge_animations(base_model, sorted(all_anim_glbs), output_path)

    # Copy to user/avatar/ for web UI
    webui_path = AVATAR_DIR / f"{avatar_name}.glb"
    shutil.copy2(output_path, webui_path)

    # === Report ===
    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"\n{GREEN}{'=' * 50}")
    print(f"  Done! {len(added)} animations merged")
    print(f"{'=' * 50}{RESET}")
    print(f"\n  {CHECK} Output: user/avatar/{avatar_name}/{avatar_name}.glb")
    print(f"  {CHECK} Copied: user/avatar/{avatar_name}.glb (for web UI)")
    print(f"  {CHECK} Size:   {size_mb:.1f} MB")
    print(f"\n  Tracks:")
    for t in added:
        print(f"    {t['name']:<35} {t['duration']:>6.1f}s")
    print(f"\n  {DIM}Select '{avatar_name}' in Settings > Avatar.{RESET}\n")


if __name__ == "__main__":
    main()
