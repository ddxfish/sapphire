"""Inbound batch → should she answer → the Sapphire event; her reply → Discord (S7 rewrite).

process_batch runs ON the daemon loop — the gateway loop — so the one Discord
call it makes (the channel window, only once she has decided to answer) is an
awaited py-cord call, never a blocking one. handle_llm_response runs on core's
reply thread (it sleeps for typing).
"""

from __future__ import annotations

import logging
import time

from plugins.discord import hooks_out
from plugins.discord.conversation.context import ReplyCooldown, build_context
from plugins.discord.conversation.gif_service import build_gif_reply_hint
from plugins.discord.conversation.images import image_urls
from plugins.discord.conversation.trigger_service import evaluate_organic_chance, evaluate_reply_trigger
from plugins.discord.conversation.typing_indicator import (
    human_pause_seconds,
    inter_chunk_pause_seconds,
    read_delay_seconds,
    typing_duration_seconds,
)

logger = logging.getLogger(__name__)

# A pending payload is kept until its reply lands; one that never comes (LLM
# error, listen-only task) used to keep it forever (M9 / row 24).
PENDING_TTL_SECONDS = 1800


class ConversationService:
    def __init__(
        self,
        *,
        event_bridge,
        transport=None,
        settings_store=None,
        reply_style_service=None,
        gif_service=None,
        reactions=None,
        bot_gate=None,
        mention_map_service=None,
        decisions=None,
        image_lane=None,
        account_repository=None,
        cooldown=None,
    ):
        self.event_bridge = event_bridge
        self.transport = transport
        self.settings_store = settings_store
        self.reply_style_service = reply_style_service
        self.gif_service = gif_service
        self.reactions = reactions
        self.bot_gate = bot_gate
        self.mention_map_service = mention_map_service
        self.decisions = decisions
        self.image_lane = image_lane
        self.account_repository = account_repository
        self.cooldown = cooldown or ReplyCooldown()
        self._pending: dict[str, dict] = {}
        self._dm_counts: dict[tuple[str, str], tuple[str, int]] = {}

    # ── inbound ──────────────────────────────────────────────────────────────

    async def process_batch(self, batch) -> bool:
        trigger = self._trigger(batch)
        settings = self.settings_store.resolve() if self.settings_store else None
        decision = self._decide(trigger, settings)
        if decision['stage'] != 'safety':
            # One reaction roll per message, answered or not (never for a DM she
            # refuses on safety grounds).
            self._silent_reaction(trigger, settings)
        if not decision['respond']:
            self._reject(trigger, decision['reason'], decision['stage'], decision.get('detail'))
            return False
        cooldown = self.cooldown.evaluate(trigger, settings)
        if not cooldown.get('allowed'):
            self._reject(trigger, cooldown.get('reason') or 'denied', 'policy', cooldown)
            return False
        rows = await self._window(trigger, settings)
        payload = self._payload(batch, trigger, settings, decision, rows)
        if not self.event_bridge.emit_discord_message(payload):
            self._reject(trigger, 'no_daemon_task', 'daemon')
            return False
        if self.bot_gate:
            self.bot_gate.note_reply(trigger.account_name, trigger.channel_id, author_is_bot=decision['author_is_bot'])
        self._sweep_pending()
        self._pending[trigger.message_id] = {
            'channel_id': trigger.channel_id,
            'account_name': trigger.account_name,
            'payload': payload,
            'at': time.time(),
        }
        return True

    @staticmethod
    def _trigger(batch):
        """The newest ADDRESSED message is the trigger — with age-capped batching
        the last message is often ambient chatter while the @mention sits earlier."""
        for candidate in reversed(batch.observations):
            if getattr(candidate, 'mentioned', False):
                return candidate
        return batch.observations[-1]

    def _decide(self, trigger, settings) -> dict:
        """Every reason she would not answer, in order: DM safety, the reply
        trigger (ignore list / reply mode), the bot gate, addressing, the organic roll."""
        def no(reason, stage, detail=None):
            return {'respond': False, 'reason': reason, 'stage': stage, 'detail': detail}

        evaluation = evaluate_reply_trigger(trigger, settings, transport=self.transport,
                                            account_repository=self.account_repository)
        trigger.name_matched = evaluation['name_matched']
        safety = getattr(settings, 'safety', None)
        if trigger.is_dm and not bool(getattr(safety, 'allow_direct_messages', True)):
            return no('direct_messages_disabled', 'safety')
        if trigger.is_dm and not self._dm_within_budget(trigger, settings):
            return no('dm_daily_budget', 'safety')
        if not evaluation['allowed']:
            return no(str(evaluation.get('reason') or 'trigger_blocked'), 'trigger', evaluation)
        author_is_bot = bool(getattr(trigger, 'author_is_bot', False))
        bot_allowed = False
        if self.bot_gate and settings:
            gate = self.bot_gate.evaluate(trigger, settings)
            if not gate.get('allowed'):
                return no(str(gate.get('reason') or 'bot_blocked'), 'bot_gate', gate)
            bot_allowed = author_is_bot
        reply_mode = str(evaluation.get('reply_mode', ''))
        respond = bool(evaluation['respond_trigger']) or bool(trigger.is_dm) or reply_mode == 'all'
        if trigger.mentioned:
            reason = 'mentioned'
        elif evaluation['name_matched']:
            reason = 'name_matched'
        elif trigger.is_dm:
            reason = 'dm'
        else:
            reason = 'reply_all'
        # Chance-based organic replies: default mode only, channel messages that
        # did not address her; bots roll only when the bot gate let them through.
        if not respond and reply_mode == 'default' and not trigger.is_dm and (not author_is_bot or bot_allowed):
            chance = evaluate_organic_chance(trigger, settings)
            if not chance['allowed']:
                return no(str(chance.get('reason') or 'chance'), 'trigger', chance)
            respond, reason, evaluation = True, 'organic', {**evaluation, **chance}
        if not respond:
            return no('not_addressed', 'routing')
        return {'respond': True, 'reason': reason, 'stage': 'ok', 'evaluation': evaluation,
                'author_is_bot': author_is_bot}

    async def _window(self, trigger, settings) -> list[dict]:
        """The last N messages of the channel, live from Discord, once per reply
        (channel.context_messages; 0 = none). Nothing is stored."""
        limit = int(getattr(getattr(settings, 'channel', None), 'context_messages', 20) or 0)
        fetch = getattr(self.transport, 'recent_messages_async', None)
        if limit <= 0 or not callable(fetch):
            return []
        try:
            return list(await fetch(trigger.account_name, trigger.channel_id, limit=max(1, min(50, limit))) or [])
        except Exception as exc:
            logger.info('[DISCORD] channel window unavailable for %s: %s', trigger.channel_id, exc)
            return []

    def _payload(self, batch, trigger, settings, decision, rows: list[dict]) -> dict:
        evaluation = decision['evaluation']
        context = build_context(rows, trigger)
        mention_map = {}
        if self.mention_map_service:
            mention_map = self.mention_map_service.build_for_channel(
                trigger.account_name, trigger.channel_id, author_id=trigger.author_id,
                username=trigger.username, display_name=trigger.display_name, rows=rows,
            )
        payload = {
            'account': trigger.account_name,
            'guild_id': trigger.guild_id,
            'guild_name': trigger.guild_name,
            'channel_id': trigger.channel_id,
            'channel_name': trigger.channel_name,
            'message_id': trigger.message_id,
            'content': self._reply_content(trigger),
            'username': trigger.username,
            'display_name': trigger.display_name,
            'author_id': trigger.author_id,
            'is_dm': trigger.is_dm,
            'mentioned': str(evaluation['respond_trigger']),
            'name_matched': str(evaluation['name_matched']),
            'recent_history': context['recent_history'],
            'batch_size': batch.message_count,
            'attachments': trigger.attachments,
            'reply_to_message_id': trigger.reply_to_message_id,
            'mention_map': mention_map,
            # Image attachments as bytes (core's plugin-event image contract):
            # she SEES them when her reply model has vision. Cache only —
            # process_batch runs on the daemon loop (broadsword H5).
            'images': self.image_lane.payload_images(trigger, settings) if self.image_lane else [],
        }
        hints = []
        if self.mention_map_service:
            hints.append(self.mention_map_service.mention_format_hint())
        if settings:
            gif_hint = build_gif_reply_hint(settings)
            if gif_hint:
                hints.append(gif_hint)
        # S0 door: add-ons append to the reply prompt (synchronous — the result
        # is used right here; handlers run on the daemon loop, keep them cheap).
        ctx_ev = hooks_out.fire('discord_prompt_context', {
            **hooks_out.observed_payload(trigger),
            'reply_reason': decision['reason'],
            'recent_history': context.get('recent_history') or [],
            'context_parts': list(hints),
        })
        hints = [str(p) for p in (ctx_ev.metadata.get('context_parts') or []) if str(p).strip()]
        if hints:
            payload['reply_hints'] = hints
            payload['reply_instructions'] = '\n\n'.join(hints)
        return payload

    @staticmethod
    def _reply_content(trigger) -> str:
        text = str(getattr(trigger, 'clean_content', '') or '')
        if text.strip():
            return text
        # Never emit an empty content field: core's event formatter falls back
        # to the raw JSON payload — routing metadata in her prompt (2026-08-06).
        if image_urls(getattr(trigger, 'attachments', None)):
            return '[The user sent an image with no caption.]'
        return '[The user sent a message with no text — an attachment, sticker, or embed.]'

    def _dm_within_budget(self, trigger, settings) -> bool:
        """safety.dm_daily_budget (M19): DM messages per person per day she
        answers. In-memory, keyed by local day; other days drop on rollover."""
        budget = int(getattr(getattr(settings, 'safety', None), 'dm_daily_budget', 30) or 0)
        if budget <= 0:
            return True
        day = time.strftime('%Y-%m-%d')
        for key in [k for k, (d, _n) in self._dm_counts.items() if d != day]:
            self._dm_counts.pop(key, None)
        key = (str(trigger.account_name), str(trigger.author_id))
        _day, used = self._dm_counts.get(key, (day, 0))
        if used >= budget:
            return False
        self._dm_counts[key] = (day, used + 1)
        return True

    def _silent_reaction(self, trigger, settings) -> bool:
        if not self.reactions or not self.transport or not settings:
            return False
        intention = self.reactions.evaluate_silent(trigger, settings=settings)
        if not intention:
            return False
        self.reactions.execute_silent(intention, transport=self.transport, settings=settings)
        return True

    def _reject(self, trigger, reason: str, stage: str, detail: dict | None = None) -> None:
        """Why she stayed quiet — ids only, for the Debug tab; the text is never kept."""
        if self.decisions:
            self.decisions.note('rejected', account=getattr(trigger, 'account_name', ''),
                                channel_id=getattr(trigger, 'channel_id', ''), channel_name=getattr(trigger, 'channel_name', ''),
                                message_id=getattr(trigger, 'message_id', ''), stage=stage or 'policy', reason=reason or 'blocked')

    # ── the pending map ──────────────────────────────────────────────────────

    def pending_reply(self, message_id: str) -> dict | None:
        return self._pending.get(message_id)

    def discard_pending(self, message_id: str) -> None:
        """Drop a message's pending payload and reply latches without delivery."""
        self._pending.pop(str(message_id), None)
        if self.reply_style_service and hasattr(self.reply_style_service, 'discard'):
            self.reply_style_service.discard(message_id)

    def _sweep_pending(self) -> int:
        now = time.time()
        stale = [mid for mid, row in self._pending.items() if now - float(row.get('at') or now) > PENDING_TTL_SECONDS]
        for mid in stale:
            self._pending.pop(mid, None)
        return len(stale)

    # ── outbound ─────────────────────────────────────────────────────────────

    def handle_llm_response(self, task, event_data: dict, response_text: str):
        if not self.reply_style_service or not self.transport:
            return None
        event_data = event_data or {}
        message_id = str(event_data.get('message_id', ''))
        account_name = str(event_data.get('account', '') or '')
        channel_id = str(event_data.get('channel_id', '') or '')
        guild_id = str(event_data.get('guild_id', '') or '')
        settings = self.settings_store.resolve() if self.settings_store else None
        delivery = settings.channel if settings else None
        proactive_kind = str(event_data.get('proactive_kind') or '').strip()
        outcome = dict(event_data=event_data)

        if self.reply_style_service.should_skip_auto_reply(message_id):
            # A tool already posted the reply; only its tags are still owed.
            tool_text = self.reply_style_service.consume_tool_sent_text(message_id)
            combined = f"{response_text or ''}\n{tool_text or ''}".strip()
            if combined:
                parsed = self.reply_style_service.parse_llm_output(combined)
                self.deliver_tags(parsed, message_id=message_id, channel_id=channel_id, account_name=account_name,
                                  settings=settings, trigger_message_id='' if proactive_kind else message_id)
            self._note_outcome(message_id, 'skipped', **outcome)
            self._pending.pop(message_id, None)
            return {'status': 'skipped'}

        # One switch for reading, typing and pausing like a person (three booleans until 2026-09-22).
        natural = bool(delivery.natural_delay) if delivery else True
        if natural and not proactive_kind:
            time.sleep(read_delay_seconds(len(str(event_data.get('content', '')))))

        parsed = self.reply_style_service.parse_llm_output(response_text)
        if not parsed.chunks:
            self._note_outcome(message_id, 'empty', **outcome)
            self._pending.pop(message_id, None)
            return {'status': 'empty'}

        chunks = list(parsed.chunks)
        # Quote the message she is answering (first chunk only); scheduled posts never quote.
        reply_to_default = ''
        if not proactive_kind:
            reply_to_default = str(event_data.get('reply_to_message_id') or '').strip() or message_id
        # S0 door: add-ons may reshape the reply before it goes out — reorder or
        # rewrite chunks, pick the quote target, set a reaction, add a delay —
        # never silence it.
        plan_ev = hooks_out.fire('discord_reply_planned', {
            'account': account_name, 'guild_id': guild_id, 'channel_id': channel_id,
            'message_id': message_id, 'proactive_kind': proactive_kind,
            'is_dm': str(event_data.get('is_dm', '')).lower() in {'true', '1'},
            'trigger_content': str(event_data.get('content', '')),
            'chunks': list(chunks), 'quote_reply': reply_to_default,
            'reaction': parsed.reaction or '', 'edit_text': parsed.edit_text or '', 'delay_s': 0.0,
        })
        hook_chunks = [str(c) for c in (plan_ev.metadata.get('chunks') or []) if str(c).strip()]
        if hook_chunks:
            chunks = hook_chunks
        reply_to_first = str(plan_ev.metadata.get('quote_reply') or '')
        hook_reaction = str(plan_ev.metadata.get('reaction') or '')
        try:
            hook_delay = max(0.0, min(30.0, float(plan_ev.metadata.get('delay_s') or 0.0)))
        except (TypeError, ValueError):
            hook_delay = 0.0
        if hook_delay:
            time.sleep(hook_delay)
        if natural:
            time.sleep(human_pause_seconds())

        results, sent_message_ids = [], []
        for index, chunk in enumerate(chunks):
            if index > 0 and natural:
                time.sleep(inter_chunk_pause_seconds())
            if natural:
                self.transport.hold_typing_sync(channel_id, typing_duration_seconds(len(chunk), text=chunk),
                                                account_name=account_name or None)
            result = self.transport.send_message_sync(
                channel_id, chunk,
                reply_to_message_id=(reply_to_first or None) if index == 0 else None,
                account_name=account_name or None, guild_id=guild_id or None,
            )
            results.append(result)
            if result.get('status') == 'error':
                break
            sent_message_ids.extend(str(item.get('message_id', '')) for item in (result.get('messages') or []))

        # A failed chunk breaks the loop. If NOTHING landed, say so — the daemon
        # once logged "reply delivered" over a 403 while the channel stayed silent.
        if not sent_message_ids and any(r.get('status') == 'error' for r in results):
            self._note_outcome(message_id, 'error', reason=str(results[-1].get('error') or 'send failed'), **outcome)
            self._pending.pop(message_id, None)
            return {'status': 'error', 'error': (results[-1].get('error') or 'send failed'), 'chunks': 0}

        hooks_out.fire('discord_reply_sent', {
            'account': account_name, 'guild_id': guild_id, 'channel_id': channel_id,
            'message_id': message_id, 'proactive_kind': proactive_kind,
            'sent_message_ids': list(sent_message_ids), 'chunks': list(chunks),
        })
        self._note_outcome(message_id, 'sent' if sent_message_ids else 'error', chunks=len(sent_message_ids), **outcome)
        # A reply_planned handler's reaction lands here; the model's own [react:]
        # tag rides deliver_tags (gated by reaction.enabled).
        if hook_reaction and hook_reaction != (parsed.reaction or '') and not proactive_kind:
            self.transport.add_reaction_sync(channel_id, message_id, hook_reaction, account_name=account_name or None)
        self.deliver_tags(parsed, message_id=message_id, channel_id=channel_id, account_name=account_name,
                          settings=settings, trigger_message_id='' if proactive_kind else message_id)
        self._pending.pop(message_id, None)
        return {'status': 'sent', 'chunks': len([r for r in results if r.get('status') != 'error'])}

    def deliver_tags(self, parsed, *, message_id: str, channel_id: str, account_name: str, settings,
                     trigger_message_id: str = '') -> None:
        """The [react:] and [gif:] tags of a parsed reply — hers, or a tool's."""
        if not self.transport or not self.reply_style_service:
            return
        message_id = str(message_id or '')
        reaction_on = bool(getattr(getattr(settings, 'reaction', None), 'enabled', True))
        if parsed.reaction and trigger_message_id and reaction_on:
            self.transport.add_reaction_sync(channel_id, trigger_message_id, parsed.reaction,
                                             account_name=account_name or None)
        gif_query = getattr(parsed, 'gif_query', '') or ''
        if not gif_query or not self.gif_service or self.reply_style_service.gif_already_sent(message_id):
            return
        gif_query = self.gif_service.maybe_send_gif(parsed, account_name=account_name, channel_id=channel_id,
                                                    settings=settings)
        if not gif_query:
            return
        url = self.gif_service.search_gif_url(gif_query, settings=settings)
        if url:
            self.transport.send_gif_sync(channel_id, url, account_name=account_name or None)
            self.reply_style_service.mark_gif_sent(message_id)
        else:
            logger.debug('[DISCORD] GIF search returned no URL for %r', gif_query)

    def _note_outcome(self, message_id: str, status: str, *, event_data: dict, reason: str = '', chunks: int = 0) -> None:
        """What happened to her reply — ids only, for the Debug tab."""
        if self.decisions:
            self.decisions.note(status, account=event_data.get('account', ''), channel_id=event_data.get('channel_id', ''),
                                channel_name=event_data.get('channel_name', ''), message_id=message_id,
                                reason=reason, chunks=chunks)
