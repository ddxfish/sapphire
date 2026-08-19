# core/prompt_vault.py — encrypted prompt overlay (the vault).
"""Prompt vault: scrypt + AES-256-GCM encrypted prompt store that merges into
the prompt system only while unlocked (tmp/prompt-vault-plan.md, build item 1).

Mirror-registry pattern like core/prompt_packs.py: decrypted content lives in
memory only, merged at READ time via the overlay_* accessors. Locked = every
accessor returns {} — vault names exist nowhere. App-wide global: unlocked for
every consumer or none.

Crypto is bytes-level on purpose — backup_crypto's file→file API would stage
plaintext on disk. Distinct magic so the vault is never misidentified as a
backup by is_encrypted_backup / the decrypt tool.

FILE FORMAT (single frame; vault JSON is small):
  MAGIC (12) | header_len (4 BE) | header_json | nonce (12) | ciphertext(+tag)
  header_json = {"v":1,"kdf":"scrypt","n":N,"r":R,"p":P,"salt":hex}
  AAD = MAGIC + header_json (binds params/salt to the ciphertext)

Lock discipline: NEVER call into the prompt system while holding _lock —
prompt_manager's properties will call overlay_* under prompt_manager._lock
once the merge lands, so vault-lock → prompt-system is an AB-BA deadlock.
lock() snapshots under _lock, then does handoff + events outside it.

References index (`vault_refs.json`, plaintext sidecar): ONLY vault names
actually referenced by main-app entities — never the full inventory (Krem's
ruling C amendment 2026-08-09). Stamping requires the vault unlocked (a
locked name isn't in any dropdown, so references can't be created sealed);
dropping works any time — the index can only shrink while locked.
"""
import hashlib
import json
import logging
import os
import struct
import threading
import time
from pathlib import Path

from core.fs_utils import replace_with_retry

logger = logging.getLogger(__name__)

MAGIC = b"SAPPHIREVLT\x01"      # 12 bytes — NOT the backup magic
_N, _R, _P = 2 ** 14, 8, 1      # scrypt params (same work factor as backups)

_PROMPTS_DIR = Path(__file__).parent.parent / "user" / "prompts"
VAULT_PATH = _PROMPTS_DIR / "prompt_vault.enc"
REFS_PATH = _PROMPTS_DIR / "vault_refs.json"

_RESERVED_NAMES = ('default', 'assembled')

_lock = threading.RLock()
_key = None            # derived AES key while unlocked, else None
_salt = None           # header salt cached so saves reuse the derived key
_data = None           # decrypted {"monoliths", "components", "scenario_presets"}
_timer = None          # idle-lock threading.Timer
_defer_timer = None    # Ruling-B deferred-lock retry (cancelled on unlock/stop)
_last_activity = 0.0   # time.monotonic() of last vault activity
_refs = {}             # {name: "monolith"|"preset"} — referenced names only
_refs_loaded = False
_piece_salt = None     # random hex salt for the piece-refs hashes (sidecar)
_piece_hashes = set()  # salted sha256 of plaintext piece keys vault presets use


class VaultCorrupt(ValueError):
    """Structural damage: bad magic, unreadable header, truncation."""


class VaultWrongKey(ValueError):
    """GCM tag mismatch: wrong passphrase (or tampered ciphertext)."""


# ── crypto (bytes-level) ──

def _derive(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=_N, r=_R, p=_P).derive(
        passphrase.encode("utf-8"))


def encrypt_bytes(plaintext: bytes, key: bytes, salt: bytes) -> bytes:
    """One-frame AES-256-GCM with the vault framing. Caller supplies the
    derived key + the salt it was derived from (salt travels in the header
    so decrypt can re-derive)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    header = json.dumps({"v": 1, "kdf": "scrypt", "n": _N, "r": _R, "p": _P,
                         "salt": salt.hex()}).encode("utf-8")
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, MAGIC + header)
    return MAGIC + struct.pack(">I", len(header)) + header + nonce + ct


def decrypt_bytes(blob: bytes, passphrase: str):
    """Decrypt a vault blob. Returns (plaintext, key, salt) so the caller can
    cache the derived key for saves. Raises VaultCorrupt on structural damage,
    VaultWrongKey on tag mismatch."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if blob[:len(MAGIC)] != MAGIC:
        raise VaultCorrupt("not a Sapphire vault (bad magic)")
    try:
        hlen = struct.unpack(">I", blob[12:16])[0]
        header = blob[16:16 + hlen]
        hdr = json.loads(header.decode("utf-8"))
        salt = bytes.fromhex(hdr["salt"])
        nonce = blob[16 + hlen:28 + hlen]
        ct = blob[28 + hlen:]
        if len(header) < hlen or len(nonce) < 12 or not ct:
            raise ValueError("truncated")
        key = _derive_with_params(passphrase, salt, hdr)
    except (struct.error, ValueError, KeyError, TypeError,
            UnicodeDecodeError) as e:
        raise VaultCorrupt(f"corrupt or unsupported vault header: {e}")
    try:
        return AESGCM(key).decrypt(nonce, ct, MAGIC + header), key, salt
    except InvalidTag:
        raise VaultWrongKey("wrong passphrase (or tampered vault)")


def _derive_with_params(passphrase: str, salt: bytes, hdr: dict) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=hdr["n"], r=hdr["r"],
                  p=hdr["p"]).derive(passphrase.encode("utf-8"))


# ── normalization (same discipline as _load_pieces / register_pack) ──

def _normalize(raw):
    """Shape-check decrypted JSON; drop+warn junk. privacy_required is forced
    True on every monolith/preset — vault prompts are private by construction."""
    if not isinstance(raw, dict):
        logger.warning("[VAULT] decrypted payload not a dict — starting empty")
        raw = {}
    monoliths = {}
    for k, v in (raw.get('monoliths') or {}).items():
        if not isinstance(k, str) or k.startswith('_'):
            continue
        if isinstance(v, str):
            monoliths[k] = {'content': v, 'privacy_required': True}
        elif isinstance(v, dict) and isinstance(v.get('content'), str):
            monoliths[k] = {**v, 'privacy_required': True}
        else:
            logger.warning(f"[VAULT] skipping monolith '{k}' (bad shape)")
    components = {}
    for ctype, entries in (raw.get('components') or {}).items():
        if not isinstance(entries, dict):
            logger.warning(f"[VAULT] skipping component type '{ctype}' (bad shape)")
            continue
        clean = {k: v for k, v in entries.items()
                 if isinstance(k, str) and not k.startswith('_')
                 and isinstance(v, str)}
        if len(clean) != len(entries):
            logger.warning(f"[VAULT] dropped non-string pieces in '{ctype}'")
        if clean:
            components[ctype] = clean
    presets = {}
    for k, v in (raw.get('scenario_presets') or {}).items():
        if isinstance(k, str) and not k.startswith('_') and isinstance(v, dict) \
                and all(isinstance(x, (str, list, bool)) for x in v.values()):
            presets[k] = {**v, '_privacy_required': True}
        else:
            logger.warning(f"[VAULT] skipping preset '{k}' (bad shape)")
    # Vault-side piece trash (soft delete): rides the encrypted frame so
    # trashed private content never touches plaintext disk. Old vaults
    # simply lack the key — everything defaults it via .get/setdefault.
    trash = []
    for it in (raw.get('trash') or []):
        if isinstance(it, dict) and isinstance(it.get('type'), str) \
                and isinstance(it.get('key'), str) \
                and isinstance(it.get('text'), str):
            trash.append({'type': it['type'], 'key': it['key'],
                          'text': it['text'],
                          'deleted_at': it.get('deleted_at', 0)})
    out = {'monoliths': monoliths, 'components': components,
           'scenario_presets': presets, 'trash': trash}
    # Vaulted chats Phase 2: the chat DATA key rides inside the vault frame
    # (wrapped by the passphrase key — rekey re-wraps it for free, chat rows
    # never re-encrypt). Normalization must carry it or unlock drops it and
    # every encrypted chat becomes unreadable.
    ck = raw.get('chat_key')
    if ck is not None:
        ok_shape = isinstance(ck, str) and len(ck) == 64
        if ok_shape:
            try:
                bytes.fromhex(ck)
            except ValueError:
                ok_shape = False
        if not ok_shape:
            # Vault hunt G3b (2026-08-15): proceeding would let the next save
            # persist the vault WITHOUT the (possibly hand-recoverable) key
            # material — making every sealed chat permanently unreadable.
            # Refuse the unlock instead; the file stays on disk untouched.
            raise VaultCorrupt("malformed chat_key in vault payload")
        out['chat_key'] = ck
    return out


# ── status ──

def vault_exists() -> bool:
    return VAULT_PATH.exists()


def vault_unlocked() -> bool:
    with _lock:
        return _key is not None


def vault_status() -> dict:
    return {"exists": vault_exists(), "unlocked": vault_unlocked()}


_CHAT_ENC_PREFIX = "@enc1:"

# Vault hunt G3: history registers a callable returning True when any
# vaulted=1 chat exists — ground truth for the mint guard below. Unregistered
# (standalone vault tests, pre-boot) means no chat store is in play, so no
# sealed rows can exist and minting is safe.
_sealed_rows_probe = None


def set_sealed_rows_probe(fn):
    global _sealed_rows_probe
    _sealed_rows_probe = fn


def chat_data_key():
    """Vaulted chats Phase 2: the 32-byte chat DATA key. Random, generated
    once, stored INSIDE the vault frame — so it's wrapped by the passphrase-
    derived key, rekey re-wraps it with the frame (chat rows never
    re-encrypt), and lock drops it from memory with everything else.
    None while locked/absent. Lazily created and persisted on first use;
    an unpersistable key is never handed out (rows encrypted under a key
    that isn't saved would be lost at lock)."""
    with _lock:
        if _key is None or _data is None:
            return None
        ck = _data.get('chat_key')
        if not ck:
            # Vault hunt G3: NEVER mint over existing sealed rows. If this
            # vault file is an older restore (predating the first vaulted
            # chat) beside a current history DB, a fresh key would overwrite
            # the file and permanently orphan every '@enc1:' row — the old
            # key in the replaced file was the last recovery path. A probe
            # failure also refuses (can't verify → don't mint).
            probe = _sealed_rows_probe
            if probe is not None:
                try:
                    sealed_exist = bool(probe())
                except Exception as e:
                    logger.error(f"[VAULT] sealed-rows probe failed — "
                                 f"refusing chat-key mint (fail closed): {e}")
                    return None
                if sealed_exist:
                    logger.error(
                        "[VAULT] REFUSING to mint a chat data key: sealed "
                        "chat rows exist but this vault has no chat_key — "
                        "this looks like an older prompt_vault.enc beside a "
                        "newer chat DB. Restore the matching vault backup.")
                    return None
            ck = os.urandom(32).hex()
            _data['chat_key'] = ck
            if not _save_locked():
                _data.pop('chat_key', None)
                logger.error("[VAULT] chat data key could not be persisted")
                return None
            logger.info("[VAULT] chat data key created")
        return bytes.fromhex(ck)


def encrypt_chat_blob(data: bytes):
    """AES-256-GCM under the chat data key → '@enc1:' + base64(nonce|ct+tag)
    as str. None while locked (caller must treat as 'cannot encrypt' and
    REFUSE the write — never fall back to plaintext)."""
    key = chat_data_key()
    if key is None:
        return None
    import base64
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, data, None)
    return _CHAT_ENC_PREFIX + base64.b64encode(nonce + ct).decode('ascii')


def is_chat_encrypted(value) -> bool:
    if isinstance(value, bytes):
        return value.startswith(_CHAT_ENC_PREFIX.encode('ascii'))
    return isinstance(value, str) and value.startswith(_CHAT_ENC_PREFIX)


def decrypt_chat_blob(value):
    """Inverse of encrypt_chat_blob (str or bytes in) → plaintext bytes, or
    None while locked / on tamper (callers: locked = expected, tamper on an
    unlocked read = corrupt, be loud)."""
    key = chat_data_key()
    if key is None:
        return None
    try:
        import base64
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        s = value.decode('ascii') if isinstance(value, bytes) else value
        raw = base64.b64decode(s[len(_CHAT_ENC_PREFIX):])
        return AESGCM(key).decrypt(raw[:12], raw[12:], None)
    except Exception as e:
        logger.error(f"[VAULT] chat blob decrypt failed: {e}")
        return None


def vault_has_prompt(name) -> bool:
    """Is this name a vault monolith/preset right now (i.e. unlocked and
    present)? Cheap membership check — no copies. Turn-time touch wiring
    keys on this; locked always answers False."""
    if not isinstance(name, str) or not name:
        return False
    with _lock:
        return bool(_data) and (name in _data['monoliths']
                                or name in _data['scenario_presets'])


def vault_has_piece(ctype, key) -> bool:
    """Is this piece a vault entry right now (unlocked and present)?"""
    with _lock:
        return bool(_data) and key in _data['components'].get(ctype, {})


# ── lifecycle ──

def setup(passphrase) -> tuple:
    """Create an empty vault and unlock it. (ok, code) — code '' | 'exists'
    | 'bad_passphrase' | 'write_failed'."""
    global _key, _salt, _data
    if not isinstance(passphrase, str) or not passphrase:
        return False, 'bad_passphrase'
    if vault_exists():
        return False, 'exists'
    salt = os.urandom(16)
    key = _derive(passphrase, salt)   # slow (scrypt) — outside the lock
    with _lock:
        if vault_exists():
            return False, 'exists'
        _key, _salt = key, salt
        _data = _normalize({})
        if not _save_locked():
            _key = _salt = _data = None
            return False, 'write_failed'
        _touch_locked()
        _arm_timer_locked()
    logger.info("[VAULT] created and unlocked")
    _migrate_unvaulted_private_chats()   # pre-vault v1 private chats, if any
    _publish("vault_changed")
    return True, ''


def unlock(passphrase) -> tuple:
    """(ok, code) — code '' | 'no_vault' | 'wrong_key' | 'corrupt'.
    Corrupt files are quarantined (renamed .bad-<stamp>), never swallowed."""
    global _key, _salt, _data, _defer_timer
    if vault_unlocked():
        touch()
        return True, ''
    if not isinstance(passphrase, str) or not passphrase:
        return False, 'wrong_key'
    try:
        blob = VAULT_PATH.read_bytes()
    except OSError:
        return False, 'no_vault'
    try:
        plaintext, key, salt = decrypt_bytes(blob, passphrase)  # slow — outside lock
        raw = json.loads(plaintext.decode("utf-8"))
    except VaultWrongKey:
        logger.warning("[VAULT] unlock refused — wrong passphrase")
        return False, 'wrong_key'
    except (VaultCorrupt, json.JSONDecodeError, UnicodeDecodeError) as e:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        quarantine = VAULT_PATH.with_name(VAULT_PATH.name + f".bad-{stamp}")
        try:
            replace_with_retry(VAULT_PATH, quarantine)
            logger.error(f"[VAULT] corrupt vault quarantined to {quarantine.name}: {e}")
        except OSError as qe:
            logger.error(f"[VAULT] corrupt vault, quarantine also failed: {e} / {qe}")
        return False, 'corrupt'
    with _lock:
        _key, _salt = key, salt
        # A pending Ruling-B deferred lock must die here — firing after a
        # successful unlock would re-seal a vault the user just opened.
        if _defer_timer is not None:
            _defer_timer.cancel()
            _defer_timer = None
        try:
            _data = _normalize(raw)
        except VaultCorrupt as e:
            # G3b: malformed chat_key — refuse WITHOUT quarantining (the
            # rest of the vault is intact; renaming it would take the
            # prompts hostage too). Manual recovery stays possible.
            _key = _salt = None
            _data = None
            logger.error(f"[VAULT] unlock refused — {e}; vault file left "
                         "in place for manual recovery")
            return False, 'corrupt'
        _touch_locked()
        _arm_timer_locked()
        counts = {'monoliths': len(_data['monoliths']),
                  'components': sum(len(v) for v in _data['components'].values()),
                  'scenario_presets': len(_data['scenario_presets'])}
    logger.info(f"[VAULT] unlocked: {counts}")
    _warn_user_shadows()   # outside _lock — reads prompt_manager dicts
    _migrate_unvaulted_private_chats()   # Phase 2: encrypt stragglers
    _reeval_degraded_chats()   # F2 latch: decrypt-cause may have just healed
    refs_reconcile()   # regen piece-refs hashes — migrates pre-index vaults
    try:
        # Restore trashed pieces a vault prompt still references (a locked-
        # vault cleanup may have swept them on stale/absent piece-refs data).
        from core import prompt_crud
        restored = prompt_crud.reconcile_trash_with_vault()
        if restored:
            logger.info(f"[VAULT] unlock reconcile: restored {restored} "
                        f"trashed piece(s) still referenced by vault prompts")
    except Exception as e:
        logger.warning(f"[VAULT] trash reconcile failed: {e}")
    _fire_plugin_hook("vault_unlocked")
    _publish("vault_changed")
    return True, ''


def _fire_plugin_hook(name):
    """Plugin hook (core/hooks): 'the vault's visibility just changed —
    re-derive anything you cached from it' (hunt 2026-08-17: a sealed
    chat's story costume stayed registered in the process-global prompt
    pack until the next story action). Double-guarded — a plugin fault
    must never fail a lock/unlock."""
    try:
        from core.hooks import hook_runner, HookEvent
        if hook_runner.has_handlers(name):
            hook_runner.fire(name, HookEvent(metadata={}))
    except Exception as e:
        logger.warning(f"[VAULT] {name} hook dispatch failed: {e}")


def lock(reason="") -> bool:
    """Seal the vault: drop key + plaintext, cancel the idle timer, hand off
    the active preset if it was a vault name. Idempotent."""
    global _key, _salt, _data, _timer, _defer_timer
    # Ruling B (2026-08-17): a mid-stream seal can't flush the in-flight
    # turn (keyless saves raise) and the end-of-stream eviction would then
    # DISCARD it while the toast claimed it was safe — so a lock during a
    # PRIVATE chat's live stream WAITS for the reply to finish. Retries
    # itself every 5s; idempotent if the vault locked some other way
    # meanwhile. Non-private streams don't defer: their saves don't need
    # the key.
    try:
        from core.api_fastapi import get_system
        _sm = get_system().llm_chat.session_manager
        if getattr(_sm, '_is_streaming', False) and \
                bool((getattr(_sm, 'current_settings', None) or {}).get('private_chat')):
            with _lock:
                still_open = _key is not None
            if still_open:
                # Tracked so unlock()/stop() can CANCEL it — an untracked
                # timer would re-lock a vault the user just unlocked with
                # their passphrase (hunt 2026-08-17 D4).
                with _lock:
                    if _defer_timer is not None:
                        _defer_timer.cancel()
                    t = threading.Timer(5.0, lambda: lock(reason=reason or "deferred"))
                    t.daemon = True
                    _defer_timer = t
                    t.start()
                logger.info("[VAULT] lock deferred — private chat mid-stream; "
                            "retrying in 5s so the turn flushes first")
                return False
    except Exception:
        pass
    with _lock:
        if _key is None:
            return False
        gone = set(_data.get('monoliths', {})) | set(_data.get('scenario_presets', {}))
        # Best-effort key destruction: drop every reference (Python can't
        # zero immutable bytes in place).
        _key = _salt = _data = None
        if _timer is not None:
            _timer.cancel()
            _timer = None
        if _defer_timer is not None:
            _defer_timer.cancel()
            _defer_timer = None
    logger.info(f"[VAULT] locked{f' ({reason})' if reason else ''}")
    # Vault hunt R6: the replay ring outlives the seal — pre-lock
    # chat_switched / settings events carry names that just became secrets.
    # BEFORE the handoffs so what late subscribers replay is only post-seal
    # events (the eviction's switch to a public landing chat is safe).
    try:
        from core.event_bus import clear_replay as _clear_replay
        _clear_replay()
    except Exception:
        pass
    _handoff_active(gone)      # outside _lock — calls into the prompt system
    _handoff_active_chat()     # vaulted chats: evict a private active chat
    _fire_plugin_hook("vault_locked")
    _publish("vault_changed")
    return True


def rekey(current, new) -> tuple:
    """Change the vault passphrase. (ok, code) — code '' | 'bad_passphrase'
    | 'no_vault' | 'wrong_key' | 'corrupt' | 'save_failed'.

    Requires the CURRENT passphrase even while unlocked — an unlocked session
    is not proof of key knowledge (a walk-up at an open screen must not be
    able to rotate the owner out). Verification is against the on-disk blob,
    so it also proves the file itself. Works in either lock state and
    PRESERVES it: unlocked → re-encrypt the in-memory data (authoritative —
    may be ahead of a failed save) and stay unlocked; locked → re-frame the
    decrypted bytes untouched and stay locked. Fresh salt either way."""
    global _key, _salt
    if not isinstance(new, str) or not new:
        return False, 'bad_passphrase'
    if not isinstance(current, str) or not current:
        return False, 'wrong_key'
    try:
        blob = VAULT_PATH.read_bytes()
    except OSError:
        return False, 'no_vault'
    try:
        plaintext, _okey, _osalt = decrypt_bytes(blob, current)  # slow — outside lock
    except VaultWrongKey:
        logger.warning("[VAULT] rekey refused — wrong current passphrase")
        return False, 'wrong_key'
    except VaultCorrupt:
        return False, 'corrupt'
    new_salt = os.urandom(16)
    new_key = _derive(new, new_salt)   # slow — outside lock
    with _lock:
        if _key is not None:
            old = (_key, _salt)
            _key, _salt = new_key, new_salt
            if not _save_locked():
                _key, _salt = old   # disk still holds the old-key blob
                return False, 'save_failed'
            _touch_locked()
        else:
            # Locked: preserve the ciphertext's content byte-for-byte; the
            # decrypted plaintext never touches the prompt system or _data.
            try:
                out = encrypt_bytes(plaintext, new_key, new_salt)
                tmp = VAULT_PATH.with_suffix('.enc.tmp')
                with open(tmp, 'wb') as f:
                    f.write(out)
                    f.flush()
                    os.fsync(f.fileno())
                replace_with_retry(tmp, VAULT_PATH)
            except Exception as e:
                logger.error(f"[VAULT] rekey write failed: {e}")
                return False, 'save_failed'
    logger.info("[VAULT] passphrase changed (lock state preserved)")
    _audit_row({'item': 'vault', 'action': 'rekey'})
    return True, ''


def _handoff_active(gone_names):
    """Active preset was a vault name → hand off to 'default' loudly, exactly
    like prompt_packs.unregister_plugin. Runtime-only: stored chat settings
    untouched, self-heals on next unlock."""
    if not gone_names:
        return
    try:
        from core import prompt_state
        from core.prompt_manager import prompt_manager
        active = prompt_state.get_active_preset_name()
        if active in gone_names and active not in prompt_manager._monoliths \
                and active not in prompt_manager._scenario_presets:
            prompt_state.set_active_preset_name('default')
            logger.warning(f"[VAULT] Active prompt '{active}' sealed in locked "
                           f"vault — active preset reset to 'default'.")
            from core.prompt_crud import revalidate_active
            revalidate_active(reason="vault locked")
    except Exception as e:
        logger.warning(f"[VAULT] active-preset handoff failed: {e}")


def _migrate_unvaulted_private_chats():
    """Phase 2 transition: chats marked private BEFORE encryption shipped
    (or whose encrypt pass failed) sit at vaulted=0 — hidden by Phase 1's
    gates but plaintext on disk. Every unlock sweeps them into the vault
    while the key is present. Self-healing: a failed vault_chat leaves the
    flag alone and the next unlock retries."""
    try:
        from core.api_fastapi import get_system
        get_system().llm_chat.session_manager.vault_pending_private()
    except Exception as e:
        logger.warning(f"[VAULT] private-chat encrypt sweep skipped: {e}")


def _reeval_degraded_chats():
    """F2 read-only-degraded latch: an unlock may make decrypt-cause latches
    obsolete (the unreadable rows belonged to the vault that just opened).
    The store drops those latches and reloads the active chat if it was one.
    Parse-cause latches survive — a key heals nothing about corrupt JSON."""
    try:
        from core.api_fastapi import get_system
        get_system().llm_chat.session_manager.reeval_degraded_chats()
    except Exception as e:
        logger.warning(f"[VAULT] degraded-chat re-eval skipped: {e}")


def _handoff_active_chat():
    """Vaulted chats Phase 1: a private ACTIVE chat must not survive a lock —
    its settings ride /api/status and /api/history serves its content. Evict
    to a safe landing BEFORE vault_changed publishes, keeping the lock
    route's synchronous contract (when lock() returns, the world IS locked).
    The landing logic lives in the store (evict_private_active) because BOOT
    needs the identical pass — a restart comes up sealed without lock() ever
    running. Runtime switch only — the private chat's stored settings are
    untouched, exactly like the prompt handoff above."""
    try:
        from core.api_fastapi import get_system
        get_system().llm_chat.session_manager.evict_private_active()
    except Exception as e:
        logger.warning(f"[VAULT] active-chat handoff failed: {e}")


def _warn_user_shadows():
    """User entries shadow same-named vault entries (packs < vault < user).
    Logged ONCE at unlock, never per property read — the merge itself stays
    silent. Names go to the local log only (never the event bus)."""
    try:
        from core.prompt_manager import prompt_manager
        with _lock:
            if not _data:
                return
            names = {**{n: 'monolith' for n in _data['monoliths']},
                     **{n: 'preset' for n in _data['scenario_presets']}}
            pieces = [(t, k) for t, e in _data['components'].items() for k in e]
        for n, kind in names.items():
            if n in prompt_manager._monoliths or n in prompt_manager._scenario_presets:
                logger.warning(f"[VAULT] {kind} '{n}' is shadowed by a "
                               f"same-named user entry — user wins")
        for t, k in pieces:
            if k in prompt_manager._components.get(t, {}):
                logger.warning(f"[VAULT] piece '{t}/{k}' is shadowed by a "
                               f"same-named user piece — user wins")
    except Exception:
        pass  # advisory only — never let a warning block an unlock


def stop():
    """Teardown (sapphire.py stop list): cancel the timers. No events."""
    global _timer, _defer_timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None
        if _defer_timer is not None:
            _defer_timer.cancel()
            _defer_timer = None


# ── idle auto-lock ──

def _idle_seconds() -> float:
    try:
        import config
        minutes = float(getattr(config, 'VAULT_IDLE_MINUTES', 30) or 30)
    except Exception:
        minutes = 30.0
    return max(60.0, minutes * 60.0)


def touch():
    """Vault activity: content reads at assembly time, CRUD, unlock. Listing
    names does NOT count (call sites enforce that). Timestamp-only — the
    armed timer re-checks activity when it fires, so no cancel/create churn
    on the hot path."""
    global _last_activity
    _last_activity = time.monotonic()


def rearm() -> bool:
    """Re-arm the idle timer with the CURRENT timeout setting. Hot-apply for
    VAULT_IDLE_MINUTES changes: a GROWN timeout self-corrects at the next
    fire anyway, but a SHRUNK one would apply up to one old-timeout late
    without this. No-op while locked."""
    with _lock:
        if _key is None:
            return False
        _arm_timer_locked()
    return True


def _touch_locked():
    global _last_activity
    _last_activity = time.monotonic()


def _arm_timer_locked(delay=None):
    """Caller holds _lock. daemon=True: a live 30-minute timer must never
    hold process exit hostage."""
    global _timer
    if _timer is not None:
        _timer.cancel()
    t = threading.Timer(delay if delay is not None else _idle_seconds(), _on_idle)
    t.daemon = True
    t.start()
    _timer = t


def _on_idle():
    """Timer callback. Re-checks activity under the lock — cancel() can't stop
    an already-running callback, and activity may have landed since arming."""
    with _lock:
        if _key is None:
            return
        idle = time.monotonic() - _last_activity
        timeout = _idle_seconds()
        if idle < timeout - 0.05:
            _arm_timer_locked(timeout - idle)
            return
    lock(reason="idle timeout")


# ── overlays (the read surface step 2 merges) ──

def overlay_monoliths() -> dict:
    with _lock:
        return dict(_data['monoliths']) if _data else {}


def overlay_components() -> dict:
    with _lock:
        return {t: dict(e) for t, e in _data['components'].items()} if _data else {}


def overlay_presets() -> dict:
    with _lock:
        return dict(_data['scenario_presets']) if _data else {}


# ── persistence ──

def _save_locked() -> bool:
    """Caller holds _lock and the vault is unlocked. Atomic tmp + replace.
    Returns False on failure (memory then runs ahead of disk until the next
    successful save — callers surface that)."""
    try:
        plaintext = json.dumps(_data, ensure_ascii=False).encode("utf-8")
        blob = encrypt_bytes(plaintext, _key, _salt)
        VAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = VAULT_PATH.with_suffix('.enc.tmp')
        with open(tmp, 'wb') as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        replace_with_retry(tmp, VAULT_PATH)
        return True
    except Exception as e:
        logger.error(f"[VAULT] save failed: {e}")
        return False


def save() -> bool:
    """Persist the unlocked vault + reconcile the refs index."""
    with _lock:
        if _key is None:
            return False
        ok = _save_locked()
        if ok:
            _touch_locked()
            _reconcile_refs_locked()
    return ok


# ── item mutators (step-3 write routing lands on these) ──

def _audit_row(event):
    """Names-only ledger row for a vault mutation (recon finding 9). The
    audit diff machinery deliberately can't see the vault — it snapshots
    the plaintext dicts — so these rows are the vault's ONLY ledger trace.
    Names/keys only, NEVER content: mind.db is plaintext."""
    try:
        from core.audit import emit, actor
        emit({'kind': 'vault', 'actor': actor(), **event})
    except Exception:
        pass


def _mutate(fn, audit_event=None) -> tuple:
    """Run fn(_data) under the lock while unlocked, then save. fn returns an
    error code ('' = proceed). audit_event: names-only row emitted on
    success (outside the lock)."""
    with _lock:
        if _key is None:
            return False, 'locked'
        code = fn(_data)
        if code:
            return False, code
        if not _save_locked():
            return False, 'save_failed'
        _touch_locked()
        _reconcile_refs_locked()
    if audit_event:
        _audit_row(audit_event)
    return True, ''


def set_monolith(name, content) -> tuple:
    if not isinstance(name, str) or not name or name.startswith('_') \
            or name in _RESERVED_NAMES:
        return False, 'bad_name'
    if not isinstance(content, str):
        return False, 'bad_value'

    def fn(d):
        if name in d['scenario_presets']:
            return 'cross_type'   # same-store ambiguity — mirror the user-store rule
        d['monoliths'][name] = {'content': content, 'privacy_required': True}
        return ''
    return _mutate(fn, {'item': 'monolith', 'name': name, 'action': 'saved'})


def delete_monolith(name) -> tuple:
    def fn(d):
        if name not in d['monoliths']:
            return 'not_found'
        del d['monoliths'][name]
        return ''
    return _mutate(fn, {'item': 'monolith', 'name': name, 'action': 'deleted'})


def set_piece(ctype, key, value) -> tuple:
    if not all(isinstance(x, str) and x for x in (ctype, key)) or key.startswith('_'):
        return False, 'bad_name'
    if not isinstance(value, str):
        return False, 'bad_value'
    return _mutate(lambda d: d['components'].setdefault(ctype, {}).__setitem__(key, value) or '',
                   {'item': 'piece', 'comp_type': ctype, 'key': key, 'action': 'saved'})


def delete_piece(ctype, key) -> tuple:
    def fn(d):
        if key not in d['components'].get(ctype, {}):
            return 'not_found'
        del d['components'][ctype][key]
        if not d['components'][ctype]:
            del d['components'][ctype]
        return ''
    return _mutate(fn, {'item': 'piece', 'comp_type': ctype, 'key': key, 'action': 'deleted'})


def trash_piece(ctype, key) -> tuple:
    """Vault-side soft delete: move a vault piece into the vault's OWN trash
    (encrypted with everything else — content never lands on plaintext disk).
    Visible/restorable only while unlocked, by construction."""
    def fn(d):
        if key not in d['components'].get(ctype, {}):
            return 'not_found'
        d.setdefault('trash', []).append({
            'type': ctype, 'key': key,
            'text': d['components'][ctype].pop(key),
            'deleted_at': time.time()})
        if not d['components'][ctype]:
            del d['components'][ctype]
        return ''
    return _mutate(fn, {'item': 'piece', 'comp_type': ctype, 'key': key,
                        'action': 'trashed'})


def trash_list() -> list:
    """Vault trash entries — [] while locked (sealed content stays sealed)."""
    with _lock:
        if _key is None:
            return []
        return [dict(it) for it in _data.get('trash', [])]


def trash_restore(ctype, key) -> tuple:
    """Restore the newest vault-trash entry for type/key; all entries for the
    key drop. Never overwrites a live vault piece ('exists')."""
    def fn(d):
        entries = [it for it in d.get('trash', [])
                   if it['type'] == ctype and it['key'] == key]
        if not entries:
            return 'not_found'
        if key in d['components'].get(ctype, {}):
            return 'exists'
        latest = max(entries, key=lambda it: it.get('deleted_at', 0))
        d['components'].setdefault(ctype, {})[key] = latest.get('text', '')
        d['trash'] = [it for it in d.get('trash', [])
                      if not (it['type'] == ctype and it['key'] == key)]
        return ''
    return _mutate(fn, {'item': 'piece', 'comp_type': ctype, 'key': key,
                        'action': 'restored'})


def trash_purge() -> tuple:
    """Empty the vault trash. (ok, count) on success, (False, code) else."""
    with _lock:
        if _key is None:
            return False, 'locked'
        n = len(_data.get('trash', []))
        if not n:
            return True, 0
        _data['trash'] = []
        if not _save_locked():
            return False, 'save_failed'
        _touch_locked()
    logger.info(f"[VAULT] purged {n} trashed piece(s)")
    return True, n


def rewrite_piece_refs(ctype, old_key, new_key=None):
    """Repoint (new_key) or strip (None) every vault-preset reference to
    ctype/old_key. UNLOCKED only. Repointing onto a key the list already
    holds just drops the old one (no duplicates). Returns (changed_names,
    code) — code '' | 'locked' | 'save_failed'; no-op saves nothing."""
    with _lock:
        if _key is None:
            return [], 'locked'
        changed = []
        for name, comps in _data['scenario_presets'].items():
            if not isinstance(comps, dict):
                continue
            val = comps.get(ctype)
            if isinstance(val, list):
                if old_key in val:
                    if new_key and new_key not in val:
                        comps[ctype] = [new_key if k == old_key else k for k in val]
                    else:
                        comps[ctype] = [k for k in val if k != old_key]
                    changed.append(name)
            elif val == old_key:
                comps[ctype] = new_key or ''
                changed.append(name)
        if not changed:
            return [], ''
        if not _save_locked():
            return [], 'save_failed'
        _touch_locked()
        _reconcile_refs_locked()
    _publish("refs_rewritten")
    return changed, ''


def set_preset(name, preset) -> tuple:
    if not isinstance(name, str) or not name or name.startswith('_') \
            or name in _RESERVED_NAMES:
        return False, 'bad_name'
    if not isinstance(preset, dict) or not all(
            isinstance(v, (str, list, bool)) for v in preset.values()):
        return False, 'bad_value'

    def fn(d):
        if name in d['monoliths']:
            return 'cross_type'
        d['scenario_presets'][name] = {**preset, '_privacy_required': True}
        return ''
    return _mutate(fn, {'item': 'preset', 'name': name, 'action': 'saved'})


def delete_preset(name) -> tuple:
    def fn(d):
        if name not in d['scenario_presets']:
            return 'not_found'
        del d['scenario_presets'][name]
        return ''
    return _mutate(fn, {'item': 'preset', 'name': name, 'action': 'deleted'})


# ── references index (in-use names ONLY — ruling C amendment) ──

def _load_refs_locked():
    global _refs, _refs_loaded, _piece_salt, _piece_hashes
    if _refs_loaded:
        return
    _refs_loaded = True
    _refs = {}
    _piece_salt, _piece_hashes = None, set()
    if not REFS_PATH.exists():
        return
    try:
        data = json.loads(REFS_PATH.read_text(encoding='utf-8-sig'))
        names = data.get('names', {}) if isinstance(data, dict) else {}
        if isinstance(names, dict):
            _refs = {k: v for k, v in names.items()
                     if isinstance(k, str) and isinstance(v, str)}
        salt = data.get('piece_salt') if isinstance(data, dict) else None
        if isinstance(salt, str) and salt:
            _piece_salt = salt
            hashes = data.get('piece_refs', [])
            if isinstance(hashes, list):
                _piece_hashes = {h for h in hashes if isinstance(h, str)}
    except Exception as e:
        logger.error(f"[VAULT] refs index unreadable — starting empty: {e}")


def _save_refs_locked() -> bool:
    try:
        REFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        out = {"version": 2, "names": _refs}
        if _piece_salt is not None:
            out["piece_salt"] = _piece_salt
            out["piece_refs"] = sorted(_piece_hashes)
        tmp = REFS_PATH.with_suffix('.json.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        replace_with_retry(tmp, REFS_PATH)
        return True
    except Exception as e:
        logger.error(f"[VAULT] refs index save failed: {e}")
        return False


def refs_names() -> dict:
    """{name: kind} of vault names referenced by main-app entities. This is
    the ONLY vault-name surface that exists while locked."""
    with _lock:
        _load_refs_locked()
        return dict(_refs)


def refs_stamp(name) -> tuple:
    """Record that a main-app entity now references a vault name. Requires
    the vault UNLOCKED (locked names aren't offered anywhere, so a stamp
    while sealed is a bug, not a feature — the index never grows sealed)."""
    with _lock:
        if _key is None:
            return False, 'locked'
        if name in _data['monoliths']:
            kind = 'monolith'
        elif name in _data['scenario_presets']:
            kind = 'preset'
        else:
            return False, 'not_in_vault'
        _load_refs_locked()
        if _refs.get(name) == kind:
            return True, ''
        _refs[name] = kind
        return (True, '') if _save_refs_locked() else (False, 'save_failed')


def refs_drop(name) -> bool:
    """Remove a name from the index (last referrer gone). Works in ANY lock
    state — removal is the one sealed-state mutation allowed. Idempotent."""
    with _lock:
        _load_refs_locked()
        if name not in _refs:
            return True
        del _refs[name]
        return _save_refs_locked()


def refs_reconcile(referenced=None) -> bool:
    """While unlocked: prune index entries no longer in the vault; if the
    caller supplies the current externally-referenced name set, also prune
    names nothing references anymore. Runs on every vault save."""
    with _lock:
        if _key is None:
            return False
        return _reconcile_refs_locked(referenced)


def _reconcile_refs_locked(referenced=None) -> bool:
    global _piece_salt, _piece_hashes
    _load_refs_locked()
    valid = set(_data['monoliths']) | set(_data['scenario_presets'])
    keep = {k: v for k, v in _refs.items()
            if k in valid and (referenced is None or k in referenced)}
    # Piece-refs snapshot: salted hashes of the PLAINTEXT piece keys vault
    # presets reference. Regenerated on every save while unlocked, so it is
    # exact-by-construction while locked (a sealed vault can't gain refs).
    # This is the only surface that lets a locked-vault cleanup skip pieces
    # a hidden prompt still needs — nothing readable leaks, only hashes.
    if _piece_salt is None:
        _piece_salt = os.urandom(16).hex()
    new_hashes = {_hash_piece(_piece_salt, t, k) for t, k in _piece_pairs_locked()}
    if keep == _refs and new_hashes == _piece_hashes:
        return True
    _refs.clear()
    _refs.update(keep)
    _piece_hashes = new_hashes
    return _save_refs_locked()


def _hash_piece(salt, ctype, key) -> str:
    return hashlib.sha256(f"{salt}:{ctype}/{key}".encode("utf-8")).hexdigest()


def _piece_pairs_locked():
    """(ctype, key) refs from vault presets to pieces OUTSIDE the vault's own
    store. Caller holds _lock, vault unlocked. A key present in both stores
    counts as outside: deleting the plaintext shadow reveals the vault copy,
    but the plaintext copy is what currently resolves."""
    pairs = set()
    vault_pieces = _data.get('components', {})
    for comps in _data.get('scenario_presets', {}).values():
        if not isinstance(comps, dict):
            continue
        for ctype, val in comps.items():
            if ctype.startswith('_'):
                continue
            keys = val if isinstance(val, list) else ([val] if val else [])
            for k in keys:
                if isinstance(k, str) and k and k not in vault_pieces.get(ctype, {}):
                    pairs.add((ctype, k))
    return pairs


def piece_ref_pairs():
    """Actual (ctype, key) plaintext-piece refs from vault presets — UNLOCKED
    only (returns None while locked). Trash reconcile uses this on unlock."""
    with _lock:
        if _key is None:
            return None
        return sorted(_piece_pairs_locked())


def piece_refs_available() -> bool:
    """Sidecar carries piece-refs data. False = vault predates the index and
    hasn't been unlocked since — locked cleanups must fall back to warning."""
    with _lock:
        _load_refs_locked()
        return _piece_salt is not None


def piece_vault_referenced(ctype, key):
    """Tri-state, works while LOCKED (consults only salted hashes):
    True/False = sidecar answer; None = no data yet (needs one unlock)."""
    with _lock:
        _load_refs_locked()
        if _piece_salt is None:
            return None
        return _hash_piece(_piece_salt, ctype, key) in _piece_hashes


# ── events ──

def _publish(action):
    """Name-free payload on purpose: SSE replays recent events to every new
    tab — vault names must never ride the bus."""
    try:
        from core.event_bus import publish, Events
        publish(Events.PROMPT_CHANGED, {"name": "", "action": action})
    except Exception:
        pass  # Event bus may not be up during early boot / tests
