"""core/chat/llm_errors.py — every provider failure becomes plain words, a Fix
It link and a Details fold; a failed turn's row never reaches the model."""
import httpx
import openai
import pytest

from core.chat.llm_errors import explain, history_row, is_error_row, LLM_TAB
from core.chat.chat import friendly_llm_error, fallback_error_text
from core.chat.history import ConversationHistory

LMS = 'http://127.0.0.1:1234/v1/chat/completions'


def _req(url=LMS):
    return httpx.Request('POST', url)


def _status_err(cls, code, body, url=LMS):
    return cls(f"Error code: {code} - {body}", response=httpx.Response(code, request=_req(url)), body=body)


def test_lm_studio_with_no_model_loaded_says_load_one():
    e = _status_err(openai.BadRequestError, 400, {'error': "No models loaded. Please load a model in the "
                                                           "developer page or use the 'lms load' command."})
    w = explain(e)
    assert w['text'] == "LM Studio has no model loaded. Open LM Studio, load a model, then send again."
    assert 'BadRequestError' in w['detail'] and 'lms load' in w['detail']    # the raw rides the fold


def test_lm_studio_not_running_names_lm_studio():
    # the SDK's text is just "Connection error." - no host to find in it
    w = explain(openai.APIConnectionError(request=_req()))
    assert w['text'].startswith("Can't reach LM Studio.")


def test_remote_unreachable_points_at_the_base_url():
    w = explain(openai.APIConnectionError(request=_req('https://api.example.com/v1/chat')))
    assert "Can't reach" in w['text'] and 'Base URL in Settings › LLM' in w['text']
    assert w['go'] == LLM_TAB


def test_timeout_is_not_called_a_lost_connection():
    w = explain(openai.APITimeoutError(request=_req()))     # a connection-error subclass
    assert 'took too long' in w['text'] and 'LLM Request Timeout' in w['text']
    assert w['go'] == {**LLM_TAB, 'key': 'LLM_REQUEST_TIMEOUT'}


def test_context_overflow_with_no_status_is_still_caught():
    w = explain(RuntimeError("the request exceeds the available context size, try increasing it"))
    assert 'Context Limit' in w['text'] and 'CONTEXT_LIMIT' not in w['text']
    assert w['go']['key'] == 'CONTEXT_LIMIT'


def test_gemini_bad_key_is_a_400_not_a_401():
    e = _status_err(openai.BadRequestError, 400, {'error': {'message': 'API key not valid. Please pass a '
                                                                       'valid API key.',
                                                            'status': 'INVALID_ARGUMENT'}},
                    url='https://generativelanguage.googleapis.com/v1beta/openai/chat/completions')
    assert 'refused its API key' in explain(e)['text']


def test_unknown_shapes_still_get_words_and_keep_the_raw():
    e = ValueError('the stream broke')
    w = explain(e)
    assert w['text'] and not w['known']
    assert friendly_llm_error(e) is None                      # callers that keep raw text for these
    assert fallback_error_text(e) == w['text']
    assert w['detail'] == 'ValueError: the stream broke'


@pytest.mark.parametrize('e', [
    openai.APIConnectionError(request=_req()),
    openai.APITimeoutError(request=_req()),
    _status_err(openai.InternalServerError, 500, {'error': 'boom'}),
    _status_err(openai.RateLimitError, 429, {'error': 'slow down'}),
    _status_err(openai.BadRequestError, 400, {'error': 'weird'}),
    ValueError('x'),
])
def test_the_words_never_show_a_class_name_or_json(e):
    t = explain(e)['text']
    assert 'Error' not in t.replace('error', '') and '{' not in t


def test_refusals_pass_through_verbatim():
    from core.chat.llm_providers.resolve import NoProvidersAvailable
    msg = "No LLM is turned on, so Auto has nothing to use."
    assert explain(NoProvidersAvailable(msg))['text'] == msg


def _provider():
    from core.chat.llm_providers.openai_compat import OpenAICompatProvider

    class _P(OpenAICompatProvider):
        supports_images = False
        def __init__(self): pass
        def _is_deepseek_official(self): return False
    return _P()


def test_failed_turn_row_is_shown_but_never_sent_to_the_model():
    h = ConversationHistory(max_history=0)
    h.add_user_message('hi')
    text, meta = history_row(openai.APIConnectionError(request=_req()))
    h.add_assistant_final(text, metadata=meta)
    h.add_user_message('hi again')
    assert is_error_row(h.messages[1])
    sent = h.get_messages_for_llm()
    assert [m['role'] for m in sent] == ['user', 'user'] and "LM Studio" not in str(sent)
    assert h.get_messages_for_display()[1]['metadata']['error']['detail']


def test_user_rows_side_by_side_become_one_on_the_wire():
    """Gemma/Mistral templates refuse user,user: a retry after a failed turn,
    a ghost line, a task's cue appended after history - one merge, at the wire."""
    clean = _provider()._sanitize_messages([
        {'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'hi'},
        {'role': 'user', 'content': 'hi again'}, {'role': 'assistant', 'content': 'hello'},
        {'role': 'user', 'content': 'a'}, {'role': 'user', 'content': 'b'}])
    assert [m['role'] for m in clean] == ['system', 'user', 'assistant', 'user']
    assert clean[1]['content'] == 'hi\n\nhi again' and clean[3]['content'] == 'a\n\nb'


def test_an_error_that_ended_a_tool_step_leaves_a_stand_in():
    h = ConversationHistory(max_history=0)
    h.add_user_message('look it up')
    h.messages.append({'role': 'assistant', 'content': '', 'tool_calls': [
        {'id': 'c1', 'type': 'function', 'function': {'name': 'web', 'arguments': '{}'}}]})
    h.add_tool_result('c1', 'web', 'result')
    text, meta = history_row(openai.APIConnectionError(request=_req()))
    h.add_assistant_final(text, metadata=meta)
    h.add_user_message('again')
    roles = [m['role'] for m in h.get_messages_for_llm()]
    assert roles == ['user', 'assistant', 'tool', 'assistant', 'user']     # never tool -> user


def test_a_failed_row_is_a_bubble_of_its_own():
    """Folded into the tool step before it, its metadata painted real tool
    results as the failure (display_format last-write-wins)."""
    from core.chat.display_format import format_messages_for_display as fmt
    text, meta = history_row(openai.APIConnectionError(request=_req()))
    rows = [{'role': 'user', 'content': 'look', 'timestamp': 't0'},
            {'role': 'assistant', 'content': 'checking', 'timestamp': 't1', 'metadata': {'model': 'm'},
             'tool_calls': [{'id': 'c1', 'type': 'function', 'function': {'name': 'web', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'c1', 'name': 'web', 'content': 'ok', 'timestamp': 't2'},
            {'role': 'assistant', 'content': text, 'metadata': meta, 'timestamp': 't3'}]
    out = [m for m in fmt(rows) if m['role'] == 'assistant']
    assert len(out) == 2 and 'error' not in (out[0].get('metadata') or {}) and out[1]['metadata']['error']


def test_odd_rows_and_odd_urls_never_raise():
    assert not is_error_row({'role': 'assistant', 'content': 'x', 'metadata': 'oops'})
    assert not is_error_row('not a row')
    from types import SimpleNamespace
    class Odd(Exception):
        request = SimpleNamespace(url='http://[::1/v1')         # unbalanced '[': urlparse raises
    assert explain(Odd('connection error'))['text']
    class NoReq(Exception):
        @property
        def request(self): raise RuntimeError('The .request property has not been set.')
    assert explain(NoReq('peer closed connection'))['text'].startswith('The LLM provider stopped')


@pytest.mark.parametrize('e,words', [
    (_status_err(openai.BadRequestError, 400, {'error': {'message': 'Your credit balance is too low to '
                                                                    'access the Anthropic API.'}}), 'out of credits'),
    (_status_err(openai.BadRequestError, 400, {'error': 'image exceeds 5 MB maximum'}), 'image is too big'),
    (_status_err(openai.BadRequestError, 400, {'error': 'The input token count (300000) exceeds the maximum '
                                                        'number of tokens allowed (1048576)'}), 'Context Limit'),
    (httpx.RemoteProtocolError('peer closed connection without sending complete message body'), 'stopped in the middle'),
])
def test_shapes_the_scouts_found(e, words):
    assert words in explain(e)['text']


def test_port_12345_is_not_lm_studio():
    w = explain(openai.APIConnectionError(request=_req('http://127.0.0.1:12345/v1/x')))
    assert 'LM Studio' not in w['text']


def test_a_quota_with_a_clock_is_a_rate_not_an_empty_account():
    """Hunt 19: Gemini's per-minute 429 says "quota" and read as out of credits."""
    url = 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'
    per_min = _status_err(openai.RateLimitError, 429, {'error': {'message': "You exceeded your current quota. "
        "Quota exceeded for metric: generate_content_free_tier_requests, limit: 10, model: x. "
        "Please retry in 31s. quotaId: GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}}, url)
    assert 'too many requests' in explain(per_min)['text']
    per_day = _status_err(openai.RateLimitError, 429, {'error': {'message': "You exceeded your current quota. "
        "quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier"}}, url)
    assert "today's request limit" in explain(per_day)['text']
    broke = _status_err(openai.RateLimitError, 429, {'error': {'message': 'insufficient_quota: add billing'}},
                        'https://api.openai.com/v1/chat/completions')      # OpenAI's empty account, not Gemini's
    assert 'out of credits' in explain(broke)['text']


def test_anthropics_input_plus_max_tokens_overflow_is_a_context_error():
    """Hunt 20: no keyword matched it, so no fit-and-retry and unhelpful words."""
    from core.chat.llm_errors import is_context_error
    e = _status_err(openai.BadRequestError, 400, {'error': {'message':
        'input length and `max_tokens` exceed context limit: 190000 + 16000 > 200000, decrease input length '
        'or `max_tokens` and try again'}}, 'https://api.anthropic.com/v1/messages')
    assert is_context_error(e)
    assert 'Max Tokens' in explain(e)['text']


def test_a_bare_gemini_quota_429_is_a_rate_not_empty_credits():
    url = 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'
    bare = _status_err(openai.RateLimitError, 429, {'error': {'message': "You exceeded your current quota, please "
        "check your plan and billing details."}}, url)
    assert 'too many requests' in explain(bare, 'gemini')['text']
