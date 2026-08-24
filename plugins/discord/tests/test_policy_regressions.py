from plugins.discord.cognition.policy_service import PolicyService
from plugins.discord.conversation.meme_service import MemeService
from plugins.discord.conversation.reply_style_service import ReplyStyleService
from plugins.discord.models.intentions import ReplyMessageIntention, SpeakVoiceIntention
from plugins.discord.models.settings import EffectiveSettings, MediaSettings, SafetySettings, VoiceSettings


def test_hostile_low_fondness_blocks_meme():
    service = MemeService()
    settings = EffectiveSettings(media=MediaSettings(meme_enabled=True))
    decision = service.evaluate_policy(settings, relationship={'fondness': 0.1, 'irritability': 0.2})
    assert decision['allowed'] is False


def test_high_irritability_blocks_voice_speak():
    policy = PolicyService()
    settings = EffectiveSettings(
        voice=VoiceSettings(enabled=True, speaking_enabled=True, mode='conversational'),
    )
    intention = SpeakVoiceIntention(
        intention_type='speak_voice',
        account_name='alpha',
        channel_id='vc1',
        message_id='',
        reason='approved_reply',
        text='hello',
    )
    decision = policy.evaluate_voice_speak(intention, settings)
    assert decision['allowed'] is True
    settings.voice.emergency_disabled = True
    assert policy.evaluate_voice_speak(intention, settings)['allowed'] is False


def test_no_double_send_when_tool_already_replied():
    style = ReplyStyleService()
    style.mark_tool_sent('m1', 'already sent')
    assert style.should_skip_auto_reply('m1') is True


def test_bot_username_no_longer_filtered_by_policy():
    policy = PolicyService()
    observation = type('O', (), {
        'author_id': 'u1',
        'username': 'helper-bot',
        'author_is_bot': True,
        'account_name': 'alpha',
        'channel_id': 'c1',
    })()
    assert policy.evaluate_text_observation(observation)['allowed'] is True


def test_scheduled_task_follow_up_bypasses_proactive_cooldown():
    policy = PolicyService()
    settings = EffectiveSettings(safety=SafetySettings(proactive_cooldown_hours=6))
    channel_id = 'c1'
    normal = ReplyMessageIntention(
        intention_type='reply_message',
        account_name='alpha',
        channel_id=channel_id,
        message_id='',
        reason='wake_reply',
        prompt='wake up',
    )
    assert policy.evaluate_proactive_intention(normal, settings)['allowed'] is True
    blocked = ReplyMessageIntention(
        intention_type='reply_message',
        account_name='alpha',
        channel_id=channel_id,
        message_id='',
        reason='wake_reply',
        prompt='again',
    )
    assert policy.evaluate_proactive_intention(blocked, settings)['allowed'] is False
    reminder = ReplyMessageIntention(
        intention_type='reply_message',
        account_name='alpha',
        channel_id=channel_id,
        message_id='',
        reason='task:reminder_follow_up',
        prompt='drink water',
        metadata={'task_id': 9},
    )
    assert policy.evaluate_proactive_intention(reminder, settings)['allowed'] is True
