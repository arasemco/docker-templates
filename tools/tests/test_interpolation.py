import pytest

from docker_templates_tools.interpolation import VarRef, find_vars, substitute


def test_find_vars_every_form():
    refs = find_vars("${A:?A is required} ${B:-x} ${C-y} ${D} $E ${F:+z} ${G?e}")
    assert refs == [
        VarRef("A", required=True),
        VarRef("B", default="x"),
        VarRef("C", default="y"),
        VarRef("D"),
        VarRef("E"),
        VarRef("F"),
        VarRef("G", required=True),
    ]


def test_find_vars_nested_default_reports_both():
    refs = find_vars("${SITE_URL:-https://${STACK_DOMAIN:?required}}")
    assert refs == [
        VarRef("SITE_URL", default="https://${STACK_DOMAIN:?required}"),
        VarRef("STACK_DOMAIN", required=True),
    ]


def test_find_vars_skips_escaped_dollars():
    assert find_vars('TS="$$(date)" ARCHIVE="$${ARCHIVE_PREFIX}-$$TS"') == []


def test_find_vars_rejects_unterminated_reference():
    with pytest.raises(ValueError):
        find_vars("${OOPS")


def test_substitute_defaults_and_values():
    text = "${SECRETS_DIR:?x}/env/${COMPOSE_PROJECT_NAME}/${NAME:-db}_password"
    values = {"SECRETS_DIR": "/s", "COMPOSE_PROJECT_NAME": "app"}
    assert substitute(text, values) == "/s/env/app/db_password"


def test_substitute_colon_dash_treats_empty_as_unset_but_dash_does_not():
    assert substitute("${A:-d}", {"A": ""}) == "d"
    assert substitute("${A-d}", {"A": ""}) == ""


def test_substitute_missing_required_raises():
    with pytest.raises(ValueError, match="SECRETS_DIR is required"):
        substitute("${SECRETS_DIR:?SECRETS_DIR is required}/x", {})


def test_substitute_unescapes_double_dollar():
    assert substitute("$$HOME ${A}", {"A": "1"}) == "$HOME 1"
