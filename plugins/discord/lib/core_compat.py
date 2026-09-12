"""Compatibility shims for Sapphire core APIs used by the Discord cognitive daemon."""

from __future__ import annotations

import inspect
import logging

logger = logging.getLogger(__name__)


def ensure_execution_context_images_support() -> None:
    """Make ExecutionContext.run accept plugin event images (idempotent).

    persona-agents monkey-patches ExecutionContext.run() without an ``images`` kwarg.
    Core's continuity executor calls ``ctx.run(msg, images=...)`` for daemon events.
    """
    try:
        from core.continuity.execution_context import ExecutionContext, current_task_persona
    except Exception as exc:
        logger.warning('[discord_cognitive] ExecutionContext import failed: %s', exc)
        return

    current = ExecutionContext.run
    if getattr(current, '__name__', '') == '_discord_cognitive_images_run':
        return

    try:
        if 'images' in inspect.signature(current).parameters:
            return
    except (TypeError, ValueError):
        pass

    persona_run = current

    def _discord_cognitive_images_run(self, user_input, history_messages=None, images=None):
        if getattr(self, '_persona_agent', False):
            return persona_run(self, user_input, history_messages)

        from core.chat.chat import filter_to_thinking_only, _inject_tool_images

        _persona_token = current_task_persona.set(self.task_settings.get('prompt'))
        try:
            return self._run_inner(
                user_input,
                history_messages,
                filter_to_thinking_only,
                _inject_tool_images,
                images,
            )
        finally:
            current_task_persona.reset(_persona_token)

    _discord_cognitive_images_run.__name__ = '_discord_cognitive_images_run'
    ExecutionContext.run = _discord_cognitive_images_run
    logger.info('[discord_cognitive] ExecutionContext.run upgraded for event image support')


def ensure_discord_llm_provider_override() -> None:
    """Honor llm_primary/llm_model from discord_message event payloads (plugin settings).

    Core sets current_event_data before ExecutionContext is constructed; we read
    it here so continuity task provider/model can be overridden without core edits.
    """
    try:
        from core.continuity.execution_context import ExecutionContext
        from core.continuity.executor import current_event_data
    except Exception as exc:
        logger.warning('[discord_cognitive] LLM provider override skipped: %s', exc)
        return

    original = ExecutionContext._resolve_provider
    if getattr(original, '_discord_cognitive_llm_override', False):
        return

    def _resolve_provider_discord(self):
        event = current_event_data.get() or {}
        llm_primary = str(event.get('llm_primary') or '').strip()
        llm_model = str(event.get('llm_model') or '').strip()
        if llm_primary and llm_primary not in ('auto', ''):
            saved_provider = self.task_settings.get('provider')
            saved_model = self.task_settings.get('model')
            self.task_settings['provider'] = llm_primary
            self.task_settings['model'] = llm_model
            try:
                return original(self)
            finally:
                self.task_settings['provider'] = saved_provider
                self.task_settings['model'] = saved_model
        return original(self)

    _resolve_provider_discord._discord_cognitive_llm_override = True
    ExecutionContext._resolve_provider = _resolve_provider_discord
    logger.info('[discord_cognitive] ExecutionContext LLM provider override enabled')
