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
        raise PrivacyRefused("Prompt-privacy check encountered an error — blocking for safety. Check logs.")


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
        raise LLMDisabled("LLM disabled for this chat (llm_primary=none)")

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
            raise PrivacyRefused("Privacy check encountered an error — blocking provider for safety. Check logs.")
        if not local:
            raise PrivacyRefused(
                f"Provider '{name}' is not marked local/private-safe and is blocked in this "
                f"private chat. Tick 'Local / private server' on the model or turn off private chat.")
    provider = _lp().get_provider_by_key(key, cfg, timeout, model_override=model)
    if not provider:
        raise ProviderUnavailable(f"Provider '{name}' not configured or disabled")
    provider.conversation = conversation or None
    if require_images and not provider_sees(provider):
        raise ProviderUnavailable(f"Provider '{name}' does not support images")
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
            raise ProviderUnavailable(
                f"Provider '{name}' failed health check - no fallback for specific provider selection")
    logger.info(f"Using chat-specific provider '{key}'" + (f" with model '{model}'" if model else ""))
    return Selection(key, provider, model)


def _auto(cfg, order, private, timeout, health, require_images, conversation=None) -> Selection:
    import config
    if order is None:
        order = getattr(config, 'LLM_FALLBACK_ORDER', None) or list(cfg.keys())
    result = _lp().get_first_available_provider(
        cfg, order, timeout,
        force_privacy=bool(private),
        health_cache=_HEALTH_CACHE if health == 'cached' else None,
        probe=(health != 'skip'),
        require_images=bool(require_images))
    if not result:
        raise NoProvidersAvailable(
            "No LLM providers available"
            + (" — this turn is private, only providers marked local qualify" if private else ""))
    key, provider = result
    provider.conversation = conversation or None
    logger.info(f"Auto mode: using '{key}' ({getattr(provider, 'model', '')})")
    return Selection(key, provider, '')


def clear_health_cache(key: Optional[str] = None) -> None:
    """Forget a trusted verdict (all of them with no key). Provider edits and
    tests call this; the registry's settings watcher may too."""
    if key is None:
        _HEALTH_CACHE.clear()
    else:
        _HEALTH_CACHE.pop(key, None)


__all__ = ['resolve', 'Selection', 'GLOBAL', 'providers_config', 'is_local', 'display_name',
           'provider_sees', 'clear_health_cache', 'ProviderRefused', 'LLMDisabled',
           'PrivacyRefused', 'ProviderUnavailable', 'NoProvidersAvailable']
