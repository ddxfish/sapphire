#!/usr/bin/env python3
"""THIS SCRIPT IS ONLY for the original sapphire.glb file, not for any other models

Build a combined avatar GLB from THE sapphire base model + Mixamo FBX animations.

Usage:
    python plugins/avatar/build_avatar.py

Directory structure (under user/avatar/):
    model/          <- Put your rigged model here (GLB or FBX, Mixamo bone names)
    animations/     <- Put Mixamo FBX files here (downloaded "Without Skin")
    output/         <- Combined GLB appears here

Requirements:
    - Blender installed and on PATH (for FBX -> GLB conversion)
    - Base model must use Mixamo bone naming (Hips, Spine, LeftArm, etc.)
    - FBX animations from Mixamo downloaded with "Without Skin" option

What it does:
    1. Finds your base model (GLB or FBX) in model/
    2. Converts FBX files to GLB via Blender (base model + animations)
    3. Merges all animation tracks into the base model (rotation + Hips position)
    4. Scales Hips position from Mixamo cm to your model's meter scale
    5. Names tracks after the source filenames
    6. Writes the combined GLB to output/
"""

import json
import struct
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
AVATAR_DIR = PROJECT_ROOT / "user" / "avatar"
MODEL_DIR = AVATAR_DIR / "model"
ANIM_DIR = AVATAR_DIR / "animations"
OUTPUT_DIR = AVATAR_DIR / "output"

# Colors for terminal output
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


def get_hips_rest_y(gltf):
    """Find the Hips bone rest Y position."""
    for node in gltf.get("nodes", []):
        name = node.get("name", "").replace("mixamorig:", "").replace("Armature|", "")
        if name == "Hips":
            return node.get("translation", [0, 1, 0])[1]
    return 1.0


def convert_fbx_to_glb(fbx_path, glb_path):
    """Convert FBX to GLB using Blender headless."""
    script = '''
import bpy, sys
argv = sys.argv[sys.argv.index("--") + 1:]
bpy.ops.wm.read_homefile(use_empty=True)
bpy.ops.import_scene.fbx(filepath=argv[0])
# Apply armature transforms so root rotation/scale is baked into the mesh
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
        # Strip conda from env so Blender uses system Python
        import platform
        env = {k: v for k, v in os.environ.items()
               if k not in ("CONDA_PREFIX", "CONDA_DEFAULT_ENV", "CONDA_EXE")}
        if platform.system() != "Windows":
            env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"

        result = subprocess.run(
            ["blender", "--background", "--factory-startup", "--python", script_path,
             "--", str(fbx_path), str(glb_path)],
            capture_output=True, text=True, timeout=60, env=env,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False
    except subprocess.TimeoutExpired:
        return False
    finally:
        Path(script_path).unlink(missing_ok=True)


def merge_animations(base_path, anim_glbs, output_path):
    """Merge animation GLBs into base model. Rotation + scaled Hips position."""
    base_gltf, base_bin = read_glb(base_path)
    result_bin = bytearray(base_bin)

    # Build bone name -> node index map for base
    # Strip common prefixes (mixamorig:, Armature|) for flexible matching
    base_name_to_idx = {}
    base_name_raw = {}  # clean name -> node index
    for j in base_gltf["skins"][0]["joints"]:
        raw_name = base_gltf["nodes"][j]["name"]
        base_name_to_idx[raw_name] = j
        clean = raw_name.replace("mixamorig:", "").replace("Armature|", "")
        base_name_raw[clean] = j

    base_hips_y = get_hips_rest_y(base_gltf)
    base_gltf["animations"] = []
    added = []

    for glb_path in anim_glbs:
        anim_gltf, anim_bin = read_glb(str(glb_path))

        # Build node remap by name — try exact match first, then stripped prefix
        remap = {}
        anim_hips_idx = None
        for i, node in enumerate(anim_gltf.get("nodes", [])):
            name = node.get("name", "")
            clean = name.replace("mixamorig:", "").replace("Armature|", "")
            if name in base_name_to_idx:
                remap[i] = base_name_to_idx[name]
            elif clean in base_name_raw:
                remap[i] = base_name_raw[clean]
            if clean == "Hips":
                anim_hips_idx = i

        if not remap:
            log(f"  {CROSS} {glb_path.stem}: no matching bones, skipped")
            continue

        # Hips position scale factor
        src_hips_y = (anim_gltf["nodes"][anim_hips_idx].get("translation", [0, 100, 0])[1]
                      if anim_hips_idx is not None else 100.0)
        pos_scale = base_hips_y / src_hips_y

        for anim in anim_gltf.get("animations", []):
            # Get duration, skip T-pose
            max_time = 0
            for s in anim.get("samplers", []):
                acc_idx = s.get("input")
                if acc_idx is not None and acc_idx < len(anim_gltf.get("accessors", [])):
                    acc = anim_gltf["accessors"][acc_idx]
                    if "max" in acc:
                        max_time = max(max_time, acc["max"][0])
            if max_time < 0.1:
                continue

            # Identify samplers to keep: rotation + Hips translation
            keep_samplers = set()
            hips_pos_samplers = set()
            for ch in anim.get("channels", []):
                target = ch.get("target", {})
                path = target.get("path", "")
                old_node = target.get("node")
                if old_node not in remap:
                    continue
                if path == "rotation":
                    keep_samplers.add(ch.get("sampler"))
                elif path == "translation" and old_node == anim_hips_idx:
                    keep_samplers.add(ch.get("sampler"))
                    hips_pos_samplers.add(ch.get("sampler"))

            # Collect referenced accessors and buffer views
            kept_acc = set()
            for si in keep_samplers:
                s = anim["samplers"][si]
                if "input" in s: kept_acc.add(s["input"])
                if "output" in s: kept_acc.add(s["output"])

            kept_bv = set()
            for ai in kept_acc:
                if ai < len(anim_gltf.get("accessors", [])):
                    bv = anim_gltf["accessors"][ai].get("bufferView")
                    if bv is not None:
                        kept_bv.add(bv)

            # Copy buffer views
            bv_remap = {}
            for old_bv in sorted(kept_bv):
                bv = dict(anim_gltf["bufferViews"][old_bv])
                old_off = bv.get("byteOffset", 0)
                new_off = len(result_bin)
                result_bin.extend(anim_bin[old_off:old_off + bv["byteLength"]])
                new_bv = dict(bv)
                new_bv["buffer"] = 0
                new_bv["byteOffset"] = new_off
                base_gltf.setdefault("bufferViews", []).append(new_bv)
                bv_remap[old_bv] = len(base_gltf["bufferViews"]) - 1

            # Copy accessors
            acc_remap = {}
            for old_ai in sorted(kept_acc):
                if old_ai >= len(anim_gltf.get("accessors", [])):
                    continue
                acc = dict(anim_gltf["accessors"][old_ai])
                old_bv = acc.get("bufferView")
                if old_bv is not None and old_bv in bv_remap:
                    acc["bufferView"] = bv_remap[old_bv]
                base_gltf.setdefault("accessors", []).append(acc)
                acc_remap[old_ai] = len(base_gltf["accessors"]) - 1

            # Build samplers
            new_samplers = []
            sampler_remap = {}
            for old_si in sorted(keep_samplers):
                s = anim["samplers"][old_si]
                ns = {}
                if "input" in s and s["input"] in acc_remap:
                    ns["input"] = acc_remap[s["input"]]
                if "output" in s and s["output"] in acc_remap:
                    ns["output"] = acc_remap[s["output"]]
                if "interpolation" in s:
                    ns["interpolation"] = s["interpolation"]
                if "input" in ns and "output" in ns:
                    sampler_remap[old_si] = len(new_samplers)
                    new_samplers.append(ns)

            # Build channels — rotation + Hips translation only
            new_channels = []
            for ch in anim.get("channels", []):
                old_s = ch.get("sampler")
                if old_s not in sampler_remap:
                    continue
                target = ch.get("target", {})
                path = target.get("path", "")
                old_node = target.get("node")
                new_node = remap.get(old_node)
                if new_node is None:
                    continue
                if path == "rotation" or (path == "translation" and old_node == anim_hips_idx):
                    new_channels.append({
                        "sampler": sampler_remap[old_s],
                        "target": {"node": new_node, "path": path},
                    })

            # Scale Hips position data
            for old_si in hips_pos_samplers:
                if old_si not in sampler_remap:
                    continue
                sampler = new_samplers[sampler_remap[old_si]]
                out_acc = base_gltf["accessors"][sampler["output"]]
                if out_acc.get("type") != "VEC3":
                    continue
                bv = base_gltf["bufferViews"][out_acc["bufferView"]]
                offset = bv.get("byteOffset", 0) + out_acc.get("byteOffset", 0)
                count = out_acc.get("count", 0)
                for i in range(count):
                    pos = offset + i * 12
                    x, y, z = struct.unpack_from("<fff", result_bin, pos)
                    struct.pack_into("<fff", result_bin, pos,
                                    x * pos_scale, y * pos_scale, z * pos_scale)

            if new_channels and new_samplers:
                track_name = glb_path.stem
                # Deduplicate names
                existing = {a["name"] for a in base_gltf["animations"]}
                if track_name in existing:
                    c = 2
                    while f"{track_name}_{c}" in existing:
                        c += 1
                    track_name = f"{track_name}_{c}"

                base_gltf["animations"].append({
                    "name": track_name,
                    "samplers": new_samplers,
                    "channels": new_channels,
                })
                added.append({"name": track_name, "duration": round(max_time, 1)})

    # Update buffer size
    if base_gltf.get("buffers"):
        base_gltf["buffers"][0]["byteLength"] = len(result_bin)

    write_glb(base_gltf, bytes(result_bin), output_path)
    return added


def main():
    print(f"\n{CYAN}{'=' * 50}")
    print(f"  Sapphire Avatar Builder")
    print(f"{'=' * 50}{RESET}\n")

    # === Gate 1: Check directories ===
    for d in (MODEL_DIR, ANIM_DIR):
        d.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # === Gate 2: Find base model (GLB or FBX) ===
    models = list(MODEL_DIR.glob("*.glb")) + list(MODEL_DIR.glob("*.fbx"))
    if not models:
        print(f"  {CROSS} No model found in {MODEL_DIR.relative_to(PROJECT_ROOT)}/")
        print(f"  {DIM}Put your Mixamo-rigged GLB or FBX model there and run again.{RESET}")
        sys.exit(1)
    if len(models) > 1:
        print(f"  {CROSS} Multiple model files in model/ — put only one.")
        for m in models:
            print(f"    {m.name}")
        sys.exit(1)

    base_model = models[0]

    # Convert FBX base model to GLB if needed
    if base_model.suffix.lower() == ".fbx":
        blender_ok = shutil.which("blender") is not None
        if not blender_ok:
            print(f"  {CROSS} Blender not found — needed to convert FBX base model.")
            print(f"  {DIM}Install Blender or convert your model to GLB manually.{RESET}")
            sys.exit(1)
        glb_model = MODEL_DIR / f"{base_model.stem}.glb"
        if not glb_model.exists():
            print(f"  {ARROW} Converting base model FBX -> GLB via Blender...")
            ok = convert_fbx_to_glb(base_model, glb_model)
            if not ok or not glb_model.exists():
                print(f"  {CROSS} Base model conversion failed.")
                sys.exit(1)
            print(f"  {CHECK} Converted {base_model.name} -> {glb_model.name}")
        base_model = glb_model

    base_gltf, _ = read_glb(base_model)
    skins = base_gltf.get("skins", [])
    joint_count = len(skins[0].get("joints", [])) if skins else 0
    morph_count = 0
    for mesh in base_gltf.get("meshes", []):
        morph_count = max(morph_count, len(mesh.get("extras", {}).get("targetNames", [])))
    hips_y = get_hips_rest_y(base_gltf)

    print(f"  {CHECK} Model: {base_model.name}")
    print(f"    {DIM}{joint_count} bones, {morph_count} blendshapes, Hips Y={hips_y:.3f}{RESET}")

    # === Gate 3: Find animations ===
    fbx_files = sorted(ANIM_DIR.glob("*.fbx"))
    glb_files = sorted(ANIM_DIR.glob("*.glb"))
    total_anims = len(fbx_files) + len(glb_files)

    if total_anims == 0:
        print(f"\n  {CROSS} No animation files in {ANIM_DIR.relative_to(PROJECT_ROOT)}/")
        print(f"  {DIM}Put Mixamo FBX or GLB animation files there and run again.{RESET}")
        sys.exit(1)

    print(f"\n  {CHECK} Found {total_anims} animation files ({len(fbx_files)} FBX, {len(glb_files)} GLB)")

    # === Gate 4: Convert FBX -> GLB ===
    converted_dir = ANIM_DIR / "_converted"
    all_anim_glbs = list(glb_files)  # GLBs are ready to go

    if fbx_files:
        # Check Blender
        blender_ok = shutil.which("blender") is not None
        if not blender_ok:
            print(f"\n  {CROSS} Blender not found — needed to convert FBX files.")
            print(f"  {DIM}Install Blender or convert FBX to GLB manually.{RESET}")
            sys.exit(1)

        converted_dir.mkdir(exist_ok=True)
        print(f"\n  {ARROW} Converting FBX files via Blender...")

        for fbx in fbx_files:
            glb_out = converted_dir / f"{fbx.stem}.glb"
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
                log(f"{CROSS} {fbx.stem} — conversion failed", RED)

    # === Gate 5: Merge ===
    output_name = base_model.stem + "_combined.glb"
    output_path = OUTPUT_DIR / output_name

    print(f"\n  {ARROW} Merging {len(all_anim_glbs)} animations into {base_model.name}...")

    added = merge_animations(base_model, sorted(all_anim_glbs), output_path)

    # === Report ===
    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"\n{GREEN}{'=' * 50}")
    print(f"  Done! {len(added)} animations merged")
    print(f"{'=' * 50}{RESET}")
    print(f"\n  {CHECK} Output: {output_path.relative_to(PROJECT_ROOT)}")
    print(f"  {CHECK} Size: {size_mb:.1f} MB")
    print(f"\n  Tracks:")
    for t in added:
        print(f"    {t['name']:<35} {t['duration']:>6.1f}s")

    print(f"\n  {DIM}Copy {output_name} to user/avatar/ and select it in Settings > Avatar.{RESET}\n")


if __name__ == "__main__":
    main()
