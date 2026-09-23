"""Scaffold a services/base/<name>/ docker-compose bundle, or a
stacks/docker-compose.<app>-<dep>.yml file, from a minimal YAML spec.

See tools/README.md for the annotated schema, and specs/services/*.yaml /
specs/stacks/*.yaml for real examples.

This module is the CLI and file-writing orchestration layer — the spec
data model lives in .models, auto-filled label defaults in .labels, plain
naming helpers in .naming, and every render_*()/resolve_*() function in
.render. Everything is re-exported here too, so `from
docker_templates_tools.scaffold import X` keeps working for any of them.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import yaml

from .compose_yaml import validate_yaml
from .labels import LABEL_CONFIG, apply_label_defaults, load_label_config  # noqa: F401
from .models import (  # noqa: F401
    BindMount,
    Config,
    Extension,
    ExtensionGroup,
    Healthcheck,
    Secret,
    ServiceSpec,
    Volume,
    VolumeMode,
    VolumeOwner,
    load_service_spec,
)
from .naming import apply_prefix, flatten_prefixed, secret_file, secret_full_name  # noqa: F401
from .render import (  # noqa: F401
    comment_block,
    generated_header,
    render_backup,
    render_base,
    render_extension,
    render_index,
    render_service_readme,
    render_stack,
    render_stacks_readme,
    resolve_extra_service,
    resolve_stack_spec,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES_DIR = REPO_ROOT / "templates"
SERVICES_BASE = TEMPLATES_DIR / "services" / "base"
STACKS_DIR = TEMPLATES_DIR / "stacks"
SERVICE_SPECS_DIR = TEMPLATES_DIR / "specs" / "services"
STACK_SPECS_DIR = TEMPLATES_DIR / "specs" / "stacks"


# --------------------------------------------------------------------------
# File writing
# --------------------------------------------------------------------------


def _emit(
    path: Path,
    content: str,
    *,
    source: str,
    force: bool,
    dry_run: bool,
    header: bool = True,
    validate: bool = True,
) -> None:
    if header:
        content = generated_header(source) + "\n" + content
    if validate:
        validate_yaml(content, label=str(path))
    if dry_run:
        print(f"----- {path.relative_to(REPO_ROOT)} -----")
        print(content, end="")
        return
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists (use --force to overwrite)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    print(f"wrote {path.relative_to(REPO_ROOT)}")


def scaffold_service(
    spec: ServiceSpec, *, source: str, force: bool = False, dry_run: bool = False
) -> None:
    out_dir = SERVICES_BASE / spec.dir_name

    # --force regenerates the directory from scratch, not file-by-file, so
    # a schema change that drops a file (an old grouped-extension variant,
    # a since-removed layer) doesn't leave it behind as an orphan. Without
    # --force, an existing directory is left untouched and this refuses
    # instead of writing into it. dry_run never touches disk either way.
    if out_dir.exists() and not dry_run:
        if not force:
            raise FileExistsError(f"{out_dir} already exists (use --force to overwrite)")
        shutil.rmtree(out_dir)
        print(f"removed {out_dir.relative_to(REPO_ROOT)}")
    elif out_dir.exists() and dry_run and force:
        print(f"would remove {out_dir.relative_to(REPO_ROOT)} before regenerating")

    files = {"docker-compose.base.yml": render_base(spec)}
    if spec.has_backup:
        files["docker-compose.backup.yml"] = render_backup(spec)

    # Flat (single-variant, ungrouped) extensions join the main include
    # manifest, same as smtp does for gitea. Grouped ones (a category with
    # several interchangeable variants, e.g. database/) are written under
    # their own subdirectory and are never auto-included — a stack picks one.
    for group in spec.extensions:
        for variant in group.variants:
            content = render_extension(spec, group, variant)
            if group.grouped:
                _emit(
                    out_dir / group.key / f"docker-compose.{variant.key}.yml",
                    content,
                    source=source,
                    force=force,
                    dry_run=dry_run,
                )
            else:
                files[f"docker-compose.{group.key}.yml"] = content

    layers = sorted(files.keys())
    files["docker-compose.yml"] = render_index(layers, spec.header_comment)

    for filename in layers + ["docker-compose.yml"]:
        _emit(out_dir / filename, files[filename], source=source, force=force, dry_run=dry_run)

    _emit(
        out_dir / "README.md",
        render_service_readme(spec, source),
        source=source,
        force=force,
        dry_run=dry_run,
        header=False,
        validate=False,
    )


def scaffold_stack(d: dict, *, source: str, force: bool = False, dry_run: bool = False) -> None:
    content = render_stack(**resolve_stack_spec(d))
    deps = d["dep"] if isinstance(d["dep"], list) else [d["dep"]]
    app_slug = d.get("app_slug", d["app"])
    dep_slug = d.get("dep_slug", "-".join(deps))
    filename = (
        f"docker-compose.{app_slug}-{dep_slug}.yml"
        if dep_slug
        else f"docker-compose.{app_slug}.yml"
    )
    out_path = STACKS_DIR / filename
    _emit(out_path, content, source=source, force=force, dry_run=dry_run)

    # Not force-gated: this directory-level notice is identical every time
    # and fully generator-owned (never hand-edited), so any stack's own
    # regeneration keeps it fresh regardless of --force.
    readme_path = STACKS_DIR / "README.md"
    readme_content = render_stacks_readme()
    if dry_run:
        print(f"----- {readme_path.relative_to(REPO_ROOT)} -----")
        print(readme_content, end="")
    else:
        readme_path.parent.mkdir(parents=True, exist_ok=True)
        readme_path.write_text(readme_content)
        print(f"wrote {readme_path.relative_to(REPO_ROOT)}")


def _load_yaml_spec(path: Path) -> tuple[dict, str]:
    spec_dict = yaml.safe_load(path.read_text())
    try:
        source = str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        source = str(path)
    return spec_dict, source


def scaffold_all(*, force: bool = False, dry_run: bool = False) -> int:
    """Regenerate every services/specs/*.yaml and stacks/specs/*.yaml spec.

    Services first, then stacks — cosmetic ordering only (rendering never
    reads generated output back off disk), but it matches the natural
    dependency direction.
    """
    had_error = False

    for spec_path in sorted(SERVICE_SPECS_DIR.glob("*.yaml")):
        spec_dict, source = _load_yaml_spec(spec_path)
        try:
            spec = load_service_spec(spec_dict)
            scaffold_service(spec, source=source, force=force, dry_run=dry_run)
        except (FileExistsError, ValueError, KeyError) as e:
            print(f"error: {source}: {e}", file=sys.stderr)
            had_error = True

    for spec_path in sorted(STACK_SPECS_DIR.glob("*.yaml")):
        spec_dict, source = _load_yaml_spec(spec_path)
        try:
            scaffold_stack(spec_dict, source=source, force=force, dry_run=dry_run)
        except (FileExistsError, ValueError, KeyError) as e:
            print(f"error: {source}: {e}", file=sys.stderr)
            had_error = True

    return 1 if had_error else 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="with no subcommand, overwrite existing files while regenerating every spec",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="with no subcommand, print every generated file instead of writing it",
    )
    sub = parser.add_subparsers(dest="command")

    svc_p = sub.add_parser(
        "service", help="scaffold a services/base/<name>/ bundle from a YAML spec"
    )
    svc_p.add_argument("spec", type=Path, help="path to a service spec YAML file")
    svc_p.add_argument("--force", action="store_true", help="overwrite existing files")
    svc_p.add_argument(
        "--dry-run", action="store_true", help="print generated files instead of writing them"
    )

    stack_p = sub.add_parser(
        "stack", help="scaffold a stacks/docker-compose.<app>-<dep>.yml from a YAML spec"
    )
    stack_p.add_argument("spec", type=Path, help="path to a stack spec YAML file")
    stack_p.add_argument("--force", action="store_true", help="overwrite existing file")
    stack_p.add_argument(
        "--dry-run", action="store_true", help="print the generated file instead of writing it"
    )

    args = parser.parse_args(argv)

    if args.command is None:
        return scaffold_all(force=args.force, dry_run=args.dry_run)

    if not args.spec.exists():
        parser.error(f"spec file not found: {args.spec}")

    spec_dict, source = _load_yaml_spec(args.spec)

    try:
        if args.command == "service":
            spec = load_service_spec(spec_dict)
            scaffold_service(spec, source=source, force=args.force, dry_run=args.dry_run)
        else:
            scaffold_stack(spec_dict, source=source, force=args.force, dry_run=args.dry_run)
    except (FileExistsError, ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
