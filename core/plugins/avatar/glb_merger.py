"""GLB merger — combine a base model with animation-only GLBs into one file.

Pure Python, no external deps. Works when all files share the same skeleton
(same bone names, same joint count).

Usage:
    from plugins.avatar.glb_merger import merge_glb
    merge_glb(
        base_path='user/avatar/model.glb',
        anim_paths=['user/avatar/idle.glb', 'user/avatar/attack.glb'],
        output_path='user/avatar/combined.glb',
        skip_tpose=True,  # skip short T-Pose tracks
    )
"""

import json
import struct
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _read_glb(path):
    """Read a GLB and return (gltf_json, binary_chunk)."""
    with open(path, 'rb') as f:
        magic, version, length = struct.unpack('<III', f.read(12))
        if magic != 0x46546C67:
            raise ValueError(f"Not a GLB file: {path}")

        # JSON chunk
        json_len, json_type = struct.unpack('<II', f.read(8))
        gltf = json.loads(f.read(json_len).decode('utf-8'))

        # Binary chunk (may not exist in animation-only files, but usually does)
        remaining = length - 12 - 8 - json_len
        bin_chunk = b''
        if remaining > 8:
            bin_len, bin_type = struct.unpack('<II', f.read(8))
            bin_chunk = f.read(bin_len)

        return gltf, bin_chunk


def _write_glb(gltf, bin_chunk, output_path):
    """Write a GLB file from gltf JSON + binary chunk."""
    json_bytes = json.dumps(gltf, separators=(',', ':')).encode('utf-8')
    # Pad JSON to 4-byte boundary
    json_pad = (4 - len(json_bytes) % 4) % 4
    json_bytes += b' ' * json_pad
    # Pad binary to 4-byte boundary
    bin_pad = (4 - len(bin_chunk) % 4) % 4
    bin_chunk += b'\x00' * bin_pad

    total_length = 12 + 8 + len(json_bytes) + 8 + len(bin_chunk)

    with open(output_path, 'wb') as f:
        # Header
        f.write(struct.pack('<III', 0x46546C67, 2, total_length))
        # JSON chunk
        f.write(struct.pack('<II', len(json_bytes), 0x4E4F534A))
        f.write(json_bytes)
        # Binary chunk
        f.write(struct.pack('<II', len(bin_chunk), 0x004E4942))
        f.write(bin_chunk)


def _build_joint_remap(base_gltf, anim_gltf):
    """Build a node index mapping from anim skeleton to base skeleton by bone name."""
    base_skin = base_gltf.get('skins', [{}])[0]
    anim_skin = anim_gltf.get('skins', [{}])[0]

    base_joints = base_skin.get('joints', [])
    anim_joints = anim_skin.get('joints', [])

    # Build name -> base node index
    base_name_to_idx = {}
    for j in base_joints:
        name = base_gltf['nodes'][j].get('name', '')
        base_name_to_idx[name] = j

    # Map anim node index -> base node index
    remap = {}
    for j in anim_joints:
        name = anim_gltf['nodes'][j].get('name', '')
        if name in base_name_to_idx:
            remap[j] = base_name_to_idx[name]

    return remap


def merge_glb(base_path, anim_paths, output_path, skip_tpose=True, clean_names=True):
    """Merge a base model GLB with animation GLBs.

    Args:
        base_path: Path to the base model (mesh + skeleton, may have animations)
        anim_paths: List of paths to animation GLBs (same skeleton)
        output_path: Where to write the combined GLB
        skip_tpose: Skip animations shorter than 0.1s (T-Pose markers)
        clean_names: Strip site prefixes from track names (e.g. "www.characters3d.com | Idle" -> "Idle")

    Returns:
        dict with merge info: {tracks: [{name, duration}], joints: int, path: str}
    """
    base_path = Path(base_path)
    output_path = Path(output_path)

    base_gltf, base_bin = _read_glb(base_path)
    result_bin = bytearray(base_bin)

    # Track what we're adding
    added_tracks = []
    base_accessor_count = len(base_gltf.get('accessors', []))
    base_bufferview_count = len(base_gltf.get('bufferViews', []))

    if 'animations' not in base_gltf:
        base_gltf['animations'] = []

    for anim_path in anim_paths:
        anim_path = Path(anim_path)
        if not anim_path.exists():
            logger.warning(f"[GLB Merger] Skipping missing file: {anim_path}")
            continue

        try:
            anim_gltf, anim_bin = _read_glb(anim_path)
        except Exception as e:
            logger.warning(f"[GLB Merger] Failed to read {anim_path}: {e}")
            continue

        # Build bone name remapping
        remap = _build_joint_remap(base_gltf, anim_gltf)

        if not remap:
            logger.warning(f"[GLB Merger] No matching bones in {anim_path.name}, skipping")
            continue

        for anim in anim_gltf.get('animations', []):
            name = anim.get('name', 'unnamed')

            # Clean up track names
            if clean_names and '|' in name:
                name = name.split('|')[-1].strip()

            # Skip T-Pose markers
            if skip_tpose:
                max_time = 0
                for s in anim.get('samplers', []):
                    acc_idx = s.get('input')
                    if acc_idx is not None and acc_idx < len(anim_gltf.get('accessors', [])):
                        acc = anim_gltf['accessors'][acc_idx]
                        if 'max' in acc:
                            max_time = max(max_time, acc['max'][0])
                if max_time < 0.1:
                    logger.info(f"[GLB Merger] Skipping T-Pose track from {anim_path.name}")
                    continue

            # Offset for binary data we're about to append
            bin_offset = len(result_bin)

            # Copy buffer views and accessors, remapping indices
            bv_remap = {}  # old bufferView index -> new
            acc_remap = {}  # old accessor index -> new

            # First pass: collect all bufferViews and accessors referenced by this animation
            referenced_accessors = set()
            for s in anim.get('samplers', []):
                if 'input' in s:
                    referenced_accessors.add(s['input'])
                if 'output' in s:
                    referenced_accessors.add(s['output'])

            referenced_bvs = set()
            for acc_idx in referenced_accessors:
                if acc_idx < len(anim_gltf.get('accessors', [])):
                    bv_idx = anim_gltf['accessors'][acc_idx].get('bufferView')
                    if bv_idx is not None:
                        referenced_bvs.add(bv_idx)

            # Copy referenced bufferViews
            for old_bv_idx in sorted(referenced_bvs):
                bv = dict(anim_gltf['bufferViews'][old_bv_idx])
                old_offset = bv.get('byteOffset', 0)
                old_length = bv['byteLength']

                # Copy binary data
                new_offset = len(result_bin)
                result_bin.extend(anim_bin[old_offset:old_offset + old_length])

                # Create new bufferView
                new_bv = dict(bv)
                new_bv['buffer'] = 0
                new_bv['byteOffset'] = new_offset
                new_bv_idx = len(base_gltf.get('bufferViews', []))
                base_gltf.setdefault('bufferViews', []).append(new_bv)
                bv_remap[old_bv_idx] = new_bv_idx

            # Copy referenced accessors
            for old_acc_idx in sorted(referenced_accessors):
                if old_acc_idx >= len(anim_gltf.get('accessors', [])):
                    continue
                acc = dict(anim_gltf['accessors'][old_acc_idx])
                old_bv = acc.get('bufferView')
                if old_bv is not None and old_bv in bv_remap:
                    acc['bufferView'] = bv_remap[old_bv]
                new_acc_idx = len(base_gltf.get('accessors', []))
                base_gltf.setdefault('accessors', []).append(acc)
                acc_remap[old_acc_idx] = new_acc_idx

            # Build the new animation with remapped indices
            new_samplers = []
            sampler_remap = {}
            for old_s_idx, s in enumerate(anim.get('samplers', [])):
                new_s = {}
                if 'input' in s and s['input'] in acc_remap:
                    new_s['input'] = acc_remap[s['input']]
                if 'output' in s and s['output'] in acc_remap:
                    new_s['output'] = acc_remap[s['output']]
                if 'interpolation' in s:
                    new_s['interpolation'] = s['interpolation']
                if 'input' in new_s and 'output' in new_s:
                    sampler_remap[old_s_idx] = len(new_samplers)
                    new_samplers.append(new_s)

            new_channels = []
            for ch in anim.get('channels', []):
                old_s = ch.get('sampler')
                if old_s not in sampler_remap:
                    continue
                target = ch.get('target', {})
                old_node = target.get('node')
                # Remap node index to base skeleton
                new_node = remap.get(old_node, old_node)
                new_channels.append({
                    'sampler': sampler_remap[old_s],
                    'target': {
                        'node': new_node,
                        'path': target.get('path', 'rotation'),
                    }
                })

            if new_channels and new_samplers:
                # Check for duplicate track name
                existing_names = [a.get('name', '') for a in base_gltf['animations']]
                final_name = name
                if final_name in existing_names:
                    counter = 2
                    while f"{final_name}_{counter}" in existing_names:
                        counter += 1
                    final_name = f"{final_name}_{counter}"

                base_gltf['animations'].append({
                    'name': final_name,
                    'samplers': new_samplers,
                    'channels': new_channels,
                })

                # Calculate duration for reporting
                max_time = 0
                for s in new_samplers:
                    acc = base_gltf['accessors'][s['input']]
                    if 'max' in acc:
                        max_time = max(max_time, acc['max'][0])

                added_tracks.append({'name': final_name, 'duration': round(max_time, 2)})
                logger.info(f"[GLB Merger] Added track '{final_name}' ({max_time:.2f}s) from {anim_path.name}")

    # Update buffer size
    if base_gltf.get('buffers'):
        base_gltf['buffers'][0]['byteLength'] = len(result_bin)

    # Write combined GLB
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_glb(base_gltf, bytes(result_bin), output_path)

    # Report
    all_tracks = []
    for a in base_gltf.get('animations', []):
        max_t = 0
        for s in a.get('samplers', []):
            acc = base_gltf['accessors'][s['input']]
            if 'max' in acc:
                max_t = max(max_t, acc['max'][0])
        all_tracks.append({'name': a['name'], 'duration': round(max_t, 2)})

    joints = len(base_gltf.get('skins', [{}])[0].get('joints', []))

    return {
        'path': str(output_path),
        'added_tracks': added_tracks,
        'all_tracks': all_tracks,
        'joints': joints,
        'size': output_path.stat().st_size,
    }
