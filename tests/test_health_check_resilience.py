"""LLM health-check resilience (2026-06-21).

A reachability probe (`models.list()`) must treat ANY HTTP status response
(4xx/5xx) as "server reachable" — a broken /models endpoint (e.g. Fireworks
returning 500 "Error listing deployed models") does NOT mean completions are
down, and the old code hard-failed chat on it. Only a genuine connection/timeout
error marks a provider unhealthy. Also: the health-check timeout default bumped
0.5s -> 3.0s (0.5s was too tight for cloud endpoints).
"""
from unittest.mock import MagicMock

import httpx
import openai
import pytest

from core.chat.llm_providers.base import server_answered
from core.chat.llm_providers.openai_compat import OpenAICompatProvider
from core.chat.llm_providers.openai_responses import OpenAIResponsesProvider

_REQ = httpx.Request("GET", "https://api.fireworks.ai/inference/v1/models")


def _status_err(cls, code):
    return cls("boom", response=httpx.Response(code, request=_REQ), body=None)


def _conn_err():
    return openai.APIConnectionError(request=_REQ)


def _timeout_err():
    return openai.APITimeoutError(request=_REQ)


# ─── server_answered classifier ─────────────────────────────────────────────

def test_server_answered_true_on_500():
    assert server_answered(_status_err(openai.InternalServerError, 500)) is True


def test_server_answered_true_on_401_and_429():
    assert server_answered(_status_err(openai.AuthenticationError, 401)) is True
    assert server_answered(_status_err(openai.RateLimitError, 429)) is True


def test_server_answered_false_on_connection_and_timeout():
    assert server_answered(_conn_err()) is False
    assert server_answered(_timeout_err()) is False


def test_server_answered_false_on_plain_exception():
    assert server_answered(ValueError("nope")) is False


# ─── openai_compat.health_check ─────────────────────────────────────────────

def _compat(base_url="https://api.fireworks.ai/inference/v1"):
    p = OpenAICompatProvider({"base_url": base_url, "api_key": "k", "model": "glm"}, 30.0)
    p._client = MagicMock()
    return p


def test_compat_health_500_is_reachable():
    """The Fireworks outage: /models 500 must NOT mark the provider dead."""
    p = _compat()
    p._client.models.list.side_effect = _status_err(openai.InternalServerError, 500)
    assert p.health_check() is True


def test_compat_health_401_429_reachable():
    p = _compat()
    p._client.models.list.side_effect = _status_err(openai.RateLimitError, 429)
    assert p.health_check() is True


def test_compat_health_connection_error_unhealthy():
    # base_url already ends in /v1 → no auto-correct retry (no real network call)
    p = _compat()
    p._client.models.list.side_effect = _conn_err()
    assert p.health_check() is False


def test_compat_health_ok_when_models_list_succeeds():
    p = _compat()
    p._client.models.list.return_value = MagicMock(data=[])
    assert p.health_check() is True


# ─── openai_responses.health_check (same lenient logic) ─────────────────────

def test_responses_health_500_is_reachable():
    p = OpenAIResponsesProvider({"base_url": "https://api.openai.com/v1", "api_key": "k", "model": "gpt-5"}, 30.0)
    p._client = MagicMock()
    p._client.models.list.side_effect = _status_err(openai.InternalServerError, 500)
    assert p.health_check() is True


def test_responses_health_connection_error_unhealthy():
    p = OpenAIResponsesProvider({"base_url": "https://api.openai.com/v1", "api_key": "k", "model": "gpt-5"}, 30.0)
    p._client = MagicMock()
    p._client.models.list.side_effect = _conn_err()
    assert p.health_check() is False


# ─── timeout default bump ───────────────────────────────────────────────────

def test_health_timeout_default_is_3s():
    p = OpenAICompatProvider({"base_url": "http://x/v1", "api_key": "k", "model": "m"}, 30.0)
    assert p.health_check_timeout == 3.0


def test_health_timeout_respects_explicit_config():
    p = OpenAICompatProvider({"base_url": "http://x/v1", "api_key": "k", "model": "m", "timeout": 1.5}, 30.0)
    assert p.health_check_timeout == 1.5
