"""Bone name mapper — converts characters3d.com animations to Sapphire's Mixamo rig.

Both rigs are standard humanoid with the same hierarchy, just different naming.
Characters3d: 52 bones (L_Upper_Arm, R_Hand, Hips, etc.)
Sapphire (Mixamo): 100 bones (LeftArm, RightHand, Hips, etc. + hair/coat/breast)

Usage:
    from plugins.avatar.bone_mapper import remap_animation
    remap_animation('user/avatar/walk.glb', 'user/avatar/sapphire.glb', 'user/avatar/sapphire_with_walk.glb')
"""

import json
import math
import struct
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# ═══ Quaternion math (x, y, z, w — glTF order) ═══

def quat_multiply(a, b):
    """Multiply two quaternions (x,y,z,w format)."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw*bx + ax*bw + ay*bz - az*by,
        aw*by - ax*bz + ay*bw + az*bx,
        aw*bz + ax*by - ay*bx + az*bw,
        aw*bw - ax*bx - ay*by - az*bz,
    )

def quat_inverse(q):
    """Inverse of a unit quaternion."""
    x, y, z, w = q
    return (-x, -y, -z, w)

def quat_normalize(q):
    """Normalize a quaternion."""
    x, y, z, w = q
    length = math.sqrt(x*x + y*y + z*z + w*w)
    if length < 1e-10:
        return (0, 0, 0, 1)
    return (x/length, y/length, z/length, w/length)

# characters3d bone name → Mixamo bone name (Sapphire's rig)
BONE_MAP = {
    'RootNode':              'Hips',        # root maps to Hips (Mixamo has no separate root)
    'Hips':                  'Hips',
    'Spine':                 'Spine',
    'Chest':                 'Spine1',
    'Upper_Chest':           'Spine2',
    'Neck':                  'Neck',
    'Head':                  'Head',
    # Left arm
    'L_Shoulder':            'LeftShoulder',
    'L_Upper_Arm':           'LeftArm',
    'L_Lower_Arm':           'LeftForeArm',
    'L_Hand':                'LeftHand',
    'L_Thumb_Proximal':      'LeftHandThumb1',
    'L_Thumb_Intermediate':  'LeftHandThumb2',
    'L_Thumb_Distal':        'LeftHandThumb3',
    'L_Index_Proximal':      'LeftHandIndex1',
    'L_Index_Intermediate':  'LeftHandIndex2',
    'L_Index_Distal':        'LeftHandIndex3',
    'L_Middle_Proximal':     'LeftHandMiddle1',
    'L_Middle_Intermediate': 'LeftHandMiddle2',
    'L_Middle_Distal':       'LeftHandMiddle3',
    'L_Ring_Proximal':       'LeftHandRing1',
    'L_Ring_Intermediate':   'LeftHandRing2',
    'L_Ring_Distal':         'LeftHandRing3',
    'L_Little_Proximal':     'LeftHandPinky1',
    'L_Little_Intermediate': 'LeftHandPinky2',
    'L_Little_Distal':       'LeftHandPinky3',
    # Right arm
    'R_Shoulder':            'RightShoulder',
    'R_Upper_Arm':           'RightArm',
    'R_Lower_Arm':           'RightForeArm',
    'R_Hand':                'RightHand',
    'R_Thumb_Proximal':      'RightHandThumb1',
    'R_Thumb_Intermediate':  'RightHandThumb2',
    'R_Thumb_Distal':        'RightHandThumb3',
    'R_Index_Proximal':      'RightHandIndex1',
    'R_Index_Intermediate':  'RightHandIndex2',
    'R_Index_Distal':        'RightHandIndex3',
    'R_Middle_Proximal':     'RightHandMiddle1',
    'R_Middle_Intermediate': 'RightHandMiddle2',
    'R_Middle_Distal':       'RightHandMiddle3',
    'R_Ring_Proximal':       'RightHandRing1',
    'R_Ring_Intermediate':   'RightHandRing2',
    'R_Ring_Distal':         'RightHandRing3',
    'R_Little_Proximal':     'RightHandPinky1',
    'R_Little_Intermediate': 'RightHandPinky2',
    'R_Little_Distal':       'RightHandPinky3',
    # Left leg
    'L_Upper_Leg':           'LeftUpLeg',
    'L_Lower_Leg':           'LeftLeg',
    'L_Foot':                'LeftFoot',
    'L_Toes':                'LeftToeBase',
    # Right leg
    'R_Upper_Leg':           'RightUpLeg',
    'R_Lower_Leg':           'RightLeg',
    'R_Foot':                'RightFoot',
    'R_Toes':                'RightToeBase',
}


def _read_glb(path):
    with open(path, 'rb') as f:
        magic, version, length = struct.unpack('<III', f.read(12))
        if magic != 0x46546C67:
            raise ValueError(f"Not a GLB: {path}")
        chunk_len, chunk_type = struct.unpack('<II', f.read(8))
        gltf = json.loads(f.read(chunk_len).decode('utf-8'))
        remaining = length - 12 - 8 - chunk_len
        bin_chunk = b''
        if remaining > 8:
            bin_len, bin_type = struct.unpack('<II', f.read(8))
            bin_chunk = f.read(bin_len)
    return gltf, bin_chunk


def _write_glb(gltf, bin_chunk, path):
    json_bytes = json.dumps(gltf, separators=(',', ':')).encode('utf-8')
    json_pad = (4 - len(json_bytes) % 4) % 4
    json_bytes += b' ' * json_pad
    bin_pad = (4 - len(bin_chunk) % 4) % 4
    bin_chunk += b'\x00' * bin_pad
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_chunk)
    with open(path, 'wb') as f:
        f.write(struct.pack('<III', 0x46546C67, 2, total))
        f.write(struct.pack('<II', len(json_bytes), 0x4E4F534A))
        f.write(json_bytes)
        f.write(struct.pack('<II', len(bin_chunk), 0x004E4942))
        f.write(bin_chunk)


def _build_node_name_map(gltf):
    """node index → bone name"""
    return {i: n.get('name', f'node_{i}') for i, n in enumerate(gltf.get('nodes', []))}


def _get_rest_rotations(gltf):
    """Extract rest pose rotation for each node index. Returns {node_idx: (x,y,z,w)}."""
    rots = {}
    for i, node in enumerate(gltf.get('nodes', [])):
        rot = node.get('rotation', [0, 0, 0, 1])  # glTF default = identity
        rots[i] = tuple(rot)
    return rots


def _strip_prefix(name):
    """Remove characters3d.com___ prefix"""
    return name.replace('characters3d.com___', '').replace('characters3d.com | ', '')


def remap_animation(anim_glb_path, base_glb_path, output_path, skip_tpose=True, clean_names=True):
    """Take animations from anim_glb and remap bone targets to base_glb's skeleton.

    Args:
        anim_glb_path: GLB with animations (characters3d format)
        base_glb_path: GLB with the target skeleton (sapphire)
        output_path: Where to write the combined GLB

    Returns:
        dict with results: {added_tracks, skipped_bones, all_tracks}
    """
    anim_glb_path = Path(anim_glb_path)
    base_glb_path = Path(base_glb_path)
    output_path = Path(output_path)

    base_gltf, base_bin = _read_glb(base_glb_path)
    anim_gltf, anim_bin = _read_glb(anim_glb_path)

    result_bin = bytearray(base_bin)

    base_names = _build_node_name_map(base_gltf)
    anim_names = _build_node_name_map(anim_gltf)

    # Rest pose rotations for correction
    src_rest = _get_rest_rotations(anim_gltf)
    dst_rest = _get_rest_rotations(base_gltf)

    # Build reverse map: sapphire bone name → node index
    sapphire_name_to_idx = {}
    for idx, name in base_names.items():
        sapphire_name_to_idx[name] = idx

    # Build: anim node index → base node index (via BONE_MAP)
    remap = {}
    skipped = []
    for anim_idx, anim_name in anim_names.items():
        clean = _strip_prefix(anim_name)
        sapphire_name = BONE_MAP.get(clean)
        if sapphire_name and sapphire_name in sapphire_name_to_idx:
            remap[anim_idx] = sapphire_name_to_idx[sapphire_name]
        else:
            skipped.append(clean)

    logger.info(f"[BoneMapper] Mapped {len(remap)}/{len(anim_names)} bones, skipped: {skipped}")

    if not remap:
        raise ValueError("No bones could be mapped — rigs are incompatible")

    if 'animations' not in base_gltf:
        base_gltf['animations'] = []

    added_tracks = []

    for anim in anim_gltf.get('animations', []):
        name = anim.get('name', 'unnamed')

        if clean_names:
            name = _strip_prefix(name)
            if '|' in name:
                name = name.split('|')[-1].strip()

        # Skip T-Pose
        if skip_tpose:
            max_time = 0
            for s in anim.get('samplers', []):
                acc_idx = s.get('input')
                if acc_idx is not None and acc_idx < len(anim_gltf.get('accessors', [])):
                    acc = anim_gltf['accessors'][acc_idx]
                    if 'max' in acc:
                        max_time = max(max_time, acc['max'][0])
            if max_time < 0.1:
                logger.info(f"[BoneMapper] Skipping T-Pose from {anim_glb_path.name}")
                continue

        # Collect referenced accessors and bufferViews
        referenced_accessors = set()
        for s in anim.get('samplers', []):
            if 'input' in s: referenced_accessors.add(s['input'])
            if 'output' in s: referenced_accessors.add(s['output'])

        referenced_bvs = set()
        for acc_idx in referenced_accessors:
            if acc_idx < len(anim_gltf.get('accessors', [])):
                bv = anim_gltf['accessors'][acc_idx].get('bufferView')
                if bv is not None: referenced_bvs.add(bv)

        # Copy bufferViews
        bv_remap = {}
        for old_bv_idx in sorted(referenced_bvs):
            bv = dict(anim_gltf['bufferViews'][old_bv_idx])
            old_offset = bv.get('byteOffset', 0)
            old_length = bv['byteLength']
            new_offset = len(result_bin)
            result_bin.extend(anim_bin[old_offset:old_offset + old_length])
            new_bv = dict(bv)
            new_bv['buffer'] = 0
            new_bv['byteOffset'] = new_offset
            new_bv_idx = len(base_gltf.get('bufferViews', []))
            base_gltf.setdefault('bufferViews', []).append(new_bv)
            bv_remap[old_bv_idx] = new_bv_idx

        # Copy accessors
        acc_remap = {}
        for old_acc_idx in sorted(referenced_accessors):
            if old_acc_idx >= len(anim_gltf.get('accessors', [])): continue
            acc = dict(anim_gltf['accessors'][old_acc_idx])
            old_bv = acc.get('bufferView')
            if old_bv is not None and old_bv in bv_remap:
                acc['bufferView'] = bv_remap[old_bv]
            new_acc_idx = len(base_gltf.get('accessors', []))
            base_gltf.setdefault('accessors', []).append(acc)
            acc_remap[old_acc_idx] = new_acc_idx

        # Build remapped samplers
        new_samplers = []
        sampler_remap = {}
        for old_s_idx, s in enumerate(anim.get('samplers', [])):
            new_s = {}
            if 'input' in s and s['input'] in acc_remap: new_s['input'] = acc_remap[s['input']]
            if 'output' in s and s['output'] in acc_remap: new_s['output'] = acc_remap[s['output']]
            if 'interpolation' in s: new_s['interpolation'] = s['interpolation']
            if 'input' in new_s and 'output' in new_s:
                sampler_remap[old_s_idx] = len(new_samplers)
                new_samplers.append(new_s)

        # Build remapped channels (this is where the bone mapping happens)
        new_channels = []
        for ch in anim.get('channels', []):
            old_s = ch.get('sampler')
            if old_s not in sampler_remap: continue
            target = ch.get('target', {})
            old_node = target.get('node')
            new_node = remap.get(old_node)
            if new_node is None: continue  # bone doesn't exist in sapphire, skip channel
            new_channels.append({
                'sampler': sampler_remap[old_s],
                'target': {'node': new_node, 'path': target.get('path', 'rotation')},
            })

        # ═══ REST POSE CORRECTION ═══
        # Convert keyframes from source rest space to target rest space.
        # Each keyframe is a local rotation relative to the bone's rest pose.
        # To retarget: strip the source rest, apply the destination rest.
        # corrected = inverse(dst_rest) * src_rest * keyframe * inverse(src_rest) * dst_rest
        # Simplified with pre/post multipliers applied per keyframe.
        for ch in new_channels:
            if ch['target']['path'] != 'rotation':
                continue

            old_node = None
            # Find the original source node for this channel
            for orig_ch in anim.get('channels', []):
                orig_target = orig_ch.get('target', {})
                mapped = remap.get(orig_target.get('node'))
                if mapped == ch['target']['node'] and orig_target.get('path') == 'rotation':
                    old_node = orig_target['node']
                    break

            if old_node is None:
                continue

            new_node = ch['target']['node']
            src_rot = src_rest.get(old_node, (0, 0, 0, 1))
            dst_rot = dst_rest.get(new_node, (0, 0, 0, 1))

            # Pre-multiplier: inverse(dst_rest) * src_rest
            pre = quat_normalize(quat_multiply(quat_inverse(dst_rot), src_rot))
            # Post-multiplier: inverse(src_rest) * dst_rest
            post = quat_normalize(quat_multiply(quat_inverse(src_rot), dst_rot))

            # Skip if both are near identity (same rest pose)
            px, py, pz, pw = pre
            if abs(pw) > 0.9999 and (px*px + py*py + pz*pz) < 0.0001:
                continue

            # Find the output accessor for this channel's sampler
            sampler_idx = ch['sampler']
            sampler = new_samplers[sampler_idx]
            out_acc_idx = sampler['output']
            out_acc = base_gltf['accessors'][out_acc_idx]

            if out_acc.get('type') != 'VEC4':
                continue

            # Get buffer view
            bv_idx = out_acc.get('bufferView')
            if bv_idx is None:
                continue
            bv = base_gltf['bufferViews'][bv_idx]
            offset = bv.get('byteOffset', 0) + out_acc.get('byteOffset', 0)
            count = out_acc.get('count', 0)
            stride = bv.get('byteStride', 16)  # 4 floats * 4 bytes = 16

            # Rewrite each quaternion in the buffer
            for i in range(count):
                pos = offset + i * stride
                x, y, z, w = struct.unpack_from('<ffff', result_bin, pos)
                # corrected = pre * keyframe * post
                temp = quat_multiply(pre, (x, y, z, w))
                corrected = quat_normalize(quat_multiply(temp, post))
                struct.pack_into('<ffff', result_bin, pos, *corrected)

            logger.debug(f"[BoneMapper] Corrected rotation for bone {new_node} ({count} keyframes)")

        if new_channels and new_samplers:
            # Deduplicate name
            existing = [a.get('name', '') for a in base_gltf['animations']]
            final_name = name
            if final_name in existing:
                counter = 2
                while f"{final_name}_{counter}" in existing: counter += 1
                final_name = f"{final_name}_{counter}"

            base_gltf['animations'].append({
                'name': final_name, 'samplers': new_samplers, 'channels': new_channels,
            })

            max_time = 0
            for s in new_samplers:
                acc = base_gltf['accessors'][s['input']]
                if 'max' in acc: max_time = max(max_time, acc['max'][0])

            added_tracks.append({'name': final_name, 'duration': round(max_time, 2)})
            logger.info(f"[BoneMapper] Added '{final_name}' ({max_time:.2f}s) — {len(new_channels)} channels")

    # Update buffer size
    if base_gltf.get('buffers'):
        base_gltf['buffers'][0]['byteLength'] = len(result_bin)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_glb(base_gltf, bytes(result_bin), output_path)

    all_tracks = []
    for a in base_gltf.get('animations', []):
        max_t = 0
        for s in a.get('samplers', []):
            acc = base_gltf['accessors'][s['input']]
            if 'max' in acc: max_t = max(max_t, acc['max'][0])
        all_tracks.append({'name': a['name'], 'duration': round(max_t, 2)})

    return {
        'added_tracks': added_tracks,
        'all_tracks': all_tracks,
        'mapped_bones': len(remap),
        'skipped_bones': skipped,
        'output': str(output_path),
        'size': output_path.stat().st_size,
    }
