#!/usr/bin/env python3
"""A firmware index for the Devices page's flasher, from ESP-IDF builds.

    python tools/firmware_manifest.py <board-id> <build-dir> <out-root> [--name "..."]

Reads the build's flasher_args.json and project_description.json, copies
the parts to <out-root>/<board-id>/, writes that folder's manifest.json in
the ESP Web Tools form (plus our "flash" settings), and adds or replaces
the board in <out-root>/index.json. Point DEVICE_FIRMWARE_SOURCE at
<out-root> (a folder) or publish it (a URL). core/devices/firmware.py reads
it; the firmware repository's release runs the same script per board.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

FAMILY = {'esp32': 'ESP32', 'esp32s2': 'ESP32-S2', 'esp32s3': 'ESP32-S3', 'esp32c3': 'ESP32-C3',
          'esp32c6': 'ESP32-C6', 'esp32h2': 'ESP32-H2'}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('board')
    ap.add_argument('build', type=Path)
    ap.add_argument('out', type=Path)
    ap.add_argument('--name', default='')
    a = ap.parse_args()
    plan = json.loads((a.build / 'flasher_args.json').read_text())
    about = json.loads((a.build / 'project_description.json').read_text())
    chip = plan.get('extra_esptool_args', {}).get('chip') or about.get('target') or ''
    home = a.out / a.board
    home.mkdir(parents=True, exist_ok=True)
    parts = []
    for offset, rel in sorted(plan['flash_files'].items(), key=lambda kv: int(kv[0], 16)):
        src = a.build / rel
        shutil.copyfile(src, home / src.name)
        parts.append({'path': src.name, 'offset': int(offset, 16)})
    fs = plan.get('flash_settings', {})
    manifest = {
        'name': a.name or about.get('project_name') or a.board,
        'version': about.get('project_version') or '0',
        'new_install_prompt_erase': False,
        'builds': [{'chipFamily': FAMILY.get(chip, chip.upper()),
                    'flash': {'mode': fs.get('flash_mode', 'keep'), 'size': fs.get('flash_size', 'keep'),
                              'freq': fs.get('flash_freq', 'keep')},
                    'parts': parts}],
    }
    (home / 'manifest.json').write_text(json.dumps(manifest, indent=1) + '\n')
    index_path = a.out / 'index.json'
    index = json.loads(index_path.read_text()) if index_path.exists() else {'boards': []}
    index['boards'] = [b for b in index.get('boards', []) if b.get('id') != a.board]
    index['boards'].append({'id': a.board, 'name': manifest['name'], 'manifest': f'{a.board}/manifest.json'})
    index['boards'].sort(key=lambda b: b['id'])
    index_path.write_text(json.dumps(index, indent=1) + '\n')
    print(f"{a.board} {manifest['version']} ({manifest['builds'][0]['chipFamily']}): "
          f"{', '.join(p['path'] for p in parts)} -> {home}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
