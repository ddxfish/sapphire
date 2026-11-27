# core/plugins/browser/routes/pair.py - POST pair: a pairing code becomes a Browser device
#
# The easy way to pair: the user presses the extension's button on her page,
# her page (web/main.js) asks them, and posts the extension's pairing code
# here. A plugin route, so only a logged-in session reaches it. This does what
# Add Device > Browser does by hand: the code is stored as the device's
# socket_key, and the extension's next knock at the pipe is let in.
import re

CODE = re.compile(r'[A-Za-z0-9_-]{4,32}\.[A-Za-z0-9_-]{32,}')


def _name(text, taken):
    """A free device id from what the user called it."""
    base = re.sub(r'[^a-z0-9_-]+', '-', str(text).lower()).strip('-_')[:28] or 'browser'
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base}-{n}"
    return name


def pair(body=None, **_):
    from fastapi import HTTPException
    from core.devices import door, engine as devices
    body = body if isinstance(body, dict) else {}
    no = devices.refusal()
    if no:
        raise HTTPException(status_code=403, detail=no)
    code = str(body.get('code') or '').strip()
    if not CODE.fullmatch(code):
        raise HTTPException(status_code=422, detail="That is not a pairing code from the Sapphire extension.")
    table = devices.rows()
    have = door.holder('browser', code.split('.', 1)[0])
    if have:                                           # this browser is a device already: nothing is added twice
        row = table.get(have) or {}
        return {'device': have, 'label': row.get('label') or have, 'already': True,
                'enabled': row.get('enabled', True) is not False}
    label = ' '.join(str(body.get('name') or '').split())[:60] or 'Browser'
    try:
        row, failed = devices.add(_name(label, set(table) | set(devices.RESERVED)), label, 'browser', {'socket_key': code})
    except devices.DeviceError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if failed:                                         # the row exists without its key: say so, the page offers the device window
        raise HTTPException(status_code=500, detail=f"{row['label']} was added, but its pairing code could not be stored. "
                                                    "Open it in Settings > Devices and paste the code there.")
    return {'device': row['id'], 'label': row['label'], 'already': False, 'enabled': True}


def unpair(body=None, **_):
    """The extension dropped its pairing: which device was it (`id` is its
    code's public half), and, with `remove`, take that device away."""
    from fastapi import HTTPException
    from core.devices import door, engine as devices
    body = body if isinstance(body, dict) else {}
    kid = str(body.get('id') or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{4,32}', kid):
        raise HTTPException(status_code=422, detail="That is not a pairing the Sapphire extension made.")
    have = door.holder('browser', kid)
    if not have:
        return {'device': None}
    label = (devices.rows().get(have) or {}).get('label') or have
    if body.get('remove') is True:
        try:
            devices.remove(have)
        except devices.DeviceError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return {'device': have, 'label': label, 'removed': True}
    return {'device': have, 'label': label, 'removed': False}
