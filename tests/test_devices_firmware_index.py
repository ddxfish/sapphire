# tests/test_devices_firmware_index.py - the firmware source's index.json (core/devices/firmware.py).
import json
from unittest.mock import patch

from core.devices import firmware

GOOD = {'id': 'cyd28', 'name': 'CYD', 'version': '0.5.2', 'chipFamily': 'ESP32', 'file': 'cyd28-0.5.2.bin',
        'size': 100, 'sha256': 'a' * 64, 'parts': [{'name': 'app', 'offset': 0, 'size': 100, 'sha256': 'b' * 64, 'app': True}]}


def test_one_bad_entry_does_not_hide_the_other_boards():
    index = json.dumps({'boards': [dict(GOOD, id='bad', version='not a version'), GOOD]}).encode()
    with patch.object(firmware, 'sources', return_value=['https://example.test/']), \
         patch.object(firmware, '_read', return_value=index), \
         patch.object(firmware, '_recent', (0, None, ())):
        boards, error = firmware._boards(fresh=True)
    assert list(boards) == ['cyd28'] and error is None
