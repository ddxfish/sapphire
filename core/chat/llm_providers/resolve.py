# core/chat/llm_providers/resolve.py
"""ONE provider resolver: per-chat pin → running provider (2026-09-21).

Before this file the same decision lived in six copies (web chat, continuity,
Discord side lanes, Discord vision, game room, compress) plus a display twin,
each drifting on its own day: the 60s health cache reached one of four auto
paths, 'none' meant five different things, the is_local rule had eight copies
(one a hardcoded name list) and the game room resolved through the operator's
open web chat. Record: tmp/provider-resolver-20260921.md.

Contract
  resolve(primary, model, *, private, prompt_name, timeout, health,
          require_images) -> Selection(key, provider, model)
  'none'        → LLMDisabled. Loud on every lane; a lane that can't show a
                  user the message logs it by name instead of going quiet.
  prompt gate   → PrivacyRefused when the named prompt (or, with
                  prompt_name=GLOBAL, the globally active prompt) is
                  privacy_required and the turn is not private. A check that
                  itself explodes BLOCKS. prompt_name=None = no gate: the
                  caller settled privacy (continuity) or sends no persona
                  prompt (vision, compress, distill).
  pinned        → a private turn refuses a provider not marked local (the
                  ONE is_local rule below); an unavailable or unhealthy pin
                  RAISES — never falls back to auto; timeout applies.
  auto          → fallback order, force_privacy=private, the module-level
                  health cache, timeout applies to auto too (Krem's ruling
                  2026-09-21 — the per-chat timeout used to reach the pinned
                  path only), and the override is dropped: auto never asks a
                  provider it picked for a model pinned for another.
  conversation  → the caller's chat/task name, stamped on the built provider
                  (provider.conversation) so session-affinity placeholders
                  ({session} in extra_headers / extra_body) hash to a stable
                  per-conversation id. Never sent raw. None = fallback id.
  never reads the active chat — the caller hands in the pin it read from its
  own settings, so a background lane cannot ride the browser tab's brain.

Every exception is a ConnectionError subclass: the engines' existing
`except ConnectionError` handlers keep working unchanged. Registry lookups
go through the module wrappers by attribute at call time so tests that patch
core.chat.llm_providers.get_provider_by_key / get_first_available_provider or
the registry instance's methods keep intercepting.
"""
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

GLOBAL = object()      # prompt_name sentinel: "chat has no prompt key → the globally active prompt decides"
HEALTH_TTL = 60.0
_HEALTH_CACHE: Dict[str, float] = {}   # {provider_key: trusted_until_epoch}; fails are never cached


class ProviderRefused(ConnectionError):
    """The turn cannot run. The message is the user-facing reason."""


class LLMDisabled(ProviderRefused):
    """llm_primary = 'none'."""


class PrivacyRefused(ProviderRefused):
    """Private prompt on a public turn, or private turn on a non-local provider."""


class ProviderUnavailable(ProviderRefused):
    """The pinned provider is unknown, disabled, unbuildable, blind or unhealthy."""


class NoProvidersAvailable(ProviderRefused):
    """Auto mode found nothing in the fallback order."""


def _lp():
    from core.chat import llm_providers
    return llm_providers


def providers_config() -> Dict[str, Dict[str, Any]]:
    """Core + custom provider map, custom keys winning. The one merge."""
    import config
    return {**(getattr(config, 'LLM_PROVIDERS', None) or {}),
            **(getattr(config, 'LLM_CUSTOM_PROVIDERS', None) or {})}


def is_local(key: str, conf: Optional[dict] = None) -> bool:
    """THE is_local rule: the provider's own config flag wins, core metadata
    fills in, unknown = cloud. Raises if metadata itself explodes so a privacy
    path can fail closed instead of guessing."""
    if conf is None:
        conf = providers_config().get(key) or {}
    meta = _lp().PROVIDER_METADATA.get(key, {})
    return bool(conf.get('is_local', meta.get('is_local', False)))


def continues_in_place(key: str, conf: Optional[dict] = None) -> bool:
    """Can this provider resume a reply from a trailing assistant message
    (Continue)? The provider's own `continues_in_place` wins. Else Anthropic-
    style APIs (native prefill; a model that refuses it 400s into llm_errors
    words) and local OpenAI-compat servers (llama.cpp, Ollama continue the
    final assistant turn) do. Hosted OpenAI-style APIs answer fresh or empty,
    and a fresh answer spliced on as a continuation corrupts the row, so
    they don't (Krem 2026-10-26: Continue fails with words, no fallback)."""
    if conf is None:
        conf = providers_config().get(key) or {}
    if 'continues_in_place' in conf:
        return bool(conf['continues_in_place'])
    tpl = conf.get('template') or conf.get('provider') or key
    if tpl in ('claude', 'anthropic'):
        return True
    try:
        return tpl == 'openai' and is_local(key, conf)
    except Exception:
        return False


def display_name(key: str, conf: Optional[dict] = None) -> str:
    """Friendly name for a provider key ('auto'/'none' pass through)."""
    if key in ('auto', 'none', ''):
        return key or 'auto'
    if conf is None:
        conf = providers_config().get(key) or {}
    try:
        meta = _lp().PROVIDER_METADATA.get(key, {})
    except Exception:
        meta = {}
    return str(conf.get('display_name') or meta.get('display_name') or key)


# ── User words ──────────────────────────────────────────────────────────────
# Errors here are shown in chat AND spoken in voice: say what happened, where to
# click (the UI's own labels), fix first and detail last, never a class name.
# The trail strings stay as they are for the log; these read them.

def health_words(err, timeout=None) -> str:
    """A failed health probe as a clause: '{name} <this>'."""
    e = str(err or '').lower()
    if any(k in e for k in ('http 401', 'http 403', 'authentication', 'permissiondenied')):
        return "refused its API key (check the key in Settings › LLM)"
    if 'http 402' in e:
        return "says the account is out of credits"
    if 'timeout' in e:
        t = f" in {timeout} s" if timeout else ""
        return (f"didn't answer{t} (if it's just slow, raise its Health timeout in Settings › LLM)")
    if 'connect' in e:
        return "couldn't be reached (is it running?)"
    return "didn't answer"


def _sick_words(why) -> str:
    """The trail's 'not answering (APITimeoutError, 0.3s)' as user words."""
    m = re.match(r"not answering \((.*), ([\d.]+)s\)", why)
    return health_words(m.group(1), m.group(2)) if m else health_words(why)


def auto_words(trail, private=False) -> str:
    """Auto found nothing: lead with the one fix that matters most."""
    def named(test):
        return [display_name(k) for k, w in trail if test(w)]
    left_out = named(lambda w: w == 'left out of Auto')
    sick = [(k, w) for k, w in trail if w.startswith('not answering')]
    if private and named(lambda w: w == 'not local (private chat)'):
        return ("This chat is private, so Auto only uses providers marked \"Local / private server\". "
                "Tick that on a local provider in Settings › LLM, or turn off private chat.")
    if left_out:
        return (f"Auto skips {', '.join(left_out)} because \"Include in Auto fallback\" is unticked. "
                f"Tick it in Settings › LLM › {left_out[0]}, or pick it as this chat's provider.")
    if sick:
        return "No LLM that's turned on answered: " + '; '.join(
            f"{display_name(k)} {_sick_words(w)}" for k, w in sick) + "."
    if all(w in ('off', 'not configured') for _, w in trail):
        return ("No LLM is turned on, so Auto has nothing to use. Open Settings › LLM and flip "
                "the switch on the right of a provider's row.")
    return ("Auto couldn't find an LLM to use. Check your providers in Settings › LLM ("
            + ' · '.join(f"{display_name(k)}: {w}" for k, w in trail if w != 'off') + ").")


def provider_sees(provider) -> bool:
    """supports_images is a @property on BaseProvider; a plain-method impl
    is tolerated. Anything odd = blind (the old TypeError-swallow class)."""
    try:
        seen = provider.supports_images
        if callable(seen):
            seen = seen()
        return bool(seen)
    except Exception:
        return False


@dataclass
class Selection:
    key: str
    provider: Any
    model: str = ''          # per-chat override; '' = the provider's own default

    @property
    def effective_model(self) -> str:
        return self.model or str(getattr(self.provider, 'model', '') or '')

    @property
    def display_name(self) -> str:
        return display_name(self.key)

    def as_tuple(self):
        return (self.key, self.provider, self.model)


# ─── The pair rule (2026-10-07) ──────────────────────────────────────────────
# A provider key and its model override are ONE value. The auto path below
# drops any model (an auto pick never asks the provider it chose for a model
# pinned for another), so a model stored beside 'auto' is dead on arrival —
# and every picker that reads it back shows a brain that will never run.
# Seven stores could write that shape (chat pin, persona, task, resident,
# agent spawn, Twilio rule) and the persona merge mixed one persona's model
# onto another provider. Every writer routes through here now: normalize at
# WRITE time, so storage never lies to the pickers.

def normalize_pair(provider, model) -> tuple:
    """(provider, model) → the coherent pair. Blank key = 'auto'; 'auto' and
    'none' carry no model; any other key keeps its model (stripped). Pure."""
    p = str(provider or 'auto').strip() or 'auto'
    m = str(model or '').strip()
    return (p, '') if p in ('auto', 'none') else (p, m)


def normalize_pair_patch(current, patch, key='llm_primary', model_key='llm_model') -> dict:
    """The pair rule over a PATCH onto a stored pair. Returns a NEW dict
    (`patch` is never mutated):
      - key in patch with a model: the pair rides, normalized.
      - key in patch, no model: a key CHANGE drops the stored model; an
        unchanged key keeps it — except auto/none, which clear a stale one.
      - model in patch, no key: the STORED key decides — auto/none drop it.
      - neither: untouched."""
    cur = current or {}
    out = dict(patch)
    old_key = normalize_pair(cur.get(key), '')[0]
    if key in out:
        new_key = str(out.get(key) or 'auto').strip() or 'auto'
        if model_key in out:
            out[model_key] = normalize_pair(new_key, out.get(model_key))[1]
        elif new_key != old_key or (new_key in ('auto', 'none') and cur.get(model_key)):
            out[model_key] = ''
    elif model_key in out:
        out[model_key] = normalize_pair(old_key, out.get(model_key))[1]
    return out


def _prompt_gate(prompt_name, private: bool) -> None:
    try:
        from core import prompts as _prompts
        if prompt_name is GLOBAL:
            required = bool(_prompts.is_current_prompt_private())
        else:
            # Unresolvable name → the runtime falls back to the assembled
            # default (never privacy_required), so the gate follows suit.
            data = _prompts.get_prompt(prompt_name)
            required = bool(isinstance(data, dict) and data.get('privacy_required', False))
        if required and not private:
            raise PrivacyRefused(
                "This prompt is marked private — unlock the vault and "
                "send a message (talking marks the chat private), or "
                "use Chat Manager's \U0001F5DD on it first.")
    except ProviderRefused:
        raise
    except Exception as e:
        logger.error(f"Prompt-privacy check failed (defaulting to BLOCK): {e}")
        raise PrivacyRefused("Couldn't check whether this chat's prompt is private, so the reply was "
                             "blocked to be safe. Try again; if it keeps happening, re-save the prompt.")


def resolve(primary, model: str = '', *, private: bool = False, prompt_name=None,
            timeout=None, health: str = 'cached', require_images: bool = False,
            fallback_order=None, cfg: Optional[Dict[str, Dict[str, Any]]] = None,
            conversation: Optional[str] = None) -> Selection:
    """See the module docstring. health: 'cached' (60s TTL, the default),
    'probe' (always ask), 'skip' (never ask — per-image lanes, dry runs)."""
    import config
    primary = str(primary or 'auto').strip() or 'auto'
    model = str(model or '').strip()
    if cfg is None:
        cfg = providers_config()
    try:
        _t = float(timeout or 0)
    except (TypeError, ValueError):
        _t = 0.0
    timeout = _t if _t > 0 else float(getattr(config, 'LLM_REQUEST_TIMEOUT', 240.0) or 240.0)

    if prompt_name is not None:
        _prompt_gate(prompt_name, private)

    if primary == 'none':
        raise LLMDisabled("This chat's provider is set to None, so there's no LLM to answer. "
                          "Pick Auto or a provider in the chat sidebar.")

    if primary != 'auto':
        return _pinned(primary, model, cfg, private, timeout, health, require_images, conversation)
    return _auto(cfg, fallback_order, private, timeout, health, require_images, conversation)


def _pinned(key, model, cfg, private, timeout, health, require_images, conversation=None) -> Selection:
    conf = cfg.get(key) or {}
    name = display_name(key, conf)
    if private:
        try:
            local = is_local(key, conf)
        except Exception as e:
            logger.error(f"Privacy check failed (defaulting to BLOCK): {e}")
            raise PrivacyRefused(f"Couldn't confirm {name} is local, so it's blocked in this private chat "
                                 f"to be safe. Pick a provider marked \"Local / private server\" in Settings › LLM.")
        if not local:
            raise PrivacyRefused(
                f"{name} isn't marked \"Local / private server\", so it's blocked in this private chat. "
                f"Tick that box in Settings › LLM › {name}, or turn off private chat.")
    provider = _lp().get_provider_by_key(key, cfg, timeout, model_override=model)
    if not provider:
        if key not in cfg:
            raise ProviderUnavailable(
                f"This chat uses '{name}', which isn't set up anymore. "
                f"Pick another provider in the chat sidebar.")
        if not conf.get('enabled', False):
            raise ProviderUnavailable(
                f"This chat uses {name}, which is turned off. Turn it on in Settings › LLM "
                f"(the switch on the right of its row), or pick another provider in the chat sidebar.")
        raise ProviderUnavailable(
            f"{name} couldn't be started. Check its API key and URL in Settings › LLM › {name}.")
    provider.conversation = conversation or None
    if require_images and not provider_sees(provider):
        raise ProviderUnavailable(f"{name} can't see images. Pick a vision model for this chat, "
                                  f"or tick \"Vision model\" in Settings › LLM › {name} if it can.")
    if health != 'skip':
        # A pinned provider has no fallback — it just raises — so the probe
        # is pure added latency on every turn; a pass is trusted for
        # HEALTH_TTL and the completion call itself is the health signal
        # between checks (2026-07-15). Fails are never cached.
        now = time.time()
        healthy = health == 'cached' and now < _HEALTH_CACHE.get(key, 0)
        if not healthy:
            try:
                healthy = bool(provider.health_check())
            except Exception:
                healthy = False
            if healthy:
                _HEALTH_CACHE[key] = now + HEALTH_TTL
        if not healthy:
            why = getattr(provider, 'last_health_error', '') or 'no answer'
            logger.warning(f"Pinned '{key}' for '{conversation or '?'}' not answering ({why}, {getattr(provider, 'health_check_timeout', '?')}s)")
            raise ProviderUnavailable(
                f"{name} {health_words(why, getattr(provider, 'health_check_timeout', None))}. "
                f"This chat is set to {name} only, so nothing else was tried. Pick Auto in the "
                f"chat sidebar to fall back to your other providers.")
    ensure = getattr(provider, 'ensure_model', None)
    if ensure and not ensure(fetch=health != 'skip'):
        logger.warning(f"Pinned '{key}' has no model set and its server named none: sending without one")
    logger.info(f"Using chat-specific provider '{key}' ({getattr(provider, 'model', '') or 'no model'}) for '{conversation or '?'}'")
    return Selection(key, provider, model)


def _auto(cfg, order, private, timeout, health, require_images, conversation=None) -> Selection:
    import config
    if order is None:
        order = getattr(config, 'LLM_FALLBACK_ORDER', None) or list(cfg.keys())
        # A provider missing from the saved order (a deleted default the
        # settings merge brought back, an order saved before it existed) is
        # still tried, last: Auto is the mode meant to work most.
        order = list(order) + [k for k in cfg if k not in order]
    trail = []
    result = _lp().get_first_available_provider(
        cfg, order, timeout,
        force_privacy=bool(private),
        health_cache=_HEALTH_CACHE if health == 'cached' else None,
        probe=(health != 'skip'),
        require_images=bool(require_images),
        trail=trail)
    why = ' · '.join(f"{display_name(k)}: {w}" for k, w in trail)
    if not result:
        logger.warning(f"Auto found no LLM for '{conversation or '?'}' (private={bool(private)}): {why or 'nothing configured'}")
        raise NoProvidersAvailable(auto_words(trail, private))
    key, provider = result
    provider.conversation = conversation or None
    logger.info(f"Auto mode: using '{key}' ({getattr(provider, 'model', '') or 'no model'}) for '{conversation or '?'}'"
                + (f", passed over: {why}" if why else ""))
    return Selection(key, provider, '')


def clear_health_cache(key: Optional[str] = None) -> None:
    """Forget a trusted verdict (all of them with no key). Provider edits and
    tests call this; the registry's settings watcher may too."""
    if key is None:
        _HEALTH_CACHE.clear()
    else:
        _HEALTH_CACHE.pop(key, None)


__all__ = ['resolve', 'Selection', 'GLOBAL', 'providers_config', 'is_local', 'display_name',
           'provider_sees', 'clear_health_cache', 'health_words', 'auto_words', 'normalize_pair', 'normalize_pair_patch',
           'ProviderRefused', 'LLMDisabled',
           'PrivacyRefused', 'ProviderUnavailable', 'NoProvidersAvailable']
