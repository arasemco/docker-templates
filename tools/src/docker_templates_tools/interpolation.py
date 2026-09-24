"""Compose variable interpolation: find the `${VAR...}` references a
compose file makes, and substitute known values into a string.

Supports the forms Compose itself does: `$VAR`, `${VAR}`, `${VAR:-default}`,
`${VAR-default}`, `${VAR:?error}`, `${VAR?error}`, `${VAR:+alt}`,
`${VAR+alt}`, nested references inside a default/alt value, and `$$` as an
escaped literal `$` (shell variables inside a `configs:` script).
"""

from __future__ import annotations

import re

from attrs import define

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_MODIFIERS = (":-", ":?", ":+", "-", "?", "+")


@define
class VarRef:
    name: str
    required: bool = False
    default: str | None = None  # None: no default given (plain ${VAR} or a :+ alt)


def _match_brace(text: str, start: int) -> int:
    """Index of the `}` closing the `${` whose body starts at `start`."""
    depth = 1
    i = start
    while i < len(text):
        if text.startswith("$$", i):
            i += 2
            continue
        if text.startswith("${", i):
            depth += 1
            i += 2
            continue
        if text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError(f"unterminated ${{ in {text!r}")


def _split_body(body: str) -> tuple[str, str | None, str]:
    """`NAME:-default` -> ("NAME", ":-", "default")."""
    m = _NAME_RE.match(body)
    if not m:
        raise ValueError(f"invalid variable reference ${{{body}}}")
    name, rest = m.group(0), body[m.end() :]
    if not rest:
        return name, None, ""
    for mod in _MODIFIERS:
        if rest.startswith(mod):
            return name, mod, rest[len(mod) :]
    raise ValueError(f"invalid variable reference ${{{body}}}")


def find_vars(text: str) -> list[VarRef]:
    """Every variable reference in `text`, in order of appearance, including
    references nested inside another one's default value."""
    refs: list[VarRef] = []
    i = 0
    while i < len(text):
        if text.startswith("$$", i):
            i += 2
            continue
        if text.startswith("${", i):
            end = _match_brace(text, i + 2)
            name, mod, arg = _split_body(text[i + 2 : end])
            if mod in (":?", "?"):
                refs.append(VarRef(name, required=True))
            elif mod in (":-", "-"):
                refs.append(VarRef(name, default=arg))
            else:
                refs.append(VarRef(name))
            refs.extend(find_vars(arg))
            i = end + 1
            continue
        if text[i] == "$":
            m = _NAME_RE.match(text, i + 1)
            if m:
                refs.append(VarRef(m.group(0)))
                i = m.end()
                continue
        i += 1
    return refs


def substitute(text: str, values: dict) -> str:
    """Interpolate `text` the way Compose would, given `values`. An unset
    required variable raises ValueError; `$$` becomes `$`."""
    out = []
    i = 0
    while i < len(text):
        if text.startswith("$$", i):
            out.append("$")
            i += 2
            continue
        if text.startswith("${", i):
            end = _match_brace(text, i + 2)
            name, mod, arg = _split_body(text[i + 2 : end])
            value = values.get(name)
            is_set = value is not None
            non_empty = bool(value)
            if mod == ":-":
                out.append(value if non_empty else substitute(arg, values))
            elif mod == "-":
                out.append(value if is_set else substitute(arg, values))
            elif mod in (":?", "?"):
                if not (non_empty if mod == ":?" else is_set):
                    raise ValueError(f"{name} is required")
                out.append(value)
            elif mod == ":+":
                out.append(substitute(arg, values) if non_empty else "")
            elif mod == "+":
                out.append(substitute(arg, values) if is_set else "")
            else:
                out.append(value or "")
            i = end + 1
            continue
        if text[i] == "$":
            m = _NAME_RE.match(text, i + 1)
            if m:
                out.append(values.get(m.group(0)) or "")
                i = m.end()
                continue
        out.append(text[i])
        i += 1
    return "".join(out)
