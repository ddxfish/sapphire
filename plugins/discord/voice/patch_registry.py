"""One door for every monkey patch (H11, hunt 2026-09-12).

A py-cord module that MOVED raised ImportError inside a patch and the patch
skipped silently (voice degraded, INFO only); a method that was RENAMED raised
AttributeError out of apply_*() → lifecycle.start → the whole daemon failed and
text chat died with it. Now every patch runs through apply_patch(): it never
raises, and the outcome per patch is recorded for voice_stack_info() and the
voice/status route.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

PATCH_STATUS: dict[str, str] = {}


def apply_patch(name: str, fn) -> bool:
    try:
        fn()
    except ImportError as exc:
        PATCH_STATUS[name] = f'missing module: {exc}'
    except AttributeError as exc:
        PATCH_STATUS[name] = f'renamed target: {exc}'
    except Exception as exc:  # a patch must never take the daemon down
        PATCH_STATUS[name] = f'failed: {exc}'
        logger.warning('[DISCORD] voice patch %s NOT applied — %s', name, PATCH_STATUS[name], exc_info=True)
        return False
    else:
        PATCH_STATUS[name] = 'applied'
        return True
    logger.warning('[DISCORD] voice patch %s NOT applied — %s', name, PATCH_STATUS[name])
    return False


def missing_patches() -> list[str]:
    return [name for name, status in PATCH_STATUS.items() if status != 'applied']
