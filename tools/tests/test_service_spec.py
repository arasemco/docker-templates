from docker_templates_tools.scaffold import load_service_spec


def test_minimal_spec_derives_names_and_defaults():
    spec = load_service_spec({"image": "mariadb"})
    assert spec.service_name == "mariadb"
    assert spec.display_name == "Mariadb"
    assert spec.upper == "MARIADB"
    assert spec.dir_name == "mariadb"
    assert spec.tag_env == "${MARIADB_TAG:-latest}"
    assert spec.has_backup is False
    assert spec.labels == {}


def test_service_name_derived_from_image_last_segment():
    spec = load_service_spec({"image": "gitea/gitea"})
    assert spec.service_name == "gitea"
    assert spec.display_name == "Gitea"


def test_explicit_service_and_display_name_override_defaults():
    spec = load_service_spec(
        {
            "image": "jc21/nginx-proxy-manager",
            "service": {"name": "npm"},
            "display": {"name": "Nginx Proxy Manager"},
        }
    )
    assert spec.service_name == "npm"
    assert spec.display_name == "Nginx Proxy Manager"
    assert spec.upper == "NPM"


def test_tag_required_renders_required_shorthand():
    spec = load_service_spec({"image": "gitea/gitea", "tag_required": True})
    assert spec.tag_env == "${GITEA_TAG:?GITEA_TAG is required}"


def test_dir_prefix_overrides_dir_name_only():
    spec = load_service_spec(
        {"image": "jc21/nginx-proxy-manager", "service": {"name": "npm"}, "dir_prefix": "nginx-proxy-manager"}
    )
    assert spec.dir_name == "nginx-proxy-manager"
    assert spec.service_name == "npm"
    assert spec.upper == "NPM"


def test_volume_name_and_default_backup_target():
    spec = load_service_spec(
        {
            "image": "mariadb",
            "volumes": {"data": {"path": "/var/lib/mysql", "backup": True}},
        }
    )
    (vol,) = spec.volumes
    assert vol.name == "mariadb_data"
    assert vol.backup_target == "/mnt/mariadb"
    assert spec.has_backup is True
    assert spec.backup_volumes == [vol]


def test_volume_non_data_suffix_backup_target_uses_path():
    spec = load_service_spec(
        {
            "image": "ghcr.io/arasemco/tor",
            "volumes": {"etc": {"path": "/etc/tor", "backup": True}},
        }
    )
    (vol,) = spec.volumes
    assert vol.backup_target == "/mnt/etc/tor"


def test_volume_backup_target_override_is_respected():
    spec = load_service_spec(
        {
            "image": "gitea/gitea",
            "volumes": {"data": {"path": "/data", "backup": True, "backup_target": "/mnt/data"}},
        }
    )
    (vol,) = spec.volumes
    assert vol.backup_target == "/mnt/data"


def test_volume_owner_only_meaningful_alongside_backup():
    spec = load_service_spec(
        {
            "image": "mariadb",
            "volumes": {
                "data": {
                    "path": "/var/lib/mysql",
                    "backup": True,
                    "owner": {"uid": 999, "gid": 999},
                }
            },
        }
    )
    (vol,) = spec.volumes
    assert vol.owner.uid == 999
    assert vol.owner.gid == 999
    assert vol.mod.dirs == "700"
    assert vol.mod.files == "600"


def test_bind_mount_default_backup_target():
    spec = load_service_spec(
        {
            "image": "gitea/gitea",
            "bind_mounts": [{"source": "/host/gpg", "target": "/data/gitea/home/.gnupg"}],
        }
    )
    (bm,) = spec.bind_mounts
    assert bm.backup_target == "/mnt/data/gitea/home/.gnupg"


def test_secret_naming_and_env_var(monkeypatch):
    spec = load_service_spec(
        {
            "image": "mariadb",
            "secrets": [{"name": "password", "env_var": "MARIADB_PASSWORD_FILE"}],
        }
    )
    (secret,) = spec.secrets
    assert secret.name == "mariadb_password"
    assert secret.env_var == "MARIADB_PASSWORD_FILE"


def test_bare_secret_skips_prefix():
    spec = load_service_spec(
        {
            "image": "gitea/gitea",
            "secrets": [{"name": "security_secret_key", "bare": True}],
        }
    )
    (secret,) = spec.secrets
    assert secret.name == "security_secret_key"


def test_config_name_is_service_prefixed():
    spec = load_service_spec(
        {
            "image": "mariadb",
            "configs": [{"name": "tuning", "target": "/etc/mysql/conf.d/tuning.cnf", "content": "x"}],
        }
    )
    (config,) = spec.configs
    assert config.name == "mariadb_tuning"


def test_healthcheck_defaults():
    spec = load_service_spec(
        {"image": "mariadb", "healthcheck": {"test": ["CMD", "true"]}}
    )
    assert spec.healthcheck.interval == "10s"
    assert spec.healthcheck.timeout == "5s"
    assert spec.healthcheck.retries == 3
    assert spec.healthcheck.start_period == "20s"


def test_no_healthcheck_test_means_no_healthcheck():
    spec = load_service_spec({"image": "mariadb", "healthcheck": {}})
    assert spec.healthcheck is None


def test_environment_nesting_and_nested_prefix():
    spec = load_service_spec(
        {
            "image": "gitea/gitea",
            "environment": {"GITEA__server__": {"SSH_": {"PORT": 22}}},
        }
    )
    assert spec.environment == {"GITEA__server__SSH_PORT": 22}


def test_grouped_extension_variants_and_secret_naming():
    spec = load_service_spec(
        {
            "image": "gitea/gitea",
            "extensions": {
                "database": {
                    "variants": {
                        "mariadb": {
                            "environment": {"GITEA__database__": {"DB_TYPE": "mariadb"}},
                            "secrets": [{"name": "password", "env_var": "X"}],
                        },
                        "sqlite3": {"environment": {"GITEA__database__": {"DB_TYPE": "sqlite3"}}},
                    }
                }
            },
        }
    )
    (group,) = spec.extensions
    assert group.key == "database"
    assert group.grouped is True
    variants = {v.key: v for v in group.variants}
    assert variants["mariadb"].environment == {"GITEA__database__DB_TYPE": "mariadb"}
    assert variants["mariadb"].secrets[0].name == "mariadb_password"
    assert variants["sqlite3"].secrets == []


def test_ungrouped_extension_is_flat_and_prefixed_by_group_key():
    spec = load_service_spec(
        {
            "image": "gitea/gitea",
            "extensions": {
                "smtp": {
                    "environment": {"GITEA__mailer__": {"PROTOCOL": "smtp"}},
                    "secrets": [{"name": "password", "env_var": "X", "shared": True}],
                }
            },
        }
    )
    (group,) = spec.extensions
    assert group.key == "smtp"
    assert group.grouped is False
    (variant,) = group.variants
    assert variant.key == "smtp"
    assert variant.secrets[0].name == "smtp_password"


def test_labels_flatten_and_get_defaults_applied():
    spec = load_service_spec(
        {
            "image": "redis",
            "labels": {"homepage.": {"description": "In-memory cache"}},
        }
    )
    assert spec.labels["homepage.description"] == "In-memory cache"
    # real tools/config/labels.yaml wires homepage.* defaults
    assert spec.labels["homepage.group"] == "${COMPOSE_PROJECT_NAME}"
    assert spec.labels["homepage.icon"] == "redis.png"


def test_header_comment_is_passed_through():
    spec = load_service_spec({"image": "redis", "header_comment": "Redis — cache."})
    assert spec.header_comment == "Redis — cache."
