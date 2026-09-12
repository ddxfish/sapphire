"""Lifecycle orchestration for startup and shutdown ordering."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class LifecycleManager:
    async def start(self, container) -> None:
        try:
            container.health.mark('starting', 'Loading settings')
            container.build_settings_store()
            container.health.mark('starting', 'Opening storage')
            container.sqlite_service.start()
            container.health.mark('starting', 'Loading repositories')
            container.build_repositories()
            container.health.mark('starting', 'Building Sapphire bridges')
            container.build_bridges()
            from plugins.discord.voice.pycord_patches import apply_pycord_voice_patches
            from plugins.discord.voice.dave_voice_patches import apply_dave_voice_patches
            from plugins.discord.lib.core_compat import ensure_discord_llm_provider_override
            apply_dave_voice_patches()
            apply_pycord_voice_patches()
            ensure_discord_llm_provider_override()
            from plugins.discord.voice.voice_deps import voice_stack_info

            stack = voice_stack_info()
            logger.info(
                '[discord_cognitive] Voice stack: pycord=%s davey=%s dave_mode=%s '
                'router_patches=%s opus_pcm_patch=%s',
                stack.get('pycord_version') or 'missing',
                stack.get('davey'),
                stack.get('dave_patch_mode'),
                stack.get('router_patches'),
                stack.get('opus_pcm_patch'),
            )
            if not stack.get('voice_sinks') or not stack.get('davey'):
                logger.warning('[discord_cognitive] Voice receive unavailable — %s', stack)
            elif not stack.get('opus_pcm_patch'):
                logger.warning(
                    '[discord_cognitive] Voice receive is available but PCM double-decrypt '
                    'skip patch did not apply — DAVE transcription may be corrupted'
                )
            container.health.mark('starting', 'Building transport')
            container.build_transport()
            container.health.mark('starting', 'Starting message pipeline')
            if container.message_pipeline:
                await container.message_pipeline.start()
            container.health.mark('starting', 'Starting scheduler')
            await container.scheduler.start()
            container.health.mark('ready', 'Runtime ready')
            if getattr(container, 'voice_event_bridge', None) is not None:
                container.voice_event_bridge.start()
            await self._connect_stored_accounts(container)
        except Exception as exc:
            container.health.mark('error', str(exc))
            logger.exception('Discord cognitive startup failed')
            raise

    async def stop(self, container) -> None:
        container.health.mark('stopping', 'Stopping message pipeline')
        if getattr(container, 'message_pipeline', None) is not None:
            try:
                await container.message_pipeline.stop()
            except Exception:
                logger.exception('Message pipeline stop failed')
        container.health.mark('stopping', 'Stopping scheduler')
        try:
            await container.scheduler.stop()
        except Exception:
            logger.exception('Scheduler stop failed')
        container.health.mark('stopping', 'Closing voice transport')
        if getattr(container, 'voice_event_bridge', None) is not None:
            try:
                container.voice_event_bridge.stop()
            except Exception:
                logger.exception('Voice event bridge shutdown failed')
        runner = getattr(container, 'discord_conversation_runner', None)
        if runner is not None:
            try:
                runner.stop_all()
            except Exception:
                logger.exception('Discord conversation runner shutdown failed')
        if getattr(container, 'voice_transport', None) is not None:
            for item in container.voice_transport.list_connections():
                try:
                    await container.voice_transport.disconnect_async(
                        item['account_name'],
                        item['channel_id'],
                    )
                except Exception:
                    logger.exception('Voice disconnect failed for %s', item)
        container.health.mark('stopping', 'Closing transport')
        if container.transport is not None:
            try:
                await container.transport.close_all()
            except Exception:
                logger.exception('Transport close failed')
        container.health.mark('stopping', 'Closing storage')
        try:
            container.sqlite_service.stop()
        except Exception:
            logger.exception('Storage close failed')
        container.health.mark('stopped', 'Runtime stopped')

    async def _connect_stored_accounts(self, container) -> None:
        if not container.transport or not container.account_repository:
            return
        for account in container.account_repository.list_accounts():
            name = account.get('name', '')
            token = container.account_repository.get_token(name)
            if not name or not token:
                continue
            try:
                await container.transport.connect_account(name, token)
            except Exception:
                logger.exception('Failed to connect stored account %s', name)
