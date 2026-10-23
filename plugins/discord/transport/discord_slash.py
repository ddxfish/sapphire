"""Application (slash) command registration for py-cord Bot clients."""

from __future__ import annotations

import logging
from plugins.discord.voice.voice_gate import VOICE_OFF_TEXT

logger = logging.getLogger(__name__)


def _runtime():
    try:
        from plugins.discord.daemon import get_runtime
        return get_runtime()
    except Exception:
        return None


def register_slash_commands(client, account_name: str) -> bool:
    """Attach /voice join|leave to a py-cord Bot. No-op for plain clients (tests)."""
    if not hasattr(client, 'create_group'):
        return False
    import discord

    from plugins.discord.models.intentions import JoinVoiceIntention, LeaveVoiceIntention

    voice = client.create_group('voice', 'Voice channel controls')

    @voice.command(name='join', description='Bring the bot into a voice channel (defaults to yours)')
    async def voice_join(
        ctx: discord.ApplicationContext,
        channel: discord.Option(discord.VoiceChannel, 'Voice channel to join', required=False) = None,
    ):
        target = channel
        if target is None:
            voice_state = getattr(ctx.author, 'voice', None)
            target = getattr(voice_state, 'channel', None)
        if target is None:
            await ctx.respond('Join a voice channel first, or pick one with the channel option.', ephemeral=True)
            return
        runtime = _runtime()
        if not runtime or not getattr(runtime, 'voice_service', None):
            await ctx.respond('Voice runtime is not available.', ephemeral=True)
            return
        await ctx.defer()
        intention = JoinVoiceIntention(
            intention_type='join_voice',
            account_name=account_name,
            channel_id=str(target.id),
            message_id='',
            reason='slash_command',
            guild_id=str(getattr(getattr(target, 'guild', None), 'id', '') or ''),
        )
        result = await runtime.voice_service.join_async(intention)
        status = result.get('status')
        if status == 'joined':
            await ctx.respond(f'Joined **{target.name}**.')
        elif status == 'blocked':
            await ctx.respond(VOICE_OFF_TEXT)
        else:
            await ctx.respond(f"Could not join: {result.get('reason') or status}")

    @voice.command(name='leave', description='Disconnect the bot from voice in this server')
    async def voice_leave(ctx: discord.ApplicationContext):
        runtime = _runtime()
        if not runtime or not getattr(runtime, 'voice_service', None) or not getattr(runtime, 'voice_transport', None):
            await ctx.respond('Voice runtime is not available.', ephemeral=True)
            return
        guild_id = str(getattr(ctx.guild, 'id', '') or '')
        connections = [
            row for row in runtime.voice_transport.list_connections(account_name)
            if not guild_id or str(row.get('guild_id') or '') == guild_id
        ]
        if not connections:
            await ctx.respond('Not connected to voice here.', ephemeral=True)
            return
        await ctx.defer()
        for row in connections:
            intention = LeaveVoiceIntention(
                intention_type='leave_voice',
                account_name=account_name,
                channel_id=str(row.get('channel_id') or ''),
                message_id='',
                reason='slash_command',
            )
            await runtime.voice_service.leave_async(intention)
        await ctx.respond('Left voice.')

    logger.info('Slash commands registered for account %s', account_name)
    return True
