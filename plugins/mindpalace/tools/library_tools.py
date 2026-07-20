# plugins/mindpalace/tools/library_tools.py
# Library (Knowledge v3) — her tool surface (P3b, 2026-07-17).
# Two tools: `library` (the card catalog — drawers WITH descriptions, that's
# how she finds where to look) and `read_document` (sequential reading +
# range digs). Search itself lives inside search_memory: layer='knowledge'
# reroutes to the library engine, mixed searches get a library append
# (palace_tools). save_memory layer='knowledge' becomes a library note —
# her muscle memory keeps working, the storage underneath got a building.

import logging
import threading

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🏛'
GROUP = 'Mind Palace'

AVAILABLE_FUNCTIONS = ['library', 'read_document', 'view_image']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "library",
            "description": ("Browse the library's card catalog: every category, "
                            "topic, and document with their descriptions. Use it "
                            "to see what reference knowledge exists before "
                            "searching (search_memory layer='knowledge' searches "
                            "inside the documents)."),
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "read_document",
            "description": ("Read a library document (ids shown as [doc N]).\n"
                            "  page=N — read sequentially, 5 sections a page\n"
                            "  around=N — section N with its neighbors "
                            "(follow a search hit)\n  start/end — explicit "
                            "section range"),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "integer", "description": "The [doc N] id"},
                    "page": {"type": "integer", "description": "Page number, from 1"},
                    "around": {"type": "integer", "description": "Center section §N, returns N±2"},
                    "start": {"type": "integer", "description": "First section of a range"},
                    "end": {"type": "integer", "description": "Last section of a range"},
                    "private_key": {"type": "string", "description": "Gating word for a private document"}
                },
                "required": ["document_id"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "view_image",
            "description": ("Look at a photo from the library (photo results "
                            "show ids as [doc N]). Returns the actual image — "
                            "it spends context, so pick the one you need "
                            "rather than viewing a whole grid."),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "integer", "description": "The [doc N] id"},
                    "private_key": {"type": "string", "description": "Gating word for a private image"}
                },
                "required": ["document_id"]
            }
        }
    },
]


def _lib():
    from plugins.mindpalace.tools import library
    return library


def _pt():
    from plugins.mindpalace.tools import palace_tools
    return palace_tools


def execute(function_name, arguments, config):
    try:
        pt = _pt()
        scope = pt._get_current_scope()
        if scope is None:
            return "The library is unavailable when memory is disabled for this chat.", False
        lib = _lib()
        if function_name == 'library':
            return lib.catalog_text(scope)
        if function_name == 'read_document':
            return lib.read_document_text(
                scope, arguments.get('document_id'),
                page=arguments.get('page'),
                around=arguments.get('around'),
                start=arguments.get('start'),
                end=arguments.get('end'),
                private_key=arguments.get('private_key'))
        if function_name == 'view_image':
            return lib.view_image_data(
                scope, arguments.get('document_id'),
                private_key=arguments.get('private_key'))
        return f"Unknown library function: {function_name}", False
    except Exception as e:
        logger.error(f"[LIBRARY] {function_name} error: {e}", exc_info=True)
        return f"Library error: {e}", False


# Boot-resume: this module is imported once at plugin load — a short timer
# later (app settled), any import jobs that survived a restart pick back up,
# and the coexistence migration runs ONCE (marker-gated no-op forever after;
# Admin's re-migrate button forces a fresh sweep). Failure-isolated.
def _boot_resume():
    try:
        _lib().resume_pending()
    except Exception as e:
        logger.warning(f"[LIBRARY] boot resume skipped: {e}")
    try:
        _lib().migrate_all()
    except Exception as e:
        logger.warning(f"[LIBRARY] boot migration skipped: {e}")
    try:
        _lib().tidy_migrated_titles()
        _lib().merge_migrated_duplicates()
    except Exception as e:
        logger.warning(f"[LIBRARY] migration polish skipped: {e}")
    try:
        _lib().backfill_doc_edges()
    except Exception as e:
        logger.warning(f"[LIBRARY] edge backfill skipped: {e}")
    try:
        _lib().backfill_places()   # photos that beat the GeoNames download
    except Exception as e:
        logger.warning(f"[LIBRARY] place backfill skipped: {e}")
    try:
        _lib().backfill_vision()   # photos missing pixel vectors get queued
    except Exception as e:
        logger.warning(f"[LIBRARY] vision backfill skipped: {e}")
    try:
        _lib().refresh_image_working()   # md render evolved → regen once
    except Exception as e:
        logger.warning(f"[LIBRARY] image working refresh skipped: {e}")
    try:
        _lib().scan_all()                # watched folders catch up
    except Exception as e:
        logger.warning(f"[LIBRARY] watch scan skipped: {e}")


if 'pytest' not in __import__('sys').modules:   # tests drive the queue by hand
    try:
        threading.Timer(15.0, _boot_resume).start()
    except Exception:
        pass
