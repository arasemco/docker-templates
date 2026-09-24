"""The spec data model and its loader from a raw spec dict.

The spec never repeats `<service_name>` — everything derived from it
(hostname, volume/config/secret names, cpu/mem env vars, restore script
paths, label defaults like homepage's icon filename) is computed here:

- a volume's key is a short suffix (e.g. "data"); the compose volume name is
  always `<service>_<suffix>`
- a config's key is a short suffix (e.g. "tuning"); the compose config name
  is always `<service>_<suffix>`
- a secret's key is a short suffix (e.g. "password"); it's prefixed by the
  owning service's own name (`mariadb_password` for both the mariadb spec's
  own secrets and gitea's "mariadb" extension variant's secrets — they
  converge on the same name independently) — deliberately NOT a shared
  generic prefix. Swapping which database engine gitea uses already means
  swapping which extension file is included, so there's no "change engines
  without touching .env" case to design for; using each variant's own name
  is what makes gitea's mariadb extension and the services/base/mariadb
  service converge on the same secret/env names without agreeing on an
  abstract category.
- an environment entry's `"?xxx"`/`"-xxx:default"` shorthand gets the same
  prefix applied to `xxx` before expanding to `${MARIADB_XXX:?...}`
- a volume only gets chown/chmod'd by the backup/restore script if it has an
  `owner:` block; the mode defaults to 700/600 (the repo-wide convention)
  and is overridable per volume, not fixed in code
- a secret marked `shared: true` isn't per-project — its file lives at
  .../env/000-generic/<prefix>/<suffix> instead of
  .../env/${COMPOSE_PROJECT_NAME}/<name>, since it's meant to be reused
  across every stack rather than duplicated per project
- `labels:` works exactly like `environment:` (same flat/nested shape). Auto
  defaults for known label groups (e.g. homepage.*) come from
  tools/config/labels.yaml, not from this file — see apply_label_defaults
"""

from __future__ import annotations

from attrs import Factory, define, field

from .labels import apply_label_defaults
from .naming import flatten_prefixed, secret_full_name

DEFAULT_DIR_MODE = "700"
DEFAULT_FILE_MODE = "600"


@define
class VolumeOwner:
    uid: int
    gid: int


@define
class VolumeMode:
    dirs: str = DEFAULT_DIR_MODE
    files: str = DEFAULT_FILE_MODE


@define
class Volume:
    suffix: str  # short key, e.g. "data" -> compose name "<service>_data"
    path: str  # real mount path inside the container
    backup: bool = False  # include this volume in the backup/restore x-volumes anchor
    owner: VolumeOwner | None = (
        None  # only meaningful when backup=True: also chown/chmod on restore
    )
    mod: VolumeMode = Factory(VolumeMode)
    read_only: bool = False
    backup_target: str | None = None  # override; else /mnt/<service> ("data") or /mnt<path>

    name: str = field(init=False, default="")


@define
class BindMount:
    """A raw passthrough mount (not a managed volume) — e.g. a secrets-dir
    path. Can optionally participate in backup, same as a Volume."""

    source: str
    target: str
    read_only: bool = False
    backup: bool = False  # include this mount in the backup/restore x-volumes anchor
    owner: VolumeOwner | None = (
        None  # only meaningful when backup=True: also chown/chmod on restore
    )
    mod: VolumeMode = Factory(VolumeMode)
    backup_target: str | None = None  # override; else /mnt<target>


@define
class Secret:
    suffix: str  # short key, e.g. "password" -> "db_password" (service/variant-prefixed)
    env_var: str | None = None  # adds "<env_var>: /run/secrets/<full name>" to environment
    shared: bool = False  # not per-project; lives under env/000-generic/<prefix>/<suffix>
    bare: bool = False  # skip the prefix entirely; name = suffix verbatim
    # (see gitea's security_secret_key/security_internal_token/etc — unlike
    # redis_password or db_password, these aren't prefixed by anything)

    name: str = field(init=False, default="")


@define
class Config:
    suffix: str  # short key, e.g. "tuning" -> "mariadb_tuning" (service-prefixed)
    target: str
    content: str
    mode: str | None = None

    name: str = field(init=False, default="")


@define
class Healthcheck:
    test: list
    interval: str = "10s"
    timeout: str = "5s"
    retries: int = 3
    start_period: str = "20s"


@define
class Extension:
    """One optional add-on layer for the main service: its own environment
    entries, secrets, configs, and bind mounts, merged into
    `services.<service_name>` when included. configs/bind_mounts exist for
    variants that differ by more than environment — e.g. homepage's
    docker-access extension, where the socket and proxy variants each need
    their own docker.yaml content, and only the socket variant needs a
    bind mount at all."""

    key: str  # variant key, e.g. "mariadb"; for a flat (ungrouped) extension this equals the group key
    shared: bool = (
        False  # env vars and secrets are shared by every stack, under env/000-generic/<key>/
    )
    environment: dict = Factory(dict)
    secrets: list = Factory(list)  # list[Secret]
    configs: list = Factory(list)  # list[Config], named "<variant.key>_<suffix>"
    bind_mounts: list = Factory(list)  # list[BindMount]


@define
class ExtensionGroup:
    key: str  # e.g. "database" or "smtp" -> directory or file name
    grouped: bool = False  # True -> services/base/<service>/<key>/docker-compose.<variant>.yml (multiple variants)
    variants: list = Factory(list)  # list[Extension]; len 1 when not grouped


@define
class ServiceSpec:
    image: str
    description: str | None = None
    service_name: str | None = None  # falls back to image's last path segment
    display_name: str | None = None  # falls back to service_name, title-cased
    tag_required: bool = False
    tag_default: str = "latest"
    command: str | None = None
    environment: dict = Factory(dict)  # short-key -> shorthand value
    secrets: list = Factory(list)  # list[Secret]
    configs: list = Factory(list)  # list[Config]
    volumes: list = Factory(list)  # list[Volume]
    bind_mounts: list = Factory(
        list
    )  # list[BindMount] — passthrough mounts (e.g. a secrets-dir path)
    healthcheck: Healthcheck | None = None
    cpu_default: str = "1.0"
    mem_default: str = "512M"
    extra: dict = Factory(
        dict
    )  # arbitrary extra top-level service keys, merged in verbatim (e.g. logging:)
    dir_prefix: str | None = (
        None  # overrides service_name for the output directory + restore-script/anchor naming
    )
    labels: dict = Factory(
        dict
    )  # flat key: value, same shape/nesting as `environment` — see apply_label_defaults
    header_comment: str | None = (
        None  # plain multi-line text, rendered as a leading comment block on docker-compose.yml
    )
    extensions: list = Factory(list)  # list[ExtensionGroup]

    def __attrs_post_init__(self) -> None:
        if self.service_name is None:
            self.service_name = self.image.rsplit("/", 1)[-1]
        if self.display_name is None:
            self.display_name = self.service_name.replace("-", " ").title()

        self.labels = apply_label_defaults(self.labels, self)

        for v in self.volumes:
            v.name = f"{self.service_name}_{v.suffix}"
            if v.backup_target is None:
                v.backup_target = (
                    f"/mnt/{self.service_name}" if v.suffix == "data" else f"/mnt{v.path}"
                )

        for bm in self.bind_mounts:
            if bm.backup_target is None:
                bm.backup_target = f"/mnt{bm.target}"

        for s in self.secrets:
            s.name = secret_full_name(s, self.service_name)

        for c in self.configs:
            c.name = f"{self.service_name}_{c.suffix}"

        for group in self.extensions:
            for variant in group.variants:
                for s in variant.secrets:
                    s.name = secret_full_name(s, variant.key)
                for c in variant.configs:
                    c.name = f"{variant.key}_{c.suffix}"
                for bm in variant.bind_mounts:
                    if bm.backup_target is None:
                        bm.backup_target = f"/mnt{bm.target}"

    @property
    def upper(self) -> str:
        return self.service_name.upper().replace("-", "_")

    @property
    def tag_env(self) -> str:
        var = f"{self.upper}_TAG"
        if self.tag_required:
            return f"${{{var}:?{var} is required}}"
        return f"${{{var}:-{self.tag_default}}}"

    @property
    def dir_name(self) -> str:
        return self.dir_prefix or self.service_name

    @property
    def backup_volumes(self) -> list:
        return [v for v in self.volumes if v.backup]

    @property
    def backup_binds(self) -> list:
        return [bm for bm in self.bind_mounts if bm.backup]

    @property
    def has_backup(self) -> bool:
        return bool(self.backup_volumes or self.backup_binds)


def _load_secret(s: dict) -> Secret:
    return Secret(
        suffix=s["suffix"] if "suffix" in s else s["name"],
        env_var=s.get("env_var"),
        shared=s.get("shared", False),
        bare=s.get("bare", False),
    )


def _load_owner_mod(d: dict) -> tuple:
    """Shared owner/mod parsing for both a Volume and a BindMount — same
    shape, same defaults, in one place."""
    owner = d.get("owner")
    mod = d.get("mod") or {}
    return (
        VolumeOwner(**owner) if owner else None,
        VolumeMode(
            dirs=str(mod.get("dirs", DEFAULT_DIR_MODE)),
            files=str(mod.get("files", DEFAULT_FILE_MODE)),
        ),
    )


def _load_bind_mounts(defs: list) -> list:
    bind_mounts = []
    for bmdef in defs or []:
        owner, mod = _load_owner_mod(bmdef)
        bind_mounts.append(
            BindMount(
                source=bmdef["source"],
                target=bmdef["target"],
                read_only=bmdef.get("read_only", False),
                backup=bool(bmdef.get("backup", False)),
                owner=owner,
                mod=mod,
                backup_target=bmdef.get("backup_target"),
            )
        )
    return bind_mounts


def _load_configs(defs: list) -> list:
    return [
        Config(
            suffix=c["suffix"] if "suffix" in c else c["name"],
            target=c["target"],
            content=c["content"],
            mode=c.get("mode"),
        )
        for c in defs or []
    ]


def _load_extension(key: str, body: dict) -> Extension:
    shared = bool(body.get("shared", False))
    secrets = [_load_secret(s) for s in body.get("secrets", [])]
    if shared:
        for s in secrets:
            s.shared = True
    return Extension(
        key=key,
        shared=shared,
        environment=flatten_prefixed(body.get("environment", {})),
        secrets=secrets,
        configs=_load_configs(body.get("configs")),
        bind_mounts=_load_bind_mounts(body.get("bind_mounts")),
    )


def load_service_spec(d: dict) -> ServiceSpec:
    volumes = []
    for suffix, vdef in (d.get("volumes") or {}).items():
        vdef = vdef or {}
        owner, mod = _load_owner_mod(vdef)
        volumes.append(
            Volume(
                suffix=suffix,
                path=vdef["path"],
                backup=bool(vdef.get("backup", False)),
                owner=owner,
                mod=mod,
                read_only=vdef.get("read_only", False),
                backup_target=vdef.get("backup_target"),
            )
        )

    bind_mounts = _load_bind_mounts(d.get("bind_mounts"))
    secrets = [_load_secret(s) for s in d.get("secrets", [])]
    configs = _load_configs(d.get("configs"))

    hc = d.get("healthcheck")
    healthcheck = Healthcheck(**hc) if hc and hc.get("test") else None

    service = (d.get("service") or {}).get("name")
    display = (d.get("display") or {}).get("name")

    labels = flatten_prefixed(d.get("labels") or {})

    extensions = []
    for key, body in (d.get("extensions") or {}).items():
        body = body or {}
        if "variants" in body:
            variants = [
                _load_extension(vkey, vbody or {})
                for vkey, vbody in (body.get("variants") or {}).items()
            ]
            extensions.append(ExtensionGroup(key=key, grouped=True, variants=variants))
        else:
            extensions.append(
                ExtensionGroup(key=key, grouped=False, variants=[_load_extension(key, body)])
            )

    return ServiceSpec(
        image=d["image"],
        description=d.get("description"),
        service_name=service,
        display_name=display,
        tag_required=d.get("tag_required", False),
        tag_default=d.get("tag_default", "latest"),
        command=d.get("command"),
        environment=flatten_prefixed(d.get("environment", {})),
        secrets=secrets,
        configs=configs,
        volumes=volumes,
        bind_mounts=bind_mounts,
        healthcheck=healthcheck,
        cpu_default=str(d.get("cpu_default", "1.0")),
        mem_default=str(d.get("mem_default", "512M")),
        extra=d.get("extra", {}),
        dir_prefix=d.get("dir_prefix"),
        labels=labels,
        header_comment=d.get("header_comment"),
        extensions=extensions,
    )
