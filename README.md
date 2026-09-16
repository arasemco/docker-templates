# Docker Compose Templates

[![Test](https://github.com/arasemco/docker-templates/actions/workflows/test.yml/badge.svg)](https://github.com/arasemco/docker-templates/actions/workflows/test.yml)
[![Lint](https://github.com/arasemco/docker-templates/actions/workflows/lint.yml/badge.svg)](https://github.com/arasemco/docker-templates/actions/workflows/lint.yml)
[![Validate compose files](https://github.com/arasemco/docker-templates/actions/workflows/validate-compose.yml/badge.svg)](https://github.com/arasemco/docker-templates/actions/workflows/validate-compose.yml)
[![Security](https://github.com/arasemco/docker-templates/actions/workflows/security.yml/badge.svg)](https://github.com/arasemco/docker-templates/actions/workflows/security.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](tools/pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Docker-compose templates for a set of self-hosted services — Gitea (with
Actions CI), MariaDB, MySQL, Nginx Proxy Manager, Passbolt, Redis, rsyslog,
Tor, and WordPress — generated from minimal YAML specs instead of
hand-copied and hand-maintained per service.

## Layout

- **`tools/`** — the generator: a small `uv`-managed Python package that
  turns a YAML spec into a `docker-compose` bundle. See `tools/README.md`
  for the full schema reference, or the `scaffold-service` skill
  (`.claude/skills/scaffold-service/`) for the step-by-step workflow.
- **`templates/`** — the specs (`templates/specs/`) and everything the
  generator produces from them: `templates/services/base/<name>/` (one
  directory per service; `templates/services/custom/` holds hand-written
  `-cli`/`-init` companions the generator can't model) and
  `templates/stacks/` (a service wired to its database/cache dependencies,
  health-gated and networked).
- **`example_app/`** — real deployments. Each one `include:`s a stack from
  `templates/stacks/` and layers deployment-specific bits on top (an extra
  network, a reverse-proxy sidecar, an onion service, ...).

Everything under `templates/` is generated — don't hand-edit it. Each
generated directory/file says so and points back at its spec; see
`tools/README.md` for why the tool is structured this way and how to
regenerate.

## CI

All workflows live under `.github/workflows/` and also run on demand via
`workflow_dispatch`:

- **Test** — the `tools/` pytest suite (including the golden-file
  regression test that every spec still reproduces its committed output).
- **Lint** — `ruff check` and `ruff format --check` over `tools/`.
- **Validate compose files** — `docker compose config` against every
  generated stack and every `example_app/*/docker-compose.yml`, catching
  Compose-schema errors the generator's own YAML validation can't see.
- **Security** — `pip-audit` against the locked `tools/` dependencies
  (also runs weekly, since new CVEs land against already-pinned versions),
  a `dependency-review` check on PRs, and a `gitleaks` scan for committed
  secrets.

Dependabot (`.github/dependabot.yml`) opens weekly PRs for `tools/`'s uv
dependencies and for the Actions themselves.

## Quick start

Using an existing stack in your own deployment — `include:` it and layer
your own bits on top (see `example_app/` for real examples):

```yaml
# your own docker-compose.yml
include:
  - templates/stacks/docker-compose.gitea-mariadb.yml

services:
  gitea:
    networks:
      - your-network

networks:
  your-network:
    external: true
```

Adding a new service, adding an extension (database/SMTP choice) to an
existing one, or wiring two services into a new stack: write or edit a
YAML spec under `templates/specs/`, then regenerate with the tool in
`tools/`:

```sh
cd tools
uv run python -m docker_templates_tools service ../templates/specs/services/<name>.yaml --dry-run
uv run python -m docker_templates_tools stack ../templates/specs/stacks/<app>-<dep>.yaml --dry-run
```

See `tools/README.md` for the full schema, or use the `scaffold-service`
skill for guided step-by-step instructions.

## License

MIT — see [LICENSE](LICENSE).
