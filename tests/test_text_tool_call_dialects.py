"""extract_function_call_from_text dialect coverage (core, 2026-06-20).

Format 5 (nested-XML, <function=NAME><parameter=KEY>...) was added after a Discord
user's local model leaked this dialect as raw text instead of executing web_search.
These lock in: the leaked sample now parses, the JSON dialects still parse and keep
precedence, and prose that merely mentions tools does NOT spuriously fire.
"""
from unittest.mock import MagicMock

import pytest

from core.chat.chat_tool_calling import ToolCallingEngine


@pytest.fixture
def engine():
    return ToolCallingEngine(MagicMock())


# The EXACT content the user's bot leaked.
LEAKED = """Yep, I've got web_search in my toolkit! 🍑 Let me fire it up on that Wikipedia link to see what happens.

<tool_call>
<function=web_search>
<parameter=query>
https://en.wikipedia.org/wiki/List_of_Swiss_cheeses
</parameter>
</function>
</tool_call>"""


def test_leaked_nested_xml_now_parses(engine):
    out = engine.extract_function_call_from_text(LEAKED)
    assert out is not None, "the regression sample must no longer leak"
    assert out["function_call"]["name"] == "web_search"
    assert out["function_call"]["arguments"]["query"] == \
        "https://en.wikipedia.org/wiki/List_of_Swiss_cheeses"


def test_bare_function_tag_without_wrapper(engine):
    out = engine.extract_function_call_from_text(
        "sure!\n<function=get_website><parameter=url>https://x.com</parameter></function>")
    assert out["function_call"]["name"] == "get_website"
    assert out["function_call"]["arguments"] == {"url": "https://x.com"}


def test_multiple_parameters(engine):
    txt = ("<function=get_site_links><parameter=url>https://x.com</parameter>"
           "<parameter=strip_nav>true</parameter></function>")
    args = engine.extract_function_call_from_text(txt)["function_call"]["arguments"]
    assert args == {"url": "https://x.com", "strip_nav": "true"}


def test_no_parameters(engine):
    out = engine.extract_function_call_from_text("<function=get_status></function>")
    assert out["function_call"]["name"] == "get_status"
    assert out["function_call"]["arguments"] == {}


# ── precedence: the JSON dialects still parse AND win over format 5 ──────────

def test_json_tool_call_dialect_still_parses(engine):
    out = engine.extract_function_call_from_text(
        '<tool_call>{"name": "web_search", "arguments": {"query": "swiss cheese"}}</tool_call>')
    assert out["function_call"]["name"] == "web_search"
    assert out["function_call"]["arguments"]["query"] == "swiss cheese"


def test_function_call_json_dialect_still_parses(engine):
    out = engine.extract_function_call_from_text(
        '<function_call>{"name": "get_wikipedia", "arguments": {"topic": "cheese"}}</function_call>')
    assert out["function_call"]["name"] == "get_wikipedia"


# ── false-positive guards: don't fire on incomplete or non-call text ────────

def test_partial_stream_does_not_fire(engine):
    """Mid-stream content without the </function> close must NOT parse — else we
    fire on a truncated call with half the arguments."""
    partial = "<tool_call>\n<function=web_search>\n<parameter=query>\nswiss"
    assert engine.extract_function_call_from_text(partial) is None


def test_plain_prose_does_not_fire(engine):
    assert engine.extract_function_call_from_text(
        "I can use web_search and get_wikipedia to look that up for you.") is None


def test_empty_text(engine):
    assert engine.extract_function_call_from_text("") is None
