# core/routes/vault.py — prompt-vault lifecycle routes.
"""POST setup / unlock / lock. Vault state rides /api/status (chat.py) as a
top-level `vault: {exists, unlocked}` key — the same poll channel that syncs
private_chat.

Wrong key = 403, NEVER 401 — the shared fetch.js redirects any 401 to
/login, so a passphrase typo would log the user out (recon finding 5). 401
is transport-layer in this app; wrong-key is application. A constant delay
on every failed unlock blunts guessing. The passphrase is read from the
JSON body and never logged or echoed.
"""
import asyncio
import logging

from fastapi import APIRouter, Request, Depends, HTTPException

from core.auth import require_login
from core import prompt_vault

logger = logging.getLogger(__name__)
router = APIRouter()

_FAIL_DELAY_S = 0.75


def _managed_guard():
    """Managed mode hides the eyeball and 403s private chats — the vault
    inherits unavailability. Locking stays allowed (safe direction)."""
    from core.settings_manager import settings as sm_settings
    if sm_settings.is_managed():
        raise HTTPException(status_code=403,
                            detail="The vault is disabled in managed mode")


@router.post("/api/vault/setup")
async def vault_setup(request: Request, _=Depends(require_login)):
    """Create the vault and unlock it. 409 if one already exists."""
    _managed_guard()
    data = await request.json()
    ok, code = prompt_vault.setup(data.get('key') or '')
    if ok:
        return {"status": "success", "vault": prompt_vault.vault_status()}
    if code == 'exists':
        raise HTTPException(status_code=409, detail="A vault already exists")
    if code == 'bad_passphrase':
        raise HTTPException(status_code=400, detail="Passphrase must not be empty")
    raise HTTPException(status_code=500, detail=f"Vault setup failed ({code})")


@router.post("/api/vault/unlock")
async def vault_unlock(request: Request, _=Depends(require_login)):
    """Unlock with the passphrase. Idempotent while unlocked."""
    _managed_guard()
    data = await request.json()
    ok, code = prompt_vault.unlock(data.get('key') or '')
    if ok:
        return {"status": "success", "vault": prompt_vault.vault_status()}
    await asyncio.sleep(_FAIL_DELAY_S)   # constant failure delay
    if code == 'no_vault':
        raise HTTPException(status_code=404,
                            detail="No vault exists — set one up first")
    if code == 'corrupt':
        raise HTTPException(status_code=500,
                            detail="Vault file was corrupt — quarantined as "
                                   ".bad-<stamp> beside it. Restore from a backup.")
    # wrong_key
    raise HTTPException(status_code=403, detail="Wrong passphrase")


@router.post("/api/vault/move")
async def vault_move(request: Request, _=Depends(require_login)):
    """v1.1 store toggle: move a prompt or piece between the vault and the
    regular store. direction 'in' = encrypt, 'out' = plaintext (the client
    confirms 'out' — it writes decrypted content to disk)."""
    _managed_guard()
    data = await request.json()
    direction = data.get('direction')
    kind = data.get('kind')
    if direction not in ('in', 'out'):
        raise HTTPException(status_code=400, detail="direction must be 'in' or 'out'")
    from core import prompt_crud
    if kind == 'prompt':
        fn = (prompt_crud.move_prompt_to_vault if direction == 'in'
              else prompt_crud.move_prompt_from_vault)
        ok, msg = fn(data.get('name') or '')
    elif kind == 'piece':
        fn = (prompt_crud.move_piece_to_vault if direction == 'in'
              else prompt_crud.move_piece_from_vault)
        ok, msg = fn(data.get('comp_type') or '', data.get('key') or '')
    else:
        raise HTTPException(status_code=400, detail="kind must be 'prompt' or 'piece'")
    if ok:
        return {"status": "success", "message": msg}
    if msg == prompt_crud.VAULT_LOCKED_MSG:
        raise HTTPException(status_code=409, detail=msg)
    raise HTTPException(status_code=400, detail=msg)


@router.post("/api/vault/lock")
async def vault_lock(_=Depends(require_login)):
    """Lock NOW. Synchronous by contract (phase 0b): active-preset handoff
    and the vault_changed event complete inside lock(), BEFORE this returns
    200 — the eyeball client's follow-up `PUT private_chat:false` must see
    the locked world. Idempotent."""
    prompt_vault.lock(reason="user")
    return {"status": "success", "vault": prompt_vault.vault_status()}
