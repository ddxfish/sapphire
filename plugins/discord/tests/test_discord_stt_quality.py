from plugins.discord.sapphire.discord_stt_quality import (
    is_likely_decrypt_noise,
    is_short_clip_hallucination,
    reject_discord_transcript,
)


def test_decrypt_noise_detects_saturated_pcm():
    assert is_likely_decrypt_noise(peak=1.0, rms=0.28, duration=0.83) is True


def test_decrypt_noise_ignores_quiet_clipped():
    assert is_likely_decrypt_noise(peak=1.0, rms=0.01, duration=0.83) is False


def test_decrypt_noise_ignores_normal_speech():
    assert is_likely_decrypt_noise(peak=0.6, rms=0.2, duration=1.0) is False


def test_short_clip_hallucination_rejects_lets_do_it():
    assert is_short_clip_hallucination("Let's do it.", duration=0.84) is True
    assert is_short_clip_hallucination("Let's do it! 2, 3, 5...", duration=2.4) is True


def test_short_clip_hallucination_allows_longer_clips():
    assert is_short_clip_hallucination("Let's do it.", duration=3.0) is False


def test_reject_discord_transcript_short_clip():
    rejected, reason = reject_discord_transcript("Let's do it.", duration=0.84)
    assert rejected is True
    assert reason == 'short_clip_hallucination'


def test_reject_discord_transcript_garbage_tail():
    from plugins.discord.sapphire.discord_stt_quality import reject_discord_transcript

    rejected, reason = reject_discord_transcript('testing oom', duration=1.5)
    assert rejected is True
    assert reason == 'garbage_tail'


def test_segments_to_transcript_drops_low_confidence_tail():
    from plugins.discord.sapphire.discord_stt_quality import segments_to_transcript

    class Seg:
        def __init__(self, text, logprob):
            self.text = text
            self.no_speech_prob = 0.1
            self.avg_logprob = logprob

    text, kept = segments_to_transcript([
        Seg('testing', -0.3),
        Seg('oom', -0.95),
    ])
    assert text == 'testing'
    assert len(kept) == 1
