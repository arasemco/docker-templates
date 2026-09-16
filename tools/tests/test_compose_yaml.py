import pytest

from docker_templates_tools.compose_yaml import (
    AlwaysQuoted,
    FlowSeq,
    Literal,
    expand_env_value,
    frag,
    literalize,
    quote_if_ambiguous,
    validate_yaml,
)


def test_frag_dumps_and_indents():
    # frag() has no quoting opinion of its own — that's what
    # quote_if_ambiguous()/AlwaysQuoted are for (see below); plain strings
    # get PyYAML's own default quoting decision.
    lines = frag({"environment": {"A": "value", "B": "two"}}, 4)
    assert lines[0] == "    environment:"
    assert lines[1] == "      A: value"
    assert lines[2] == "      B: two"


def test_frag_literal_renders_block_style():
    lines = frag({"command": Literal("line one\nline two\n")}, 2)
    assert lines[0] == "  command: |"
    assert lines[1] == "    line one"
    assert lines[2] == "    line two"


def test_frag_always_quoted_renders_double_quotes():
    lines = frag({"k": AlwaysQuoted("on")}, 0)
    assert lines == ['k: "on"']


def test_frag_flow_seq_renders_inline():
    lines = frag({"test": FlowSeq(["CMD", "curl"])}, 0)
    assert lines == ["test: [CMD, curl]"]


def test_frag_none_renders_bare_key():
    lines = frag({"backend": None}, 0)
    assert lines == ["backend:"]


@pytest.mark.parametrize(
    "value",
    ["", "true", "True", "FALSE", "yes", "no", "on", "off", "null", "~", "1", "3.14", "-2"],
)
def test_quote_if_ambiguous_quotes_yaml_lookalikes(value):
    result = quote_if_ambiguous(value)
    assert isinstance(result, AlwaysQuoted)
    assert str(result) == value


@pytest.mark.parametrize("value", ["hello", "mariadb:3306", "some text"])
def test_quote_if_ambiguous_leaves_normal_strings_alone(value):
    result = quote_if_ambiguous(value)
    assert result == value
    assert not isinstance(result, AlwaysQuoted)


def test_quote_if_ambiguous_passes_through_non_strings():
    assert quote_if_ambiguous(True) is True


def test_expand_env_value_required_shorthand():
    assert expand_env_value("?name") == "${name:?name is required}"


def test_expand_env_value_default_shorthand():
    assert expand_env_value("-name:default") == "${name:-default}"


def test_expand_env_value_default_shorthand_empty_default():
    assert expand_env_value("-name:") == "${name:-}"


def test_expand_env_value_default_shorthand_value_containing_colon():
    # partition() only splits on the first ":", so a default with its own
    # colon (e.g. a host:port pair) survives whole.
    assert expand_env_value("-host:mariadb:3306") == "${host:-mariadb:3306}"


def test_expand_env_value_passthrough():
    assert expand_env_value("plain value") == "plain value"
    assert expand_env_value(True) is True


def test_literalize_wraps_multiline_strings_only():
    result = literalize("no newline")
    assert result == "no newline"
    assert not isinstance(result, Literal)

    result = literalize("line one\nline two")
    assert isinstance(result, Literal)


def test_literalize_recurses_into_dicts_and_lists():
    data = {"a": ["x\ny", "z"], "b": {"c": "1\n2"}}
    result = literalize(data)
    assert isinstance(result["a"][0], Literal)
    assert not isinstance(result["a"][1], Literal)
    assert isinstance(result["b"]["c"], Literal)


def test_literalize_leaves_already_literal_untouched():
    lit = Literal("a\nb")
    assert literalize(lit) is lit


def test_validate_yaml_accepts_valid_yaml():
    validate_yaml("a: 1\nb: 2\n", label="test")


def test_validate_yaml_rejects_invalid_yaml():
    with pytest.raises(ValueError, match="not valid YAML"):
        validate_yaml("a: [1, 2\n", label="test")
