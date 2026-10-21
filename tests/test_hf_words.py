"""core.hf_words: a Hugging Face hub failure in words a person can act on.
The hub wraps its own offline mode in LocalEntryNotFoundError ("check your
Internet connection"); a Windows user with HF_HUB_OFFLINE set found the real
cause only in a traceback (user report 2026-10-21)."""
import pytest
from huggingface_hub.errors import LocalEntryNotFoundError, OfflineModeIsEnabled

from core import hf_words


def _hub_offline_failure():
    try:
        try:
            raise OfflineModeIsEnabled("Cannot reach https://huggingface.co/x: offline mode is enabled.")
        except OfflineModeIsEnabled as cause:
            raise LocalEntryNotFoundError("An error happened ... make sure your Internet connection is on.") from cause
    except LocalEntryNotFoundError as e:
        return e


@pytest.fixture
def clean_env(monkeypatch):
    for k in hf_words.OFFLINE_VARS:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_offline_cause_names_the_variable_not_the_internet(clean_env):
    clean_env.setenv('HF_HUB_OFFLINE', '1')
    words = hf_words.why(_hub_offline_failure())
    assert 'offline mode is on' in words and 'HF_HUB_OFFLINE' in words and 'restart' in words
    assert 'Internet connection' not in words and 'TRANSFORMERS_OFFLINE' not in words


def test_names_every_offline_variable_that_is_set(clean_env):
    clean_env.setenv('TRANSFORMERS_OFFLINE', 'yes')
    assert hf_words.offline_vars() == ['TRANSFORMERS_OFFLINE']
    assert 'TRANSFORMERS_OFFLINE' in hf_words.why(_hub_offline_failure())
    clean_env.setenv('HF_HUB_OFFLINE', 'ON')
    assert hf_words.offline_vars() == ['HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE']
    clean_env.setenv('HF_HUB_OFFLINE', '0')        # off, as the hub reads it
    assert hf_words.offline_vars() == ['TRANSFORMERS_OFFLINE']


def test_bare_offline_error_and_unset_env_still_say_so(clean_env):
    words = hf_words.why(OfflineModeIsEnabled("offline"))
    assert 'offline mode is on' in words and 'HF_HUB_OFFLINE' in words


def test_other_failures_keep_their_own_words():
    assert hf_words.why(ConnectionError('no route to host')) == 'no route to host'
    assert hf_words.why(RuntimeError()) == 'RuntimeError'
