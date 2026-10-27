"""The app's one voice for LLM failures.

explain(e, provider_key) turns ANY exception from a provider call into what a
person can act on: plain words (what happened + where to click, the UI's own
labels, never a class name or a setting key), a Fix It link for the chat row,
and the raw text for the row's folded Details. The log keeps the raw error;
the screen, the voice and the chat history get the words.
"""
import json
import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

LLM_TAB = {'view': 'settings', 'tab': 'llm'}
_LOCAL_HOSTS = ('127.0.0.1', 'localhost', '0.0.0.0', '::1')
_CONTEXT = ('context size', 'context length', 'context_length', 'maximum context', 'context window',
            'token limit', 'n_ctx', 'exceeds the available context', 'prompt is too long',
            'exceeds the maximum number of tokens', 'input token count')
# Max Tokens itself refused: above the model's own reply cap (Anthropic,
# OpenAI, Groq wordings) or prompt + Max Tokens past the server's context
# (vLLM "N in the completion", TGI-style hosts "max_new_tokens").
_OUT_CAP = ('maximum allowed number of output tokens', 'is too large', 'supports at most',
            'must be less than or equal', 'maximum value for')
_OUT_ROOM = ('in the completion', 'max_new_tokens')
_KEY_WORDS = ('api key not valid', 'api_key_invalid', 'invalid api key', 'incorrect api key',
              'invalid x-api-key', 'authentication', 'unauthorized')


def _conf(key):
    try:
        from core.chat.llm_providers.resolve import providers_config
        c = providers_config().get(key)
        return c if isinstance(c, dict) else {}
    except Exception:
        return {}


def _url(e, conf):
    try:
        req = getattr(e, 'request', None)       # httpx raises RuntimeError when it was never set
        return str(req.url) if req is not None else (conf.get('base_url') or '')
    except Exception:
        return conf.get('base_url') or ''


def _parse(url):
    """urlparse that never raises (an unbalanced '[' in a base_url does)."""
    try:
        return urlparse(str(url or ''))
    except Exception:
        return urlparse('')


def _port(u):
    try:
        return u.port
    except Exception:
        return None


def _who(e, key):
    """(name, local, port) for the provider that failed. The key when the caller
    knows it; else the request URL matched against the configured providers."""
    url = _url(e, _conf(key) if key else {})
    if not key and url:
        here = _parse(url).netloc
        try:
            from core.chat.llm_providers.resolve import providers_config
            key = next((k for k, c in providers_config().items()
                        if isinstance(c, dict) and c.get('base_url')
                        and _parse(c['base_url']).netloc == here), None)
        except Exception:
            key = None
    conf = _conf(key) if key else {}
    name = None
    if key:
        try:
            from core.chat.llm_providers.resolve import display_name
            name = display_name(key, conf)
        except Exception:
            name = conf.get('display_name') or key
    u = _parse(url)
    local = bool(conf.get('is_local')) or (u.hostname or '') in _LOCAL_HOSTS
    return name, local, u


def _status(e):
    s = getattr(e, 'status_code', None) or getattr(getattr(e, 'response', None), 'status_code', None)
    return s if isinstance(s, int) else None


def explain(e, provider_key=None):
    """{'text', 'go', 'detail', 'known'} — text is never empty. `known` is False
    when no rule matched (callers that want raw text for an unknown shape).
    Never raises: its callers are except handlers, and a raise there would
    hide the real error and skip the history row."""
    try:
        return _explain(e, provider_key)
    except Exception as oops:
        logger.warning(f"[LLM-ERRORS] explaining {type(e).__name__} failed: {oops!r}")
        try:
            detail = f"{type(e).__name__}: {e}"[:2000]
        except Exception:
            detail = type(e).__name__
        return {'text': _unknown('the LLM provider'), 'go': None, 'detail': detail, 'known': False}


def _explain(e, provider_key):
    from core.chat.llm_providers.resolve import ProviderRefused
    raw = str(e)
    err = raw.lower()
    detail = f"{type(e).__name__}: {raw}"[:2000]

    def out(text, go=None, known=True):
        if go is None and 'Settings › LLM' in text:
            go = dict(LLM_TAB)
        return {'text': text, 'go': go, 'detail': detail, 'known': known}

    # The resolver's refusals are already the words (resolve.py auto_words /
    # health_words), privacy refusals too.
    if isinstance(e, ProviderRefused) or (isinstance(e, ConnectionError) and 'privat' in err):
        return out(raw)

    name, local, u = _who(e, provider_key)
    who = name or ('your local LLM server' if local else 'the LLM provider')
    Who = who[0].upper() + who[1:]
    port = _port(u)
    lms = (name or '').lower() == 'lm studio' or 'lms load' in err or port == 1234
    ollama = 'ollama' in (name or '').lower() or port == 11434
    tname = type(e).__name__
    status = _status(e)

    # Max Tokens before context: these name the reply budget, and "start a new
    # chat" alone won't fix a Max Tokens the model can't take.
    card = {**LLM_TAB, 'key': f'provider:{provider_key}'} if provider_key else dict(LLM_TAB)
    if any(k in err for k in ('max_tokens', 'max_completion_tokens', 'output tokens')) \
            and any(k in err for k in _OUT_CAP):
        return out(f"Max Tokens is set higher than the model at {who} can write in one reply. "
                   f"Lower Max Tokens for this provider in Settings › LLM.", card)
    if any(k in err for k in _OUT_ROOM):
        return out(f"This chat plus the reply's Max Tokens is more than the model at {who} can hold. "
                   f"Lower Max Tokens for this provider in Settings › LLM, or start a new chat.", card)

    # Context before status: llama.cpp / LM Studio raise it mid-stream with none.
    if any(k in err for k in _CONTEXT):
        if local:
            app = 'LM Studio' if lms else who
            return out(f"This chat is longer than the model in {app} can hold. Reload the model with a "
                       f"bigger Context Length, or lower Context Limit in Settings › LLM › General to match.",
                       {**LLM_TAB, 'key': 'CONTEXT_LIMIT'})
        return out("This chat is too long for the model. Start a new chat, or lower Context Limit "
                   "in Settings › LLM › General.", {**LLM_TAB, 'key': 'CONTEXT_LIMIT'})

    # Timeouts first: the OpenAI SDK's timeout IS a connection error subclass.
    if 'timeout' in tname.lower() or 'timed out' in err or status == 408:
        return out(f"{Who} took too long to answer. If it's a slow or local model, raise "
                   f"LLM Request Timeout in Settings › LLM › General.",
                   {**LLM_TAB, 'key': 'LLM_REQUEST_TIMEOUT'})

    # The server hung up mid-reply (it crashed, or ran out of memory): not a
    # connection the person can fix, and not their internet.
    if status is None and ('remoteprotocol' in tname.lower() or any(k in err for k in (
            'peer closed', 'server disconnected', 'incomplete chunked read', 'connection reset'))):
        return out(f"{Who} stopped in the middle of its reply. Try again; if it keeps happening, "
                   f"it may be running out of memory.")

    if status is None and (isinstance(e, ConnectionError) or 'connect' in tname.lower()
                           or 'connection' in err):
        if lms:
            return out("Can't reach LM Studio. Open it, start its server (Developer tab), and load a model.")
        if ollama:
            return out("Can't reach Ollama. Make sure it's running, then send again.")
        if local:
            host = u.netloc
            return out(f"Can't reach {who}{f' at {host}' if host else ''}. Make sure it's running "
                       f"with a model loaded.")
        return out(f"Can't reach {who}. Check your internet connection, or its Base URL in Settings › LLM.")

    if isinstance(e, json.JSONDecodeError):
        return out(f"{Who} sent back a reply that couldn't be read. Try again, or pick another "
                   f"provider in the chat sidebar.")

    if status is None:
        return out(_unknown(who), known=False)

    if lms and ('no models loaded' in err or 'lms load' in err or 'model unloaded' in err
                or 'model is not loaded' in err):
        return out("LM Studio has no model loaded. Open LM Studio, load a model, then send again.")
    if lms and 'failed to load model' in err:
        m = re.search(r'failed to load model "([^"]+)"', raw, re.I)
        return out(f"LM Studio couldn't load {m.group(1) if m else 'that model'}: it may not fit in memory, "
                   f"or the file is broken. Load a model in LM Studio yourself, or set one on its card in "
                   f"Settings › LLM.")
    if ollama and 'try pulling' in err:
        return out("Ollama doesn't have that model downloaded. Download it in Ollama, or pick a model "
                   "you have in Settings › LLM.")
    if 'credit balance' in err or 'insufficient_quota' in err or (
            status in (402, 429) and any(k in err for k in ('billing', 'quota', 'credit', 'insufficient',
                                                              'budget', 'balance'))):
        return out(f"{Who} says the account is out of credits. Add credits on its website, or pick "
                   f"another provider in the chat sidebar.")
    if status in (401, 403) or any(k in err for k in _KEY_WORDS):
        if status == 403 and not any(k in err for k in ('key', 'permission', 'auth')):
            return out(f"{Who} refused this request. Check its API key and model in Settings › LLM.")
        return out(f"{Who} refused its API key. Check the key in Settings › LLM.")
    if status == 429:
        return out(f"{Who} is getting too many requests right now. Wait a minute, then send again.")
    if status == 529 or 'overloaded' in err:
        return out(f"{Who} is overloaded right now. Wait a minute, then send again.")
    if status >= 500:
        return out(f"{Who} had a problem on its end ({status}). It's usually temporary, so try again in a minute.")
    if status == 413:
        return out(f"That message was too big for {who}, usually a large image or file. Try a smaller one.")
    if 'model' in err and any(k in err for k in ('not found', 'not loaded', 'does not exist', 'no such model',
                                                  'invalid model')):
        return out(f"{Who} doesn't have that model. Check the model name in Settings › LLM.")
    if status == 404:
        return out(f"{Who}'s address is wrong (nothing there). Check its Base URL in Settings › LLM.")
    if 'image' in err and any(k in err for k in ('exceeds', 'too large', 'too big', 'maximum', 'mb')):
        return out(f"That image is too big for {who}. Try a smaller one.")
    if any(k in err for k in ('image', 'vision', 'multimodal', 'content_type')):
        return out("This model can't see images. Send without the image, or pick a vision model in Settings › LLM.")
    if 'prefill' in err or 'final assistant content' in err:
        return out(f"{Who} can't continue a reply in place with this model. Ask it to go on in a new "
                   f"message instead.")
    if 'invalid tool call' in err or 'tool call arguments' in err:
        return out(f"{Who} rejected a tool step earlier in this chat. Start a new chat, or pick another "
                   f"provider in the chat sidebar.")
    if 'tool' in err and any(k in err for k in ('not support', "doesn't support", 'unsupported')):
        return out("This model can't use tools. Pick one that can in Settings › LLM, or set the chat's "
                   "Toolset to none in the chat sidebar.")
    return out(f"{Who} turned the request down ({status}). Try again, or pick another provider "
               f"in the chat sidebar.", known=False)


def explain_finish(finish, said, provider_key=None, model=None):
    """{'text', 'go'} when a generation ended on the reply token ceiling, else
    None. Not an exception: the words stay and the row is a real reply, so this
    rides beside it (metadata.cutoff) instead of replacing it. `said` = the
    reply has visible words (False = a reasoning model thought to the limit).
    Continue is offered only where it works (resolve.continues_in_place); the
    model is named because a core card's Max Tokens is set per model."""
    from core.chat.llm_providers.base import LENGTH_FINISH
    if finish not in LENGTH_FINISH:
        return None
    go = {**LLM_TAB, 'key': f'provider:{provider_key}'} if provider_key else dict(LLM_TAB)
    where = f"for {str(model).split('/')[-1]} in Settings › LLM" if model else "for this provider in Settings › LLM"
    if not said:
        return {'text': f"The model used its whole reply budget thinking and never got to answer. Raise "
                        f"Max Tokens {where}, or lower its reasoning effort.", 'go': go}
    try:
        from core.chat.llm_providers.resolve import continues_in_place
        can = bool(provider_key) and continues_in_place(provider_key)
    except Exception:
        can = False
    then = "Press Continue to pick up where it stopped" if can else "Ask it to go on in a new message"
    return {'text': f"This reply hit its length limit and was cut off. {then}, or raise Max Tokens {where}.",
            'go': go}


def _unknown(who):
    return f"Something went wrong talking to {who}. Try again, or pick another provider in the chat sidebar."


def history_row(e, provider_key=None):
    """(content, metadata) for the chat row a failed turn leaves: the words
    show, the raw text folds under Details, and the row never reaches the LLM
    (history.get_messages_for_llm skips metadata.error rows)."""
    w = explain(e, provider_key)
    return w['text'], {'error': {'detail': w['detail'], 'go': w['go']}}


_LEGACY_ROW = re.compile(r'^\[(Error|Connection error): ')


def is_error_row(msg):
    """A failed turn's row: marked (this module), or the old raw shape."""
    if not isinstance(msg, dict) or msg.get('role') != 'assistant' or msg.get('tool_calls'):
        return False
    meta = msg.get('metadata')
    if isinstance(meta, dict) and meta.get('error'):
        return True
    c = msg.get('content')
    return isinstance(c, str) and bool(_LEGACY_ROW.match(c))
