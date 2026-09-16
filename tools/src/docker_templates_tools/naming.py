"""Naming helpers — a service's own secrets/env, and each of its
extensions' secrets/env, are always prefixed by a plain name: the
service's own name for the main spec, or the variant's own key for an
extension (e.g. gitea's "mariadb" variant prefixes with MARIADB, its
"mysql" variant with MYSQL). There's deliberately no shared "category"
indirection (like a generic DB_NAME/DB_USER pair every database engine
reads): swapping the database engine a service uses already means
swapping which extension file gets included, so there's no "change
engines without touching .env" case to design for — and using each
variant's own name is what makes a consuming extension (gitea's
database/docker-compose.mariadb.yml) and the providing service
(services/base/mariadb) converge on the same secret/env names without
needing to agree on an abstract category at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Secret


def apply_prefix(value, prefix: str):
    """Expand the "?xxx"/"-xxx:default" shorthand's var name with `prefix`
    before handing off to expand_env_value, e.g. "?name" + prefix "MARIADB"
    -> "?MARIADB_NAME" -> "${MARIADB_NAME:?MARIADB_NAME is required}".
    """
    if isinstance(value, str) and value and value[0] in "?-":
        marker, rest = value[0], value[1:]
        if marker == "-":
            var, _, default = rest.partition(":")
            return f"{marker}{prefix}_{var.upper()}:{default}"
        return f"{marker}{prefix}_{rest.upper()}"
    return value


def flatten_prefixed(d: dict, prefix: str = "") -> dict:
    """Recursively flattens a nested mapping — used for both `environment:`
    and `labels:`. A key whose value is itself a mapping is a prefix
    segment, concatenated onto its parent's prefix with no separator
    inserted — the key's own trailing `_`/`.`/`__` (if any) is what
    supplies it, e.g.:

        GITEA__server__:
          SSH_:
            PORT: 22

    flattens to {"GITEA__server__SSH_PORT": 22}, and:

        widget.:
          type: gitea

    flattens to {"widget.type": "gitea"}. A key whose value is anything else
    (str, bool, int, ...) is a leaf.
    """
    result = {}
    for k, v in d.items():
        full_key = f"{prefix}{k}"
        if isinstance(v, dict):
            result.update(flatten_prefixed(v, full_key))
        else:
            result[full_key] = v
    return result


def secret_full_name(secret: "Secret", prefix: str) -> str:
    if secret.bare:
        return secret.suffix
    return f"{prefix.lower()}_{secret.suffix}"


def secret_file(secret: "Secret", prefix: str) -> str:
    if secret.shared:
        return f"${{SECRETS_DIR:?SECRETS_DIR is required}}/env/000-generic/{prefix.lower()}/{secret.suffix}"
    return f"${{SECRETS_DIR:?SECRETS_DIR is required}}/env/${{COMPOSE_PROJECT_NAME}}/{secret.name}"
