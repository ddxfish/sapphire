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
_last_activity = 0.0   # time.monotonic() of last vault activity
_refs = {}             # {name: "monolith"|"preset"} — referenced names only
_refs_loaded = False


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
    return {'monoliths': monoliths, 'components': components,
            'scenario_presets': presets}


# ── status ──

def vault_exists() -> bool:
    return VAULT_PATH.exists()


def vault_unlocked() -> bool:
    with _lock:
        return _key is not None


def vault_status() -> dict:
    return {"exists": vault_exists(), "unlocked": vault_unlocked()}


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
    _publish("vault_changed")
    return True, ''


def unlock(passphrase) -> tuple:
    """(ok, code) — code '' | 'no_vault' | 'wrong_key' | 'corrupt'.
    Corrupt files are quarantined (renamed .bad-<stamp>), never swallowed."""
    global _key, _salt, _data
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
        _data = _normalize(raw)
        _touch_locked()
        _arm_timer_locked()
        counts = {'monoliths': len(_data['monoliths']),
                  'components': sum(len(v) for v in _data['components'].values()),
                  'scenario_presets': len(_data['scenario_presets'])}
    logger.info(f"[VAULT] unlocked: {counts}")
    _warn_user_shadows()   # outside _lock — reads prompt_manager dicts
    _publish("vault_changed")
    return True, ''


def lock(reason="") -> bool:
    """Seal the vault: drop key + plaintext, cancel the idle timer, hand off
    the active preset if it was a vault name. Idempotent."""
    global _key, _salt, _data, _timer
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
    logger.info(f"[VAULT] locked{f' ({reason})' if reason else ''}")
    _handoff_active(gone)      # outside _lock — calls into the prompt system
    _publish("vault_changed")
    return True


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
    """Teardown (sapphire.py stop list): cancel the idle timer. No events."""
    global _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None


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

def _mutate(fn) -> tuple:
    """Run fn(_data) under the lock while unlocked, then save. fn returns an
    error code ('' = proceed)."""
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
    return _mutate(fn)


def delete_monolith(name) -> tuple:
    def fn(d):
        if name not in d['monoliths']:
            return 'not_found'
        del d['monoliths'][name]
        return ''
    return _mutate(fn)


def set_piece(ctype, key, value) -> tuple:
    if not all(isinstance(x, str) and x for x in (ctype, key)) or key.startswith('_'):
        return False, 'bad_name'
    if not isinstance(value, str):
        return False, 'bad_value'
    return _mutate(lambda d: d['components'].setdefault(ctype, {}).__setitem__(key, value) or '')


def delete_piece(ctype, key) -> tuple:
    def fn(d):
        if key not in d['components'].get(ctype, {}):
            return 'not_found'
        del d['components'][ctype][key]
        if not d['components'][ctype]:
            del d['components'][ctype]
        return ''
    return _mutate(fn)


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
    return _mutate(fn)


def delete_preset(name) -> tuple:
    def fn(d):
        if name not in d['scenario_presets']:
            return 'not_found'
        del d['scenario_presets'][name]
        return ''
    return _mutate(fn)


# ── references index (in-use names ONLY — ruling C amendment) ──

def _load_refs_locked():
    global _refs, _refs_loaded
    if _refs_loaded:
        return
    _refs_loaded = True
    _refs = {}
    if not REFS_PATH.exists():
        return
    try:
        data = json.loads(REFS_PATH.read_text(encoding='utf-8-sig'))
        names = data.get('names', {}) if isinstance(data, dict) else {}
        if isinstance(names, dict):
            _refs = {k: v for k, v in names.items()
                     if isinstance(k, str) and isinstance(v, str)}
    except Exception as e:
        logger.error(f"[VAULT] refs index unreadable — starting empty: {e}")


def _save_refs_locked() -> bool:
    try:
        REFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = REFS_PATH.with_suffix('.json.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({"version": 1, "names": _refs}, f, indent=2,
                      ensure_ascii=False)
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
    _load_refs_locked()
    valid = set(_data['monoliths']) | set(_data['scenario_presets'])
    keep = {k: v for k, v in _refs.items()
            if k in valid and (referenced is None or k in referenced)}
    if keep == _refs:
        return True
    _refs.clear()
    _refs.update(keep)
    return _save_refs_locked()


# ── events ──

def _publish(action):
    """Name-free payload on purpose: SSE replays recent events to every new
    tab — vault names must never ride the bus."""
    try:
        from core.event_bus import publish, Events
        publish(Events.PROMPT_CHANGED, {"name": "", "action": action})
    except Exception:
        pass  # Event bus may not be up during early boot / tests
