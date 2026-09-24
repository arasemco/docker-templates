"""Interactive `app` command: turn a stack or service template into a
deployable app directory.

1. Pick a stack or a service. A service's grouped extensions (database,
   mailer, ...) are offered as dependency choices; a picked variant that is
   itself a service (postgres, mariadb, redis) becomes a dependency.
2. The picked combination resolves to an existing stack, a newly generated
   stack spec (a service with dependencies no stack covers yet), or the
   service's own files (a service with no dependencies).
3. Every variable the included compose files reference is asked for:
   required ones need a value, optional ones keep their default unless
   overridden, and generic tunables (restart/pull policy, CPU/memory
   limits) are only asked when requested. Values already set in the env
   files are confirmed one by one.
4. Every secret file gets a generated value to accept or replace; existing
   secret files are confirmed one by one.

Output follows the layout common.mk expects: <stacks>/<name>/docker-compose.yml
(+ Makefile), <secrets>/env/<name>/.env, and the secret files at the paths
the compose files point at.
"""

from __future__ import annotations

import os
import re
import secrets as _secrets
import string
import textwrap
from pathlib import Path

import yaml
from attrs import Factory, define

from . import scaffold
from .compose_yaml import ComposeDumper
from .interpolation import VarRef, find_vars, substitute
from .models import ServiceSpec, load_service_spec
from .prompts import Prompter
from .render import render_stack, resolve_stack_spec

# Variables Compose or common.mk provide at run time, never asked for.
RUNTIME_VARS = {"COMPOSE_PROJECT_NAME", "BACKUP_FILE", "HOME"}
# Variables that belong in env/000-generic/.env rather than an app's own file.
GENERIC_VARS = {"SECRETS_DIR"}
ADVANCED_VARS = {"RESTART", "PULL_POLICY"}
ADVANCED_SUFFIXES = ("_CPU_LIMIT", "_MEM_LIMIT")
DEFAULT_NETWORK = "domain_reverse-proxy"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SECRET_ALPHABET = string.ascii_letters + string.digits


def generate_secret(length: int = 32) -> str:
    return "".join(_secrets.choice(SECRET_ALPHABET) for _ in range(length))


def is_advanced(name: str) -> bool:
    return name in ADVANCED_VARS or name.endswith(ADVANCED_SUFFIXES)


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------


def load_services() -> dict[str, ServiceSpec]:
    services = {}
    for path in sorted(scaffold.SERVICE_SPECS_DIR.glob("*.yaml")):
        spec = load_service_spec(yaml.safe_load(path.read_text()))
        services[spec.service_name] = spec
    return services


def load_stacks() -> list[dict]:
    return [yaml.safe_load(p.read_text()) for p in sorted(scaffold.STACK_SPECS_DIR.glob("*.yaml"))]


def _deps(stack: dict) -> list[str]:
    return stack["dep"] if isinstance(stack["dep"], list) else [stack["dep"]]


def describe_stack(stack: dict) -> str:
    parts = [stack["app"], *_deps(stack)]
    ext = ", ".join(f"{g}: {v}" for g, v in (stack.get("extensions") or {}).items())
    return " + ".join(parts) + (f" ({ext})" if ext else "")


def find_stack(stacks: list[dict], app: str, deps: list[str], extensions: dict) -> dict | None:
    for stack in stacks:
        if (
            stack["app"] == app
            and sorted(_deps(stack)) == sorted(deps)
            and (stack.get("extensions") or {}) == extensions
        ):
            return stack
    return None


def new_stack_spec(spec: ServiceSpec, deps: list[ServiceSpec], extensions: dict) -> dict:
    d = {"app": spec.service_name}
    if spec.dir_name != spec.service_name:
        d["app_slug"] = spec.dir_name
    names = [dep.service_name for dep in deps]
    d["dep"] = names[0] if len(names) == 1 else names
    d["extensions"] = dict(extensions)
    d["app_extra"] = {"profiles": ["frontend"]}
    d["dep_extra"] = {"profiles": ["backend"]}
    title = " + ".join(s.display_name for s in [spec, *deps])
    bases = [f"base/{s.dir_name}" for s in [spec, *deps]]
    wired = ", ".join(bases[:-1]) + " and " + bases[-1]
    d["header_comment"] = textwrap.fill(
        f"Wires {wired} together on shared networks with a health-gated "
        "startup order. Include this single file from a stack's "
        "docker-compose.yml; it needs no other includes alongside it.",
        width=72,
    )
    d["header_comment"] = f"Composed template: {title}.\n{d['header_comment']}\n"
    return d


def render_stack_spec(d: dict) -> str:
    """A stack spec as YAML in the hand-written specs' layout: plain
    scalar keys together at the top, each nested block separated by a
    blank line, indented sequences, header_comment as a block literal."""
    blocks: list[str] = []
    scalar_block = False  # whether blocks[-1] is a run of plain scalar keys
    for key, value in d.items():
        if key == "header_comment":
            continue
        text = yaml.dump({key: value}, Dumper=ComposeDumper, sort_keys=False)
        nested = isinstance(value, (dict, list))
        if scalar_block and not nested:
            blocks[-1] += text
        else:
            blocks.append(text)
        scalar_block = not nested
    if d.get("header_comment"):
        lines = d["header_comment"].rstrip("\n").splitlines()
        blocks.append("header_comment: |\n" + "".join(f"  {line}\n" for line in lines))
    return "\n".join(blocks)


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


@define
class Selection:
    app: str  # compose service key of the main service
    app_dir: str  # its services/base/<dir>/ name, the default app name
    includes: list  # compose files the app file includes, as Paths
    new_stack: dict | None = None  # a stack spec to write and generate first
    new_stack_path: Path | None = None
    new_stack_content: str | None = None
    shared_groups: list = Factory(list)  # shared extension variants in use, e.g. ["smtp"]


def shared_groups(services: dict, app: str, deps: list[str], extensions: dict) -> list[str]:
    """Keys of the shared extension variants the app's include tree pulls
    in: the app's chosen variants plus every flat (always-included)
    extension of the app and its dependencies."""
    keys: list[str] = []
    for name in [app, *deps]:
        spec = services.get(name)
        if spec is None:
            continue
        chosen = extensions if name == app else {}
        for group in spec.extensions:
            for variant in group.variants:
                used = not group.grouped or chosen.get(group.key) == variant.key
                if used and variant.shared and variant.key not in keys:
                    keys.append(variant.key)
    return keys


def select(p: Prompter, services: dict, stacks: list[dict]) -> Selection:
    stack_labels = [f"stack    {describe_stack(s)}" for s in stacks]
    service_names = list(services)
    service_labels = [
        f"service  {name}"
        + (f" — {services[name].description}" if services[name].description else "")
        for name in service_names
    ]
    idx = p.choose("Available stacks and services:", stack_labels + service_labels)

    if idx < len(stacks):
        stack = stacks[idx]
        p.say(f"Stack {describe_stack(stack)}")
        return _stack_selection(stack, services)

    spec = services[service_names[idx - len(stacks)]]
    extensions = {}
    for group in spec.extensions:
        if not group.grouped:
            continue
        keys = [v.key for v in group.variants]
        labels = [f"{k} (service)" if k in services else k for k in keys]
        choice = p.choose(f"{spec.service_name}: {group.key}", labels, none_label="none")
        if choice is not None:
            extensions[group.key] = keys[choice]

    deps = [v for v in extensions.values() if v in services and v != spec.service_name]
    stack = find_stack(stacks, spec.service_name, deps, extensions)
    if stack:
        p.say(f"Using existing stack {describe_stack(stack)}")
        return _stack_selection(stack, services)

    if not deps:
        base = scaffold.SERVICES_BASE / spec.dir_name
        includes = [base / "docker-compose.yml"]
        includes += [base / g / f"docker-compose.{v}.yml" for g, v in extensions.items()]
        return Selection(
            spec.service_name,
            spec.dir_name,
            includes,
            shared_groups=shared_groups(services, spec.service_name, [], extensions),
        )

    d = new_stack_spec(spec, [services[x] for x in deps], extensions)
    path = scaffold.STACKS_DIR / scaffold.stack_filename(d)
    if path.exists():
        raise ValueError(
            f"{path.name} already exists for a different combination; pick that stack "
            "instead, or give the new one its own spec"
        )
    p.say(f"No stack covers {describe_stack(d)} yet; a new one will be generated")
    return Selection(
        spec.service_name,
        spec.dir_name,
        [path],
        new_stack=d,
        new_stack_path=path,
        new_stack_content=scaffold.generated_header(_stack_spec_source(d))
        + "\n"
        + render_stack(**resolve_stack_spec(d)),
        shared_groups=shared_groups(services, spec.service_name, deps, extensions),
    )


def _stack_selection(stack: dict, services: dict) -> Selection:
    app_dir = stack.get("app_slug", stack["app"])
    groups = shared_groups(services, stack["app"], _deps(stack), stack.get("extensions") or {})
    return Selection(
        stack["app"],
        app_dir,
        [scaffold.STACKS_DIR / scaffold.stack_filename(stack)],
        shared_groups=groups,
    )


def _stack_spec_path(d: dict) -> Path:
    stem = scaffold.stack_filename(d).removeprefix("docker-compose.").removesuffix(".yml")
    return scaffold.STACK_SPECS_DIR / f"{stem}.yaml"


def _stack_spec_source(d: dict) -> str:
    return str(_stack_spec_path(d).relative_to(scaffold.REPO_ROOT))


# --------------------------------------------------------------------------
# Variables and secrets referenced by the included compose files
# --------------------------------------------------------------------------


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def _include_paths(data: dict, base: Path) -> list[Path]:
    paths = []
    for entry in data.get("include") or []:
        targets = entry.get("path") if isinstance(entry, dict) else entry
        for t in targets if isinstance(targets, list) else [targets]:
            paths.append((base / t).resolve())
    return paths


def collect(includes: list[Path], virtual: dict | None = None) -> tuple[dict, dict]:
    """Walk the include tree. Returns ({var name: VarRef}, {secret name:
    file template}), both in first-seen order. `virtual` maps a path to
    content not written to disk yet (a stack about to be generated)."""
    virtual = {Path(k).resolve(): v for k, v in (virtual or {}).items()}
    variables: dict[str, VarRef] = {}
    secrets: dict[str, str] = {}
    seen: set[Path] = set()

    def walk(path: Path) -> None:
        path = path.resolve()
        if path in seen:
            return
        seen.add(path)
        text = virtual.get(path)
        if text is None:
            text = path.read_text()
        data = yaml.safe_load(text) or {}
        for s in _strings({k: v for k, v in data.items() if k != "include"}):
            for ref in find_vars(s):
                known = variables.get(ref.name)
                if known is None:
                    variables[ref.name] = ref
                else:
                    known.required = known.required or ref.required
                    if known.default is None:
                        known.default = ref.default
        for name, sdef in (data.get("secrets") or {}).items():
            if isinstance(sdef, dict) and "file" in sdef:
                secrets.setdefault(name, sdef["file"])
        for inc in _include_paths(data, path.parent):
            walk(inc)

    for path in includes:
        walk(path)
    for name in RUNTIME_VARS:
        variables.pop(name, None)
    return variables, secrets


# --------------------------------------------------------------------------
# Env files
# --------------------------------------------------------------------------

_ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value.split(" #", 1)[0].strip()


def read_env_file(path: Path) -> dict[str, str]:
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            m = _ENV_LINE_RE.match(line)
            if m and not line.lstrip().startswith("#"):
                values[m.group(1)] = _unquote(m.group(2))
    return values


@define
class EnvLayout:
    """The env files an app's include lists, in order (last wins): the
    generic one, one per shared extension group it uses, then its own."""

    generic: Path
    shared: dict  # group key -> Path
    app: Path

    @property
    def files(self) -> list[Path]:
        return [self.generic, *self.shared.values(), self.app]

    def target(self, name: str, group: str) -> Path:
        """The file a variable is written to: SECRETS_DIR to the generic
        file, a shared group's variables (SMTP_*) to that group's file,
        everything else to the app's own."""
        if name in GENERIC_VARS:
            return self.generic
        return self.shared.get(group, self.app)


def env_layout(secrets_root: Path, name: str, groups: list[str]) -> EnvLayout:
    generic = secrets_root / "env" / "000-generic"
    return EnvLayout(
        generic=generic / ".env",
        shared={g: generic / g / ".env" for g in groups},
        app=secrets_root / "env" / name / ".env",
    )


def format_env_value(value: str) -> str:
    if value and not re.search(r"[\s#'\"\\]", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def update_env_text(text: str, updates: dict[str, str]) -> str:
    """Set `updates` in an env file's text: existing keys are replaced in
    place, new ones appended; comments and other keys are kept."""
    remaining = dict(updates)
    lines = []
    for line in text.splitlines():
        m = _ENV_LINE_RE.match(line)
        if m and not line.lstrip().startswith("#") and m.group(1) in remaining:
            key = m.group(1)
            lines.append(f"{key}={format_env_value(remaining.pop(key))}")
        else:
            lines.append(line)
    lines += [f"{k}={format_env_value(v)}" for k, v in remaining.items()]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Prompting for values
# --------------------------------------------------------------------------


@define
class Answers:
    values: dict = Factory(dict)  # every effective value, for resolving secret paths
    env_updates: dict = Factory(dict)  # env file Path -> {var: value} to set in it
    secret_writes: dict = Factory(dict)  # Path -> value
    secret_kept: list = Factory(list)  # Paths left as they are


def _display(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def group_variables(variables: dict, prefixes: set[str]) -> dict[str, list[str]]:
    """Group var names by the service/variant prefix they start with
    (POSTGRES_NAME -> "postgres"); shared ones (STACK_DOMAIN, SECRETS_DIR,
    RESTART, ...) come first under "shared". Longest prefix wins, so
    ADGUARD_HOME_TZ lands under adguard-home, not a shorter match."""
    ordered = sorted(prefixes, key=len, reverse=True)
    groups: dict[str, list[str]] = {"shared": []}
    for name in variables:
        group = next(
            (p.lower().replace("_", "-") for p in ordered if name.startswith(p + "_")),
            "shared",
        )
        groups.setdefault(group, []).append(name)
    return {g: names for g, names in groups.items() if names}


def ask_variables(
    p: Prompter,
    variables: dict,
    existing: dict,
    *,
    defaults: dict,
    answers: Answers,
    layout: EnvLayout,
    prefixes: set[str] = frozenset(),
) -> None:
    """`existing` maps a var name to (value, source) from the app's env
    files. Only values those files don't already provide end up in
    answers.env_updates, under the file layout.target() picks."""
    advanced_new = [n for n in variables if is_advanced(n) and n not in existing]
    customize = bool(advanced_new) and p.confirm(
        "Customize advanced settings (restart/pull policy, CPU/memory limits)?", default=False
    )

    for group, names in group_variables(variables, prefixes).items():
        shared = f" (shared: {layout.shared[group]})" if group in layout.shared else ""
        p.say(f"-- {group}{shared} --")
        for name in names:
            new = _ask_variable(p, name, variables[name], existing, defaults, answers, customize)
            if new is not None:
                answers.env_updates.setdefault(layout.target(name, group), {})[name] = new


def _ask_variable(p, name, ref, existing, defaults, answers, customize) -> str | None:
    """Ask for one variable; returns a value to write, or None."""
    kind = "required" if ref.required else "optional"
    if name in existing:
        value, source = existing[name]
        if p.confirm(f"{name} ({kind}) = {value!r} from {source}. Keep?"):
            answers.values[name] = value
            return None
        new = p.ask(f"  new value for {name}", required=ref.required)
    elif is_advanced(name) and not customize:
        return None
    elif ref.required:
        new = p.ask(f"{name} (required)", default=defaults.get(name), required=True)
    else:
        shown = ref.default if ref.default else "empty"
        new = p.ask(f"{name} (optional, default {shown}; Enter to keep)")
        if not new:
            return None
    answers.values[name] = new
    if name in existing and existing[name][0] == new:
        return None
    return new


def ask_secrets(p: Prompter, secret_files: dict, answers: Answers, *, secrets_root: Path) -> None:
    for name, template in secret_files.items():
        path = Path(substitute(template, answers.values))
        where = _display(path, secrets_root)
        if path.exists():
            size = len(path.read_text())
            if p.confirm(f"Secret {name} exists at {where} ({size} chars). Keep?"):
                answers.secret_kept.append(path)
                continue
        suggestion = generate_secret()
        p.say(f"Secret {name} -> {where}")
        if p.confirm(f"  use generated value {suggestion}?"):
            answers.secret_writes[path] = suggestion
        else:
            answers.secret_writes[path] = p.ask(f"  value for {name}", required=True)


# --------------------------------------------------------------------------
# App compose file
# --------------------------------------------------------------------------


def _home_path(path: Path) -> str:
    """`path` as ${HOME}/... when it's under the home directory."""
    try:
        return "${HOME}/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def env_file_lines(layout: EnvLayout, app: str, name: str) -> list[tuple[str, str]]:
    """(path, comment) for each env file, in include order. The app's own
    file is written as env/${COMPOSE_PROJECT_NAME}/.env, so it follows the
    project name (the directory, or -p) like the secret files do."""
    lines = [(_home_path(layout.generic), "shared by every app")]
    lines += [
        (_home_path(path), f"shared, because {app} uses {group}")
        for group, path in layout.shared.items()
    ]
    own = _home_path(layout.app.parent.parent) + "/${COMPOSE_PROJECT_NAME}/.env"
    lines.append((own, f"{name}'s own values, last so they win"))
    return lines


def render_app_compose(
    sel: Selection, deploy_dir: Path, network: str | None, env_files: list[tuple[str, str]]
) -> str:
    rel = [os.path.relpath(path, deploy_dir) for path in sel.includes]
    lines = ["include:"]
    if len(rel) == 1:
        lines.append(f"  - path: {rel[0]}")
    else:
        lines.append("  - path:")
        lines += [f"      - {r}" for r in rel]
    lines.append("    env_file:")
    width = max(len(path) for path, _ in env_files)
    lines += [f"      - {path.ljust(width)}  # {comment}" for path, comment in env_files]
    if network:
        lines += [
            "",
            "services:",
            f"  {sel.app}:",
            "    networks:",
            "      - frontend",
            "",
            "networks:",
            "  frontend:",
            f"    name: {network}",
            "    external: true",
        ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def run_app(
    p: Prompter,
    *,
    stacks_root: Path,
    secrets_root: Path,
    dry_run: bool = False,
) -> int:
    services = load_services()
    stacks = load_stacks()
    sel = select(p, services, stacks)

    while True:
        name = p.ask("App name (directory and compose project)", default=sel.app_dir)
        if NAME_RE.match(name):
            break
        p.say("  use lowercase letters, digits, '-' and '_'")

    deploy_dir = stacks_root / name
    compose_path = deploy_dir / "docker-compose.yml"
    write_compose = True
    if compose_path.exists():
        p.say(f"{compose_path} already exists:")
        p.say(compose_path.read_text().rstrip())
        write_compose = p.confirm("Replace it?", default=False)
    network = None
    if write_compose:
        answer = p.ask(
            f"External network for {sel.app} to join ('-' for none)", default=DEFAULT_NETWORK
        )
        network = None if answer == "-" else answer

    virtual = {sel.new_stack_path: sel.new_stack_content} if sel.new_stack else None
    variables, secret_files = collect(sel.includes, virtual)

    layout = env_layout(secrets_root, name, sel.shared_groups)
    existing = {}
    for env_path in layout.files:
        for key, value in read_env_file(env_path).items():
            existing[key] = (value, env_path)
    shown_existing = {
        k: (v, _display(src, secrets_root.parent)) for k, (v, src) in existing.items()
    }

    prefixes = {spec.upper for spec in services.values()}
    prefixes |= {
        v.key.upper().replace("-", "_")
        for spec in services.values()
        for g in spec.extensions
        for v in g.variants
    }
    answers = Answers(values={"COMPOSE_PROJECT_NAME": name})
    ask_variables(
        p,
        variables,
        {k: shown_existing[k] for k in variables if k in existing},
        defaults={"SECRETS_DIR": str(secrets_root)},
        answers=answers,
        layout=layout,
        prefixes=prefixes,
    )
    ask_secrets(p, secret_files, answers, secrets_root=secrets_root)

    writes: dict[Path, tuple[str, int | None]] = {}
    if sel.new_stack:
        spec_path = _stack_spec_path(sel.new_stack)
        writes[spec_path] = (render_stack_spec(sel.new_stack), None)
    if write_compose:
        env_files = env_file_lines(layout, sel.app, name)
        writes[compose_path] = (render_app_compose(sel, deploy_dir, network, env_files), None)
    makefile = deploy_dir / "Makefile"
    if (stacks_root / "common.mk").exists() and not makefile.exists():
        writes[makefile] = ("include ../common.mk\n", None)
    # Every listed env file must exist (compose errors on a missing one).
    for env_path in layout.files:
        updates = answers.env_updates.get(env_path)
        if updates:
            current = env_path.read_text() if env_path.exists() else ""
            writes[env_path] = (update_env_text(current, updates), 0o600)
        elif not env_path.exists():
            writes[env_path] = ("", 0o600)
    for path, value in answers.secret_writes.items():
        writes[path] = (value, 0o600)

    p.say("")
    p.say("Summary:")
    if sel.new_stack:
        p.say(f"  generate stack {sel.new_stack_path.name}")
    for path in writes:
        what = "(secret)" if path in answers.secret_writes else ""
        p.say(f"  write {path} {what}".rstrip())
    for path in answers.secret_kept:
        p.say(f"  keep  {path}")
    if not writes:
        p.say("  nothing to write")
        return 0

    if dry_run:
        shown = dict(writes)
        if sel.new_stack:
            shown[sel.new_stack_path] = (sel.new_stack_content, None)
        for path, (content, _) in shown.items():
            if path in answers.secret_writes:
                continue
            p.say(f"----- {path} -----")
            p.say(content.rstrip())
        return 0

    if not p.confirm("Write these files?"):
        p.say("Nothing written.")
        return 1

    for path, (content, mode) in writes.items():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if mode else 0o777)
        path.write_text(content)
        if mode is not None:
            path.chmod(mode)
        p.say(f"wrote {path}")
    if sel.new_stack:
        scaffold.scaffold_stack(sel.new_stack, source=_stack_spec_source(sel.new_stack))

    p.say("")
    p.say(f"Next: cd {deploy_dir} && docker compose up -d")
    return 0
