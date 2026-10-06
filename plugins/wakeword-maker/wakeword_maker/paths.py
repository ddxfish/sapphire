# paths.py - where everything lives. One root: <sapphire>/wakeword/ unless the user picks another folder (nothing of
# this size goes under user/, which the nightly backup tars; wakeword/ is in .gitignore and outside the backup):
#   <root>/datasets/<id>/           downloads, each with a receipt.json
#   <root>/projects/<slug>/         one wake phrase: project.json, the collections, runs, qa
#   <root>/jobs/<id>/               every job, project or not: job.json, status.json, progress.jsonl, log.txt
import json
import os
import re
import threading
from pathlib import Path

SAMPLE_RATE = 16000

# collection -> what it is for. The folder IS the earmark: a clip in positive/uploaded was uploaded by the
# user and nothing the app generates ever lands there.
COLLECTIONS = {
    'positive/synth': 'the phrase, synthesized',
    'positive/recorded': 'the phrase, recorded in the app',
    'positive/uploaded': 'the phrase, uploaded by the user',
    'negative/synth': 'sound-alikes, synthesized',
    'negative/recorded': 'other words, recorded in the app',
    'negative/uploaded': 'other words, uploaded by the user',
    'negative/mined': 'false accepts found by a trained model',
    'ambient/recorded': 'the room, minutes at a time, recorded in the app',
    'ambient/uploaded': 'the room, uploaded by the user',
    'previews/synth': 'what the voices sounded like when previewed; never trained on',
}
PREVIEWS = 'previews/synth'
SOURCES = ('synth', 'recorded', 'uploaded', 'mined')


SAPPHIRE_ROOT = Path(__file__).absolute().parents[3]       # plugins/wakeword-maker/wakeword_maker/paths.py -> the app
DEFAULT_ROOT = SAPPHIRE_ROOT / 'wakeword'


def data_root(settings=None):
    """The data folder: the one the user picked, else <sapphire>/wakeword. Settings win over the environment (jobs get
    it as WWM_DATA_DIR). A relative path is read against the Sapphire folder, not the process's cwd."""
    raw = str((settings or {}).get('data_dir') or os.environ.get('WWM_DATA_DIR') or '').strip()
    if not raw:
        return DEFAULT_ROOT
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (SAPPHIRE_ROOT / p).absolute()


def is_default(root):
    return Path(root).absolute() == DEFAULT_ROOT.absolute()


def write_text(path, text):
    """Whole file or nothing: a unique temp file next to the target, then an atomic rename. A full disk or a kill
    mid-write leaves the old file in place instead of a truncated one."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def write_json(path, data, indent=None):
    write_text(path, json.dumps(data, indent=indent))


def read_json(path, default=None):
    """A missing, truncated or half-written file reads as `default`, never as an exception."""
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def slug_of(phrase):
    s = re.sub(r"[^a-z0-9]+", '_', str(phrase or '').lower()).strip('_')
    return s[:40] or 'wakeword'


def datasets_dir(root):
    return Path(root) / 'datasets'


def projects_dir(root):
    return Path(root) / 'projects'


def _index_path(root):
    return Path(root) / 'projects.json'


def projects_index(root):
    """{slug: folder} for wake words that live outside <root>/projects/."""
    try:
        return json.loads(_index_path(root).read_text(encoding='utf-8'))
    except Exception:
        return {}


def set_project_folder(root, slug, folder):
    """Remember (or forget, with folder=None) where a wake word lives."""
    idx = projects_index(root)
    default = str(projects_dir(root) / slug)
    if folder is None or str(folder) == default:
        idx.pop(slug, None)
    else:
        idx[slug] = str(folder)
    write_json(_index_path(root), idx, indent=2)


def project_dir(root, slug):
    where = projects_index(root).get(slug)
    return Path(where) if where else projects_dir(root) / slug


def jobs_dir(root):
    return Path(root) / 'jobs'


def voices_dir(root):
    """Piper voices (<name>.onnx + .json) and, under hf/, the Hugging Face cache for Kokoro's model and packs and
    Whisper's model. All of it under the data folder, none of it in the user's home."""
    return Path(root) / 'voices'


def hf_cache(root):
    return voices_dir(root) / 'hf'


def job_env(root):
    """Environment every job and helper gets: where the data is, where the hub cache is."""
    return {'WWM_DATA_DIR': str(root), 'HF_HUB_CACHE': str(hf_cache(root)), 'HF_HOME': str(hf_cache(root) / 'home')}


def collection_dir(pdir, collection):
    if collection not in COLLECTIONS:
        raise ValueError(f"no such collection: {collection}")
    return Path(pdir) / collection
