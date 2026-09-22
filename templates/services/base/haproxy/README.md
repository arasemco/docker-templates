# HAProxy — generated, do not edit

Everything in this directory is generated from `templates/specs/services/haproxy.yaml` — do
not edit or add files here directly. Regenerating this service
(`--force`) replaces the whole directory, so anything hand-added
here is silently lost.

- To change this service, edit the spec and regenerate — see
  tools/README.md.
- To add a hand-written companion service (e.g. a `-cli`/`-init`
  `extends:` companion), put it under `templates/services/custom/haproxy/`
  instead, and wire it into the owning stack spec's `extra_services`
  (see tools/README.md).
