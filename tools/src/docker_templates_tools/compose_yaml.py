"""YAML helpers for emitting docker-compose fragments that match this repo's
hand-written style (block literals for scripts, flow-style quoted healthcheck
tests, bare volume/network keys instead of explicit `null`).

Everything renders through PyYAML so scalar quoting (e.g. an env value that
would otherwise be misread as the YAML boolean `on`) is decided correctly
instead of by hand.
"""

from __future__ import annotations

import re

import yaml

# Scalars that YAML's implicit resolver would read back as bool/null/int/float
# instead of a string. PyYAML auto-quotes these, but defaults to single quotes;
# this repo's convention is double quotes, so we force AlwaysQuoted on them.
_AMBIGUOUS_SCALAR_RE = re.compile(
    r"^(|~|[Nn]ull|NULL"
    r"|[Tt]rue|TRUE|[Ff]alse|FALSE"
    r"|[Yy]es|YES|[Nn]o|NO|[Oo]n|ON|[Oo]ff|OFF"
    r"|[-+]?[0-9]+|[-+]?[0-9]*\.[0-9]+([eE][-+]?[0-9]+)?)$"
)


class Literal(str):
    """A multi-line string that must render as a block literal (``|``)."""


class AlwaysQuoted(str):
    """A string that must always render double-quoted."""


class FlowSeq(list):
    """A list that must render in flow style (``[a, b, c]``)."""


class ComposeDumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):
        # PyYAML defaults to indentless block sequences (`key:\n- item`); this
        # repo's files indent list items under their key (`key:\n  - item`).
        return super().increase_indent(flow, False)


def _represent_literal(dumper: yaml.Dumper, data: Literal):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style="|")


def _represent_quoted(dumper: yaml.Dumper, data: AlwaysQuoted):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style='"')


def _represent_flow_seq(dumper: yaml.Dumper, data: FlowSeq):
    return dumper.represent_sequence("tag:yaml.org,2002:seq", list(data), flow_style=True)


def _represent_none(dumper: yaml.Dumper, data: None):
    # Bare "key:" with nothing after it, matching e.g. `volumes:\n  redis_data:`
    # instead of PyYAML's default `redis_data: null`.
    return dumper.represent_scalar("tag:yaml.org,2002:null", "")


ComposeDumper.add_representer(Literal, _represent_literal)
ComposeDumper.add_representer(AlwaysQuoted, _represent_quoted)
ComposeDumper.add_representer(FlowSeq, _represent_flow_seq)
ComposeDumper.add_representer(type(None), _represent_none)


def literalize(obj):
    """Recursively wrap any plain multi-line string as `Literal`, so raw
    passthrough data (e.g. a stack spec's `extra:`/`app_extra:` dicts) gets
    the same block-literal (``|``) style this repo uses for every other
    multi-line value, instead of PyYAML folding it into a quoted scalar."""
    if isinstance(obj, dict):
        return {k: literalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [literalize(v) for v in obj]
    if isinstance(obj, str) and not isinstance(obj, Literal) and "\n" in obj:
        return Literal(obj)
    return obj


def frag(obj: dict, indent: int) -> list:
    """Dump `obj` (a single-key dict, usually) and indent every line by `indent`
    spaces. Used to render one section of a compose file at a time so callers
    keep full control over blank-line spacing between sections."""
    text = yaml.dump(
        obj,
        Dumper=ComposeDumper,
        sort_keys=False,
        default_flow_style=False,
        width=1000,
        allow_unicode=True,
    )
    pad = " " * indent
    return [(pad + line if line else line) for line in text.rstrip("\n").splitlines()]


def quote_if_ambiguous(value: str) -> str:
    """Force double-quote style for scalars that YAML would otherwise parse
    as bool/null/number (e.g. "1", "true", "on")."""
    if isinstance(value, str) and _AMBIGUOUS_SCALAR_RE.match(value):
        return AlwaysQuoted(value)
    return value


def expand_env_value(value: str) -> str:
    """Minimal shorthand so specs don't have to spell out `${VAR:?...}` noise:

    - "?VAR"       -> ${VAR:?VAR is required}
    - "-VAR:default" -> ${VAR:-default}
    - anything else is passed through unchanged (already-literal YAML text)
    """
    if isinstance(value, str):
        if value.startswith("?"):
            var = value[1:]
            return f"${{{var}:?{var} is required}}"
        if value.startswith("-"):
            var, _, default = value[1:].partition(":")
            return f"${{{var}:-{default}}}"
    return value


def validate_yaml(text: str, *, label: str) -> None:
    """Parse the generated text back to catch formatting bugs immediately."""
    try:
        yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"generated {label} is not valid YAML: {exc}") from exc
