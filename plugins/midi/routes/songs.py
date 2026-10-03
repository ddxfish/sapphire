# GET /api/plugin/midi/song/<id>.mp3|.mid : serve a song she saved.
# Login is enforced by the framework. The .mp3 plays in the browser unless
# ?dl=1 asks for a download; the .mid always downloads.
import sys
from pathlib import Path

# .absolute(), never .resolve(): symlinked plugin dirs must not escape.
_PLUGIN_ROOT = str(Path(__file__).absolute().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)


def serve(name=None, query=None, **_):
    from fastapi.responses import FileResponse, JSONResponse
    import midi_songs as songs
    folder = songs.song_dir()
    p = songs.find(folder, name)
    if p is None:
        return JSONResponse({"error": "No such song"}, status_code=404)
    mp3 = p.suffix == '.mp3'
    download = (not mp3) or str((query or {}).get('dl') or '') == '1'
    title = songs.slug(songs.meta(folder, p.stem).get('title'))
    return FileResponse(str(p), media_type='audio/mpeg' if mp3 else 'audio/midi',
                        filename=f'{title}{p.suffix}',
                        content_disposition_type='attachment' if download else 'inline',
                        headers={'Cache-Control': 'private, max-age=86400'})
