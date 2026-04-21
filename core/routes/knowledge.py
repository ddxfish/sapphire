# core/routes/knowledge.py - Memory scopes, goal scopes, knowledge base, per-chat RAG, memory CRUD
import asyncio
import io
import logging
from pathlib import Path

from fastapi import APIRouter, Request, Depends, HTTPException, UploadFile, File

import config
from core.auth import require_login
from core.api_fastapi import get_system

logger = logging.getLogger(__name__)

router = APIRouter()

PROJECT_ROOT = Path(__file__).parent.parent.parent


# =============================================================================
# EMBEDDING TEST
# =============================================================================

@router.post("/api/embedding/test")
async def test_embedding(request: Request, _=Depends(require_login)):
    """Test current embedding provider with a real embedding call."""
    import time
    from core.embeddings import get_embedder
    embedder = get_embedder()
    provider = type(embedder).__name__
    if not embedder.available:
        return {"success": False, "provider": provider, "error": "Embedder not available"}
    t0 = time.time()
    result = await asyncio.to_thread(
        embedder.embed, ["This is a test sentence for embedding verification."], 'search_document')
    elapsed = round((time.time() - t0) * 1000)
    if result is None:
        return {"success": False, "provider": provider, "error": "Embedding returned None", "ms": elapsed}
    dim = result.shape[1] if len(result.shape) > 1 else len(result[0])
    return {"success": True, "provider": provider, "dimensions": dim, "ms": elapsed}


@router.get("/api/embedding/integrity")
async def embedding_integrity(request: Request, _=Depends(require_login)):
    """Report how stored vectors are stamped vs the active provider.

    Used by the Settings UI to warn before a provider swap: shows how many
    memory/knowledge/people vectors would become invisible to vector search
    if the user proceeded. Powers the 'Swap anyway?' confirmation dialog.

    Runs in a worker thread — the report issues 6 `SELECT ... GROUP BY`
    queries over BLOB-bearing tables with no index on (provider, dim). On
    100k+ rows that's a multi-second synchronous scan, long enough to stall
    the FastAPI event loop and queue every other HTTP request behind it.
    Scout finding #14 — 2026-04-20.
    """
    import asyncio
    from core.embeddings import integrity_report
    return await asyncio.to_thread(integrity_report)


@router.post("/api/embedding/reembed")
async def embedding_reembed_start(request: Request, _=Depends(require_login)):
    """Kick off a background re-embed of all stored vectors stamped with a
    non-active provider (or legacy unstamped). Fires SSE `reembed_progress`
    events throughout. Idempotent: refuses if already running."""
    from core.embeddings.reembed import start_reembed
    ok, msg = start_reembed()
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"status": "started", "message": msg}


@router.get("/api/embedding/reembed/status")
async def embedding_reembed_status(request: Request, _=Depends(require_login)):
    """Current re-embed status snapshot. Useful for clients that missed the
    SSE events or opened the page mid-run."""
    from core.embeddings.reembed import get_status
    return get_status()


@router.post("/api/embedding/reembed/cancel")
async def embedding_reembed_cancel(request: Request, _=Depends(require_login)):
    """Request graceful cancellation of an in-progress re-embed. Worker
    finishes current batch then exits (no half-stamped rows)."""
    from core.embeddings.reembed import cancel_reembed
    ok, msg = cancel_reembed()
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"status": "cancelling", "message": msg}


# =============================================================================
# MEMORY SCOPE ROUTES
# =============================================================================

@router.get("/api/memory/scopes")
async def get_memory_scopes(request: Request, _=Depends(require_login)):
    """Get list of memory scopes."""
    from plugins.memory.tools import memory_tools as memory
    scopes = memory.get_scopes()
    return {"scopes": scopes}


@router.post("/api/memory/scopes")
async def create_memory_scope(request: Request, _=Depends(require_login)):
    """Create a new memory scope."""
    import re
    from plugins.memory.tools import memory_tools as memory
    data = await request.json()
    name = data.get('name', '').strip().lower()
    if not name or not re.match(r'^[a-z0-9_]{1,32}$', name):
        raise HTTPException(status_code=400, detail="Invalid scope name")
    if memory.create_scope(name):
        from core.event_bus import publish, Events
        publish(Events.SCOPE_CHANGED, {"kind": "memory", "action": "created", "name": name})
        return {"created": name}
    else:
        raise HTTPException(status_code=500, detail="Failed to create scope")


@router.delete("/api/memory/scopes/{scope_name}")
async def delete_memory_scope(scope_name: str, request: Request, _=Depends(require_login)):
    """Delete a memory scope and ALL its memories. Requires confirmation token."""
    from plugins.memory.tools import memory_tools as memory
    data = await request.json()
    if data.get('confirm') != 'DELETE':
        raise HTTPException(status_code=400, detail="Confirmation required")
    result = memory.delete_scope(scope_name)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    from core.event_bus import publish, Events
    publish(Events.SCOPE_CHANGED, {"kind": "memory", "action": "deleted", "name": scope_name})
    return result


# =============================================================================
# GOAL SCOPE ROUTES
# =============================================================================

@router.get("/api/goals/scopes")
async def get_goal_scopes(request: Request, _=Depends(require_login)):
    """Get list of goal scopes."""
    from plugins.memory.tools import goals_tools as goals
    scopes = goals.get_scopes()
    return {"scopes": scopes}


@router.post("/api/goals/scopes")
async def create_goal_scope(request: Request, _=Depends(require_login)):
    """Create a new goal scope."""
    import re
    from plugins.memory.tools import goals_tools as goals
    data = await request.json()
    name = data.get('name', '').strip().lower()
    if not name or not re.match(r'^[a-z0-9_]{1,32}$', name):
        raise HTTPException(status_code=400, detail="Invalid scope name")
    if goals.create_scope(name):
        from core.event_bus import publish, Events
        publish(Events.SCOPE_CHANGED, {"kind": "goal", "action": "created", "name": name})
        return {"created": name}
    else:
        raise HTTPException(status_code=500, detail="Failed to create scope")


@router.delete("/api/goals/scopes/{scope_name}")
async def remove_goal_scope(scope_name: str, request: Request, _=Depends(require_login)):
    from plugins.memory.tools import goals_tools as goals
    data = await request.json()
    if data.get('confirm') != 'DELETE':
        raise HTTPException(status_code=400, detail="Confirmation required")
    result = goals.delete_scope(scope_name)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    from core.event_bus import publish, Events
    publish(Events.SCOPE_CHANGED, {"kind": "goal", "action": "deleted", "name": scope_name})
    return result


@router.get("/api/goals")
async def list_goals_api(request: Request, _=Depends(require_login)):
    from plugins.memory.tools import goals_tools as goals
    scope = request.query_params.get('scope', 'default')
    status = request.query_params.get('status', 'active')
    return {"goals": goals.get_goals_list(scope, status)}


@router.get("/api/goals/{goal_id}")
async def get_goal_api(goal_id: int, request: Request, _=Depends(require_login)):
    from plugins.memory.tools import goals_tools as goals
    detail = goals.get_goal_detail(goal_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Goal not found")
    return detail


@router.post("/api/goals")
async def create_goal_endpoint(request: Request, _=Depends(require_login)):
    from plugins.memory.tools import goals_tools as goals
    data = await request.json()
    try:
        goal_id = goals.create_goal_api(
            title=data.get('title', ''),
            description=data.get('description'),
            priority=data.get('priority', 'medium'),
            parent_id=data.get('parent_id'),
            scope=data.get('scope', 'default'),
            permanent=data.get('permanent', False),
        )
        return {"id": goal_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/api/goals/{goal_id}")
async def update_goal_endpoint(goal_id: int, request: Request, _=Depends(require_login)):
    from plugins.memory.tools import goals_tools as goals
    data = await request.json()
    try:
        goals.update_goal_api(
            goal_id,
            title=data.get('title'),
            description=data.get('description'),
            priority=data.get('priority'),
            status=data.get('status'),
            progress_note=data.get('progress_note'),
            permanent=data.get('permanent'),
        )
        return {"updated": goal_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/goals/{goal_id}/progress")
async def add_goal_progress(goal_id: int, request: Request, _=Depends(require_login)):
    from plugins.memory.tools import goals_tools as goals
    data = await request.json()
    try:
        note_id = goals.add_progress_note(goal_id, data.get('note', ''))
        return {"id": note_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/goals/{goal_id}")
async def delete_goal_endpoint(goal_id: int, request: Request, _=Depends(require_login)):
    from plugins.memory.tools import goals_tools as goals
    # force=true lets the UI confirm + override the permanent-goal guard
    force = request.query_params.get('force', '').lower() in ('1', 'true', 'yes')
    try:
        title = goals.delete_goal_api(goal_id, force=force)
        return {"deleted": goal_id, "title": title}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# =============================================================================
# KNOWLEDGE BASE ROUTES
# =============================================================================

@router.get("/api/knowledge/scopes")
async def get_knowledge_scopes(request: Request, _=Depends(require_login)):
    from plugins.memory.tools import knowledge_tools as knowledge
    scopes = knowledge.get_scopes()
    return {"scopes": scopes}


@router.post("/api/knowledge/scopes")
async def create_knowledge_scope(request: Request, _=Depends(require_login)):
    import re as _re
    from plugins.memory.tools import knowledge_tools as knowledge
    data = await request.json()
    name = data.get('name', '').strip().lower()
    if not name or not _re.match(r'^[a-z0-9_]{1,32}$', name):
        raise HTTPException(status_code=400, detail="Invalid scope name")
    if knowledge.create_scope(name):
        from core.event_bus import publish, Events
        publish(Events.SCOPE_CHANGED, {"kind": "knowledge", "action": "created", "name": name})
        return {"created": name}
    else:
        raise HTTPException(status_code=500, detail="Failed to create scope")


@router.delete("/api/knowledge/scopes/{scope_name}")
async def delete_knowledge_scope(scope_name: str, request: Request, _=Depends(require_login)):
    """Delete a knowledge scope, ALL its tabs, and ALL entries. Requires confirmation token."""
    from plugins.memory.tools import knowledge_tools as knowledge
    data = await request.json()
    if data.get('confirm') != 'DELETE':
        raise HTTPException(status_code=400, detail="Confirmation required")
    result = knowledge.delete_scope(scope_name)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    from core.event_bus import publish, Events
    publish(Events.SCOPE_CHANGED, {"kind": "knowledge", "action": "deleted", "name": scope_name})
    return result


@router.get("/api/knowledge/people/scopes")
async def list_people_scopes(request: Request, _=Depends(require_login)):
    from plugins.memory.tools import knowledge_tools as knowledge
    return {"scopes": knowledge.get_people_scopes()}


@router.post("/api/knowledge/people/scopes")
async def create_people_scope(request: Request, _=Depends(require_login)):
    from plugins.memory.tools import knowledge_tools as knowledge
    data = await request.json()
    name = data.get('name', '').strip().lower()
    if not name or len(name) > 32:
        raise HTTPException(status_code=400, detail="Invalid scope name")
    knowledge.create_people_scope(name)
    from core.event_bus import publish, Events
    publish(Events.SCOPE_CHANGED, {"kind": "people", "action": "created", "name": name})
    return {"created": name}


@router.delete("/api/knowledge/people/scopes/{scope_name}")
async def remove_people_scope(scope_name: str, request: Request, _=Depends(require_login)):
    from plugins.memory.tools import knowledge_tools as knowledge
    data = await request.json()
    if data.get('confirm') != 'DELETE':
        raise HTTPException(status_code=400, detail="Confirmation required")
    result = knowledge.delete_people_scope(scope_name)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    from core.event_bus import publish, Events
    publish(Events.SCOPE_CHANGED, {"kind": "people", "action": "deleted", "name": scope_name})
    return result


@router.get("/api/knowledge/people")
async def list_people(request: Request, _=Depends(require_login)):
    from plugins.memory.tools import knowledge_tools as knowledge
    scope = request.query_params.get('scope', 'default')
    return {"people": knowledge.get_people(scope)}


@router.post("/api/knowledge/people")
async def save_person(request: Request, _=Depends(require_login)):
    from plugins.memory.tools import knowledge_tools as knowledge
    data = await request.json()
    name = data.get('name', '').strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    scope = data.get('scope', 'default')
    pid, is_new = knowledge.create_or_update_person(
        name=name,
        relationship=data.get('relationship'),
        phone=data.get('phone'),
        email=data.get('email'),
        address=data.get('address'),
        notes=data.get('notes'),
        scope=scope,
        person_id=data.get('id'),
        email_whitelisted=data.get('email_whitelisted'),
    )
    return {"id": pid, "created": is_new}


@router.delete("/api/knowledge/people/{person_id}")
async def remove_person(person_id: int, request: Request, _=Depends(require_login)):
    from plugins.memory.tools import knowledge_tools as knowledge
    if knowledge.delete_person(person_id):
        return {"deleted": person_id}
    raise HTTPException(status_code=404, detail="Person not found")


@router.post("/api/knowledge/people/import-vcf")
async def import_vcf(request: Request, _=Depends(require_login)):
    """Import contacts from a VCF (vCard) file."""
    from plugins.memory.tools import knowledge_tools as knowledge
    import re

    form = await request.form()
    file = form.get('file')
    scope = form.get('scope', 'default')
    if not file:
        raise HTTPException(status_code=400, detail="No file uploaded")

    content = (await file.read()).decode('utf-8', errors='replace')

    # Parse vCards
    cards = []
    current = {}
    for line in content.splitlines():
        line = line.strip()
        if line.upper() == 'BEGIN:VCARD':
            current = {'phones': [], 'emails': [], 'addresses': [], 'notes': [], 'org': '', 'title': ''}
        elif line.upper() == 'END:VCARD':
            if current.get('name'):
                cards.append(current)
            current = {}
        elif not current and not isinstance(current, dict):
            continue
        else:
            # Strip type params: "TEL;TYPE=CELL:+1234" -> key=TEL, val=+1234
            if ':' not in line:
                continue
            key_part, val = line.split(':', 1)
            key = key_part.split(';')[0].upper()
            val = val.strip()
            if not val:
                continue

            if key == 'FN':
                current['name'] = val
            elif key == 'TEL':
                current['phones'].append(val)
            elif key == 'EMAIL':
                current['emails'].append(val)
            elif key == 'ADR':
                # ADR format: ;;street;city;state;zip;country (semicolons separate parts)
                parts = [p.strip() for p in val.split(';') if p.strip()]
                current['addresses'].append(', '.join(parts))
            elif key == 'NOTE':
                current['notes'].append(val)
            elif key == 'ORG':
                current['org'] = val.replace(';', ', ')
            elif key == 'TITLE':
                current['title'] = val

    # Get existing people for duplicate detection
    existing = knowledge.get_people(scope)
    existing_keys = set()
    for p in existing:
        key = (p['name'].lower().strip(), (p.get('email') or '').lower().strip())
        existing_keys.add(key)

    imported = 0
    skipped = []
    for card in cards:
        name = card.get('name', '').strip()
        if not name:
            continue

        email = card['emails'][0] if card['emails'] else ''
        phone = card['phones'][0] if card['phones'] else ''
        address = card['addresses'][0] if card['addresses'] else ''

        # Build notes from extra data
        note_parts = list(card['notes'])
        if card['org']:
            note_parts.insert(0, card['org'])
        if card['title']:
            note_parts.insert(0, card['title'])
        # Extra emails/phones beyond the first
        if len(card['emails']) > 1:
            note_parts.append('Other emails: ' + ', '.join(card['emails'][1:]))
        if len(card['phones']) > 1:
            note_parts.append('Other phones: ' + ', '.join(card['phones'][1:]))
        notes = '. '.join(note_parts) if note_parts else ''

        # Duplicate check: name + email
        dup_key = (name.lower(), email.lower())
        if dup_key in existing_keys:
            skipped.append(f"{name}" + (f" ({email})" if email else ""))
            continue

        knowledge.create_or_update_person(
            name=name, phone=phone, email=email,
            address=address, notes=notes, scope=scope
        )
        existing_keys.add(dup_key)
        imported += 1

    return {
        "imported": imported,
        "skipped_count": len(skipped),
        "skipped": skipped[:25],
        "total_in_file": len(cards)
    }


@router.get("/api/knowledge/tabs")
async def list_tabs(request: Request, _=Depends(require_login)):
    from plugins.memory.tools import knowledge_tools as knowledge
    scope = request.query_params.get('scope', 'default')
    tab_type = request.query_params.get('type')
    return {"tabs": knowledge.get_tabs(scope, tab_type)}


@router.get("/api/knowledge/tabs/{tab_id}")
async def get_tab(tab_id: int, request: Request, _=Depends(require_login)):
    from plugins.memory.tools import knowledge_tools as knowledge
    scope = request.query_params.get('scope', 'default')
    entries = knowledge.get_tab_entries(tab_id, scope)
    return {"entries": entries}


@router.post("/api/knowledge/tabs")
async def create_knowledge_tab(request: Request, _=Depends(require_login)):
    from plugins.memory.tools import knowledge_tools as knowledge
    data = await request.json()
    name = data.get('name', '').strip()
    scope = data.get('scope', 'default')
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    tab_id = knowledge.create_tab(name, scope, data.get('description'), data.get('type', 'user'))
    if tab_id:
        return {"id": tab_id}
    raise HTTPException(status_code=409, detail="Tab already exists in this scope")


@router.put("/api/knowledge/tabs/{tab_id}")
async def update_knowledge_tab(tab_id: int, request: Request, _=Depends(require_login)):
    from plugins.memory.tools import knowledge_tools as knowledge
    data = await request.json()
    if knowledge.update_tab(tab_id, data.get('name'), data.get('description')):
        return {"updated": tab_id}
    raise HTTPException(status_code=404, detail="Tab not found")


@router.delete("/api/knowledge/tabs/{tab_id}")
async def delete_knowledge_tab(tab_id: int, request: Request, _=Depends(require_login)):
    from plugins.memory.tools import knowledge_tools as knowledge
    if knowledge.delete_tab(tab_id):
        return {"deleted": tab_id}
    raise HTTPException(status_code=404, detail="Tab not found")


@router.post("/api/knowledge/tabs/{tab_id}/entries")
async def add_knowledge_entry(tab_id: int, request: Request, _=Depends(require_login)):
    from plugins.memory.tools import knowledge_tools as knowledge
    from datetime import datetime
    data = await request.json()
    content = data.get('content', '').strip()
    if not content:
        raise HTTPException(status_code=400, detail="Content is required")
    chunks = knowledge._chunk_text(content)
    if len(chunks) == 1:
        entry_id = knowledge.add_entry(tab_id, chunks[0], source_filename=data.get('source_filename'))
        return {"id": entry_id}
    # Multiple chunks — group under a timestamped paste name
    source = data.get('source_filename') or f"paste-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    entry_ids = []
    for i, chunk in enumerate(chunks):
        eid = knowledge.add_entry(tab_id, chunk, chunk_index=i, source_filename=source)
        entry_ids.append(eid)
    return {"ids": entry_ids, "chunks": len(chunks)}


@router.post("/api/knowledge/tabs/{tab_id}/upload")
async def upload_knowledge_file(tab_id: int, file: UploadFile = File(...), _=Depends(require_login)):
    """Upload a text file into a knowledge tab — chunks and embeds automatically."""
    from plugins.memory.tools import knowledge_tools as knowledge

    # Verify tab exists
    tab = knowledge.get_tabs_by_id(tab_id)
    if not tab:
        raise HTTPException(status_code=404, detail="Tab not found")

    # Read and decode file
    raw = await file.read()
    if len(raw) > 2 * 1024 * 1024:  # 2MB cap
        raise HTTPException(status_code=400, detail="File too large (max 2MB)")

    # Try common encodings
    text = None
    for enc in ('utf-8', 'utf-8-sig', 'latin-1'):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, ValueError):
            continue
    if text is None:
        raise HTTPException(status_code=400, detail="Could not decode file — unsupported encoding")

    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="File is empty")

    filename = file.filename or 'upload.txt'

    # Auto-replace: remove existing entries from same filename in this tab
    replaced = knowledge.delete_entries_by_filename(tab_id, filename)

    chunks = knowledge._chunk_text(text)
    entry_ids = []
    for i, chunk in enumerate(chunks):
        eid = knowledge.add_entry(tab_id, chunk, chunk_index=i, source_filename=filename)
        entry_ids.append(eid)

    return {"filename": filename, "chunks": len(chunks), "entry_ids": entry_ids, "replaced": replaced}


@router.delete("/api/knowledge/tabs/{tab_id}/file/{filename}")
async def delete_knowledge_file(tab_id: int, filename: str, _=Depends(require_login)):
    """Delete all entries from a specific uploaded file."""
    from plugins.memory.tools import knowledge_tools as knowledge
    count = knowledge.delete_entries_by_filename(tab_id, filename)
    if count == 0:
        raise HTTPException(status_code=404, detail="No entries found for that file")
    return {"deleted": count, "filename": filename}


@router.put("/api/knowledge/entries/{entry_id}")
async def update_knowledge_entry(entry_id: int, request: Request, _=Depends(require_login)):
    from plugins.memory.tools import knowledge_tools as knowledge
    data = await request.json()
    content = data.get('content', '').strip()
    if not content:
        raise HTTPException(status_code=400, detail="Content is required")
    if knowledge.update_entry(entry_id, content):
        return {"updated": entry_id}
    raise HTTPException(status_code=404, detail="Entry not found")


@router.delete("/api/knowledge/entries/{entry_id}")
async def delete_knowledge_entry(entry_id: int, request: Request, _=Depends(require_login)):
    from plugins.memory.tools import knowledge_tools as knowledge
    if knowledge.delete_entry(entry_id):
        return {"deleted": entry_id}
    raise HTTPException(status_code=404, detail="Entry not found")


# =============================================================================
# PER-CHAT RAG (Document Context)
# =============================================================================

@router.post("/api/chats/{chat_name}/documents")
async def upload_chat_document(chat_name: str, file: UploadFile = File(...), _=Depends(require_login)):
    """Upload a document for per-chat RAG context."""
    from plugins.memory.tools import knowledge_tools as knowledge

    filename = file.filename or 'upload.txt'
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 5MB)")

    # Extract text — PDF is special, everything else try to decode as text
    if ext == 'pdf':
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            pages = [page.extract_text() or '' for page in reader.pages]
            text = '\n\n'.join(p for p in pages if p.strip())
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to read PDF: {e}")
    else:
        text = None
        for enc in ('utf-8', 'utf-8-sig', 'latin-1'):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, ValueError):
                continue
        if text is None:
            raise HTTPException(status_code=400, detail="Could not decode file — binary or unsupported encoding")

    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="File is empty or has no extractable text")

    rag_scope = f"__rag__:{chat_name}"

    # Ensure scope + tab exist (one tab per file)
    knowledge.create_scope(rag_scope)
    tab_id = knowledge.create_tab(filename, scope=rag_scope, tab_type='user')
    if not tab_id:
        # Tab already exists for this filename — delete old entries and re-upload
        with knowledge._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM knowledge_tabs WHERE name = ? AND scope = ?', (filename, rag_scope))
            row = cursor.fetchone()
        if row:
            tab_id = row[0]
            knowledge.delete_entries_by_filename(tab_id, filename)
        else:
            raise HTTPException(status_code=500, detail="Failed to create document tab")

    chunks = knowledge._chunk_text(text)
    for i, chunk in enumerate(chunks):
        knowledge.add_entry(tab_id, chunk, chunk_index=i, source_filename=filename)

    return {"filename": filename, "chunks": len(chunks), "scope": rag_scope}


@router.get("/api/chats/{chat_name}/documents")
async def list_chat_documents(chat_name: str, _=Depends(require_login)):
    """List uploaded documents for a chat."""
    from plugins.memory.tools import knowledge_tools as knowledge
    rag_scope = f"__rag__:{chat_name}"
    entries = knowledge.get_entries_by_scope(rag_scope)
    return {"documents": entries}


@router.delete("/api/chats/{chat_name}/documents/{filename:path}")
async def delete_chat_document(chat_name: str, filename: str, _=Depends(require_login)):
    """Delete a specific document from a chat's RAG scope."""
    from plugins.memory.tools import knowledge_tools as knowledge
    rag_scope = f"__rag__:{chat_name}"
    count = knowledge.delete_entries_by_scope_and_filename(rag_scope, filename)
    if count == 0:
        raise HTTPException(status_code=404, detail="Document not found")
    # If scope is now empty, clean it up
    remaining = knowledge.get_entries_by_scope(rag_scope)
    if not remaining:
        knowledge.delete_scope(rag_scope)
    return {"deleted": count, "filename": filename}


# =============================================================================
# MEMORY CRUD ROUTES (for Mind view management)
# =============================================================================

@router.get("/api/memory/list")
async def list_memories(request: Request, _=Depends(require_login)):
    """List memories for the Mind view. Includes private_key plaintext —
    the Mind view is the user's own UI surface, so showing the gating word
    is the feature, not a leak. Tools/MCP have their own filtered paths."""
    from plugins.memory.tools import memory_tools as memory
    scope = request.query_params.get('scope', 'default')
    with memory._get_connection() as conn:
        cursor = conn.cursor()
        scope_sql, scope_params = memory._scope_condition(scope)
        cursor.execute(
            f'SELECT id, content, timestamp, label, private_key FROM memories WHERE {scope_sql} ORDER BY label, timestamp DESC',
            scope_params
        )
        rows = cursor.fetchall()
    grouped = {}
    for mid, content, ts, label, private_key in rows:
        key = label or 'unlabeled'
        if key not in grouped:
            grouped[key] = []
        grouped[key].append({
            "id": mid, "content": content, "timestamp": ts,
            "label": label, "private_key": private_key,
        })
    return {"memories": grouped, "total": len(rows)}


@router.put("/api/memory/{memory_id}")
async def update_memory(memory_id: int, request: Request, _=Depends(require_login)):
    """Update memory content and re-embed."""
    from plugins.memory.tools import memory_tools as memory
    data = await request.json()
    content = data.get('content', '').strip()
    scope = data.get('scope', 'default')
    if not content:
        raise HTTPException(status_code=400, detail="Content is required")
    if len(content) > memory.MAX_MEMORY_LENGTH:
        raise HTTPException(status_code=400, detail=f"Max {memory.MAX_MEMORY_LENGTH} chars")

    with memory._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM memories WHERE id = ? AND scope = ?', (memory_id, scope))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Memory not found")

        keywords = memory._extract_keywords(content)
        label = data.get('label')

        embedding_blob = None
        embedding_provider = None
        embedding_dim = None
        embedder = memory._get_embedder()
        if embedder.available:
            embs = embedder.embed([content], prefix='search_document')
            if embs is not None:
                from core.embeddings import stamp_embedding
                embedding_blob, embedding_provider, embedding_dim = stamp_embedding(embs[0], embedder)

        # Only overwrite the embedding columns when we actually produced a
        # fresh vector — a transient remote-embedder failure should NOT strip
        # the row's existing vector off. Scout finding: this used to silently
        # delete semantic-search reachability on every edit while remote was
        # down.
        if embedding_blob is not None:
            cursor.execute(
                'UPDATE memories SET content = ?, keywords = ?, label = ?, '
                'embedding = ?, embedding_provider = ?, embedding_dim = ?, '
                'timestamp = CURRENT_TIMESTAMP WHERE id = ? AND scope = ?',
                (content, keywords, label, embedding_blob,
                 embedding_provider, embedding_dim, memory_id, scope)
            )
        else:
            cursor.execute(
                'UPDATE memories SET content = ?, keywords = ?, label = ?, '
                'timestamp = CURRENT_TIMESTAMP WHERE id = ? AND scope = ?',
                (content, keywords, label, memory_id, scope)
            )
        conn.commit()
    try:
        from core.mind_events import publish_mind_changed
        publish_mind_changed('memory', scope, 'update')
    except Exception:
        pass
    return {"updated": memory_id}


@router.delete("/api/memory/{memory_id}")
async def delete_memory_api(memory_id: int, request: Request, _=Depends(require_login)):
    """Delete a memory by ID. Accepts ?private_key=... so the Mind UI can
    delete private rows it can already see plaintext (the user is authenticated
    and the gate is for AI tool calls, not their own UI). Without this, the
    UI's delete button silently fails on private rows. 2026-04-21."""
    from plugins.memory.tools import memory_tools as memory
    scope = request.query_params.get('scope', 'default')
    private_key = request.query_params.get('private_key')
    result, success = memory._delete_memory(memory_id, scope, private_key=private_key)
    if success:
        return {"deleted": memory_id}
    raise HTTPException(status_code=404, detail=result)


# =============================================================================
# MEMORY EXPORT/IMPORT
# =============================================================================

@router.get("/api/memory/export")
async def export_memories(request: Request, _=Depends(require_login)):
    """Export all memories in a scope as JSON (no vectors).

    Includes private rows verbatim with their `private_key` plaintext.
    Rationale (Krem, 2026-04-21): losing the irreplaceable intimate moments
    is the most catastrophic thing in the app. Export means export. A
    user-initiated export that silently drops content is the worse failure
    mode than a user-initiated export that includes everything they own.
    The Mind UI already shows private keys plaintext — no new exposure.
    Import preserves the `private_key` on round-trip (see finding C3).
    """
    from plugins.memory.tools import memory_tools as memory
    scope = request.query_params.get('scope', 'default')
    with memory._get_connection() as conn:
        cursor = conn.cursor()
        scope_sql, scope_params = memory._scope_condition(scope)
        cursor.execute(
            f'SELECT content, label, timestamp, private_key FROM memories WHERE {scope_sql} ORDER BY timestamp',
            scope_params,
        )
        entries = [
            {"text": r[0], "label": r[1], "timestamp": r[2], "private_key": r[3]}
            for r in cursor.fetchall()
        ]
    return {
        "sapphire_export": True, "type": "memories", "version": 2,
        "scope": scope, "count": len(entries), "entries": entries,
    }


@router.get("/api/memory/duplicates")
async def find_duplicate_memories(request: Request, _=Depends(require_login)):
    """Find near-duplicate memories using vector similarity."""
    import numpy as np
    from plugins.memory.tools import memory_tools as memory

    scope = request.query_params.get('scope', 'default')
    default_thresh = getattr(config, 'MEMORY_DEDUP_THRESHOLD', 0.92)
    threshold = float(request.query_params.get('threshold', str(default_thresh)))

    # Dedup is only meaningful within a single vector space. Pull only rows
    # that share the currently-active provider's stamp — mixing spaces in
    # np.stack blows up, and even same-dim across different models produces
    # nonsense similarity scores that'd get surfaced as "duplicates".
    embedder = memory._get_embedder()
    active_provider = getattr(embedder, 'provider_id', None) if embedder else None

    with memory._get_connection() as conn:
        cursor = conn.cursor()
        scope_sql, scope_params = memory._scope_condition(scope)
        # Exclude private rows from dedup. The response returns full content
        # of both `keep` and `remove` rows in JSON — surfacing a private row
        # next to its near-duplicate would leak plaintext. Dedup only across
        # public rows. Witch-hunt 2026-04-21 finding C2.
        cursor.execute(
            f'SELECT id, content, timestamp, label, embedding, embedding_dim FROM memories '
            f'WHERE {scope_sql} AND embedding IS NOT NULL AND embedding_provider = ? '
            f'AND private_key IS NULL '
            f'ORDER BY timestamp',
            scope_params + [active_provider]
        )
        rows = cursor.fetchall()

    if len(rows) < 2:
        return {"pairs": [], "count": 0}

    # Load embeddings, filtering out any stray shape mismatches defensively.
    ids, contents, timestamps, labels, embeddings = [], [], [], [], []
    expected_dim = rows[0][5]
    for row_id, content, ts, lbl, emb_blob, stored_dim in rows:
        if stored_dim != expected_dim:
            continue
        try:
            vec = np.frombuffer(emb_blob, dtype=np.float32)
            if vec.shape[0] != expected_dim:
                continue
        except Exception:
            continue
        ids.append(row_id)
        contents.append(content)
        timestamps.append(ts)
        labels.append(lbl)
        embeddings.append(vec)

    if len(embeddings) < 2:
        return {"pairs": [], "count": 0}

    emb_matrix = np.stack(embeddings)  # (N, D)

    # Compute pairwise similarities (dot product on L2-normalized vectors)
    # Only compute upper triangle to avoid duplicates
    pairs = []
    n = len(ids)
    # Batch: compute full similarity matrix then extract pairs above threshold
    sim_matrix = emb_matrix @ emb_matrix.T  # (N, N)

    for i in range(n):
        for j in range(i + 1, n):
            sim = float(sim_matrix[i, j])
            if sim >= threshold:
                # Older memory is the one to keep (earlier timestamp / lower index)
                pairs.append({
                    "similarity": round(sim, 3),
                    "keep": {"id": ids[i], "content": contents[i], "timestamp": timestamps[i], "label": labels[i]},
                    "remove": {"id": ids[j], "content": contents[j], "timestamp": timestamps[j], "label": labels[j]},
                })

    # Sort by similarity descending (most similar first)
    pairs.sort(key=lambda p: p["similarity"], reverse=True)

    return {"pairs": pairs[:200], "count": len(pairs)}


@router.post("/api/memory/import")
async def import_memories(request: Request, _=Depends(require_login)):
    """Import memories from JSON export. Skips exact text duplicates."""
    import hashlib
    from plugins.memory.tools import memory_tools as memory

    data = await request.json()
    entries = data.get("entries", [])
    scope = data.get("scope", "default")
    if not entries:
        raise HTTPException(status_code=400, detail="No entries to import")

    # Build hash set of existing memories for dup detection
    with memory._get_connection() as conn:
        cursor = conn.cursor()
        scope_sql, scope_params = memory._scope_condition(scope)
        cursor.execute(f'SELECT content FROM memories WHERE {scope_sql}', scope_params)
        existing_hashes = {hashlib.sha256(r[0].strip().lower().encode()).hexdigest() for r in cursor.fetchall()}

    imported = 0
    skipped = 0
    for entry in entries:
        text = (entry.get("text") or "").strip()
        if not text:
            continue
        text_hash = hashlib.sha256(text.strip().lower().encode()).hexdigest()
        if text_hash in existing_hashes:
            skipped += 1
            continue
        label = entry.get("label")
        # Preserve private_key on round-trip. Without this, an export that
        # included private rows (via ?include_private=1) would re-import as
        # public, permanently losing the gating word. Witch-hunt 2026-04-21
        # finding C3.
        private_key = entry.get("private_key")
        memory._save_memory(text, label=label, scope=scope, private_key=private_key)
        existing_hashes.add(text_hash)
        imported += 1

    return {"imported": imported, "skipped": skipped, "total": len(entries)}


# =============================================================================
# PEOPLE EXPORT/IMPORT
# =============================================================================

@router.get("/api/knowledge/people/export")
async def export_people(request: Request, _=Depends(require_login)):
    """Export all people in a scope as JSON."""
    from plugins.memory.tools import knowledge_tools as knowledge
    scope = request.query_params.get('scope', 'default')
    people = knowledge.get_people(scope)
    # Strip internal IDs, keep portable fields
    entries = []
    for p in people:
        entries.append({
            "name": p["name"],
            "relationship": p.get("relationship"),
            "phone": p.get("phone"),
            "email": p.get("email"),
            "address": p.get("address"),
            "notes": p.get("notes"),
        })
    return {
        "sapphire_export": True, "type": "people", "version": 1,
        "scope": scope, "count": len(entries), "entries": entries,
    }


@router.post("/api/knowledge/people/import")
async def import_people_json(request: Request, _=Depends(require_login)):
    """Import people from JSON export. Skips duplicates by name+email."""
    from plugins.memory.tools import knowledge_tools as knowledge

    data = await request.json()
    entries = data.get("entries", [])
    scope = data.get("scope", "default")
    if not entries:
        raise HTTPException(status_code=400, detail="No entries to import")

    # Build existing set for dup detection (same logic as VCF import)
    existing = knowledge.get_people(scope)
    existing_keys = {(p['name'].lower().strip(), (p.get('email') or '').lower().strip()) for p in existing}

    imported = 0
    skipped = 0
    for entry in entries:
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        email = (entry.get("email") or "").strip()
        dup_key = (name.lower(), email.lower())
        if dup_key in existing_keys:
            skipped += 1
            continue
        knowledge.create_or_update_person(
            name=name, relationship=entry.get("relationship"),
            phone=entry.get("phone"), email=email,
            address=entry.get("address"), notes=entry.get("notes"),
            scope=scope
        )
        existing_keys.add(dup_key)
        imported += 1

    return {"imported": imported, "skipped": skipped, "total": len(entries)}


# =============================================================================
# KNOWLEDGE TAB EXPORT/IMPORT
# =============================================================================

@router.get("/api/knowledge/tabs/{tab_id}/export")
async def export_knowledge_tab(tab_id: int, request: Request, _=Depends(require_login)):
    """Export a knowledge tab with all entries as JSON (no vectors)."""
    from plugins.memory.tools import knowledge_tools as knowledge
    scope = request.query_params.get('scope', 'default')
    tabs = knowledge.get_tabs(scope)
    tab = next((t for t in tabs if t["id"] == tab_id), None)
    if not tab:
        raise HTTPException(status_code=404, detail="Tab not found")

    entries = knowledge.get_tab_entries(tab_id, scope=scope)
    return {
        "sapphire_export": True, "type": "knowledge_tab", "version": 1,
        "name": tab["name"], "description": tab.get("description", ""),
        "tab_type": tab.get("type", "user"), "scope": scope,
        "count": len(entries),
        "entries": [{"content": e["content"], "source_filename": e.get("source_filename")} for e in entries],
    }


@router.post("/api/knowledge/tabs/import")
async def import_knowledge_tab(request: Request, _=Depends(require_login)):
    """Import a knowledge tab from JSON export. Creates tab, adds entries."""
    from plugins.memory.tools import knowledge_tools as knowledge

    data = await request.json()
    name = (data.get("name") or "").strip()
    scope = data.get("scope", "default")
    entries = data.get("entries", [])
    if not name:
        raise HTTPException(status_code=400, detail="Tab name required")

    overwrite = data.get("overwrite", False)

    # Check if tab exists in this scope
    existing_tabs = knowledge.get_tabs(scope)
    existing = next((t for t in existing_tabs if t["name"].lower() == name.lower()), None)

    if existing and not overwrite:
        # Merge: add only entries with text not already in the tab
        existing_entries = knowledge.get_tab_entries(existing["id"], scope=scope)
        existing_texts = {e["content"].strip().lower() for e in existing_entries}
        tab_id = existing["id"]
        imported = 0
        skipped = 0
        for entry in entries:
            content = (entry.get("content") or "").strip()
            if not content or content.lower() in existing_texts:
                skipped += 1
                continue
            knowledge.add_entry(tab_id, content, source_filename=entry.get("source_filename"))
            existing_texts.add(content.lower())
            imported += 1
        return {"imported": imported, "skipped": skipped, "tab_id": tab_id, "merged": True}
    elif existing and overwrite:
        # Delete existing tab, recreate
        knowledge.delete_tab(existing["id"])

    # Create new tab
    tab_type = data.get("tab_type", "user")
    description = data.get("description")
    tab_id = knowledge.create_tab(name, scope=scope, description=description, tab_type=tab_type)
    if not tab_id:
        raise HTTPException(status_code=500, detail="Failed to create tab")

    imported = 0
    for entry in entries:
        content = (entry.get("content") or "").strip()
        if not content:
            continue
        knowledge.add_entry(tab_id, content, source_filename=entry.get("source_filename"))
        imported += 1

    return {"imported": imported, "skipped": 0, "tab_id": tab_id, "merged": False}


@router.get("/api/knowledge/dedup")
async def find_duplicates(request: Request, _=Depends(require_login)):
    """Scan knowledge base for duplicate entries. Returns grouped duplicates.

    Three detection modes:
    - exact: identical content text
    - file: same filename in multiple tabs
    - similar: embedding cosine similarity > threshold
    """
    import hashlib
    import numpy as np
    from plugins.memory.tools import knowledge_tools as knowledge

    scope = request.query_params.get("scope", "")
    threshold = float(request.query_params.get("threshold", "0.95"))
    mode = request.query_params.get("mode", "all")  # exact, file, similar, all

    with knowledge._get_connection() as conn:
        cursor = conn.cursor()

        scope_filter = "WHERE t.scope = ?" if scope else ""
        scope_params = (scope,) if scope else ()

        cursor.execute(f'''
            SELECT e.id, e.tab_id, e.content, e.source_filename, e.chunk_index,
                   e.embedding, t.name as tab_name, t.scope
            FROM knowledge_entries e JOIN knowledge_tabs t ON e.tab_id = t.id
            {scope_filter}
            ORDER BY t.scope, t.name, e.source_filename, e.chunk_index
        ''', scope_params)
        rows = cursor.fetchall()

    if not rows:
        return {"duplicates": [], "stats": {"total_entries": 0}}

    entries = []
    for r in rows:
        entries.append({
            "id": r[0], "tab_id": r[1], "content": r[2], "filename": r[3],
            "chunk_index": r[4], "embedding": r[5], "tab_name": r[6], "scope": r[7],
        })

    results = {"exact": [], "file": [], "similar": []}

    # --- Exact duplicates: identical content ---
    if mode in ("exact", "all"):
        by_hash = {}
        for e in entries:
            h = hashlib.md5(e["content"].encode()).hexdigest()
            by_hash.setdefault(h, []).append(e)

        for h, group in by_hash.items():
            if len(group) > 1:
                results["exact"].append({
                    "count": len(group),
                    "preview": group[0]["content"][:120],
                    "entries": [{
                        "id": e["id"], "tab_name": e["tab_name"], "scope": e["scope"],
                        "filename": e["filename"], "chunk_index": e["chunk_index"],
                    } for e in group],
                })

    # --- File duplicates: same filename in different tabs ---
    if mode in ("file", "all"):
        by_file = {}
        for e in entries:
            if e["filename"]:
                key = e["filename"]
                by_file.setdefault(key, {}).setdefault(e["tab_id"], []).append(e)

        for filename, tabs in by_file.items():
            if len(tabs) > 1:
                results["file"].append({
                    "filename": filename,
                    "tabs": [{
                        "tab_id": tid, "tab_name": elist[0]["tab_name"],
                        "scope": elist[0]["scope"], "chunks": len(elist),
                    } for tid, elist in tabs.items()],
                })

    # --- Similar entries: embedding cosine > threshold ---
    if mode in ("similar", "all"):
        # Only check entries with embeddings, cap at 2000 to avoid O(n^2) explosion
        with_emb = [e for e in entries if e["embedding"]][:2000]
        if with_emb:
            vecs = []
            for e in with_emb:
                vecs.append(np.frombuffer(e["embedding"], dtype=np.float32))

            seen_pairs = set()
            similar_groups = {}  # leader_id -> [member entries]

            for i in range(len(vecs)):
                for j in range(i + 1, len(vecs)):
                    # Skip if same tab + same file (those are sequential chunks, not dups)
                    if (with_emb[i]["tab_id"] == with_emb[j]["tab_id"] and
                            with_emb[i]["filename"] == with_emb[j]["filename"]):
                        continue

                    sim = float(np.dot(vecs[i], vecs[j]))
                    if sim >= threshold:
                        pair = (with_emb[i]["id"], with_emb[j]["id"])
                        if pair not in seen_pairs:
                            seen_pairs.add(pair)
                            leader = with_emb[i]["id"]
                            if leader not in similar_groups:
                                similar_groups[leader] = {
                                    "score": sim,
                                    "preview": with_emb[i]["content"][:120],
                                    "entries": [{
                                        "id": with_emb[i]["id"], "tab_name": with_emb[i]["tab_name"],
                                        "scope": with_emb[i]["scope"], "filename": with_emb[i]["filename"],
                                    }],
                                }
                            similar_groups[leader]["entries"].append({
                                "id": with_emb[j]["id"], "tab_name": with_emb[j]["tab_name"],
                                "scope": with_emb[j]["scope"], "filename": with_emb[j]["filename"],
                                "score": round(sim, 3),
                            })

            results["similar"] = list(similar_groups.values())

    total_dups = len(results["exact"]) + len(results["file"]) + len(results["similar"])

    return {
        "duplicates": results,
        "stats": {
            "total_entries": len(entries),
            "exact_groups": len(results["exact"]),
            "file_groups": len(results["file"]),
            "similar_groups": len(results["similar"]),
            "total_duplicate_groups": total_dups,
        },
    }


@router.delete("/api/knowledge/dedup/resolve")
async def resolve_duplicates(request: Request, _=Depends(require_login)):
    """Delete specific duplicate entries by ID list. Keeps the first, deletes the rest."""
    from plugins.memory.tools import knowledge_tools as knowledge

    data = await request.json()
    delete_ids = data.get("ids", [])

    if not delete_ids:
        raise HTTPException(status_code=400, detail="No entry IDs provided")

    deleted = 0
    for eid in delete_ids:
        try:
            knowledge.delete_entry(eid)
            deleted += 1
        except Exception:
            pass

    return {"deleted": deleted, "requested": len(delete_ids)}
