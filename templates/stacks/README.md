# Stacks — generated, do not edit

Every `docker-compose.<app>-<dep>.yml` file in this directory is
generated from `templates/specs/stacks/<app>-<dep>.yaml` — do not
edit or add files here directly.

- To change a stack, edit its spec and regenerate — see
  tools/README.md.
- For real per-deployment wiring on top of a stack (extra
  networks, a reverse-proxy sidecar, TLS, ...), `include:` the
  stack file from your own docker-compose.yml instead of editing
  it here — see example_app/ for real examples.
