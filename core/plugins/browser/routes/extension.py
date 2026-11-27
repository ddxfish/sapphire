# core/plugins/browser/routes/extension.py - the browser extension that ships in
# ../extension: GET extension.zip (zipped, for a browser on another machine) and
# GET extension (where the folder is, for "Load unpacked" on this one: a path
# that does not move, so the extension updates with Sapphire).
import io
import zipfile
from pathlib import Path

FOLDER = Path(__file__).absolute().parent.parent / 'extension'


def version():
    import json
    try:
        return str(json.loads((FOLDER / 'manifest.json').read_text(encoding='utf-8')).get('version') or '')
    except Exception:
        return ''


def download(**_):
    from fastapi.responses import Response
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in sorted(FOLDER.rglob('*')):
            if f.is_file():
                z.write(f, 'sapphire-browser-bridge/' + f.relative_to(FOLDER).as_posix())
    return Response(buf.getvalue(), media_type='application/zip',
                    headers={'Content-Disposition': 'attachment; filename="sapphire-browser-bridge.zip"'})


def where(**_):
    return {'path': str(FOLDER), 'version': version()}
