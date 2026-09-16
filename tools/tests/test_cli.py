import pytest

from docker_templates_tools import scaffold
from docker_templates_tools.scaffold import load_service_spec, main, scaffold_service


@pytest.fixture
def isolated_repo(tmp_path, monkeypatch):
    """Point the module's output-path globals at a scratch directory so
    writing files for real in a test never touches the real repo."""
    monkeypatch.setattr(scaffold, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(scaffold, "SERVICES_BASE", tmp_path / "services" / "base")
    monkeypatch.setattr(scaffold, "STACKS_DIR", tmp_path / "stacks")
    return tmp_path


def _write_spec(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content)
    return path


# --------------------------------------------------------------------------
# scaffold_service (direct call, not via CLI)
# --------------------------------------------------------------------------


def test_scaffold_service_writes_expected_files(isolated_repo):
    spec = load_service_spec({"image": "redis"})
    scaffold_service(spec, source="specs/services/redis.yaml", force=False, dry_run=False)

    out_dir = isolated_repo / "services" / "base" / "redis"
    assert (out_dir / "docker-compose.base.yml").exists()
    assert (out_dir / "docker-compose.yml").exists()
    assert not (out_dir / "docker-compose.backup.yml").exists()

    content = (out_dir / "docker-compose.base.yml").read_text()
    assert content.startswith("# Auto-generated from specs/services/redis.yaml")

    readme = (out_dir / "README.md").read_text()
    assert readme.startswith("# Redis — generated, do not edit\n")
    assert "specs/services/redis.yaml" in readme
    assert "services/custom/redis/" in readme
    # the README isn't a compose file, so it's not in the include manifest
    # and doesn't get the YAML-comment auto-generated banner
    assert "- README.md" not in (out_dir / "docker-compose.yml").read_text()
    assert not readme.startswith("# Auto-generated")


def test_scaffold_service_dry_run_writes_nothing(isolated_repo, capsys):
    spec = load_service_spec({"image": "redis"})
    scaffold_service(spec, source="specs/services/redis.yaml", force=False, dry_run=True)

    out_dir = isolated_repo / "services" / "base" / "redis"
    assert not out_dir.exists()
    captured = capsys.readouterr()
    assert "----- services/base/redis/docker-compose.base.yml -----" in captured.out


def test_scaffold_service_refuses_to_overwrite_without_force(isolated_repo):
    spec = load_service_spec({"image": "redis"})
    scaffold_service(spec, source="s", force=False, dry_run=False)
    with pytest.raises(FileExistsError):
        scaffold_service(spec, source="s", force=False, dry_run=False)


def test_scaffold_service_force_overwrites(isolated_repo):
    spec = load_service_spec({"image": "redis"})
    scaffold_service(spec, source="s", force=False, dry_run=False)
    scaffold_service(spec, source="s", force=True, dry_run=False)  # should not raise


def test_scaffold_service_force_removes_legacy_files_not_in_current_spec(isolated_repo):
    spec = load_service_spec({"image": "redis"})
    scaffold_service(spec, source="s", force=False, dry_run=False)

    out_dir = isolated_repo / "services" / "base" / "redis"
    orphan = out_dir / "docker-compose.homepage.yml"
    orphan.write_text("services:\n  redis:\n    labels: []\n")
    assert orphan.exists()

    scaffold_service(spec, source="s", force=True, dry_run=False)
    assert not orphan.exists()
    assert (out_dir / "docker-compose.base.yml").exists()


def test_scaffold_service_dry_run_force_does_not_touch_existing_directory(isolated_repo, capsys):
    spec = load_service_spec({"image": "redis"})
    scaffold_service(spec, source="s", force=False, dry_run=False)
    out_dir = isolated_repo / "services" / "base" / "redis"
    marker = out_dir / "docker-compose.homepage.yml"
    marker.write_text("x")

    scaffold_service(spec, source="s", force=True, dry_run=True)

    assert marker.exists()  # dry-run never deletes, even with --force
    captured = capsys.readouterr()
    assert "would remove services/base/redis before regenerating" in captured.out


def test_scaffold_service_grouped_extension_writes_its_own_file(isolated_repo):
    spec = load_service_spec(
        {
            "image": "gitea/gitea",
            "extensions": {
                "database": {
                    "variants": {"mariadb": {"environment": {"GITEA__database__DB_TYPE": "mariadb"}}}
                }
            },
        }
    )
    scaffold_service(spec, source="s", force=False, dry_run=False)

    out_dir = isolated_repo / "services" / "base" / "gitea"
    variant_file = out_dir / "database" / "docker-compose.mariadb.yml"
    assert variant_file.exists()
    assert "GITEA__database__DB_TYPE: mariadb" in variant_file.read_text()
    # a grouped variant is never auto-included in the main manifest
    assert "database/docker-compose.mariadb.yml" not in (out_dir / "docker-compose.yml").read_text()


def test_scaffold_service_ungrouped_extension_joins_main_manifest(isolated_repo):
    spec = load_service_spec(
        {
            "image": "gitea/gitea",
            "extensions": {"smtp": {"environment": {"GITEA__mailer__PROTOCOL": "smtp"}}},
        }
    )
    scaffold_service(spec, source="s", force=False, dry_run=False)

    out_dir = isolated_repo / "services" / "base" / "gitea"
    assert (out_dir / "docker-compose.smtp.yml").exists()
    assert "docker-compose.smtp.yml" in (out_dir / "docker-compose.yml").read_text()


def test_scaffold_service_backup_file_only_when_needed(isolated_repo):
    spec = load_service_spec(
        {"image": "redis", "volumes": {"data": {"path": "/data", "backup": True}}}
    )
    scaffold_service(spec, source="s", force=False, dry_run=False)
    out_dir = isolated_repo / "services" / "base" / "redis"
    assert (out_dir / "docker-compose.backup.yml").exists()


# --------------------------------------------------------------------------
# main() / CLI wiring
# --------------------------------------------------------------------------


def test_main_missing_spec_file_exits_nonzero(isolated_repo, capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["service", str(isolated_repo / "nope.yaml")])
    assert exc_info.value.code == 2


def test_main_service_dry_run(isolated_repo, capsys):
    spec_path = _write_spec(isolated_repo, "redis.yaml", "image: redis\n")
    rc = main(["service", str(spec_path), "--dry-run"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "----- services/base/redis/docker-compose.base.yml -----" in captured.out


def test_main_service_write_then_conflict_without_force(isolated_repo, capsys):
    spec_path = _write_spec(isolated_repo, "redis.yaml", "image: redis\n")
    assert main(["service", str(spec_path)]) == 0
    rc = main(["service", str(spec_path)])
    assert rc == 1
    captured = capsys.readouterr()
    assert "error:" in captured.err


def test_main_service_force_after_conflict_succeeds(isolated_repo):
    spec_path = _write_spec(isolated_repo, "redis.yaml", "image: redis\n")
    assert main(["service", str(spec_path)]) == 0
    assert main(["service", str(spec_path), "--force"]) == 0


def test_main_stack_dry_run(isolated_repo, capsys):
    spec_path = _write_spec(
        isolated_repo,
        "stack.yaml",
        "app: npm\napp_slug: nginx-proxy-manager\ndep: mariadb\n",
    )
    rc = main(["stack", str(spec_path), "--dry-run"])
    assert rc == 0
    captured = capsys.readouterr()
    # app_slug ("nginx-proxy-manager"), not the compose service key ("npm"),
    # names the output file.
    assert "----- stacks/docker-compose.nginx-proxy-manager-mariadb.yml -----" in captured.out
