"""Auto-filled label defaults, driven by tools/config/labels.yaml — see
apply_label_defaults(). Detection works by pattern-matching a service's
already-flattened label keys (e.g. does any key start with "homepage."),
never by checking for a structural namespace key, so any label group
using the same `<group-prefix>.<key>` convention gets this for free just
by being listed in the config file.
"""

from __future__ import annotations

from pathlib import Path

import yaml

TOOLS_DIR = Path(__file__).resolve().parents[2]
LABEL_CONFIG_PATH = TOOLS_DIR / "config" / "labels.yaml"

_MISSING = object()


def load_label_config() -> dict:
    if not LABEL_CONFIG_PATH.exists():
        return {}
    return yaml.safe_load(LABEL_CONFIG_PATH.read_text()) or {}


LABEL_CONFIG = load_label_config()


def apply_label_defaults(labels: dict, spec) -> dict:
    """For each group in LABEL_CONFIG whose `pattern` matches at least one
    key already in `labels`, fill in any of that group's `defaults` keys not
    already present. A spec's own keys always win. Ordering: each matched
    group's keys (defaults first, in their declared order, then that
    group's own extra keys in the spec's order) come first, followed by
    anything left over that didn't match any group's pattern."""
    context = {
        "display_name": spec.display_name,
        "service_name": spec.service_name,
        "description": (spec.description or spec.display_name).capitalize(),
    }
    remaining = dict(labels)
    blocks = []
    for group in LABEL_CONFIG.values():
        pattern = group.get("pattern", "")
        if not pattern or not any(k.startswith(pattern) for k in remaining):
            continue
        block = {}
        for k, template in group.get("defaults", {}).items():
            val = remaining.pop(k, _MISSING)
            block[k] = template % context if val is _MISSING else val
        for k in [k for k in remaining if k.startswith(pattern)]:
            block[k] = remaining.pop(k)
        blocks.append(block)
    result = {}
    for block in blocks:
        result.update(block)
    result.update(remaining)
    return result
