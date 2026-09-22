# Template scaffolding

A generator (`docker_templates_tools`) that produces `templates/services/base/<name>/`
docker-compose bundles — and `templates/stacks/docker-compose.<app>-<dep>.yml` files —
from small YAML specs, instead of hand-copying an existing service.

If you're writing a new spec, use the `scaffold-service` skill
(`.claude/skills/scaffold-service/`) rather than reading this file cold —
it walks through the workflow. This file is the schema reference.

## Repo layout

Three separate concerns, three separate top-level directories:

- `tools/` (this directory) — the generator itself: `src/`, `tests/`,
  `pyproject.toml`. Pure code, no spec/template data.
- `templates/` — everything the generator reads and writes: `specs/`
  (the YAML you author) and its output, `services/` and `stacks/`. Specs
  and their generated output live together because they're two views of
  the same thing, not because either belongs to the tool.
- `example_app/` — real deployments that `include:` a stack from
  `templates/stacks/` and layer deployment-specific bits on top (an extra
  network, a reverse-proxy sidecar, ...). Consumes `templates/`, isn't
  part of it.

Every file this tool writes opens with a two-line comment (`Auto-generated
from templates/specs/... — do not edit directly.`) pointing back at the spec
that produced it, so anyone who opens the compose file directly knows where
to make the real change — see `generated_header()` in `scaffold.py`.

## The pattern

Every `templates/services/base/<name>/` directory is the same shape:

- `docker-compose.yml` — an `include:` list of the layer files below
  (alphabetical: backup, base, and any flat extensions like smtp), preceded
  by the spec's `header_comment` if it has one.
- `docker-compose.base.yml` — the real service: image pinned by an env var
  (`${<NAME>_TAG:?...}` or `:-default`), `hostname: <name>.${STACK_DOMAIN:?...}.local`,
  `restart`/`pull_policy` both overridable via env vars, secrets sourced from
  `${SECRETS_DIR}/env/${COMPOSE_PROJECT_NAME}/<secret>`, a healthcheck
  (10s/5s/3 retries/20s start_period is the repo-wide default),
  `deploy.resources.limits` with `<NAME>_CPU_LIMIT`/`<NAME>_MEM_LIMIT` env
  overrides, and — if the spec has any — its `labels:`.
- `docker-compose.backup.yml` — includes `../backup/docker-compose.yml`,
  defines an `x-volumes` anchor reused by the shared `backup`/`restore`
  services, and a `restore.d` script that `chown`s and locks down
  permissions on the volumes that opted into it.
- `README.md` — a plain-English notice (not a compose file, not part of
  the `include:` manifest) saying this directory is generated from the
  spec and pointing at `templates/services/custom/<name>/` for anything hand-written.

`--force` regenerates a service's whole directory from scratch — it
deletes `templates/services/base/<name>/` first, then writes everything fresh, so a
schema change that drops a file (an old grouped-extension variant, a
since-removed layer) doesn't leave it behind as an orphan. Without
`--force`, an existing directory is left untouched and the generator
refuses instead of writing into it. `--dry-run` never touches disk
regardless of `--force` — it just reports (`would remove ... before
regenerating`) what a real `--force` run would delete first.

`templates/stacks/` gets the same treatment at the directory level: every
`docker-compose.<app>-<dep>.yml` there is generated, and a shared
`README.md` (rewritten by whichever stack you regenerate next, not
force-gated — it's identical every time) says so and points at
`example_app/` for real per-deployment wiring.

A service can also declare **extensions** — optional add-on layers like
gitea's SMTP support or its choice of database backend (see
`templates/services/base/gitea` and its `database/` subdirectory). An extension group
with more than one variant (gitea: mariadb/mysql/sqlite3, grouped under
"database") gets its own `<group>/` subdirectory, since only one variant is
ever included at a time by a stack. A group with a single variant (smtp)
stays a flat sibling file — there's no choice to make, so no directory.

## Labels

`labels:` on a service spec works exactly like `environment:` — the same
flat map, the same nested-prefix shorthand (see the schema table below) —
and renders straight into `docker-compose.base.yml`, no separate file. A
string leaf goes through the same `?`/`-` shorthand as environment values;
any other leaf type (a list, a dict, a bare bool) gets JSON-encoded rather
than Python's `str()`, so e.g. a homepage widget's `fields: [a, b]` or an
npm proxy's `domain_names: [...]` come out as real JSON (`["a", "b"]`,
lowercase `true`/`false`) instead of `['a', 'b']`/`True`.

`tools/config/labels.yaml` declares auto-filled label defaults by
**pattern**, not by checking for a hardcoded namespace key: each entry has
a `pattern` (e.g. `"homepage."`) and a `defaults` map. If *any* of a
service's already-flattened label keys starts with that pattern, whichever
of that group's `defaults` keys the spec didn't already set get filled in
(the spec's own value always wins), with `%(display_name)s`/
`%(service_name)s`/`%(description)s` placeholders substituted per service —
`${...}` is left untouched, since that's a literal compose variable
reference, not a template placeholder. This is how every service's
`homepage.group`/`name`/`icon`/`description` get filled in from just
`labels: {homepage.: {...}}` without repeating them per spec — see
`apply_label_defaults()` in `scaffold.py`.

## Out of scope, by design

`-cli`/`-init` companion services (gitea's `gitea-cli`/`gitea-init`,
wordpress's `wordpress-cli`/`wordpress-init`, passbolt's `passbolt-cli`/
`passbolt-init` — anything using `extends: service: <svc>`, or defining more
than one service in a file, which this generator can't model). Hand-write
these as `templates/services/custom/<name>/docker-compose.custom.yml` (see
`templates/services/custom/gitea/docker-compose.custom.yml` for the pattern), set
`custom: true` in the *stack* spec (which derives the include path from the
app's own name), and wire each companion service into `extra_services` —
see `templates/specs/stacks/gitea-mariadb.yaml`. A service with its own image
that merely depends on another one (act-runner on gitea) isn't a
`custom.yml` case at all — it gets its own normal spec, wired into a stack's
`extra_services` with its own `include:`.

## Using the generator

Requires `uv` (already set up here — `pyproject.toml` + `uv.lock`; `attrs`
and PyYAML are the only runtime dependencies, used for the spec model and
to load/dump/validate the YAML rather than hand-formatting strings). Tests
live in their own `test`/`dev` dependency-groups (`pytest`, `pytest-cov`,
`pytest-html`) — see "Running the tests" below.

Specs live under `templates/specs/services/<name>.yaml` and `templates/specs/stacks/<app>-<dep>.yaml`.

```sh
cd tools
uv run python -m docker_templates_tools service ../templates/specs/services/mariadb.yaml --dry-run
uv run python -m docker_templates_tools service ../templates/specs/services/mariadb.yaml   # writes templates/services/base/mariadb/*
uv run python -m docker_templates_tools stack ../templates/specs/stacks/nginx-proxy-manager-mariadb.yaml --dry-run
```

`--force` overwrites existing output — for a service that means deleting
and regenerating its whole `templates/services/base/<name>/` directory (see "The
pattern" above); for a stack it's a plain per-file overwrite, since a
stack is just one file. Every generated file is parsed back with
`yaml.safe_load` before being written, so a malformed spec fails loudly
instead of producing broken YAML.

With no `service`/`stack` subcommand, it regenerates every spec under
`templates/specs/services/*.yaml` and `templates/specs/stacks/*.yaml` in
one pass instead of a single named one — same `--force`/`--dry-run`
semantics, just applied across the board:

```sh
cd tools
uv run python -m docker_templates_tools --dry-run   # preview every spec's output
uv run python -m docker_templates_tools --force     # regenerate everything
```

### Running the tests

```sh
cd tools
uv sync --all-groups        # installs pytest, pytest-cov, pytest-html
uv run pytest
uv run pytest --cov --cov-report=term-missing   # coverage
uv run pytest --html=report.html --self-contained-html   # HTML report
```

`tests/test_golden.py` is the important one for anyone touching
`scaffold.py`/`render.py`/`models.py`: it runs the real CLI against every
spec under `specs/` and diffs the output byte-for-byte against the
committed files under `templates/services/base/`/`templates/stacks/` — a change that alters
generated output for any real spec fails there.

The `Test` GitHub Actions workflow (`.github/workflows/test.yml`) runs
this same suite on every push/PR and can be triggered manually from the
Actions tab. `Lint` (`ruff check` + `ruff format --check`) and `Security`
(`pip-audit` against `tools/`'s locked dependencies) run alongside it —
see the root `README.md` for the full list of workflows.

### Naming convention: no shared "category" prefix

Env vars and secrets are always prefixed by **the owning service's own
name** — `MARIADB_NAME`/`mariadb_password` for the mariadb service, and the
*same* `MARIADB_NAME`/`mariadb_password` for gitea's "mariadb" database
extension variant. They converge on the same names independently, because
both are named after "mariadb" — no shared generic prefix (like a
cross-engine `DB_NAME`) is needed. Swapping which database engine a service
uses already means swapping which extension file gets included, so there's
no "change engines without touching `.env`" case to design for.

### Service spec schema

Only `image` is required — everything else has a convention-based default.
See `templates/specs/services/mariadb.yaml` (single volume + backup + homepage),
`templates/specs/services/redis.yaml` (a custom `command:`), and `templates/specs/services/gitea.yaml`
(extensions: a grouped `database/` variant set plus a flat `smtp`) for real
examples.

**No dependency wiring in a service spec.** A service spec describes only
that service — never another one. Even a hard, always-present dependency
(act-runner always needs gitea) is wired in the *stack* spec instead (see
the stack schema below), not as a field here. This keeps specs minimal and
keeps a service's own file reusable outside any particular stack.

| field | default | notes |
|---|---|---|
| `image` | — | e.g. `mariadb`, `gitea/gitea` |
| `service.name` | `image`'s last `/`-segment | the compose service key and `templates/services/base/<name>` directory |
| `display.name` | `service.name`, title-cased | override for brand casing (`MariaDB`, not `Mariadb`) |
| `description` | — | short noun phrase; falls into the homepage description default |
| `tag_required` | `false` | `true` → `${<NAME>_TAG:?<NAME>_TAG is required}` |
| `tag_default` | `"latest"` | used when not required |
| `command` | — | raw shell text, rendered as a block literal |
| `environment` | `{}` | `KEY: value` — value supports shorthand: `"?xxx"` → `${<NAME>_XXX:?<NAME>_XXX is required}`, `"-xxx:default"` → `${<NAME>_XXX:-default}`, anything else is literal passthrough. Values that YAML would otherwise read as bool/null/number (e.g. `"1"`, `"on"`) are auto double-quoted. A key can nest instead of repeating a shared prefix: `GITEA__server__: {SSH_: {PORT: 22}}` flattens to `GITEA__server__SSH_PORT: 22` — nesting is pure string concatenation (no separator inserted), so each level's key must already end in whatever `_`/`.`/`__` the real var name needs (see `templates/specs/services/gitea.yaml`). |
| `secrets` | `[]` | `{name, env_var?, shared?, bare?}` — `name` is the short suffix (`password` → `mariadb_password`); `env_var` adds `<env_var>: /run/secrets/<full name>` to `environment`; `shared: true` stores the file at `.../env/000-generic/<prefix>/<suffix>` instead of per-project (see gitea's smtp password); `bare: true` skips the prefix entirely — the secret name is the suffix verbatim (see gitea's `security_secret_key`, which isn't `gitea_security_secret_key`) |
| `configs` | `[]` | `{name, target, content, mode?}` — `name` is the short suffix, always prefixed by the service name (`tuning` → `mariadb_tuning`) |
| `volumes` | `{}` | a map: `<suffix>: {path, backup?, backup_target?, owner?, mod?, read_only?}`. The compose volume **name is never given directly** — always `<service>_<suffix>`. `path` is the real in-container mount path. `backup: true` includes the volume in the backup/restore `x-volumes` anchor (and is what triggers generating `docker-compose.backup.yml` at all — at least one volume or bind mount needs it). `owner: {uid, gid}` is independent and only meaningful alongside `backup: true`: it additionally writes a chown/chmod step into the restore script for that volume — a volume can be backed up without one (nothing to fix), but not the other way around. `mod: {dirs, files}` defaults to `700`/`600` (the repo-wide convention), overridable per volume. `backup_target` defaults to `/mnt/<service>` when the suffix is `"data"`, else `/mnt<path>` — override when the hand-written convention renamed it (mariadb's `/mnt/var/lib/mariadb`, not `mysql`). |
| `bind_mounts` | `[]` | `[{source, target, backup?, backup_target?, owner?, mod?, read_only?}]` — raw passthrough bind mounts that aren't managed volumes (e.g. gitea's gpg-key directory mount). `backup`/`owner`/`mod` work exactly as on a volume. |
| `healthcheck` | — | `{test, interval?, timeout?, retries?, start_period?}` — the last four default to the repo-wide 10s/5s/3/20s |
| `cpu_default` / `mem_default` | `"1.0"` / `"512M"` | |
| `labels` | `{}` | a flat/nested map, same rules as `environment` (including the `?`/`-` value shorthand) — see the "Labels" section above |
| `dir_prefix` | — | overrides the output directory + restore-script/anchor naming only (not the compose service key/hostname/env-var naming) — for a service whose image's last path segment doesn't match its real name (nginx-proxy-manager's service is `npm`, its directory is `nginx-proxy-manager`) |
| `header_comment` | — | a plain multi-line string (no `#` prefixes) rendered as a leading comment block on `docker-compose.yml`, right after the auto-generated-file notice — a one-glance summary of what the service is and any non-obvious fact (e.g. that a database/smtp extension is required, or that it's never used standalone) |
| `extensions` | `{}` | a map: `<group-key>: {...}`. If the value has a `variants:` map, each variant key becomes its own file under `templates/services/base/<name>/<group-key>/docker-compose.<variant>.yml`, and each variant's naming prefix is its own key (gitea's `database` group: `mariadb`/`mysql`/`sqlite3`). If the value has `environment`/`secrets` directly (no `variants:`), it's a single flat `docker-compose.<group-key>.yml` sibling file (gitea's `smtp`), prefixed by the group key itself. Either shape supports the same `environment`/`secrets` fields as the main spec (minus volumes/healthcheck/etc — an extension only ever adds environment and secrets to the main service). |

### Stack spec schema

```yaml
app: wordpress
dep:
  - mariadb
  - redis

extensions:
  database: mariadb

custom: true

app_extra:
  secrets:
    - redis_password
  environment:
    WORDPRESS_CONFIG_EXTRA: |
      define('WP_REDIS_HOST', 'redis');
      ...

extra_services:
  - name: wordpress-cli
    extra:
      secrets: [redis_password]
      environment: {WORDPRESS_CONFIG_EXTRA: "..."}
  - name: wordpress-init
    networks: []
    extends: wordpress-cli
    extra:
      configs:
        - source: wordpress_init_05_redis_object_cache
          target: /docker-entrypoint-init.d/05-redis-object-cache.sh

extra:
  configs:
    wordpress_init_05_redis_object_cache:
      content: |
        set -e
        ...

header_comment: |
  Composed template: WordPress + MariaDB + Redis.
  ...
```

- `app`/`dep` are the compose service keys used inside the generated file.
  `dep` can be a single name or a list — `app` health-gate-depends_on's
  every one of them (wordpress depends on both `mariadb` and `redis`).
- `app_slug`/`dep_slug` (optional; default to `app`, and to `dep` joined
  with `-` when it's a list) control both the output filename
  (`docker-compose.<app_slug>-<dep_slug>.yml`) and the `templates/services/base/<dir>/`
  directory looked up for every include below — only needed when a
  service's directory differs from its compose service key (npm's service
  is `npm`, its directory is `nginx-proxy-manager`).
- Nothing about `app`'s or any `dep`'s own `docker-compose.yml` include is
  ever spelled out — it's always derived as
  `../services/base/<app_slug-or-dep>/docker-compose.yml`.
- `extensions: {<group>: <variant>}` derives that variant's include path
  (`../services/base/<app_slug>/<group>/docker-compose.<variant>.yml`) —
  e.g. gitea's `database: mariadb`.
- `custom: true` derives `../services/custom/<app_slug>/docker-compose.custom.yml`
  — the hand-written `-cli`/`-init` companion file.
- No service spec declares its own `networks:` — the stack owns all of it.
  `app` and every `dep` always get `backend`; `app_networks`/`dep_networks`
  are *extra* networks on top of that (gitea also needs `runner`, to reach
  act-runner). Any network name besides `backend` gets declared at the
  stack's top level automatically.
- `app_extra` merges arbitrary compose keys straight into the app's own
  service block (before `networks:`/`depends_on:`) — this is where a hard,
  stack-specific bridge between two services belongs (wordpress's redis
  cache wiring), never in either service's own spec.
- `dep_extra` is the same merge, applied uniformly to every `dep` service's
  own block (before `networks:`) — there's no per-dep variant, since every
  `dep` plays the same role from the stack's point of view. Used for e.g.
  tagging every dep with a stack-tier profile (`backend`) that isn't part
  of the dep's own generic spec.
- `extra_services` is for anything beyond the plain app/deps set. Each
  entry is either a plain service name (rides `backend`, e.g. `gitea-cli`)
  or a dict:
  - `include:` adds another file to the top-level `include:` list, for a
    service not already pulled in via `app`/`extensions`/`custom` (act-runner
    has its own image and spec, so it needs its own include).
  - `networks` defaults to `["backend"]`; pass `[]` to opt a service out of
    it entirely (act-runner only needs `runner`; wordpress-init just
    extends wordpress-cli and needs nothing of its own).
  - `depends_on: <name>` (a plain string) expands to the same health-gated
    form app/dep wiring already uses; give the full compose shape directly
    if you need something other than `condition: service_healthy, restart: true`.
  - `extends: <name>` expands to `extra: {extends: {service: <name>}}` — the
    shorthand for a service that's really just `extends:`-ing another one
    already defined by the included custom file.
  - `extra:` is a generic arbitrary-keys passthrough merged into that
    service's block verbatim (secrets/environment overrides, alone or
    combined with `extends:` above).
- top-level `extra:` merges arbitrary keys into the file's root, alongside
  `services:`/`networks:` — used for a `configs:` block backing something
  in `extra_services` (wordpress-init's redis/cache-enabler init scripts).
- Any multi-line string anywhere in `app_extra`/`extra_services[].extra`/the
  top-level `extra` renders as a YAML block literal (`|`) automatically,
  not folded — see `literalize()` in `compose_yaml.py`.
- `header_comment` is a plain multi-line string (no `#` prefixes) describing
  the stack; it's rendered as a leading comment block, right after the
  auto-generated-file notice every file gets.

Real examples: `templates/specs/stacks/nginx-proxy-manager-mariadb.yaml` is close to
the plainest form (`app`/`app_slug`/`dep` plus its one `extensions:`
choice, no `custom`/extras at all); `templates/specs/stacks/gitea-mariadb.yaml` and
`templates/specs/stacks/passbolt-mariadb.yaml` show `custom: true` plus
`extra_services` for the `-cli`/`-init` companions (gitea's also adds
act-runner as a genuinely separate, dependent service); `templates/specs/stacks/wordpress-mariadb-redis.yaml`
shows a list `dep:`, `app_extra`, and top-level `extra:` together.

## Known cosmetic deviations from hand-written files

- Multi-line shell snippets (`command:`, config `content:`) render as
  literal block scalars (`|`) rather than folded (`>`). Semantically
  equivalent for `sh -c` scripts that already end each line with `;`
  (a real newline and a folded space both just separate statements), but
  worth knowing if you're diffing against an old file byte-for-byte.
- PyYAML sometimes picks single quotes, or no quotes, where a hand-written
  file used double quotes defensively (e.g. a value containing `: `). Both
  are valid YAML with no functional difference — not worth chasing, since
  these files are meant to be generated, not hand-tuned afterward.
