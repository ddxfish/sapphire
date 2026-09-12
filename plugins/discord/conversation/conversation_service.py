from __future__ import annotations

import time

from plugins.discord.conversation.trigger_service import evaluate_reply_trigger
from plugins.discord.conversation.name_match import bot_names_for_account
from plugins.discord.conversation.gif_service import build_gif_reply_hint
from plugins.discord.conversation.media_prompt import build_reply_content
from plugins.discord.conversation.typing_indicator import (
    human_pause_seconds,
    inter_chunk_pause_seconds,
    read_delay_seconds,
    typing_duration_seconds,
)

from plugins.discord.conversation.post_reply_tags import deliver_gif_and_reaction
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
        reply_style_service=None,
        delivery_style_service=None,
        edit_history_service=None,
        transport=None,
        profile_service=None,
        profile_distill_service=None,
        attention_service=None,
        gif_service=None,
        reaction_service=None,
        settings_store=None,
        trace_service=None,
        cognitive_orchestrator=None,
        world_state_builder=None,
        account_repository=None,
        sleep_service=None,
        bot_session_service=None,
        mention_map_service=None,
    ):
        self.event_bridge = event_bridge
        self.policy_service = policy_service
        self.prompt_context_service = prompt_context_service
        self.trace_repository = trace_repository
        self.reply_style_service = reply_style_service
        self.delivery_style_service = delivery_style_service
        self.edit_history_service = edit_history_service
        self.transport = transport
        self.profile_service = profile_service
        self.profile_distill_service = profile_distill_service
        self.attention_service = attention_service
        self.gif_service = gif_service
        self.reaction_service = reaction_service
        self.settings_store = settings_store
        self.trace_service = trace_service
        self.cognitive_orchestrator = cognitive_orchestrator
        self.world_state_builder = world_state_builder
        self.account_repository = account_repository
        self.sleep_service = sleep_service
        self.bot_session_service = bot_session_service
        self.mention_map_service = mention_map_service
        self._pending: dict[str, dict] = {}

    def process_batch(self, batch) -> bool:
        trigger = batch.observations[-1]
        settings = self.settings_store.resolve(
            guild_id=trigger.guild_id,
            channel_id=trigger.channel_id,
            dm_id=trigger.channel_id if trigger.is_dm else None,
        ) if self.settings_store else None
        trigger_eval = evaluate_reply_trigger(
            trigger,
            settings,
            transport=self.transport,
            account_repository=self.account_repository,
        )
        trigger.name_matched = trigger_eval['name_matched']
        if self.sleep_service and settings:
            sleep_gate = self.sleep_service.evaluate_reply_gate(
                trigger,
                settings,
                respond_trigger=bool(trigger_eval['respond_trigger']),
                mentioned=bool(trigger_eval['mentioned']),
            )
            if not sleep_gate.get('allow'):
                if self.trace_service:
                    self.trace_service.record_policy_rejection(sleep_gate.get('reason', 'sleep'), {
                        'channel_id': trigger.channel_id,
                        'message_id': trigger.message_id,
                    })
                self.trace_repository.record_trace('event_dropped', 'Sleep schedule blocked reply', sleep_gate)
                return False
            sleep_wake_hint = sleep_gate.get('hint')
        else:
            sleep_wake_hint = None
        if self.attention_service:
            self.attention_service.apply_signal('channel', trigger.channel_id, trigger.account_name, boost=0.2, reason='message')
            self.attention_service.apply_signal('user', trigger.author_id, trigger.account_name, boost=0.2, reason='message')
            if trigger_eval['respond_trigger']:
                reason = 'mentioned' if trigger.mentioned else 'name_matched'
                self.attention_service.apply_signal('user', trigger.author_id, trigger.account_name, boost=0.5, reason=reason)
        if self.profile_service:
            self.profile_service.record_interaction(trigger.account_name, trigger.author_id, username=trigger.username)
            self.profile_service.buffer_message(trigger.account_name, trigger.author_id, trigger.clean_content)
            if trigger_eval['respond_trigger']:
                self.profile_service.adjust_affect(trigger.account_name, sociability=0.02, energy=-0.01)
                if self.trace_service:
                    self.trace_service.record_affect_modulation({
                        'account_name': trigger.account_name,
                        'sociability_delta': 0.02,
                        'energy_delta': -0.01,
                        'reason': 'mentioned' if trigger.mentioned else 'name_matched',
                    })
            if self.profile_distill_service:
                self.profile_distill_service.run_pending(trigger.account_name, trigger.author_id)
        world_state = self._world_state_for(trigger, trigger_eval)
        self._maybe_execute_silent_reaction(trigger, settings, world_state, read_only=False)
        if not trigger_eval['allowed']:
            self._maybe_execute_silent_reaction(trigger, settings, world_state, read_only=True)
            if self.trace_service:
                self.trace_service.record_policy_rejection(trigger_eval['reason'], {
                    'channel_id': trigger.channel_id,
                    'message_id': trigger.message_id,
                })
            self.trace_repository.record_trace('event_dropped', 'Reply trigger blocked message', trigger_eval)
            return False
        if self.bot_session_service and settings:
            channel_settings = getattr(settings, 'channel', None)
            name_match_enabled = bool(getattr(channel_settings, 'name_match_enabled', False))
            bot_names = bot_names_for_account(
                trigger.account_name,
                transport=self.transport,
                account_repository=self.account_repository,
            )
            bot_decision = self.bot_session_service.evaluate(
                trigger,
                settings,
                respond_trigger=bool(trigger_eval['respond_trigger']),
                bot_names=bot_names,
                name_match_enabled=name_match_enabled,
            )
            if not bot_decision.get('allowed'):
                if self.trace_service:
                    self.trace_service.record_policy_rejection(bot_decision.get('reason', 'bot_blocked'), {
                        'channel_id': trigger.channel_id,
                        'message_id': trigger.message_id,
                        'author_id': trigger.author_id,
                    })
                self.trace_repository.record_trace('event_dropped', 'Bot session gate blocked message', bot_decision)
                return False
        decision = self.policy_service.evaluate_text_observation(trigger, settings)
        if not decision.get('allowed'):
            if self.trace_service:
                self.trace_service.record_policy_rejection(decision.get('reason', 'denied'), {
                    'channel_id': trigger.channel_id,
                    'message_id': trigger.message_id,
                })
            self.trace_repository.record_trace('event_dropped', 'Policy rejected observation', decision)
            return False
        context = self.prompt_context_service.build(batch)
        reply_content = build_reply_content(trigger.clean_content, context.get('media') or [])
        mention_map = {}
        if self.mention_map_service:
            mention_map = self.mention_map_service.build_for_channel(
                trigger.account_name,
                trigger.channel_id,
                author_id=trigger.author_id,
                username=trigger.username,
                display_name=trigger.display_name,
            )
        if self.cognitive_orchestrator and settings:
            intentions = self.cognitive_orchestrator.evaluate_message_batch(batch, settings)
            if not intentions:
                self._maybe_execute_silent_reaction(trigger, settings, world_state, read_only=True)
                self.trace_repository.record_trace('event_dropped', 'No cognitive intention generated', {
                    'message_id': trigger.message_id,
                })
                return False
            intention = intentions[0]
            intention.prompt = reply_content
            intention.metadata = {**(intention.metadata or {}), 'context': context, 'batch_size': batch.message_count}
        else:
            intention = ReplyMessageIntention(
                intention_type='reply_message',
                account_name=trigger.account_name,
                channel_id=trigger.channel_id,
                message_id=trigger.message_id,
                reason='batched_message',
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
        }
        hints = []
        follow_up_hints = list(getattr(trigger, 'follow_up_hints', []) or [])
        if self.mention_map_service:
            hints.append(self.mention_map_service.mention_format_hint())
        if sleep_wake_hint:
            hints.append(sleep_wake_hint)
        if settings:
            gif_hint = build_gif_reply_hint(settings)
            if gif_hint:
                hints.append(gif_hint)
        hints.extend(follow_up_hints)
        if hints:
            payload['reply_hints'] = hints
            payload['reply_instructions'] = '\n\n'.join(hints)
        if follow_up_hints:
            payload['plugin_scheduled'] = 'true'
        if self.edit_history_service:
            edit_hint = self.edit_history_service.build_prompt_hint(trigger.account_name, trigger.channel_id)
            if edit_hint:
                hints = list(payload.get('reply_hints') or [])
                hints.append(edit_hint)
                payload['reply_hints'] = hints
                payload['reply_instructions'] = '\n\n'.join(hints)
        if settings:
            from plugins.discord.sapphire.llm_settings import llm_event_fields

            payload.update(llm_event_fields(settings))
        accepted = self.event_bridge.emit_discord_message(payload)
        if not accepted:
            self._maybe_execute_silent_reaction(trigger, settings, world_state, read_only=True)
            self.trace_repository.record_trace('event_dropped', 'No Sapphire task accepted event', {'message_id': trigger.message_id})
            return False
        self._pending[trigger.message_id] = {
            'channel_id': trigger.channel_id,
            'account_name': trigger.account_name,
            'payload': payload,
        }
        self.trace_repository.record_trace('event_emitted', 'Queued Discord message event', {'message_id': trigger.message_id})
        return True

    def queue_slash_command(self, command_name: str, content: str, context: dict) -> ReplyMessageIntention:
        return ReplyMessageIntention(
            intention_type='reply_message',
            account_name=context['account_name'],
            channel_id=context['channel_id'],
            message_id=context['message_id'],
            reason=f'slash:{command_name}',
            prompt=content or f'/{command_name}',
            metadata={'slash_command': command_name},
        )

    def pending_reply(self, message_id: str) -> dict | None:
        return self._pending.get(message_id)

    def handle_llm_response(self, task, event_data: dict, response_text: str):
        if not self.reply_style_service or not self.transport:
            return None
        message_id = str((event_data or {}).get('message_id', ''))
        account_name = str((event_data or {}).get('account', '') or '')
        channel_id = str((event_data or {}).get('channel_id', '') or '')
        guild_id = str((event_data or {}).get('guild_id', '') or '')
        settings = self.settings_store.resolve(
            guild_id=guild_id,
            channel_id=channel_id,
            dm_id=channel_id if str(event_data.get('is_dm', '')).lower() in {'true', '1'} else None,
        ) if self.settings_store else None
        delivery = settings.channel if settings else None
        strip_thinking = delivery.strip_think_tags if delivery else True
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
                    trigger_message_id=message_id,
                )
            self.trace_repository.record_trace('delivery_skipped', 'Tool already sent reply', {'message_id': message_id})
            self._pending.pop(message_id, None)
            self._complete_task_follow_up_if_needed(event_data)
            return {'status': 'skipped'}
        typing_enabled = delivery.typing_indicator_enabled if delivery else True
        human_pause_enabled = delivery.human_pause_enabled if delivery else True
        read_delay_enabled = delivery.read_delay_enabled if delivery else True

        if read_delay_enabled:
            time.sleep(read_delay_seconds(len(str((event_data or {}).get('content', '')))))

        parsed = self.reply_style_service.parse_llm_output(response_text, strip_thinking=strip_thinking)
        if not parsed.chunks:
            self.trace_repository.record_trace('delivery_empty', 'No visible content after reply parsing', {
                'message_id': message_id,
                'strip_thinking': strip_thinking,
            })
            self._pending.pop(message_id, None)
            return {'status': 'empty'}

        trigger_content = str((event_data or {}).get('content', ''))
        if self.delivery_style_service:
            plan = self.delivery_style_service.plan_delivery(
                parsed=parsed,
                raw_text=response_text or '',
                event_data=event_data or {},
                settings=settings,
                trigger_content=trigger_content,
            )
            chunks = plan.chunks
            reply_to_default = plan.reply_to_message_id
            edit_plan = plan
        else:
            chunks = parsed.chunks
            reply_to_default = None
            edit_plan = None

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
            reply_to = None
            if index == 0:
                reply_to = reply_to_default
                if reply_to is None:
                    raw_reply_to = str(event_data.get('reply_to_message_id') or '').strip()
                    if not raw_reply_to and message_id and not str(message_id).startswith('task-followup-'):
                        raw_reply_to = message_id
                    reply_to = raw_reply_to or None
            send_result = self.transport.send_message_sync(
                channel_id,
                chunk,
                reply_to_message_id=reply_to,
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
            if self.bot_session_service and send_result.get('messages'):
                last_sent = send_result['messages'][-1]
                self.bot_session_service.record_sent_message(
                    account_name,
                    channel_id,
                    str(last_sent.get('message_id', '')),
                )

        if edit_plan and edit_plan.edit_text and sent_message_ids and self.transport:
            edit_index = min(edit_plan.edit_chunk_index, len(sent_message_ids) - 1)
            target_message_id = sent_message_ids[edit_index]
            original_text = chunks[edit_index] if edit_index < len(chunks) else ''
            if edit_plan.edit_delay > 0:
                time.sleep(edit_plan.edit_delay)
            edit_result = self.transport.edit_message_sync(
                channel_id,
                target_message_id,
                edit_plan.edit_text,
                account_name=account_name or None,
            )
            if edit_result.get('status') != 'error' and self.edit_history_service:
                self.edit_history_service.record(
                    account_name,
                    channel_id,
                    message_id=target_message_id,
                    before=original_text,
                    after=edit_plan.edit_text,
                    kind='auto_typo' if original_text != edit_plan.edit_text else 'edit',
                )
                self.trace_repository.record_trace('delivery_edit', 'Applied post-send message edit', {
                    'message_id': target_message_id,
                    'channel_id': channel_id,
                })
        reaction = parsed.reaction
        if self.reaction_service:
            reaction = self.reaction_service.maybe_react(parsed) or reaction
        if reaction:
            self.transport.add_reaction_sync(channel_id, message_id, reaction, account_name=account_name or None)
        from plugins.discord.daemon import get_runtime

        deliver_gif_and_reaction(
            runtime=get_runtime(),
            parsed=parsed,
            message_id=message_id,
            channel_id=channel_id,
            account_name=account_name,
            settings=settings,
            trigger_message_id=message_id,
        )
        self._pending.pop(message_id, None)
        self._complete_task_follow_up_if_needed(event_data)
        self.trace_repository.record_trace('delivery_sent', 'Delivered LLM reply to Discord', {
            'message_id': message_id,
            'channel_id': channel_id,
            'chunks': len(result),
        })
        return {'status': 'sent', 'chunks': len(result)}

    def _complete_task_follow_up_if_needed(self, event_data: dict | None) -> None:
        if not event_data or not self.cognitive_orchestrator:
            return
        if str(event_data.get('task_follow_up', '')).lower() not in {'true', '1'}:
            return
        task_id = event_data.get('task_id')
        if not task_id:
            message_id = str(event_data.get('message_id', ''))
            if message_id.startswith('task-followup-'):
                task_id = message_id.rsplit('-', 1)[-1]
        if not task_id:
            return
        try:
            self.cognitive_orchestrator.complete_task(int(task_id))
        except (TypeError, ValueError):
            return

    def _world_state_for(self, trigger, trigger_eval: dict) -> dict:
        if self.world_state_builder:
            trigger.name_matched = bool(trigger_eval.get('name_matched'))
            state = self.world_state_builder.from_observation(trigger)
            state['respond_trigger'] = bool(trigger_eval.get('respond_trigger'))
            return state
        return {
            'account_name': trigger.account_name,
            'channel_id': trigger.channel_id,
            'message_id': trigger.message_id,
            'mentioned': bool(trigger_eval.get('mentioned')),
            'name_matched': bool(trigger_eval.get('name_matched')),
            'respond_trigger': bool(trigger_eval.get('respond_trigger')),
        }

    def _maybe_execute_silent_reaction(self, trigger, settings, world_state: dict, *, read_only: bool = False) -> bool:
        if not self.reaction_service or not self.transport or not settings:
            return False
        if self.sleep_service and self.sleep_service.should_drop_observation(trigger, settings):
            return False
        intention = self.reaction_service.evaluate_silent(
            trigger,
            settings=settings,
            world_state=world_state,
            read_only=read_only,
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
