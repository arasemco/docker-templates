---
name: scaffold-service
description: Write a YAML spec and generate a new templates/services/base/<name>/ docker-compose bundle (or a templates/stacks/docker-compose.<app>-<dep>.yml file) using this repo's tools/ generator, instead of hand-copying an existing service's files. Use when asked to add a new base service, add a database/SMTP-style extension to an existing service, or wire two services into a stack.
---

# Scaffolding a service template

This repo generates `templates/services/base/<name>/` docker-compose bundles from
small YAML specs instead of hand-writing them — see `tools/README.md` for
the full schema reference. Service specs live under `templates/specs/services/`,
stack specs under `templates/specs/stacks/`. This skill is the step-by-step
workflow.

**Specs stay minimal, and a service spec never wires cross-service
concerns.** A service spec describes only that one service — image, its own
environment, its own volumes. Dependencies (`depends_on:`) and networks
(`networks:`) are never fields on a service spec, even for a hard,
always-present relationship (act-runner always needs gitea, and shares a
network with it) — both are wired in the *stack* spec instead (see
"Workflow: wiring a stack" below).

## Prerequisites

The generator is a `uv`-managed Python project at `tools/`. Check it's
usable before doing anything else:

```sh
cd tools && uv run python -m docker_templates_tools --help
```

If `uv` (or Python) isn't installed and you can't install it yourself in
this environment, say so and ask the person running this to install it —
don't try to work around a missing interpreter.

**`service` and `stack` are different schemas — match the subcommand to
the spec.** Running `service` against a file under `templates/specs/stacks/`
(or vice versa) isn't a graceful error, it's a raw Python traceback (a
stack spec's `extensions: {database: mariadb}` is a group→variant string;
a service spec's `extensions:` is a group→`{variants: {...}}` mapping, and
`load_service_spec` crashes trying to treat one as the other). If you see
an `AttributeError`/`KeyError` traceback instead of a clean `error: ...`
line, check you used the right subcommand before assuming the spec itself
is broken.

CI (`.github/workflows/`) checks every push/PR against what this skill
produces: `Test` re-runs `tools/`'s pytest suite (its golden tests fail if
committed output doesn't match what the current specs generate — the same
thing to watch for in step 5/regenerate-and-diff below), and `Validate
compose files` runs `docker compose config` against every generated stack.
That second one auto-discovers every `${FOO:?...}`-required variable
across `templates/`/`example_app/` to build its dummy `.env`, so a new
required variable in a spec needs no separate CI change — just run
`cd tools && uv run pytest` yourself before pushing to catch the same
class of failure locally first.

## Workflow: adding a new base service

1. **Gather the real facts about the image first.** Don't guess: check the
   image's own documentation (Docker Hub page, upstream README) for its
   actual environment variables, whether it needs secrets via `_FILE`
   suffixes, its data directory, and a working healthcheck command. Getting
   this right matters more than getting the spec's YAML shape right.

2. **Look at 1-2 existing specs first** — `templates/specs/services/mariadb.yaml`
   (single volume, backup, homepage) and `templates/specs/services/redis.yaml` (a
   custom `command:` block) are the simplest references. `templates/specs/services/gitea.yaml`
   shows a service with a large environment block plus extensions if the new
   service needs those too, and `templates/specs/services/act-runner.yaml` shows
   a service that depends on another one (with that dependency deliberately
   *not* in this file — see the stack workflow below).

3. **Write `templates/specs/services/<name>.yaml`.** Key points that are easy to
   get wrong:
   - `image` is the only required field. `service.name` only needs setting
     explicitly when it should differ from the image's last `/`-segment
     (e.g. image `jc21/nginx-proxy-manager` → set `service.name: npm`).
   - Every environment variable name the image actually expects (its LEFT
     side, e.g. `MARIADB_DATABASE`) is literal — write it exactly as the
     image needs it. Only the VALUE can use the `"?xxx"`/`"-xxx:default"`
     shorthand, which becomes `${<SERVICE>_XXX:...}` — never invent a
     value's variable name from a category or convention, it's always
     `<SERVICE_NAME_UPPER>_<XXX>`.
   - `secrets` entries are short suffixes (`password`), not full names — the
     generator prefixes them with the service name (`mariadb_password`).
     Use `bare: true` only for the rare secret that genuinely isn't
     prefixed by anything (check an existing example like gitea's
     `security_secret_key` before reaching for this).
   - `volumes` is a **map** keyed by a short suffix, not a list. A service
     with exactly one volume can omit the suffix key entirely and just use
     `data:` — with more than one volume, give each a suffix that describes
     it (see `templates/services/base/tor`'s real `etc`/`var` split for the pattern).
   - A volume (or bind mount) is only backed up if it has `backup: true` —
     that's also what triggers generating `docker-compose.backup.yml` at
     all (at least one volume/mount needs it). `owner: {uid, gid}` is
     separate and only meaningful alongside `backup: true`: it adds a
     chown/chmod step to the restore script. Look up the image's actual
     runtime user, don't guess — getting this wrong means restore chowns
     files to the wrong owner. A volume can be backed up with no `owner:`
     (nothing to fix on restore, like passbolt's `images` volume).
   - `labels` works exactly like `environment` (same flat/nested key: value
     shape, same `?`/`-` value shorthand), and renders straight into
     `docker-compose.base.yml` — there's no separate homepage file. Any
     `homepage.*` key (even just `labels: {homepage.: {}}`) is enough to
     trigger `homepage.group`/`name`/`icon`/`description` defaults, filled
     in from `tools/config/labels.yaml` by matching the `homepage.` pattern
     against your label keys — give any of those keys yourself to override
     just that one (the auto description is a plain
     `"<description> for ${COMPOSE_PROJECT_NAME}"`, fine but generic). A
     leaf value that isn't a string (a list, a bool) gets JSON-encoded, so
     lists/dicts/bools come out correct without extra care.
   - `header_comment` (a plain multi-line string, no `#` prefixes) renders
     as a leading comment block on `docker-compose.yml` — a one-glance
     summary of what the service is and any non-obvious fact worth knowing
     up front (e.g. tor's spec notes it needs no environment/secrets of its
     own; act-runner's notes it's never used standalone). Not required, but
     write one for any new service.

4. **Preview before writing.** Never skip the dry run:

   ```sh
   cd tools
   uv run python -m docker_templates_tools service ../templates/specs/services/<name>.yaml --dry-run
   ```

   Read the output. Check the environment keys are exactly what the image
   expects, the secret/volume names look right, and the healthcheck is
   something that will actually pass once the container is healthy.

5. **Write for real**, then diff:

   ```sh
   uv run python -m docker_templates_tools service ../templates/specs/services/<name>.yaml
   # (add --force if the service's directory already exists and you're regenerating)
   cd .. && git diff templates/services/base/<name>/
   ```

   `--force` deletes `templates/services/base/<name>/` and regenerates it from
   scratch (not file-by-file), so a stale file a previous schema no longer
   produces doesn't linger as an orphan — `git diff` after a `--force` run
   will show it as deleted, which is expected. Every generated directory
   also gets a `README.md` pointing back at the spec; that's the one file
   you never need to compare for content since it's the same template
   every time.

   If regenerating an existing hand-written service, a clean diff on
   `docker-compose.backup.yml` (i.e. no changes) is a good sign the spec
   faithfully captures the existing convention. Changes to
   `docker-compose.base.yml`'s env var *names* are usually intentional
   (this generator's naming convention, see below) — changes to anything
   else, including its `labels:` block, are worth double-checking.

6. **What NOT to put in the spec** — a service that merely depends on
   another one (act-runner on gitea) still gets a normal spec of its own;
   see "Workflow: wiring a stack" for how the dependency gets expressed.
   The real exception is `-cli`/`-init` companions that `extends: service:
   <svc>` (gitea's `gitea-cli`/`gitea-init`, wordpress's `wordpress-cli`/
   `wordpress-init`, passbolt's `passbolt-cli`/`passbolt-init`) — this
   generator can't model `extends:` or multiple services in one file at
   all. Hand-write these as `templates/services/custom/<name>/docker-compose.custom.yml`
   (see `templates/services/custom/gitea/docker-compose.custom.yml`), then wire that
   file in from the *stack* spec — set `custom: true` (which derives the
   include path from the app's own name) and add each companion service to
   `extra_services` (see `templates/specs/stacks/gitea-mariadb.yaml`, where
   `gitea-cli`/`gitea-init` are extra services sourced from
   `templates/services/custom/gitea/docker-compose.custom.yml`).

## Workflow: adding an extension to an existing service

Extensions are optional add-on layers — a database backend choice, SMTP
support — that only add `environment`/`secrets` to a service's main
container. See `templates/services/base/gitea/database/` (three variants: mariadb,
mysql, sqlite3) and `templates/services/base/gitea/docker-compose.smtp.yml` (a single,
flat extension) for the real, generated reference.

1. Add (or edit) an `extensions:` block in the service's spec.
2. A group with **multiple interchangeable variants** (e.g. "pick a
   database") needs a `variants:` map — each variant key becomes its own
   file under `<service>/<group-key>/docker-compose.<variant>.yml`, and each
   variant is named after **itself**, not the group (gitea's `mariadb`
   variant uses `MARIADB_*`/`mariadb_password` — the same names the actual
   `templates/services/base/mariadb` service uses, so they interoperate without a
   shared "category" concept).
3. A group with exactly **one variant** (e.g. "optionally enable SMTP") skips
   `variants:` — put `environment`/`secrets` directly under the group key,
   and it becomes one flat sibling file, prefixed by the group key itself.
4. A secret that's meant to be reused across every stack instead of being
   per-project (SMTP credentials are the only real example so far) gets
   `shared: true` — its file lives at
   `${SECRETS_DIR}/env/000-generic/<prefix>/<suffix>` instead of the normal
   per-project path.
5. Regenerate and diff, same as step 4-5 above.

## Workflow: wiring a stack

A stack combines an app with one or more backend dependencies with
health-gated `depends_on` on a shared internal network. **This is also
where any cross-service dependency belongs** — including one that's always
true for a given service (act-runner always needs gitea) — never in the
dependent service's own spec. Write `templates/specs/stacks/<app>-<dep>.yaml`
(see `templates/specs/stacks/nginx-proxy-manager-mariadb.yaml` for the plainest
possible two-service case, `templates/specs/stacks/gitea-mariadb.yaml` and
`templates/specs/stacks/passbolt-mariadb.yaml` for `custom: true` plus
`-cli`/`-init` companions, and `templates/specs/stacks/wordpress-mariadb-redis.yaml`
for a stack with more than one dependency and stack-specific env wiring),
then:

```sh
cd tools
uv run python -m docker_templates_tools stack ../templates/specs/stacks/<app>-<dep>.yaml --dry-run
uv run python -m docker_templates_tools stack ../templates/specs/stacks/<app>-<dep>.yaml
```

This also (re)writes `templates/stacks/README.md` — a directory-level
notice shared by every stack, not per-stack content, so it's refreshed
every time regardless of `--force`.

`dep` can be a single name or a list (wordpress depends on both `mariadb`
and `redis` — `app` health-gate-depends_on's every one of them). Nothing
about `app`'s or any `dep`'s own `docker-compose.yml` include is ever
spelled out; it's always derived from the name. Two things extend that
derived include list:
- `extensions: {<group>: <variant>}` — the app's chosen extension variant
  (gitea's `database: mariadb`).
- `custom: true` — the app's hand-written `templates/services/custom/<app>/docker-compose.custom.yml`.

Use `extra_services` for anything beyond the plain app/deps set:
- A companion that just needs to ride the `backend` network (e.g.
  `gitea-cli`) — a plain string, or `{name: ...}`.
- A service with its own `docker-compose.yml` that isn't pulled in by
  `app`/`extensions`/`custom` (act-runner has its own image and spec) —
  give it `include:`.
- A service that depends on another one in the stack (act-runner → gitea) —
  `depends_on: gitea` (a plain string) expands to the same health-gated
  form the app/dep wiring already uses. This is the *only* place that
  dependency should be expressed.
- A companion that's really just `extends:`-ing another service already
  defined in the custom file — `extends: gitea-cli` (a plain string)
  expands to the full `extra: {extends: {service: gitea-cli}}`.
- A one-off extra key merged into that service's block verbatim — `extra:`
  (can combine with `extends:` above, e.g. wordpress-init's extra `configs:`
  on top of extending wordpress-cli).

No service spec declares its own `networks:` — the stack owns all of it.
`app`/every `dep` always get `backend`; `app_networks`/`dep_networks` are
*extra* networks on top of that — don't repeat `backend` in them (gitea
needs `runner` to reach act-runner: `app_networks: [runner]`). Each
`extra_services` entry also defaults to `networks: [backend]`; pass `[]` to
opt out entirely (act-runner only needs `runner`, not `backend`;
wordpress-init just extends wordpress-cli and needs nothing of its own).
Any network name besides `backend` gets declared at the stack's top level
automatically.

Cross-service wiring that's specific to one particular stack (e.g.
wordpress reading redis's connection details into its own config) belongs
in the stack spec, not bolted onto the generated file by hand afterward:
`app_extra` merges arbitrary compose keys straight into the app's own
service block (`secrets`/`environment`), and a top-level `extra:` merges
keys into the file's root (a `configs:` block backing one of those
`environment` values) — see `templates/specs/stacks/wordpress-mariadb-redis.yaml`.
Any multi-line string anywhere in `app_extra`/`extra_services[].extra`/the
top-level `extra` automatically renders as a proper YAML block literal, not
a folded/quoted mess.

## If something doesn't fit the schema

Don't force it. This generator intentionally doesn't model every possible
compose shape — most notably a file defining more than one service, or one
using `extends:`, for a *service* spec (a single extra top-level service key
like `logging:` is covered by the main service's `extra` field — see
gitea's spec). A stack spec's `app_extra`/`extra_services[].extra`/top-level
`extra` already cover arbitrary cross-service wiring, so reach for those
before hand-editing a generated stack file. If a service genuinely needs
something further out of scope, generate what the tool *can* produce, then
hand-edit or hand-add the rest — same as the existing carve-out (companion
services needing `extends:`) documented in `tools/README.md`.
