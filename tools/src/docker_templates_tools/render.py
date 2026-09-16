"""Renderers — one file each, split the same way as the hand-written
services/base/* directories are: docker-compose.yml is just an include
manifest, docker-compose.base.yml is the real service (env, secrets,
volumes, and its `labels:`), and docker-compose.backup.yml is the
backup/restore layer. On top of that, a service can declare "extensions" —
optional add-on layers like gitea's SMTP support or its choice of database
backend (see services/base/gitea and its database/ subdirectory for the
reference). An extension group with more than one variant (gitea:
mariadb/mysql/sqlite3, grouped under "database") gets its own <group>/
subdirectory, since only one variant is ever included at a time by a stack.
A group with a single variant (smtp) stays a flat sibling file — there's no
choice to make, so no directory.

render_stack()/resolve_stack_spec() are the stack-spec counterparts — see
their own docstrings for the stack schema.
"""

from __future__ import annotations

import json
from typing import Optional

from .compose_yaml import (
    AlwaysQuoted,
    FlowSeq,
    Literal,
    expand_env_value,
    frag,
    literalize,
    quote_if_ambiguous,
)
from .models import Extension, ExtensionGroup, ServiceSpec
from .naming import apply_prefix, secret_file


def comment_block(text: str) -> list:
    """A plain multi-line string (no `#` prefixes) rendered as a leading
    YAML comment block — used for both a service's and a stack's
    `header_comment`."""
    return [f"# {hl}" if hl else "#" for hl in text.strip("\n").splitlines()]


def generated_header(source: str) -> str:
    """Comment header prepended to every file this tool writes, so anyone
    who opens it knows it's generated and where the real source lives."""
    return (
        f"# Auto-generated from {source} — do not edit directly.\n"
        "# Edit the spec and regenerate; see tools/README.md.\n"
    )


def render_index(layers: list, header_comment: Optional[str] = None) -> str:
    lines = []
    if header_comment:
        lines.extend(comment_block(header_comment))
        lines.append("")
    lines.append("include:")
    for layer in layers:
        lines.append(f"  - {layer}")
    lines.append("")
    return "\n".join(lines) + "\n"


def render_service_readme(spec: ServiceSpec, source: str) -> str:
    """A plain-English notice dropped into every generated
    services/base/<name>/ directory — the compose files already carry a
    comment-style version of this, but a README is what a person actually
    opens first when browsing the directory tree."""
    return (
        f"# {spec.display_name} — generated, do not edit\n"
        "\n"
        f"Everything in this directory is generated from `{source}` — do\n"
        "not edit or add files here directly. Regenerating this service\n"
        "(`--force`) replaces the whole directory, so anything hand-added\n"
        "here is silently lost.\n"
        "\n"
        "- To change this service, edit the spec and regenerate — see\n"
        "  tools/README.md.\n"
        "- To add a hand-written companion service (e.g. a `-cli`/`-init`\n"
        f"  `extends:` companion), put it under `templates/services/custom/{spec.dir_name}/`\n"
        "  instead, and wire it into the owning stack spec's `extra_services`\n"
        "  (see tools/README.md).\n"
    )


def render_stacks_readme() -> str:
    """A plain-English notice dropped into templates/stacks/ — same idea as
    render_service_readme(), but not per-stack: every stack file in this
    one flat directory shares the same notice, so it's rewritten (not
    force-gated) each time any single stack is regenerated."""
    return (
        "# Stacks — generated, do not edit\n"
        "\n"
        "Every `docker-compose.<app>-<dep>.yml` file in this directory is\n"
        "generated from `templates/specs/stacks/<app>-<dep>.yaml` — do not\n"
        "edit or add files here directly.\n"
        "\n"
        "- To change a stack, edit its spec and regenerate — see\n"
        "  tools/README.md.\n"
        "- For real per-deployment wiring on top of a stack (extra\n"
        "  networks, a reverse-proxy sidecar, TLS, ...), `include:` the\n"
        "  stack file from your own docker-compose.yml instead of editing\n"
        "  it here — see example_app/ for real examples.\n"
    )


def render_base(spec: ServiceSpec) -> str:
    lines = ["services:", f"  {spec.service_name}:"]
    lines.append(f"    image: {spec.image}:{spec.tag_env}")
    lines.append(f"    hostname: {spec.service_name}.${{STACK_DOMAIN:?STACK_DOMAIN is required}}.local")
    lines.append("    restart: ${RESTART:-unless-stopped}")
    lines.append("    pull_policy: ${PULL_POLICY:-missing}")

    if spec.command:
        lines.append("")
        lines.extend(frag({"command": Literal(spec.command)}, 4))

    env = {
        k: quote_if_ambiguous(expand_env_value(apply_prefix(v, spec.upper)))
        for k, v in spec.environment.items()
    }
    for sec in spec.secrets:
        if sec.env_var:
            env[sec.env_var] = f"/run/secrets/{sec.name}"
    if env:
        lines.append("")
        lines.extend(frag({"environment": env}, 4))

    if spec.secrets:
        lines.append("")
        lines.extend(frag({"secrets": [s.name for s in spec.secrets]}, 4))

    if spec.configs:
        lines.append("")
        configs_list = [
            {"source": c.name, "target": c.target, **({"mode": c.mode} if c.mode else {})}
            for c in spec.configs
        ]
        lines.extend(frag({"configs": configs_list}, 4))

    if spec.volumes or spec.bind_mounts:
        lines.append("")
        vol_list = [
            {"type": "volume", "source": v.name, "target": v.path, "read_only": v.read_only}
            for v in spec.volumes
        ]
        for bm in spec.bind_mounts:
            vol_list.append({"type": "bind", "source": bm.source, "target": bm.target, "read_only": bm.read_only})
        lines.extend(frag({"volumes": vol_list}, 4))

    if spec.healthcheck:
        hc = spec.healthcheck
        lines.append("")
        lines.extend(
            frag(
                {
                    "healthcheck": {
                        "test": FlowSeq(AlwaysQuoted(t) for t in hc.test),
                        "interval": hc.interval,
                        "timeout": hc.timeout,
                        "retries": hc.retries,
                        "start_period": hc.start_period,
                    }
                },
                4,
            )
        )

    lines.append("")
    lines.extend(
        frag(
            {
                "deploy": {
                    "resources": {
                        "limits": {
                            "cpus": AlwaysQuoted(f"${{{spec.upper}_CPU_LIMIT:-{spec.cpu_default}}}"),
                            "memory": f"${{{spec.upper}_MEM_LIMIT:-{spec.mem_default}}}",
                        }
                    }
                }
            },
            4,
        )
    )

    if spec.extra:
        lines.append("")
        lines.extend(frag(spec.extra, 4))

    if spec.labels:
        label_lines = [
            f"{k}={expand_env_value(v) if isinstance(v, str) else json.dumps(v)}"
            for k, v in spec.labels.items()
        ]
        lines.append("")
        lines.extend(frag({"labels": label_lines}, 4))

    trailing_sections = []

    top_secrets = {s.name: {"file": secret_file(s, spec.service_name)} for s in spec.secrets}
    if top_secrets:
        trailing_sections.append(frag({"secrets": top_secrets}, 0))

    if spec.configs:
        configs_section = ["configs:"]
        for i, c in enumerate(spec.configs):
            if i > 0:
                configs_section.append("")
            configs_section.extend(frag({c.name: {"content": Literal(c.content)}}, 2))
        trailing_sections.append(configs_section)

    if spec.volumes:
        volumes_doc = {
            v.name: {
                "labels": [
                    f"pro.asemo.description={spec.display_name} {v.suffix} directory for ${{COMPOSE_PROJECT_NAME}}",
                    f"pro.asemo.service={spec.service_name}",
                ]
            }
            for v in spec.volumes
        }
        trailing_sections.append(frag({"volumes": volumes_doc}, 0))

    lines.append("")
    for i, section in enumerate(trailing_sections):
        if i > 0:
            lines.append("")
        lines.extend(section)

    lines.append("")
    return "\n".join(lines)


def render_backup(spec: ServiceSpec) -> str:
    # Every volume/bind mount with backup: true shares one anchor, so it gets
    # tarred and restored. Only the ones that also have owner: get a
    # chown/chmod step — a volume can be backed up without its ownership
    # being fixed on restore, if that's not needed.
    participants = [(v.name, v) for v in spec.backup_volumes] + [(bm.source, bm) for bm in spec.backup_binds]
    owned = [(source, p) for source, p in participants if p.owner is not None]
    anchor = f"{spec.dir_name}-volumes"

    lines = ["include:", "  - ../backup/docker-compose.yml", ""]
    vol_lines = [f"{source}:{p.backup_target}" for source, p in participants]
    lines.append(f"x-volumes: &{anchor}")
    lines.extend(frag({"volumes": vol_lines}, 2))
    lines.append("")
    lines.append("services:")
    lines.append(f"  backup: *{anchor}")
    lines.append("  restore:")
    lines.append(f"    <<: *{anchor}")

    if owned:
        lines.extend(
            frag(
                {
                    "configs": [
                        {
                            "source": f"{spec.dir_name}_restore_00_owner",
                            "target": f"/srv/configs/restore.d/00-{spec.dir_name}-owner.sh",
                        }
                    ]
                },
                4,
            )
        )
        lines.append("")

        script_lines = ["set -e"]
        for i, (_, p) in enumerate(owned):
            if i > 0:
                script_lines.append("")
            script_lines.append(f"chown -R {p.owner.uid}:{p.owner.gid} {p.backup_target}")
            script_lines.append(f"find {p.backup_target} -type d -exec chmod {p.mod.dirs} {{}} +")
            script_lines.append(f"find {p.backup_target} -type f -exec chmod {p.mod.files} {{}} +")
        script = "\n".join(script_lines) + "\n"

        lines.extend(frag({"configs": {f"{spec.dir_name}_restore_00_owner": {"content": Literal(script)}}}, 0))

    lines.append("")
    return "\n".join(lines)


def render_extension(spec: ServiceSpec, group: ExtensionGroup, variant: Extension) -> str:
    prefix = variant.key.upper()
    env = {
        k: quote_if_ambiguous(expand_env_value(apply_prefix(v, prefix)))
        for k, v in variant.environment.items()
    }
    for sec in variant.secrets:
        if sec.env_var:
            env[sec.env_var] = f"/run/secrets/{sec.name}"

    lines = ["services:", f"  {spec.service_name}:"]
    if env:
        lines.extend(frag({"environment": env}, 4))
    if variant.secrets:
        lines.append("")
        lines.extend(frag({"secrets": [s.name for s in variant.secrets]}, 4))

    if variant.secrets:
        top_secrets = {s.name: {"file": secret_file(s, variant.key)} for s in variant.secrets}
        lines.append("")
        lines.extend(frag({"secrets": top_secrets}, 0))

    lines.append("")
    return "\n".join(lines)


def resolve_extra_service(svc):
    """Expand an extra_services entry's shorthand: `extends: <name>` becomes
    the full `extra: {extends: {service: <name>}}` passthrough (listed
    first, ahead of any other `extra` keys already given), and a plain
    string `depends_on: <name>` becomes the same health-gated form app/dep
    wiring already uses."""
    if isinstance(svc, str):
        return svc
    svc = dict(svc)
    extends = svc.pop("extends", None)
    if extends:
        svc["extra"] = {"extends": {"service": extends}, **(svc.get("extra") or {})}
    depends_on = svc.get("depends_on")
    if isinstance(depends_on, str):
        svc["depends_on"] = {depends_on: {"condition": "service_healthy", "restart": True}}
    return svc


def resolve_stack_spec(d: dict) -> dict:
    """Expand a minimal stack spec the same way a service spec derives names
    from its own key: app/dep's own docker-compose.yml include is never
    spelled out, an `extensions:` choice derives that variant's include
    path, and `custom: true` derives the hand-written companion file's
    path — none of it is written twice in the spec. `dep` may be a single
    name or a list (e.g. wordpress depends on both mariadb and redis)."""
    app = d["app"]
    deps = d["dep"] if isinstance(d["dep"], list) else [d["dep"]]
    # app_slug is the services/base/<dir>/ directory name, which only
    # differs from the app's own compose service key when a spec set
    # dir_prefix (nginx-proxy-manager's service is "npm" but its directory
    # is "nginx-proxy-manager") — same slug already used for the output
    # stack filename below.
    app_dir = d.get("app_slug", app)

    app_includes = [f"../services/base/{app_dir}/docker-compose.yml"]
    for group, variant in (d.get("extensions") or {}).items():
        app_includes.append(f"../services/base/{app_dir}/{group}/docker-compose.{variant}.yml")
    if d.get("custom"):
        app_includes.append(f"../services/custom/{app_dir}/docker-compose.custom.yml")

    dep_includes = [f"../services/base/{dep}/docker-compose.yml" for dep in deps]

    return {
        "app": app,
        "app_includes": app_includes,
        "deps": deps,
        "dep_includes": dep_includes,
        "app_networks": d.get("app_networks"),
        "dep_networks": d.get("dep_networks"),
        "app_extra": d.get("app_extra"),
        "extra_services": [resolve_extra_service(s) for s in d.get("extra_services", [])],
        "extra": d.get("extra"),
        "header_comment": d.get("header_comment"),
    }


def render_stack(
    app: str,
    app_includes: list,
    deps: list,
    dep_includes: list,
    app_networks: Optional[list] = None,
    dep_networks: Optional[list] = None,
    app_extra: Optional[dict] = None,
    extra_services: Optional[list] = None,
    extra: Optional[dict] = None,
    header_comment: Optional[str] = None,
) -> str:
    """A stack, not a service spec, owns every network attachment — no
    service spec declares its own `networks:`. `app`/`deps` always get
    `backend`; `app_networks`/`dep_networks` are *extra* networks on top of
    that (e.g. gitea also needs `runner`, to reach act-runner). Any network
    name other than `backend` gets declared at the top level automatically.
    `app` health-gated-depends_on's every service in `deps` (e.g. wordpress
    depends on both mariadb and redis).

    `app_extra` merges arbitrary compose keys into the app's own service
    block (before `networks`/`depends_on`) — this is where a hard,
    stack-specific bridge between two services belongs (e.g. wordpress's
    redis cache wiring), never in either service's own spec. `extra` does
    the same at the top level of the file (e.g. a `configs:` block backing
    an extra_services entry).

    extra_services entries are either a plain service name (just attached
    to `backend` — e.g. gitea-cli riding along with gitea) or a dict
    {name, include?, depends_on?, networks?, extra?} for a service that
    needs its own include and/or its own dependency wiring — this is where
    a hard, always-present dependency between two services belongs (e.g.
    act-runner always depends on gitea), never in either service's own
    spec. `networks` defaults to ["backend"]; pass [] for a service that
    doesn't need `backend` at all (e.g. act-runner only needs `runner`).
    """
    extra_services = extra_services or []
    app_networks = ["backend"] + [n for n in (app_networks or []) if n != "backend"]
    dep_networks = ["backend"] + [n for n in (dep_networks or []) if n != "backend"]
    extra_includes = [
        s["include"] for s in extra_services if isinstance(s, dict) and s.get("include")
    ]

    all_networks = set(app_networks) | set(dep_networks)
    for svc in extra_services:
        if isinstance(svc, dict):
            all_networks.update(svc.get("networks", ["backend"]))
    extra_network_names = sorted(all_networks - {"backend"})

    lines = []
    if header_comment:
        lines.extend(comment_block(header_comment))
        lines.append("")

    lines.append("include:")
    if len(app_includes) > 1:
        lines.append("  - path:")
        for inc in app_includes:
            lines.append(f"      - {inc}")
    else:
        lines.append(f"  - {app_includes[0]}")
    for inc in dep_includes:
        lines.append(f"  - {inc}")
    for inc in extra_includes:
        lines.append(f"  - {inc}")
    lines.append("")

    lines.append("services:")
    lines.append(f"  {app}:")
    if app_extra:
        lines.extend(frag(literalize(app_extra), 4))
    lines.extend(frag({"networks": app_networks}, 4))
    lines.extend(
        frag(
            {
                "depends_on": {
                    dep: {"condition": "service_healthy", "restart": True} for dep in deps
                }
            },
            4,
        )
    )
    lines.append("")

    for svc in extra_services:
        if isinstance(svc, str):
            name, networks, depends_on, svc_extra = svc, ["backend"], None, None
        else:
            name = svc["name"]
            networks = svc.get("networks", ["backend"])
            depends_on = svc.get("depends_on")
            svc_extra = svc.get("extra")
        lines.append(f"  {name}:")
        if svc_extra:
            lines.extend(frag(literalize(svc_extra), 4))
        if networks:
            lines.extend(frag({"networks": networks}, 4))
        if depends_on:
            lines.extend(frag({"depends_on": depends_on}, 4))
        lines.append("")

    for dep in deps:
        lines.append(f"  {dep}:")
        lines.extend(frag({"networks": dep_networks}, 4))
        lines.append("")

    if extra:
        lines.extend(frag(literalize(extra), 0))
        lines.append("")

    lines.append("networks:")
    networks_doc = {"backend": {"internal": True}}
    for n in extra_network_names:
        networks_doc[n] = None
    lines.extend(frag(networks_doc, 2))
    lines.append("")
    return "\n".join(lines)
