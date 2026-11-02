# core/plugins/social/drop.py - the public image URL some platforms demand
#
# Instagram (and Threads) do not take image bytes: they fetch a URL. The
# drop folder is a directory on a web server the person already runs, reached
# with scp over their own ssh key; the public URL base is the other half.
# Both are plugin settings. Nothing here deletes - the folder is theirs to prune.
import logging
import secrets
import subprocess
import tempfile
from pathlib import Path

from plugins.social import store
from plugins.social.errors import SocialError

logger = logging.getLogger(__name__)

_EXT = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/gif': 'gif', 'image/webp': 'webp'}


def configured():
    s = store.settings()
    return bool((s.get('SOCIAL_DROP_SCP') or '').strip() and (s.get('SOCIAL_DROP_URL') or '').strip())


def publish(raw, media_type):
    """Copy one image to the drop folder; its public URL. Raises SocialError."""
    s = store.settings()
    target = (s.get('SOCIAL_DROP_SCP') or '').strip()
    base = (s.get('SOCIAL_DROP_URL') or '').strip().rstrip('/')
    if not target or not base:
        raise SocialError("this platform fetches images from a public URL: set the image drop folder "
                          "in Settings > Plugins > Social, or give an https:// image URL instead of a handle")
    if ':' not in target or target.startswith('-') or not base.startswith('https://'):
        raise SocialError("the drop folder must be user@host:/path and its URL must start with https://")
    name = f"{secrets.token_urlsafe(12)}.{_EXT.get(media_type, 'jpg')}"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        p.write_bytes(raw)
        cmd = ['scp', '-q', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new',
               '-o', 'ConnectTimeout=10', str(p), f"{target.rstrip('/')}/{name}"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except FileNotFoundError:
            raise SocialError("scp is not installed on this machine, so the drop folder is out of reach") from None
        except subprocess.TimeoutExpired:
            raise SocialError("scp to the drop folder timed out") from None
    if r.returncode != 0:
        raise SocialError(f"scp to the drop folder failed: {(r.stderr or '').strip()[:200] or 'no reason given'}")
    return f"{base}/{name}"
