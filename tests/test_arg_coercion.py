"""Tests for LLM tool-arg type coercion at the execute_function chokepoint.

Covers the failure mode where models (especially local ones — Qwen/GLM)
stringify numbers, which then crash tools on numeric comparisons. Coercion
is best-effort with an LLM-actionable error when a typed arg can't be coerced.
"""
from core.chat.function_manager import _coerce_args, _coerce_num


def _params(props):
    return {"type": "object", "properties": props}


# ── Scalar int / number ──────────────────────────────────────────────────────

def test_stringified_int_coerced():
    args, err = _coerce_args({"index": "2"}, _params({"index": {"type": "integer"}}))
    assert args["index"] == 2 and err is None


def test_stringified_count_coerced():
    args, err = _coerce_args({"count": "20"}, _params({"count": {"type": "integer"}}))
    assert args["count"] == 20 and err is None


def test_already_int_passthrough():
    args, err = _coerce_args({"index": 2}, _params({"index": {"type": "integer"}}))
    assert args["index"] == 2 and err is None


def test_number_coerced():
    args, err = _coerce_args({"amount": "3.14"}, _params({"amount": {"type": "number"}}))
    assert args["amount"] == 3.14 and err is None


def test_int_with_whole_float_string():
    args, err = _coerce_args({"index": "5.0"}, _params({"index": {"type": "integer"}}))
    assert args["index"] == 5 and err is None


# ── Arrays ───────────────────────────────────────────────────────────────────

def test_array_of_string_ints_coerced():
    args, err = _coerce_args(
        {"indices": ["1", "2", "3"]},
        _params({"indices": {"type": "array", "items": {"type": "integer"}}}),
    )
    assert args["indices"] == [1, 2, 3] and err is None


def test_comma_string_to_int_array():
    args, err = _coerce_args(
        {"indices": "1,2,3"},
        _params({"indices": {"type": "array", "items": {"type": "integer"}}}),
    )
    assert args["indices"] == [1, 2, 3] and err is None


def test_comma_string_with_spaces():
    args, err = _coerce_args(
        {"indices": "1, 2, 3"},
        _params({"indices": {"type": "array", "items": {"type": "integer"}}}),
    )
    assert args["indices"] == [1, 2, 3] and err is None


def test_array_already_ints_passthrough():
    args, err = _coerce_args(
        {"indices": [1, 2]},
        _params({"indices": {"type": "array", "items": {"type": "integer"}}}),
    )
    assert args["indices"] == [1, 2] and err is None


# ── Booleans ─────────────────────────────────────────────────────────────────

def test_bool_true_string():
    args, err = _coerce_args({"flag": "true"}, _params({"flag": {"type": "boolean"}}))
    assert args["flag"] is True and err is None


def test_bool_false_string():
    args, err = _coerce_args({"flag": "false"}, _params({"flag": {"type": "boolean"}}))
    assert args["flag"] is False and err is None


# ── Strings are never coerced ────────────────────────────────────────────────

def test_string_param_left_untouched():
    # A zip code is a string — must not become an int.
    args, err = _coerce_args({"zip": "02134"}, _params({"zip": {"type": "string"}}))
    assert args["zip"] == "02134" and err is None


# ── Clean-fail path (Option 2) ───────────────────────────────────────────────

def test_uncoercible_int_returns_error():
    args, err = _coerce_args({"index": "abc"}, _params({"index": {"type": "integer"}}))
    assert err is not None and "index" in err and args["index"] == "abc"


def test_non_whole_float_for_integer_errors():
    args, err = _coerce_args({"index": "2.5"}, _params({"index": {"type": "integer"}}))
    assert err is not None and args["index"] == "2.5"


def test_bad_array_element_errors():
    args, err = _coerce_args(
        {"indices": ["1", "x"]},
        _params({"indices": {"type": "array", "items": {"type": "integer"}}}),
    )
    assert err is not None and "indices" in err


# ── No-op guards ─────────────────────────────────────────────────────────────

def test_unknown_param_ignored():
    args, err = _coerce_args({"mystery": "2"}, _params({"index": {"type": "integer"}}))
    assert args["mystery"] == "2" and err is None


def test_empty_parameters_noop():
    args, err = _coerce_args({"index": "2"}, {})
    assert args["index"] == "2" and err is None


def test_none_parameters_noop():
    args, err = _coerce_args({"index": "2"}, None)
    assert args["index"] == "2" and err is None


# ── _coerce_num unit ─────────────────────────────────────────────────────────

def test_coerce_num_non_string_passthrough():
    assert _coerce_num(7, "integer") == (7, True)


def test_coerce_num_garbage_fails():
    val, ok = _coerce_num("nope", "integer")
    assert ok is False and val == "nope"
