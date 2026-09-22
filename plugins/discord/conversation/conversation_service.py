from __future__ import annotations

import time

from plugins.discord.conversation.trigger_service import evaluate_organic_chance, evaluate_reply_trigger
from plugins.discord.conversation.name_match import bot_names_for_account
from plugins.discord.conversation.gif_service import build_gif_reply_hint
from plugins.discord.conversation.images import image_urls
from plugins.discord.conversation.typing_indicator import (
    human_pause_seconds,
    inter_chunk_pause_seconds,
    read_delay_seconds,
    typing_duration_seconds,
)

from plugins.discord.conversation.post_reply_tags import deliver_gif_and_reaction
from plugins.discord import hooks_out
from plugins.discord.models.intentions import ReplyMessageIntention
from plugins.discord.models.observations import SlashCommandObservation


class ConversationService:
    def __init__(
        self,
        *,
        event_bridge,
        policy_service,
        prompt_context_service,
        trace_repository,
        image_lane=None,
        reply_style_service=None,
        transport=None,
        gif_service=None,
        reaction_service=None,
        settings_store=None,
        trace_service=None,
        account_repository=None,
        bot_gate=None,
        mention_map_service=None,
        llm_debug_service=None,
    ):
        self.event_bridge = event_bridge
        self.policy_service = policy_service
        self.prompt_context_service = prompt_context_service
        self.trace_repository = trace_repository
        self.reply_style_service = reply_style_service
        self.transport = transport
        self.gif_service = gif_service
        self.reaction_service = reaction_service
        self.settings_store = settings_store
        self.trace_service = trace_service
        self.account_repository = account_repository
        self.bot_gate = bot_gate
        self.mention_map_service = mention_map_service
        self.llm_debug_service = llm_debug_service
        self._pending: dict[str, dict] = {}
        self.image_lane = image_lane

    def process_batch(self, batch) -> bool:
        trigger = batch.observations[-1]
        # Prefer the newest ADDRESSED message as the trigger — with age-capped
        # batching the last message is often ambient chatter while the actual
        # @mention sits earlier in the batch; keying every gate off plain
        # observations[-1] silently dropped those mentions as "Not addressed".
        for candidate in reversed(batch.observations):
            if getattr(candidate, 'mentioned', False):
                trigger = candidate
                break
        settings = self.settings_store.resolve() if self.settings_store else None
        trigger_eval = evaluate_reply_trigger(
            trigger,
            settings,
            transport=self.transport,
            account_repository=self.account_repository,
        )
        trigger.name_matched = trigger_eval['name_matched']
        if trigger.is_dm and not bool(getattr(getattr(settings, 'safety', None), 'allow_direct_messages', True)):
            self.trace_repository.record_trace('event_dropped', 'Direct messages disabled', {
                'message_id': trigger.message_id,
            })
            self._record_debug_rejection(trigger, reason='direct_messages_disabled', stage='safety')
            return False
        if trigger.is_dm and not self._dm_within_budget(trigger, settings):
            self.trace_repository.record_trace('event_dropped', 'DM daily budget reached', {
                'message_id': trigger.message_id,
                'author_id': trigger.author_id,
            })
            self._record_debug_rejection(trigger, reason='dm_daily_budget', stage='safety')
            return False
        world_state = self._world_state_for(trigger, trigger_eval)
        if not trigger_eval['allowed']:
            # One reaction roll per message. Reply-path messages use reaction_chance
            # gated by react_on_reply_path; everything she won't answer uses the
            # fixed read-only roll.
            self._maybe_execute_silent_reaction(
                trigger, settings, world_state,
                read_only=True, reply_planned=False)
            if self.trace_service:
                self.trace_service.record_policy_rejection(trigger_eval['reason'], {
                    'channel_id': trigger.channel_id,
                    'message_id': trigger.message_id,
                })
            self.trace_repository.record_trace('event_dropped', 'Reply trigger blocked message', trigger_eval)
            self._record_debug_rejection(
                trigger,
                reason=str(trigger_eval.get('reason') or 'trigger_blocked'),
                stage='trigger',
                detail=trigger_eval,
            )
            return False
        author_is_bot = bool(getattr(trigger, 'author_is_bot', False))
        bot_organic_candidate = False
        if self.bot_gate and settings:
            bot_decision = self.bot_gate.evaluate(trigger, settings)
            if not bot_decision.get('allowed'):
                self._maybe_execute_silent_reaction(
                    trigger, settings, world_state,
                    read_only=True, reply_planned=False)
                if self.trace_service:
                    self.trace_service.record_policy_rejection(bot_decision.get('reason', 'bot_blocked'), {
                        'channel_id': trigger.channel_id,
                        'message_id': trigger.message_id,
                        'author_id': trigger.author_id,
                    })
                self.trace_repository.record_trace('event_dropped', 'Bot gate blocked message', bot_decision)
                self._record_debug_rejection(
                    trigger,
                    reason=str(bot_decision.get('reason') or 'bot_blocked'),
                    stage='bot_gate',
                    detail=bot_decision,
                )
                return False
            bot_organic_candidate = author_is_bot
        respond = bool(trigger_eval['respond_trigger']) or bool(trigger.is_dm) \
            or str(trigger_eval.get('reply_mode', '')) == 'all'
        organic_reply = False
        # Chance-based organic replies: default mode only, channel messages that
        # did not address her. DMs always reply. Mentions/name match bypass.
        # Bot organic rolls only for bots the bot gate let through.
        if (
            not respond
            and str(trigger_eval.get('reply_mode', '')) == 'default'
            and not trigger.is_dm
            and (not getattr(trigger, 'author_is_bot', False) or bot_organic_candidate)
        ):
            chance_eval = evaluate_organic_chance(trigger, settings)
            if chance_eval['allowed']:
                respond = True
                organic_reply = True
                trigger_eval = {**trigger_eval, **chance_eval}
            else:
                self._maybe_execute_silent_reaction(
                    trigger, settings, world_state,
                    read_only=True, reply_planned=False)
                if self.trace_service:
                    self.trace_service.record_policy_rejection(chance_eval['reason'], {
                        'channel_id': trigger.channel_id,
                        'message_id': trigger.message_id,
                    })
                self.trace_repository.record_trace(
                    'event_dropped', 'Organic reply chance missed', chance_eval)
                self._record_debug_rejection(
                    trigger,
                    reason=str(chance_eval.get('reason') or 'chance'),
                    stage='trigger',
                    detail=chance_eval,
                )
                return False
        will_reply = bool(respond)
        self._maybe_execute_silent_reaction(
            trigger, settings, world_state,
            read_only=not will_reply, reply_planned=will_reply)
        if not respond:
            self.trace_repository.record_trace('event_dropped', 'Not addressed', {
                'message_id': trigger.message_id,
            })
            self._record_debug_rejection(trigger, reason='not_addressed', stage='routing')
            return False
        # Policy (reply cooldown) runs AFTER the respond decision — evaluating
        # it earlier stamped the cooldown clock on ambient chatter she never
        # answers, starving real mentions in busy channels (scout, 2026-08-05).
        decision = self.policy_service.evaluate_text_observation(trigger, settings)
        if not decision.get('allowed'):
            if self.trace_service:
                self.trace_service.record_policy_rejection(decision.get('reason', 'denied'), {
                    'channel_id': trigger.channel_id,
                    'message_id': trigger.message_id,
                })
            self.trace_repository.record_trace('event_dropped', 'Policy rejected observation', decision)
            self._record_debug_rejection(
                trigger,
                reason=str(decision.get('reason') or 'denied'),
                stage='policy',
                detail=decision,
            )
            return False
        # The trigger rides the batch so prompt context builds for the SAME
        # message every gate keyed off (row 15) — attribute, not argument, so
        # every context double with a one-arg build() keeps working.
        try:
            batch.trigger = trigger
        except Exception:
            pass
        context = self.prompt_context_service.build(batch)
        reply_content = self._reply_content(trigger)
        mention_map = {}
        if self.mention_map_service:
            mention_map = self.mention_map_service.build_for_channel(
                trigger.account_name,
                trigger.channel_id,
                author_id=trigger.author_id,
                username=trigger.username,
                display_name=trigger.display_name,
            )
        if trigger.mentioned:
            reply_reason = 'mentioned'
        elif trigger_eval['name_matched']:
            reply_reason = 'name_matched'
        elif trigger.is_dm:
            reply_reason = 'dm'
        elif organic_reply:
            reply_reason = 'organic'
        else:
            reply_reason = 'reply_all'
        intention = ReplyMessageIntention(
            intention_type='reply_message',
            account_name=trigger.account_name,
            channel_id=trigger.channel_id,
            message_id=trigger.message_id,
            reason=reply_reason,
            prompt=reply_content,
            metadata={'context': context, 'batch_size': batch.message_count},
        )
        if self.trace_service:
            self.trace_service.record_intention('reply_message', {
                'channel_id': trigger.channel_id,
                'reason': intention.reason,
                'batch_size': batch.message_count,
            })
        payload = {
            'account': trigger.account_name,
            'guild_id': trigger.guild_id,
            'guild_name': trigger.guild_name,
            'channel_id': trigger.channel_id,
            'channel_name': trigger.channel_name,
            'message_id': trigger.message_id,
            'content': reply_content,
            'username': trigger.username,
            'display_name': trigger.display_name,
            'author_id': trigger.author_id,
            'is_dm': trigger.is_dm,
            'mentioned': str(trigger_eval['respond_trigger']),
            'name_matched': str(trigger_eval['name_matched']),
            'recent_history': context['recent_history'],
            'batch_size': batch.message_count,
            'attachments': trigger.attachments,
            'reply_to_message_id': trigger.reply_to_message_id,
            'mention_map': mention_map,
            # Image attachments as bytes (core's plugin-event image contract):
            # she SEES them when her reply model has vision, and each one gets
            # an img: handle in the Discord chat → memory_save_image can file
            # it. The caption lane stays as the text fallback. 2026-09-13.
            'images': self._payload_images(trigger, settings),
        }
        hints = []
        if self.mention_map_service:
            hints.append(self.mention_map_service.mention_format_hint())
        if settings:
            gif_hint = build_gif_reply_hint(settings)
            if gif_hint:
                hints.append(gif_hint)
        # S0 door: add-ons append to the reply prompt (synchronous — the
        # result is used right here; handlers run on the gateway loop).
        ctx_ev = hooks_out.fire('discord_prompt_context', {
            **hooks_out.observed_payload(trigger),
            'reply_reason': reply_reason,
            'recent_history': context.get('recent_history') or [],
            'context_parts': list(hints),
        })
        hints = [str(p) for p in (ctx_ev.metadata.get('context_parts') or []) if str(p).strip()]
        if hints:
            payload['reply_hints'] = hints
            payload['reply_instructions'] = '\n\n'.join(hints)
        payload['_debug_prompt_context'] = {
            'source': 'discord_message',
            'reason': intention.reason,
            'batch_size': batch.message_count,
        }
        accepted = self.event_bridge.emit_discord_message(payload)
        if not accepted:
            # No second reaction roll here — the single roll already happened
            # up top (a rejected emit could double-react on DMs otherwise).
            self.trace_repository.record_trace('event_dropped', 'No Sapphire task accepted event', {'message_id': trigger.message_id})
            self._record_debug_rejection(trigger, reason='no_daemon_task', stage='daemon')
            return False
        if self.bot_gate:
            self.bot_gate.note_reply(trigger.account_name, trigger.channel_id, author_is_bot=author_is_bot)
        self._sweep_pending()
        self._pending[trigger.message_id] = {
            'channel_id': trigger.channel_id,
            'account_name': trigger.account_name,
            'payload': payload,
            'at': time.time(),
        }
        self.trace_repository.record_trace('event_emitted', 'Queued Discord message event', {'message_id': trigger.message_id})
        return True

    def pending_reply(self, message_id: str) -> dict | None:
        return self._pending.get(message_id)

    _PENDING_TTL_SECONDS = 1800

    def _sweep_pending(self) -> int:
        """M9's twin: _pending held the full payload (content, history, images)
        forever on any lane that never reached handle_llm_response (row 24)."""
        now = time.time()
        stale = [mid for mid, row in self._pending.items() if now - float(row.get('at') or now) > self._PENDING_TTL_SECONDS]
        for mid in stale:
            self._pending.pop(mid, None)
        return len(stale)

    def discard_pending(self, message_id: str) -> None:
        """Drop a message's pending payload and reply latches without delivery."""
        self._pending.pop(str(message_id), None)
        if self.reply_style_service and hasattr(self.reply_style_service, 'discard'):
            self.reply_style_service.discard(message_id)

    def _record_llm_debug_response(
        self,
        message_id: str,
        *,
        task,
        event_data: dict | None = None,
        response_text: str,
        parsed_chunks: list[str] | None = None,
        status: str,
        strip_think_tags: bool | None = None,
        delivery: dict | None = None,
    ) -> None:
        if not self.llm_debug_service:
            return
        self.llm_debug_service.record_response(
            message_id,
            raw_text=response_text or '',
            parsed_chunks=list(parsed_chunks or []),
            status=status,
            strip_think_tags=strip_think_tags,
            delivery=delivery,
            task=task,
            event_data=event_data,
        )

    def _record_debug_rejection(self, trigger, *, reason: str, stage: str, detail: dict | None = None) -> None:
        if not self.llm_debug_service:
            return
        self.llm_debug_service.record_rejection(
            message_id=str(getattr(trigger, 'message_id', '') or ''),
            account=str(getattr(trigger, 'account_name', '') or ''),
            guild_id=str(getattr(trigger, 'guild_id', '') or ''),
            guild_name=str(getattr(trigger, 'guild_name', '') or ''),
            channel_id=str(getattr(trigger, 'channel_id', '') or ''),
            channel_name=str(getattr(trigger, 'channel_name', '') or ''),
            username=str(getattr(trigger, 'display_name', '') or getattr(trigger, 'username', '') or ''),
            author_id=str(getattr(trigger, 'author_id', '') or ''),
            content=str(getattr(trigger, 'clean_content', '') or getattr(trigger, 'content', '') or ''),
            reason=str(reason or 'blocked'),
            stage=str(stage or 'policy'),
            detail=detail or {},
        )

    def handle_llm_response(self, task, event_data: dict, response_text: str):
        if not self.reply_style_service or not self.transport:
            return None
        message_id = str((event_data or {}).get('message_id', ''))
        account_name = str((event_data or {}).get('account', '') or '')
        channel_id = str((event_data or {}).get('channel_id', '') or '')
        guild_id = str((event_data or {}).get('guild_id', '') or '')
        settings = self.settings_store.resolve() if self.settings_store else None
        delivery = settings.channel if settings else None
        strip_thinking = delivery.strip_think_tags if delivery else True
        proactive_kind = str((event_data or {}).get('proactive_kind') or '').strip()
        if self.reply_style_service.should_skip_auto_reply(message_id):
            tool_text = self.reply_style_service.consume_tool_sent_text(message_id)
            combined = f"{response_text or ''}\n{tool_text or ''}".strip()
            if combined:
                from plugins.discord.daemon import get_runtime

                parsed = self.reply_style_service.parse_llm_output(combined, strip_thinking=strip_thinking)
                deliver_gif_and_reaction(
                    runtime=get_runtime(),
                    parsed=parsed,
                    message_id=message_id,
                    channel_id=channel_id,
                    account_name=account_name,
                    settings=settings,
                    trigger_message_id='' if proactive_kind else message_id,
                )
            self.trace_repository.record_trace('delivery_skipped', 'Tool already sent reply', {'message_id': message_id})
            self._record_llm_debug_response(
                message_id,
                task=task,
                event_data=event_data,
                response_text=response_text,
                status='skipped',
                strip_think_tags=strip_thinking,
            )
            self._pending.pop(message_id, None)
            return {'status': 'skipped'}
        typing_enabled = delivery.typing_indicator_enabled if delivery else True
        human_pause_enabled = delivery.human_pause_enabled if delivery else True
        read_delay_enabled = delivery.read_delay_enabled if delivery else True

        if read_delay_enabled and not proactive_kind:
            time.sleep(read_delay_seconds(len(str((event_data or {}).get('content', '')))))

        parsed = self.reply_style_service.parse_llm_output(response_text, strip_thinking=strip_thinking)
        if not parsed.chunks:
            self.trace_repository.record_trace('delivery_empty', 'No visible content after reply parsing', {
                'message_id': message_id,
                'strip_thinking': strip_thinking,
            })
            self._record_llm_debug_response(
                message_id,
                task=task,
                event_data=event_data,
                response_text=response_text,
                status='empty',
                strip_think_tags=strip_thinking,
            )
            self._pending.pop(message_id, None)
            return {'status': 'empty'}

        trigger_content = str((event_data or {}).get('content', ''))
        chunks = list(parsed.chunks)
        # Quote the message she is answering (first chunk only) — a
        # reply_planned handler may clear or change it; scheduled posts never quote.
        reply_to_default = ''
        if not proactive_kind:
            reply_to_default = str((event_data or {}).get('reply_to_message_id') or '').strip() or message_id

        # S0 door: add-ons may reshape the reply before it goes out (worker
        # thread). A handler can reorder/rewrite chunks, pick the quote target,
        # set the reaction or add a delay — never silence the reply.
        plan_ev = hooks_out.fire('discord_reply_planned', {
            'account': account_name, 'guild_id': guild_id, 'channel_id': channel_id,
            'message_id': message_id, 'proactive_kind': proactive_kind,
            'is_dm': str((event_data or {}).get('is_dm', '')).lower() in {'true', '1'},
            'trigger_content': trigger_content,
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

        if human_pause_enabled:
            time.sleep(human_pause_seconds())

        result = []
        sent_message_ids: list[str] = []
        for index, chunk in enumerate(chunks):
            if index > 0 and human_pause_enabled:
                time.sleep(inter_chunk_pause_seconds())
            if typing_enabled:
                self.transport.hold_typing_sync(
                    channel_id,
                    typing_duration_seconds(len(chunk), text=chunk),
                    account_name=account_name or None,
                )
            send_result = self.transport.send_message_sync(
                channel_id,
                chunk,
                reply_to_message_id=(reply_to_first or None) if index == 0 else None,
                account_name=account_name or None,
                guild_id=guild_id or None,
            )
            result.append(send_result)
            if send_result.get('status') == 'error':
                self.trace_repository.record_trace('delivery_failed', send_result.get('error', 'send failed'), {
                    'message_id': message_id,
                    'channel_id': channel_id,
                })
                break
            if send_result.get('messages'):
                sent_message_ids.extend(str(item.get('message_id', '')) for item in send_result['messages'])

        # A failed chunk send breaks the loop above. If NOTHING landed, say so —
        # falling through to 'sent' made the daemon log "reply delivered" over a
        # 403 (missing channel permissions) while the channel stayed silent.
        # Live-caught 2026-08-06.
        if not sent_message_ids and any(r.get('status') == 'error' for r in result):
            self._record_llm_debug_response(
                message_id,
                task=task,
                event_data=event_data,
                response_text=response_text,
                parsed_chunks=list(chunks),
                status='error',
                strip_think_tags=strip_thinking,
            )
            self._pending.pop(message_id, None)
            return {'status': 'error',
                    'error': (result[-1].get('error') or 'send failed'),
                    'chunks': 0}

        delivery_debug = {
            'sent_text': chunks[0] if chunks else '',
            'quote_reply_to': reply_to_first,
            'chunks_sent': len(sent_message_ids),
        }
        # S0 door: what actually landed (worker thread).
        hooks_out.fire('discord_reply_sent', {
            'account': account_name, 'guild_id': guild_id, 'channel_id': channel_id,
            'message_id': message_id, 'proactive_kind': proactive_kind,
            'sent_message_ids': list(sent_message_ids), 'chunks': list(chunks),
        })
        if self.llm_debug_service:
            self._record_llm_debug_response(
                message_id,
                task=task,
                event_data=event_data,
                response_text=response_text,
                parsed_chunks=list(chunks),
                status='sent' if sent_message_ids else 'error',
                strip_think_tags=strip_thinking,
                delivery=delivery_debug,
            )
        # A reply_planned handler's reaction lands here; the model's own
        # [react:] tag rides deliver_gif_and_reaction (gated by reaction.enabled).
        if hook_reaction and hook_reaction != (parsed.reaction or '') and not proactive_kind:
            self.transport.add_reaction_sync(channel_id, message_id, hook_reaction, account_name=account_name or None)
        from plugins.discord.daemon import get_runtime

        deliver_gif_and_reaction(
            runtime=get_runtime(),
            parsed=parsed,
            message_id=message_id,
            channel_id=channel_id,
            account_name=account_name,
            settings=settings,
            trigger_message_id='' if proactive_kind else message_id,
        )
        self._pending.pop(message_id, None)
        self.trace_repository.record_trace('delivery_sent', 'Delivered LLM reply to Discord', {
            'message_id': message_id,
            'channel_id': channel_id,
            'chunks': len(result),
        })
        return {'status': 'sent',
                'chunks': len([r for r in result if r.get('status') != 'error'])}

    def _payload_images(self, trigger, settings) -> list[dict]:
        """Cache only — process_batch runs ON the daemon loop (broadsword H5)."""
        lane = getattr(self, 'image_lane', None)
        return lane.payload_images(trigger, settings) if lane else []

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
        answers. In-memory, keyed by local day; other days are dropped on
        rollover so the map stays the size of today's DM senders."""
        budget = int(getattr(getattr(settings, 'safety', None), 'dm_daily_budget', 30) or 0)
        if budget <= 0:
            return True
        counts = getattr(self, '_dm_counts', None)
        if counts is None:
            counts = self._dm_counts = {}
        day = time.strftime('%Y-%m-%d')
        for key in [k for k, (d, _n) in counts.items() if d != day]:
            counts.pop(key, None)
        key = (str(trigger.account_name), str(trigger.author_id))
        _day, used = counts.get(key, (day, 0))
        if used >= budget:
            return False
        counts[key] = (day, used + 1)
        return True

    def _world_state_for(self, trigger, trigger_eval: dict) -> dict:
        """What the silent-reaction roll needs to know about this message."""
        return {
            'account_name': trigger.account_name,
            'channel_id': trigger.channel_id,
            'message_id': trigger.message_id,
            'mentioned': bool(trigger_eval.get('mentioned')),
            'name_matched': bool(trigger_eval.get('name_matched')),
            'respond_trigger': bool(trigger_eval.get('respond_trigger')),
            'reaction_multiplier': 1.0,
        }

    def _maybe_execute_silent_reaction(self, trigger, settings, world_state: dict, *, read_only: bool = False, reply_planned: bool = False) -> bool:
        if not self.reaction_service or not self.transport or not settings:
            return False
        intention = self.reaction_service.evaluate_silent(
            trigger,
            settings=settings,
            world_state=world_state,
            read_only=read_only,
            reply_planned=reply_planned,
        )
        if not intention:
            return False
        self.reaction_service.execute_silent(intention, transport=self.transport, settings=settings)
        if self.trace_service:
            self.trace_service.record_intention(intention.intention_type, {
                'channel_id': intention.channel_id,
                'reason': intention.reason,
                'emoji': intention.emoji,
            })
        return True
