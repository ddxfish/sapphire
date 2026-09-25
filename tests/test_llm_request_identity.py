"""Request identity (2026-09-25): `extra_headers` on any custom provider, the
header-side twin of `extra_body`, with two placeholders filled per request —
{session} (stable per-conversation id, salted hash, never the chat name) and
{version}. The ONE resolver stamps provider.conversation; the Fireworks
host-sniff that hard-coded `user` retired onto the same rail. OFF path: a
provider with no extra_headers sends byte-identical requests.

Record: tmp/opencode-affinity-plan.md (VIP hit OpenCode Go's 400 MissingSessionID).
"""
import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

import core.api_fastapi  # noqa: F401 — settles the import order for route modules
import core.chat.llm_providers as lp
from core.chat.llm_providers import provider_registry
from core.chat.llm_providers import resolve as rz
from core.chat.llm_providers.base import SAPPHIRE_VERSION, fill_placeholders
from core.chat.llm_providers.openai_compat import OpenAICompatProvider
from core.chat.llm_providers.anthropic_compat import AnthropicCompatProvider
from core.chat.llm_providers.openai_responses import OpenAIResponsesProvider
from core.chat.llm_providers.resolve import resolve

HDRS = {"x-opencode-session": "{session}", "User-Agent": "sapphire/{version}"}


@pytest.fixture(autouse=True)
def _fresh_rejected_memory():
    from core.chat.llm_providers import openai_compat as oc
    oc._REJECTED_PARAMS.clear()
    yield
    oc._REJECTED_PARAMS.clear()


def _oai(**extra):
    return OpenAICompatProvider({"provider": "openai", "base_url": "http://t/v1",
                                 "api_key": "key-1", "model": "m", **extra})


def _wire(provider, sdk_cls, base_url, handler):
    """Swap the provider's SDK client for one on an httpx MockTransport — the
    REAL request bytes, not a mocked SDK method."""
    provider._client = sdk_cls(api_key="key-1", base_url=base_url,
                               http_client=httpx.Client(transport=httpx.MockTransport(handler)))


CHAT_JSON = {"id": "c", "object": "chat.completion", "created": 0, "model": "m",
             "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"},
                          "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
MSG_JSON = {"id": "m1", "type": "message", "role": "assistant", "model": "m",
            "content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}
MODELS_JSON = {"object": "list", "data": [{"id": "m", "object": "model", "created": 0, "owned_by": "t"}]}


# ── placeholders + session id ────────────────────────────────────────────────

def test_fill_placeholders_recursive_and_inert_on_unknowns():
    out = fill_placeholders({"a": "{session}", "b": ["x/{version}", 3], "c": "{other}", "d": None},
                            "S", "9.9")
    assert out == {"a": "S", "b": ["x/9.9", 3], "c": "{other}", "d": None}
    assert fill_placeholders(7, "S") == 7


def test_session_id_is_stable_per_conversation_salted_and_never_the_name():
    a = _oai(); a.conversation = "trinity"
    b = _oai(); b.conversation = "trinity"
    c = _oai(); c.conversation = "lookout"
    other_key = _oai(api_key="key-2"); other_key.conversation = "trinity"
    assert a.session_id() == b.session_id()                 # stable across per-turn rebuilds
    assert a.session_id() != c.session_id()                 # per conversation
    assert a.session_id() != other_key.session_id()         # salted by key (no cross-install collisions)
    assert "trinity" not in a.session_id() and a.session_id().startswith("sapphire-")
    none1, none2 = _oai(), _oai()
    assert none1.session_id() == none2.session_id()         # no conversation → stable fallback, still present


def test_request_headers_accepts_dict_or_json_string_and_fills():
    d = _oai(extra_headers=HDRS); d.conversation = "trinity"
    j = _oai(extra_headers=json.dumps(HDRS)); j.conversation = "trinity"
    assert d.request_headers() == j.request_headers()
    assert d.request_headers()["x-opencode-session"] == d.session_id()
    assert d.request_headers()["User-Agent"] == f"sapphire/{SAPPHIRE_VERSION}"
    assert SAPPHIRE_VERSION not in ("", "?")


@pytest.mark.parametrize("bad", ["not json", "[1,2]", 5, ""])
def test_request_headers_bad_config_is_empty_not_fatal(bad):
    assert _oai(extra_headers=bad).request_headers() == {}
    assert _oai(extra_headers=bad)._hdr_kwargs() == {}


# ── OpenAI-compat: real bytes ────────────────────────────────────────────────

def test_openai_compat_sends_headers_on_the_wire_and_ua_wins():
    seen = []
    def handler(req):
        seen.append(dict(req.headers)); return httpx.Response(200, json=CHAT_JSON)
    p = _oai(extra_headers=HDRS); p.conversation = "trinity"
    _wire(p, __import__("openai").OpenAI, "http://t/v1", handler)
    p.chat_completion([{"role": "user", "content": "hi"}], generation_params={"max_tokens": 16})
    p.chat_completion([{"role": "user", "content": "hi"}], generation_params={"max_tokens": 16})
    assert seen[0]["x-opencode-session"] == p.session_id() == seen[1]["x-opencode-session"]
    assert seen[0]["user-agent"] == f"sapphire/{SAPPHIRE_VERSION}"


def test_openai_compat_off_path_is_byte_identical():
    seen = {}
    def handler(req):
        seen.update(dict(req.headers)); seen["body"] = json.loads(req.content); return httpx.Response(200, json=CHAT_JSON)
    p = _oai(); p.conversation = "trinity"
    _wire(p, __import__("openai").OpenAI, "http://t/v1", handler)
    p.chat_completion([{"role": "user", "content": "hi"}], generation_params={"max_tokens": 16})
    assert "x-opencode-session" not in seen
    assert seen["user-agent"].startswith("OpenAI/Python")
    assert "user" not in seen["body"] and "extra_headers" not in seen["body"]


def test_openai_compat_health_and_discovery_carry_headers():
    seen = []
    def handler(req):
        seen.append(req.headers.get("x-opencode-session")); return httpx.Response(200, json=MODELS_JSON)
    p = _oai(extra_headers=HDRS); p.conversation = "trinity"
    _wire(p, __import__("openai").OpenAI, "http://t/v1", handler)
    assert p.health_check() is True and p.list_models() == [{"id": "m", "name": "m"}]
    assert seen == [p.session_id(), p.session_id()]


def test_openai_compat_stream_path_carries_headers():
    p = _oai(extra_headers=HDRS); p.conversation = "trinity"
    p._client = MagicMock()
    p._client.chat.completions.create.return_value = iter([])
    list(p.chat_completion_stream([{"role": "user", "content": "hi"}], generation_params={"max_tokens": 16}))
    kw = p._client.chat.completions.create.call_args.kwargs
    assert kw["extra_headers"]["x-opencode-session"] == p.session_id()


# ── extra_body rides the same rail; the Fireworks sniff is gone ──────────────

def test_extra_body_session_placeholder_fills_and_fireworks_sniff_is_gone():
    fw = OpenAICompatProvider({"provider": "openai", "base_url": "https://api.fireworks.ai/inference/v1",
                               "api_key": "k", "model": "accounts/fireworks/models/kimi-k3",
                               "extra_body": {"user": "{session}"}})
    fw.conversation = "trinity"
    fw._client = MagicMock(); fw._client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="OK", tool_calls=None), finish_reason="stop")], usage=None)
    fw.chat_completion([{"role": "user", "content": "hi"}], generation_params={"max_tokens": 16})
    kw = fw._client.chat.completions.create.call_args.kwargs
    assert kw["extra_body"]["user"] == fw.session_id() and "user" not in kw

    bare = OpenAICompatProvider({"provider": "openai", "base_url": "https://api.fireworks.ai/inference/v1",
                                 "api_key": "k", "model": "accounts/fireworks/models/kimi-k3"})
    bare._client = MagicMock(); bare._client.chat.completions.create.return_value = fw._client.chat.completions.create.return_value
    bare.chat_completion([{"role": "user", "content": "hi"}], generation_params={"max_tokens": 16})
    kw = bare._client.chat.completions.create.call_args.kwargs
    assert "user" not in kw and "extra_body" not in kw          # no config → no sniff, nothing injected
    assert not hasattr(bare, "_fireworks_session_id")


# ── Anthropic-compat + Responses ─────────────────────────────────────────────

def test_anthropic_compat_sends_headers_on_the_wire():
    seen = []
    def handler(req):
        seen.append(dict(req.headers)); return httpx.Response(200, json=MSG_JSON)
    p = AnthropicCompatProvider({"provider": "anthropic", "base_url": "http://t", "api_key": "key-1",
                                 "model": "m", "extra_headers": HDRS})
    p.conversation = "trinity"
    _wire(p, __import__("anthropic").Anthropic, "http://t", handler)
    p.chat_completion([{"role": "user", "content": "hi"}], generation_params={"max_tokens": 16})
    assert p.health_check() is True
    assert [h["x-opencode-session"] for h in seen] == [p.session_id()] * 2
    assert seen[0]["user-agent"] == f"sapphire/{SAPPHIRE_VERSION}"


def test_anthropic_compat_stream_path_carries_headers():
    p = AnthropicCompatProvider({"provider": "anthropic", "base_url": "http://t", "api_key": "key-1",
                                 "model": "m", "extra_headers": HDRS})
    p.conversation = "trinity"
    p._client = MagicMock()
    ctx = MagicMock(); ctx.__enter__ = MagicMock(return_value=iter([])); ctx.__exit__ = MagicMock(return_value=False)
    p._client.messages.stream.return_value = ctx
    list(p.chat_completion_stream([{"role": "user", "content": "hi"}], generation_params={"max_tokens": 16}))
    assert p._client.messages.stream.call_args.kwargs["extra_headers"]["x-opencode-session"] == p.session_id()


def test_responses_provider_carries_headers_on_both_paths_and_health():
    p = OpenAIResponsesProvider({"provider": "openai_responses", "base_url": "http://t/v1",
                                 "api_key": "key-1", "model": "gpt-5.5", "extra_headers": HDRS})
    p.conversation = "trinity"
    seen = []
    def handler(req):
        seen.append(req.headers.get("x-opencode-session")); return httpx.Response(200, json=MODELS_JSON)
    _wire(p, __import__("openai").OpenAI, "http://t/v1", handler)
    assert p.health_check() is True and seen == [p.session_id()]
    p._client = MagicMock()
    p._client.responses.create.return_value = MagicMock(output=[], usage=None, status="completed")
    with patch.object(p, "_parse_response", return_value=MagicMock()):
        p.chat_completion([{"role": "user", "content": "hi"}], generation_params={"max_tokens": 16})
    assert p._client.responses.create.call_args.kwargs["extra_headers"]["x-opencode-session"] == p.session_id()
    p._client.responses.create.return_value = iter([])
    list(p.chat_completion_stream([{"role": "user", "content": "hi"}], generation_params={"max_tokens": 16}))
    assert p._client.responses.create.call_args.kwargs["extra_headers"]["x-opencode-session"] == p.session_id()


# ── the ONE resolver stamps the conversation; every lane hands it in ────────

@pytest.fixture
def _cfg(monkeypatch):
    rz.clear_health_cache()
    import config
    monkeypatch.setattr(config, 'LLM_PROVIDERS', {'claude': {'enabled': True}}, raising=False)
    monkeypatch.setattr(config, 'LLM_CUSTOM_PROVIDERS', {'lanbox': {'enabled': True, 'is_local': True}}, raising=False)
    monkeypatch.setattr(config, 'LLM_FALLBACK_ORDER', ['claude', 'lanbox'], raising=False)
    yield
    rz.clear_health_cache()


def test_resolver_stamps_conversation_pinned_and_auto(_cfg, monkeypatch):
    pinned = MagicMock(); pinned.health_check.return_value = True
    monkeypatch.setattr(lp, 'get_provider_by_key', MagicMock(return_value=pinned))
    assert resolve('lanbox', conversation='trinity', health='skip').provider.conversation == 'trinity'
    assert resolve('lanbox', health='skip').provider.conversation is None            # '' / None → fallback id
    auto = MagicMock()
    monkeypatch.setattr(lp, 'get_first_available_provider', MagicMock(return_value=('claude', auto)))
    assert resolve('auto', conversation='lookout', health='skip').provider.conversation == 'lookout'


def test_chat_lane_hands_in_the_effective_chat(monkeypatch):
    from core.chat.chat import LLMChat
    chat = LLMChat.__new__(LLMChat)
    chat.session_manager = MagicMock()
    chat.session_manager.get_chat_settings.return_value = {'llm_primary': 'lanbox'}
    chat.session_manager._effective_chat_name.return_value = 'phone-call'
    fake = MagicMock()
    monkeypatch.setattr(rz, 'resolve', fake)
    chat._select_provider()
    assert fake.call_args.kwargs['conversation'] == 'phone-call'


def test_continuity_lane_hands_in_the_target_chat(monkeypatch):
    from core.continuity.execution_context import ExecutionContext
    ctx = ExecutionContext.__new__(ExecutionContext)
    ctx.task_settings = {'provider': 'lanbox', 'chat_target': 'trinity'}
    ctx._prompt_privacy_required = False
    fake = MagicMock()
    monkeypatch.setattr(rz, 'resolve', fake)
    ctx._resolve_provider()
    assert fake.call_args.kwargs['conversation'] == 'trinity'
    ctx.task_settings = {'provider': 'lanbox', 'chat_target': ''}
    ctx._resolve_provider()
    assert fake.call_args.kwargs['conversation'] is None


def test_compress_lane_hands_in_the_chat(monkeypatch):
    from core.chat import compress
    fake = MagicMock()
    monkeypatch.setattr(rz, 'resolve', fake)
    compress.make_provider('lanbox', '', conversation='trinity')
    assert fake.call_args.kwargs['conversation'] == 'trinity'


# ── config plumbing: registry, add route, migration, presets ────────────────

def test_registry_carries_extra_headers_and_dropped_the_dead_hint():
    cfg = {'x': {'enabled': True, 'template': 'openai', 'base_url': 'http://t/v1',
                 'api_key': 'k', 'model': 'm', 'extra_headers': HDRS}}
    p = provider_registry.get_provider_by_key('x', cfg)
    assert p.config['extra_headers'] == HDRS
    assert 'session_affinity' not in p.config


def test_add_route_accepts_extra_headers(client, monkeypatch):
    from core.settings_manager import settings as live_settings
    c, csrf = client
    captured = {}
    monkeypatch.setattr(live_settings, 'get',
                        lambda key, default=None: {} if key == 'LLM_CUSTOM_PROVIDERS' else (default if default is not None else []))
    monkeypatch.setattr(live_settings, 'set',
                        lambda key, val, persist=True: captured.__setitem__(key, val))
    r = c.post('/api/llm/custom-providers', headers={'X-CSRF-Token': csrf},
               json={'name': 'gobox', 'template': 'openai', 'base_url': 'https://opencode.ai/zen/go/v1',
                     'extra_headers': HDRS, 'session_affinity': True})
    assert r.status_code == 200, r.text
    saved = captured['LLM_CUSTOM_PROVIDERS']['gobox']
    assert saved['extra_headers'] == HDRS
    assert 'session_affinity' not in saved


def _sm(user):
    from core.settings_manager import SettingsManager
    sm = SettingsManager.__new__(SettingsManager)
    sm._user = user
    sm.save = MagicMock()
    return sm


def test_fireworks_migration_adds_user_once_and_leaves_others_alone():
    user = {'LLM_CUSTOM_PROVIDERS': {
        'fw': {'base_url': 'https://api.fireworks.ai/inference/v1'},
        'fw_str': {'base_url': 'https://api.fireworks.ai/inference/v1', 'extra_body': '{"top_k": 5}'},
        'fw_mine': {'base_url': 'https://api.fireworks.ai/inference/v1', 'extra_body': {'user': 'me'}},
        'fw_broken': {'base_url': 'https://api.fireworks.ai/inference/v1', 'extra_body': 'not json'},
        'other': {'base_url': 'https://openrouter.ai/api/v1'},
        'junk': 'not-a-dict'}}
    sm = _sm(user)
    sm._migrate_fireworks_affinity()
    c = user['LLM_CUSTOM_PROVIDERS']
    assert c['fw']['extra_body'] == {'user': '{session}'}
    assert json.loads(c['fw_str']['extra_body']) == {'top_k': 5, 'user': '{session}'}   # string stays a string
    assert c['fw_mine']['extra_body'] == {'user': 'me'}                                 # hand-typed wins
    assert c['fw_broken']['extra_body'] == 'not json'                                   # untouched
    assert 'extra_body' not in c['other']
    assert sm.save.call_count == 1
    sm.save.reset_mock()
    sm._migrate_fireworks_affinity()                                                    # idempotent: no second save
    assert sm.save.call_count == 0


def test_migration_is_a_noop_without_custom_providers():
    sm = _sm({})
    sm._migrate_fireworks_affinity()
    assert sm.save.call_count == 0


def test_presets_carry_the_affinity_hints():
    presets = provider_registry.get_presets()
    for key, hdr in (('opencode_go', 'x-opencode-session'), ('opencode_zen', 'x-opencode-session')):
        p = presets[key]
        assert p['template'] in provider_registry._classes
        assert p['config_hints']['extra_headers'][hdr] == '{session}'
        assert p['config_hints']['extra_headers']['User-Agent'] == 'sapphire/{version}'
        assert p['base_url'].startswith('https://opencode.ai/zen/')
    assert presets['opencode_go']['base_url'] != presets['opencode_zen']['base_url']
    assert presets['fireworks']['config_hints']['extra_body'] == {'user': '{session}'}
