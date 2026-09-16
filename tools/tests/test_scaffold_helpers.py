from types import SimpleNamespace

import pytest

from docker_templates_tools import labels as labels_module
from docker_templates_tools.scaffold import (
    Secret,
    apply_label_defaults,
    apply_prefix,
    comment_block,
    flatten_prefixed,
    generated_header,
    secret_file,
    secret_full_name,
)


# --------------------------------------------------------------------------
# apply_prefix
# --------------------------------------------------------------------------


def test_apply_prefix_required_shorthand():
    assert apply_prefix("?name", "MARIADB") == "?MARIADB_NAME"


def test_apply_prefix_default_shorthand():
    assert apply_prefix("-host:mariadb:3306", "MARIADB") == "-MARIADB_HOST:mariadb:3306"


def test_apply_prefix_default_shorthand_no_default():
    assert apply_prefix("-verbose", "ACT_RUNNER") == "-ACT_RUNNER_VERBOSE:"


def test_apply_prefix_leaves_plain_values_alone():
    assert apply_prefix("literal value", "MARIADB") == "literal value"
    assert apply_prefix(True, "MARIADB") is True


# --------------------------------------------------------------------------
# flatten_prefixed
# --------------------------------------------------------------------------


def test_flatten_prefixed_nested_dicts_concatenate_with_no_separator():
    nested = {"GITEA__server__": {"SSH_": {"PORT": 22}}}
    assert flatten_prefixed(nested) == {"GITEA__server__SSH_PORT": 22}


def test_flatten_prefixed_dotted_prefix():
    nested = {"widget.": {"type": "gitea"}}
    assert flatten_prefixed(nested) == {"widget.type": "gitea"}


def test_flatten_prefixed_leaves_non_dict_values_untouched():
    flat = {"A": "1", "B": True, "C": ["x", "y"]}
    assert flatten_prefixed(flat) == flat


def test_flatten_prefixed_empty():
    assert flatten_prefixed({}) == {}


# --------------------------------------------------------------------------
# secret naming
# --------------------------------------------------------------------------


def test_secret_full_name_prefixed():
    s = Secret(suffix="password")
    assert secret_full_name(s, "MARIADB") == "mariadb_password"


def test_secret_full_name_bare_ignores_prefix():
    s = Secret(suffix="security_secret_key", bare=True)
    assert secret_full_name(s, "GITEA") == "security_secret_key"


def test_secret_file_per_project():
    s = Secret(suffix="password")
    s.name = "mariadb_password"
    path = secret_file(s, "MARIADB")
    assert path == (
        "${SECRETS_DIR:?SECRETS_DIR is required}/env/${COMPOSE_PROJECT_NAME}/mariadb_password"
    )


def test_secret_file_shared_is_not_per_project():
    s = Secret(suffix="password", shared=True)
    s.name = "gitea_password"
    path = secret_file(s, "GITEA")
    assert path == "${SECRETS_DIR:?SECRETS_DIR is required}/env/000-generic/gitea/password"


# --------------------------------------------------------------------------
# apply_label_defaults
# --------------------------------------------------------------------------


@pytest.fixture
def fake_spec():
    return SimpleNamespace(display_name="Gitea", service_name="gitea", description=None)


def test_apply_label_defaults_noop_when_no_pattern_matches(monkeypatch, fake_spec):
    monkeypatch.setattr(
        labels_module,
        "LABEL_CONFIG",
        {"homepage": {"pattern": "homepage.", "defaults": {"homepage.group": "x"}}},
    )
    labels = {"widget.type": "gitea"}
    assert apply_label_defaults(labels, fake_spec) == labels


def test_apply_label_defaults_fills_missing_defaults(monkeypatch, fake_spec):
    monkeypatch.setattr(
        labels_module,
        "LABEL_CONFIG",
        {
            "homepage": {
                "pattern": "homepage.",
                "defaults": {
                    "homepage.group": "${COMPOSE_PROJECT_NAME}",
                    "homepage.name": "%(display_name)s",
                    "homepage.icon": "%(service_name)s.png",
                },
            }
        },
    )
    labels = {"homepage.description": "Self-hosted Git service"}
    result = apply_label_defaults(labels, fake_spec)
    assert result == {
        "homepage.group": "${COMPOSE_PROJECT_NAME}",
        "homepage.name": "Gitea",
        "homepage.icon": "gitea.png",
        "homepage.description": "Self-hosted Git service",
    }


def test_apply_label_defaults_spec_value_always_wins(monkeypatch, fake_spec):
    monkeypatch.setattr(
        labels_module,
        "LABEL_CONFIG",
        {"homepage": {"pattern": "homepage.", "defaults": {"homepage.name": "%(display_name)s"}}},
    )
    labels = {"homepage.name": "Custom Name"}
    result = apply_label_defaults(labels, fake_spec)
    assert result["homepage.name"] == "Custom Name"


def test_apply_label_defaults_description_falls_back_to_display_name(monkeypatch):
    monkeypatch.setattr(
        labels_module,
        "LABEL_CONFIG",
        {
            "homepage": {
                "pattern": "homepage.",
                "defaults": {"homepage.description": "%(description)s for x"},
            }
        },
    )
    spec = SimpleNamespace(display_name="redis", service_name="redis", description=None)
    result = apply_label_defaults({"homepage.group": "x"}, spec)
    assert result["homepage.description"] == "Redis for x"


def test_apply_label_defaults_unmatched_keys_pass_through_untouched(monkeypatch, fake_spec):
    monkeypatch.setattr(labels_module, "LABEL_CONFIG", {})
    labels = {"npm.proxy.host": "gitea"}
    assert apply_label_defaults(labels, fake_spec) == labels


# --------------------------------------------------------------------------
# comment_block / generated_header
# --------------------------------------------------------------------------


def test_comment_block_prefixes_each_line():
    assert comment_block("line one\nline two") == ["# line one", "# line two"]


def test_comment_block_renders_bare_hash_for_blank_lines():
    assert comment_block("a\n\nb") == ["# a", "#", "# b"]


def test_comment_block_strips_leading_and_trailing_blank_lines():
    assert comment_block("\na\n\n") == ["# a"]


def test_generated_header_mentions_source():
    header = generated_header("tools/specs/services/gitea.yaml")
    assert "tools/specs/services/gitea.yaml" in header
    assert "do not edit directly" in header
