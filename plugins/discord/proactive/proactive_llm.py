"""Shared LLM call path for proactive Discord messages."""

from __future__ import annotations

import logging
import re

from plugins.discord.conversation.think_tags import strip_think_tags
from plugins.discord.proactive.bot_identity import bot_identity_fields, strip_self_address
from plugins.discord.sapphire.llm_settings import resolve_discord_llm_provider

logger = logging.getLogger(__name__)

PROACTIVE_SYSTEM_MESSAGE = (
    'You write short Discord messages for a bot account. '
    'Output only the message text to post — no quotes, labels, or explanation. '
    'Never reason aloud or use thinking tags.'
)

_META_FRAGMENTS = (
    'write only',
    'discord message',
    'good morning',
    'good night',
    'good-night',
    'good-morning',
    'the channel',
    'recent chat',
    'you are posting',
    'never greet',
)


def proactive_llm_gen_params(gen_params: dict, *, max_tokens: int) -> dict:
    """Disable model thinking so token budget goes to the posted message."""
    params = dict(gen_params)
    params['max_tokens'] = max_tokens
    params['disable_thinking'] = True
    extra_body = dict(params.get('extra_body') or {})
    chat_template_kwargs = dict(extra_body.get('chat_template_kwargs') or {})
    chat_template_kwargs['enable_thinking'] = False
    extra_body['chat_template_kwargs'] = chat_template_kwargs
    if 'minimax' in str(params.get('model') or '').lower():
        extra_body.setdefault('thinking', {'type': 'disabled'})
    params['extra_body'] = extra_body
    return params


def _looks_like_proactive_meta(text: str) -> bool:
    lower = (text or '').lower()
    return any(fragment in lower for fragment in _META_FRAGMENTS)


def salvage_proactive_from_thinking(raw: str) -> str:
    """Best-effort extraction when the model only returned reasoning text."""
    if not raw or not raw.strip():
        return ''

    for match in re.finditer(r'"([^"]{10,500})"', raw):
        candidate = match.group(1).strip()
        if candidate and not _looks_like_proactive_meta(candidate):
            return candidate

    for match in re.finditer(r"'([^']{10,500})'", raw):
        candidate = match.group(1).strip()
        if candidate and not _looks_like_proactive_meta(candidate):
            return candidate

    body = re.sub(r'(?is)<(?:redacted_thinking|thinking)>\s*', '', raw, count=1)
    body = re.sub(r'(?is)</(?:redacted_thinking|thinking)>\s*', '', body).strip()
    stripped = strip_think_tags(body)
    if stripped and not _looks_like_proactive_meta(stripped):
        return stripped

    for part in re.split(r'[\n]+', body):
        candidate = part.strip().strip('"').strip("'")
        if not candidate or _looks_like_proactive_meta(candidate):
            continue
        if 10 <= len(candidate) <= 500:
            return candidate
    return ''


def normalize_proactive_output(raw: str, fields: dict) -> str:
    text = strip_think_tags(raw or '')
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    if len(text) > 2000:
        text = text[:1997].rstrip() + '…'
    return strip_self_address(text, fields)


def run_proactive_llm(
    system,
    *,
    prompt: str,
    account: str = '',
    provider_key: str = '',
    model_name: str = '',
    max_tokens: int = 180,
    log_label: str = 'Proactive',
    transport=None,
    account_repository=None,
) -> str:
    """Call the LLM for a short proactive post; empty string on failure."""
    if not system or not getattr(system, 'llm_chat', None):
        logger.warning('[discord] %s LLM: no system.llm_chat available', log_label)
        return ''

    fields = bot_identity_fields(account, transport=transport, account_repository=account_repository)
    try:
        provider_key, provider, base_gen = resolve_discord_llm_provider(
            system,
            provider_key,
            model_name,
        )
        if not provider:
            logger.warning('[discord] %s model %s/%s unavailable', log_label, provider_key, model_name)
            return ''

        messages = [
            {'role': 'system', 'content': PROACTIVE_SYSTEM_MESSAGE},
            {'role': 'user', 'content': prompt},
        ]
        base_max = max(40, min(500, int(max_tokens)))
        token_budgets = (base_max, max(base_max, 512))
        last_raw = ''

        for budget in dict.fromkeys(token_budgets):
            gen_params = proactive_llm_gen_params(base_gen, max_tokens=budget)
            response = provider.chat_completion(messages, tools=None, generation_params=gen_params)
            raw = str(getattr(response, 'content', '') or '')
            last_raw = raw or last_raw
            text = normalize_proactive_output(raw, fields)
            if text:
                return text
            salvaged = salvage_proactive_from_thinking(raw)
            if salvaged:
                salvaged_text = normalize_proactive_output(salvaged, fields)
                if salvaged_text:
                    return salvaged_text

        if last_raw and last_raw.strip():
            preview = last_raw.strip().replace('\n', ' ')[:120]
            logger.warning(
                '[discord] %s LLM unusable output for %s (%s chars): %r',
                log_label,
                account or 'bot',
                len(last_raw),
                preview,
            )
        return ''
    except Exception as exc:
        logger.warning('[discord] %s LLM failed: %s', log_label, exc)
        return ''
