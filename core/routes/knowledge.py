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


# =============================================================================
# MEMORY SCOPE ROUTES
# =============================================================================

@router.get("/api/memory/scopes")
async def get_memory_scopes(request: Request, _=Depends(require_login)):
    """Get list of memory scopes."""
    from functions import memory
    scopes = memory.get_scopes()
    return {"scopes": scopes}


@router.post("/api/memory/scopes")
async def create_memory_scope(request: Request, _=Depends(require_login)):
    """Create a new memory scope."""
    import re
    from functions import memory
    data = await request.json()
    name = data.get('name', '').strip().lower()
    if not name or not re.match(r'^[a-z0-9_]{1,32}$', name):
        raise HTTPException(status_code=400, detail="Invalid scope name")
    if memory.create_scope(name):
        return {"created": name}
    else:
        raise HTTPException(status_code=500, detail="Failed to create scope")


@router.delete("/api/memory/scopes/{scope_name}")
async def delete_memory_scope(scope_name: str, request: Request, _=Depends(require_login)):
    """Delete a memory scope and ALL its memories. Requires confirmation token."""
    from functions import memory
    data = await request.json()
    if data.get('confirm') != 'DELETE':
        raise HTTPException(status_code=400, detail="Confirmation required")
    result = memory.delete_scope(scope_name)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# =============================================================================
# GOAL SCOPE ROUTES
# =============================================================================

@router.get("/api/goals/scopes")
async def get_goal_scopes(request: Request, _=Depends(require_login)):
    """Get list of goal scopes."""
    from functions import goals
    scopes = goals.get_scopes()
    return {"scopes": scopes}


@router.post("/api/goals/scopes")
async def create_goal_scope(request: Request, _=Depends(require_login)):
    """Create a new goal scope."""
    import re
    from functions import goals
    data = await request.json()
    name = data.get('name', '').strip().lower()
    if not name or not re.match(r'^[a-z0-9_]{1,32}$', name):
        raise HTTPException(status_code=400, detail="Invalid scope name")
    if goals.create_scope(name):
        return {"created": name}
    else:
        raise HTTPException(status_code=500, detail="Failed to create scope")


@router.delete("/api/goals/scopes/{scope_name}")
async def remove_goal_scope(scope_name: str, request: Request, _=Depends(require_login)):
    from functions import goals
    data = await request.json()
    if data.get('confirm') != 'DELETE':
        raise HTTPException(status_code=400, detail="Confirmation required")
    result = goals.delete_scope(scope_name)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/api/goals")
async def list_goals_api(request: Request, _=Depends(require_login)):
    from functions import goals
    scope = request.query_params.get('scope', 'default')
    status = request.query_params.get('status', 'active')
    return {"goals": goals.get_goals_list(scope, status)}


@router.get("/api/goals/{goal_id}")
async def get_goal_api(goal_id: int, request: Request, _=Depends(require_login)):
    from functions import goals
    detail = goals.get_goal_detail(goal_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Goal not found")
    return detail


@router.post("/api/goals")
async def create_goal_endpoint(request: Request, _=Depends(require_login)):
    from functions import goals
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
    from functions import goals
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
    from functions import goals
    data = await request.json()
    try:
        note_id = goals.add_progress_note(goal_id, data.get('note', ''))
        return {"id": note_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/goals/{goal_id}")
async def delete_goal_endpoint(goal_id: int, request: Request, _=Depends(require_login)):
    from functions import goals
    try:
        title = goals.delete_goal_api(goal_id)
        return {"deleted": goal_id, "title": title}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# =============================================================================
# KNOWLEDGE BASE ROUTES
# =============================================================================

@router.get("/api/knowledge/scopes")
async def get_knowledge_scopes(request: Request, _=Depends(require_login)):
    from functions import knowledge
    scopes = knowledge.get_scopes()
    return {"scopes": scopes}


@router.post("/api/knowledge/scopes")
async def create_knowledge_scope(request: Request, _=Depends(require_login)):
    import re as _re
    from functions import knowledge
    data = await request.json()
    name = data.get('name', '').strip().lower()
    if not name or not _re.match(r'^[a-z0-9_]{1,32}$', name):
        raise HTTPException(status_code=400, detail="Invalid scope name")
    if knowledge.create_scope(name):
        return {"created": name}
    else:
        raise HTTPException(status_code=500, detail="Failed to create scope")


@router.delete("/api/knowledge/scopes/{scope_name}")
async def delete_knowledge_scope(scope_name: str, request: Request, _=Depends(require_login)):
    """Delete a knowledge scope, ALL its tabs, and ALL entries. Requires confirmation token."""
    from functions import knowledge
    data = await request.json()
    if data.get('confirm') != 'DELETE':
        raise HTTPException(status_code=400, detail="Confirmation required")
    result = knowledge.delete_scope(scope_name)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/api/knowledge/people/scopes")
async def list_people_scopes(request: Request, _=Depends(require_login)):
    from functions import knowledge
    return {"scopes": knowledge.get_people_scopes()}


@router.post("/api/knowledge/people/scopes")
async def create_people_scope(request: Request, _=Depends(require_login)):
    from functions import knowledge
    data = await request.json()
    name = data.get('name', '').strip().lower()
    if not name or len(name) > 32:
        raise HTTPException(status_code=400, detail="Invalid scope name")
    knowledge.create_people_scope(name)
    return {"created": name}


@router.delete("/api/knowledge/people/scopes/{scope_name}")
async def remove_people_scope(scope_name: str, request: Request, _=Depends(require_login)):
    from functions import knowledge
    data = await request.json()
    if data.get('confirm') != 'DELETE':
        raise HTTPException(status_code=400, detail="Confirmation required")
    result = knowledge.delete_people_scope(scope_name)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/api/knowledge/people")
async def list_people(request: Request, _=Depends(require_login)):
    from functions import knowledge
    scope = request.query_params.get('scope', 'default')
    return {"people": knowledge.get_people(scope)}


@router.post("/api/knowledge/people")
async def save_person(request: Request, _=Depends(require_login)):
    from functions import knowledge
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
    from functions import knowledge
    if knowledge.delete_person(person_id):
        return {"deleted": person_id}
    raise HTTPException(status_code=404, detail="Person not found")


@router.post("/api/knowledge/people/import-vcf")
async def import_vcf(request: Request, _=Depends(require_login)):
    """Import contacts from a VCF (vCard) file."""
    from functions import knowledge
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
    from functions import knowledge
    scope = request.query_params.get('scope', 'default')
    tab_type = request.query_params.get('type')
    return {"tabs": knowledge.get_tabs(scope, tab_type)}


@router.get("/api/knowledge/tabs/{tab_id}")
async def get_tab(tab_id: int, request: Request, _=Depends(require_login)):
    from functions import knowledge
    scope = request.query_params.get('scope', 'default')
    entries = knowledge.get_tab_entries(tab_id, scope)
    return {"entries": entries}


@router.post("/api/knowledge/tabs")
async def create_knowledge_tab(request: Request, _=Depends(require_login)):
    from functions import knowledge
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
    from functions import knowledge
    data = await request.json()
    if knowledge.update_tab(tab_id, data.get('name'), data.get('description')):
        return {"updated": tab_id}
    raise HTTPException(status_code=404, detail="Tab not found")


@router.delete("/api/knowledge/tabs/{tab_id}")
async def delete_knowledge_tab(tab_id: int, request: Request, _=Depends(require_login)):
    from functions import knowledge
    if knowledge.delete_tab(tab_id):
        return {"deleted": tab_id}
    raise HTTPException(status_code=404, detail="Tab not found")


@router.post("/api/knowledge/tabs/{tab_id}/entries")
async def add_knowledge_entry(tab_id: int, request: Request, _=Depends(require_login)):
    from functions import knowledge
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
    from functions import knowledge

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
    chunks = knowledge._chunk_text(text)
    entry_ids = []
    for i, chunk in enumerate(chunks):
        eid = knowledge.add_entry(tab_id, chunk, chunk_index=i, source_filename=filename)
        entry_ids.append(eid)

    return {"filename": filename, "chunks": len(chunks), "entry_ids": entry_ids}


@router.delete("/api/knowledge/tabs/{tab_id}/file/{filename}")
async def delete_knowledge_file(tab_id: int, filename: str, _=Depends(require_login)):
    """Delete all entries from a specific uploaded file."""
    from functions import knowledge
    count = knowledge.delete_entries_by_filename(tab_id, filename)
    if count == 0:
        raise HTTPException(status_code=404, detail="No entries found for that file")
    return {"deleted": count, "filename": filename}


@router.put("/api/knowledge/entries/{entry_id}")
async def update_knowledge_entry(entry_id: int, request: Request, _=Depends(require_login)):
    from functions import knowledge
    data = await request.json()
    content = data.get('content', '').strip()
    if not content:
        raise HTTPException(status_code=400, detail="Content is required")
    if knowledge.update_entry(entry_id, content):
        return {"updated": entry_id}
    raise HTTPException(status_code=404, detail="Entry not found")


@router.delete("/api/knowledge/entries/{entry_id}")
async def delete_knowledge_entry(entry_id: int, request: Request, _=Depends(require_login)):
    from functions import knowledge
    if knowledge.delete_entry(entry_id):
        return {"deleted": entry_id}
    raise HTTPException(status_code=404, detail="Entry not found")


# =============================================================================
# PER-CHAT RAG (Document Context)
# =============================================================================

@router.post("/api/chats/{chat_name}/documents")
async def upload_chat_document(chat_name: str, file: UploadFile = File(...), _=Depends(require_login)):
    """Upload a document for per-chat RAG context."""
    from functions import knowledge

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
        conn = knowledge._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM knowledge_tabs WHERE name = ? AND scope = ?', (filename, rag_scope))
        row = cursor.fetchone()
        conn.close()
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
    from functions import knowledge
    rag_scope = f"__rag__:{chat_name}"
    entries = knowledge.get_entries_by_scope(rag_scope)
    return {"documents": entries}


@router.delete("/api/chats/{chat_name}/documents/{filename:path}")
async def delete_chat_document(chat_name: str, filename: str, _=Depends(require_login)):
    """Delete a specific document from a chat's RAG scope."""
    from functions import knowledge
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
    """List memories grouped by label for the Mind view."""
    from functions import memory
    scope = request.query_params.get('scope', 'default')
    with memory._get_connection() as conn:
        cursor = conn.cursor()
        scope_sql, scope_params = memory._scope_condition(scope)
        cursor.execute(
            f'SELECT id, content, timestamp, label FROM memories WHERE {scope_sql} ORDER BY label, timestamp DESC',
            scope_params
        )
        rows = cursor.fetchall()
    grouped = {}
    for mid, content, ts, label in rows:
        key = label or 'unlabeled'
        if key not in grouped:
            grouped[key] = []
        grouped[key].append({"id": mid, "content": content, "timestamp": ts, "label": label})
    return {"memories": grouped, "total": len(rows)}


@router.put("/api/memory/{memory_id}")
async def update_memory(memory_id: int, request: Request, _=Depends(require_login)):
    """Update memory content and re-embed."""
    from functions import memory
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
        embedder = memory._get_embedder()
        if embedder.available:
            embs = embedder.embed([content], prefix='search_document')
            if embs is not None:
                embedding_blob = embs[0].tobytes()

        cursor.execute(
            'UPDATE memories SET content = ?, keywords = ?, label = ?, embedding = ?, timestamp = CURRENT_TIMESTAMP WHERE id = ? AND scope = ?',
            (content, keywords, label, embedding_blob, memory_id, scope)
        )
        conn.commit()
    return {"updated": memory_id}


@router.delete("/api/memory/{memory_id}")
async def delete_memory_api(memory_id: int, request: Request, _=Depends(require_login)):
    """Delete a memory by ID."""
    from functions import memory
    scope = request.query_params.get('scope', 'default')
    result, success = memory._delete_memory(memory_id, scope)
    if success:
        return {"deleted": memory_id}
    raise HTTPException(status_code=404, detail=result)


# =============================================================================
# MEMORY EXPORT/IMPORT
# =============================================================================

@router.get("/api/memory/export")
async def export_memories(request: Request, _=Depends(require_login)):
    """Export all memories in a scope as JSON (no vectors)."""
    from functions import memory
    scope = request.query_params.get('scope', 'default')
    with memory._get_connection() as conn:
        cursor = conn.cursor()
        scope_sql, scope_params = memory._scope_condition(scope)
        cursor.execute(
            f'SELECT content, label, timestamp FROM memories WHERE {scope_sql} ORDER BY timestamp',
            scope_params
        )
        entries = [{"text": r[0], "label": r[1], "timestamp": r[2]} for r in cursor.fetchall()]
    return {
        "sapphire_export": True, "type": "memories", "version": 1,
        "scope": scope, "count": len(entries), "entries": entries,
    }


@router.get("/api/memory/duplicates")
async def find_duplicate_memories(request: Request, _=Depends(require_login)):
    """Find near-duplicate memories using vector similarity."""
    import numpy as np
    from functions import memory

    scope = request.query_params.get('scope', 'default')
    default_thresh = getattr(config, 'MEMORY_DEDUP_THRESHOLD', 0.92)
    threshold = float(request.query_params.get('threshold', str(default_thresh)))

    with memory._get_connection() as conn:
        cursor = conn.cursor()
        scope_sql, scope_params = memory._scope_condition(scope)
        cursor.execute(
            f'SELECT id, content, timestamp, label, embedding FROM memories WHERE {scope_sql} AND embedding IS NOT NULL ORDER BY timestamp',
            scope_params
        )
        rows = cursor.fetchall()

    if len(rows) < 2:
        return {"pairs": [], "count": 0}

    # Load all embeddings into a matrix for fast comparison
    ids, contents, timestamps, labels, embeddings = [], [], [], [], []
    for row_id, content, ts, lbl, emb_blob in rows:
        ids.append(row_id)
        contents.append(content)
        timestamps.append(ts)
        labels.append(lbl)
        embeddings.append(np.frombuffer(emb_blob, dtype=np.float32))

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
    from functions import memory

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
        memory._save_memory(text, label=label, scope=scope)
        existing_hashes.add(text_hash)
        imported += 1

    return {"imported": imported, "skipped": skipped, "total": len(entries)}


# =============================================================================
# PEOPLE EXPORT/IMPORT
# =============================================================================

@router.get("/api/knowledge/people/export")
async def export_people(request: Request, _=Depends(require_login)):
    """Export all people in a scope as JSON."""
    from functions import knowledge
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
    from functions import knowledge

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
    from functions import knowledge
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
    from functions import knowledge

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
