"""[REGRESSION_GUARD] Voice privacy gates (vault v1.1, ruling D): a private
chat refuses cloud STT (voice in) and cloud TTS (speech out).

Failure posture is asymmetric BY DESIGN: unreadable chat settings fail OPEN
(systemic error — don't mute the app); absent/unreadable provider metadata in
a private chat fails CLOSED (an unlabeled provider is the attack surface).
Plan: tmp/vault-stt-gate-plan.md.
"""
import pytest

from core import voice_privacy as vp


class _FakeRegistry:
    def __init__(self, key='prov', entry=None, raise_on_entry=False):
        self._key = key
        self._entry = entry
        self._raise = raise_on_entry

    def get_active_key(self):
        return self._key

    def get_all(self):
        return []

    def get_entry(self, key):
        if self._raise:
            raise RuntimeError("registry exploded")
        return self._entry


def _gate(settings, registry):
    return vp._gate(settings, lambda: registry, 'STT', 'your voice audio')


class TestGateCore:
    def test_public_chat_passes_any_provider(self):
        assert _gate({'private_chat': False},
                     _FakeRegistry(entry={'is_local': False})) == ''
        assert _gate({}, _FakeRegistry(entry={})) == ''
        assert _gate(None, _FakeRegistry(entry={})) == ''

    def test_private_local_passes(self):
        assert _gate({'private_chat': True},
                     _FakeRegistry(entry={'is_local': True})) == ''

    def test_private_cloud_refused(self):
        reason = _gate({'private_chat': True},
                       _FakeRegistry(key='fireworks', entry={'is_local': False}))
        assert 'fireworks' in reason and 'not local' in reason

    def test_private_absent_metadata_refused(self):
        """Fail-closed: no is_local declaration = cloud."""
        assert _gate({'private_chat': True}, _FakeRegistry(entry={})) != ''

    def test_private_unknown_provider_refused(self):
        assert _gate({'private_chat': True}, _FakeRegistry(entry=None)) != ''

    def test_disabled_provider_passes(self):
        assert _gate({'private_chat': True}, _FakeRegistry(key='none')) == ''
        assert _gate({'private_chat': True}, _FakeRegistry(key='')) == ''

    def test_registry_error_fails_closed(self):
        reason = _gate({'private_chat': True},
                       _FakeRegistry(entry={'is_local': True}, raise_on_entry=True))
        assert 'fail-closed' in reason

    def test_settings_error_fails_open(self):
        class _Bad:
            def get(self, *a):
                raise RuntimeError("settings exploded")
        assert _gate(_Bad(), _FakeRegistry(entry={})) == ''


class TestRealRegistries:
    """The gates read the REAL registries' is_local metadata."""

    def test_stt_registry_labels(self):
        from core.stt.providers import stt_registry
        stt_registry.get_all()   # lazy core registration
        assert stt_registry.get_entry('faster_whisper').get('is_local') is True
        # Explicit since v1.1 — the gate reads this; implicit-False was sloppy
        assert stt_registry.get_entry('fireworks_whisper').get('is_local') is False
        assert stt_registry.get_entry('sapphire_router').get('is_local') is False
        assert stt_registry.get_entry('none').get('is_local') is True

    def test_stt_gate_reads_real_registry(self, monkeypatch):
        from core.stt.providers import stt_registry
        monkeypatch.setattr(stt_registry, 'get_active_key', lambda: 'fireworks_whisper')
        assert vp.stt_gate_reason({'private_chat': True}) != ''
        monkeypatch.setattr(stt_registry, 'get_active_key', lambda: 'faster_whisper')
        assert vp.stt_gate_reason({'private_chat': True}) == ''

    def test_tts_gate_reads_real_registry(self, monkeypatch):
        from core.tts.providers import tts_registry
        monkeypatch.setattr(tts_registry, 'get_active_key', lambda: 'kokoro')
        assert vp.tts_gate_reason({'private_chat': True}) == ''

    def test_tts_gate_no_system_fails_open(self, monkeypatch):
        """chat_settings=None resolution with no system up — don't mute."""
        import core.api_fastapi as af
        monkeypatch.setattr(af, 'get_system',
                            lambda: (_ for _ in ()).throw(RuntimeError()))
        assert vp.tts_gate_reason(None) == ''


class TestDoors:
    def test_transcribe_route_403(self, monkeypatch):
        import asyncio
        from fastapi import HTTPException
        import core.api_fastapi  # noqa: F401
        import core.routes.tts as tts_routes
        monkeypatch.setattr(tts_routes, 'check_endpoint_rate', lambda *a, **k: None)
        monkeypatch.setattr(tts_routes, 'can_transcribe', lambda c: (True, ''))
        monkeypatch.setattr(vp, 'stt_gate_reason', lambda s: 'blocked for test')

        class _SM:
            def get_chat_settings(self):
                return {'private_chat': True}

        class _Sys:
            llm_chat = type('L', (), {'session_manager': _SM()})()
            whisper_client = object()

        with pytest.raises(HTTPException) as ei:
            asyncio.run(tts_routes.handle_transcribe(None, audio=None,
                                                     _=None, system=_Sys()))
        assert ei.value.status_code == 403

    def test_conversation_turn_skipped(self, monkeypatch):
        """Gated driver turn returns before transcription (either leg —
        STT or TTS — kills the whole voice turn)."""
        from core.conversation.driver import ConversationDriver

        class _Boom(Exception):
            pass

        d = ConversationDriver.__new__(ConversationDriver)
        d._chat_name = 'call-chat'
        d._privacy_gate_logged = False

        class _SM:
            def get_settings_for(self, name):
                return {'private_chat': True}

        d.system = type('S', (), {'llm_chat': type('L', (), {'session_manager': _SM()})()})()
        d._transcribe_fn = lambda pcm: (_ for _ in ()).throw(_Boom())
        d._cue_fn = None
        monkeypatch.setattr(vp, 'stt_gate_reason', lambda s: 'blocked')
        d._run_turn(b'\x00\x00')   # returns silently — _Boom never raised
        assert d._privacy_gate_logged is True
        d._run_turn(b'\x00\x00')   # second turn: no double logging path blowup

    def test_tts_speak_skipped(self, monkeypatch):
        from core.tts.tts_client import TTSClient
        monkeypatch.setattr(vp, 'tts_gate_reason', lambda s=None: 'blocked')
        c = TTSClient.__new__(TTSClient)   # gate fires before any attr access
        assert c.speak('hello world, this is long enough') is False
        assert c.speak_sync('hello world, this is long enough') is False

    def test_tts_stream_pump_skipped(self, monkeypatch):
        """The streaming pump self-gates on FIRST PUSH (vaulted-chats Phase
        0): both its owners — the web-turn stream and /api/tts/stream — went
        straight to the provider before this. The gated turn emits one
        notice, then stays silent; synth is never reached."""
        from core.tts import stream_pump as sp
        monkeypatch.setattr(sp.config, 'TTS_ENABLED', True, raising=False)
        monkeypatch.setattr(sp.config, 'TTS_STREAMING_ENABLED', True, raising=False)
        monkeypatch.setattr(vp, 'tts_gate_reason', lambda s=None: 'blocked')

        class _Prov:
            supports_streaming = True

            def generate_stream(self, *a, **k):
                raise AssertionError("synth reached in a gated turn")

            def generate(self, *a, **k):
                raise AssertionError("synth reached in a gated turn")

        class _TTS:
            _provider = _Prov()
            voice_name = 'af_heart'
            speed = 1.0
            pitch_shift = 1.0

        pump = sp.StreamingTTSPump(system=type('S', (), {'tts': _TTS()})())
        out = pump.push('hello world')
        assert out and out[0]['type'] == 'notice'
        assert pump.push('more text') == []   # closed — stays silent

    def test_tts_file_mode_403(self, monkeypatch):
        """/api/tts output_mode='file' calls generate_audio_data directly —
        the browser's non-streaming TTS path ships chat response text through
        it, bypassing the speak() gate. 403, never 401."""
        import asyncio
        from fastapi import HTTPException
        import core.routes.tts as tts_routes
        monkeypatch.setattr(tts_routes, 'check_endpoint_rate', lambda *a, **k: None)
        monkeypatch.setattr(tts_routes.config, 'TTS_ENABLED', True, raising=False)
        monkeypatch.setattr(vp, 'tts_gate_reason', lambda s=None: 'blocked')

        class _Req:
            async def json(self):
                return {'text': 'hello world', 'output_mode': 'file'}

        with pytest.raises(HTTPException) as ei:
            asyncio.run(tts_routes.handle_tts_speak(_Req(), _=None, system=None))
        assert ei.value.status_code == 403
