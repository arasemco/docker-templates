from docker_templates_tools.scaffold import (
    Extension,
    ExtensionGroup,
    load_service_spec,
    render_backup,
    render_base,
    render_extension,
    render_index,
)


def test_render_index_plain():
    out = render_index(["docker-compose.backup.yml", "docker-compose.base.yml"])
    assert out == ("include:\n  - docker-compose.backup.yml\n  - docker-compose.base.yml\n\n")


def test_render_index_with_header_comment():
    out = render_index(["docker-compose.base.yml"], "A service.\nSecond line.")
    assert out.startswith("# A service.\n# Second line.\n\ninclude:\n")


def test_render_base_minimal_service():
    spec = load_service_spec({"image": "mariadb"})
    out = render_base(spec)
    assert "services:\n  mariadb:\n" in out
    assert "image: mariadb:${MARIADB_TAG:-latest}\n" in out
    assert "hostname: mariadb.${STACK_DOMAIN:?STACK_DOMAIN is required}.local\n" in out
    assert "restart: ${RESTART:-unless-stopped}\n" in out
    assert "pull_policy: ${PULL_POLICY:-missing}\n" in out
    assert "deploy:\n" in out
    assert '"${MARIADB_CPU_LIMIT:-1.0}"' in out
    # nothing optional got rendered
    assert "environment:" not in out
    assert "secrets:" not in out
    assert "labels:" not in out


def test_render_base_environment_shorthand_and_ambiguous_quoting():
    spec = load_service_spec(
        {
            "image": "mariadb",
            "environment": {"MARIADB_DATABASE": "?name", "MARIADB_AUTO_UPGRADE": "1"},
        }
    )
    out = render_base(spec)
    assert "MARIADB_DATABASE: ${MARIADB_NAME:?MARIADB_NAME is required}" in out
    assert 'MARIADB_AUTO_UPGRADE: "1"' in out


def test_render_base_secret_env_var_injected_into_environment():
    spec = load_service_spec(
        {
            "image": "mariadb",
            "secrets": [{"name": "password", "env_var": "MARIADB_PASSWORD_FILE"}],
        }
    )
    out = render_base(spec)
    assert "MARIADB_PASSWORD_FILE: /run/secrets/mariadb_password" in out
    assert "secrets:\n      - mariadb_password\n" in out
    assert (
        "mariadb_password:\n    file: ${SECRETS_DIR:?SECRETS_DIR is required}/env/${COMPOSE_PROJECT_NAME}/mariadb_password"
        in out
    )


def test_render_base_command_is_block_literal():
    spec = load_service_spec({"image": "redis", "command": "sh -c 'true'\n"})
    out = render_base(spec)
    assert "command: |\n      sh -c 'true'\n" in out


def test_render_base_labels_json_encodes_non_string_leaves():
    spec = load_service_spec(
        {
            "image": "gitea/gitea",
            "labels": {
                "npm.": {"proxy.": {"host.": {"details.": {"domain_names": ["${STACK_DOMAIN}"]}}}}
            },
        }
    )
    out = render_base(spec)
    assert 'npm.proxy.host.details.domain_names=["${STACK_DOMAIN}"]' in out


def test_render_base_labels_lowercase_booleans():
    spec = load_service_spec(
        {"image": "gitea/gitea", "labels": {"npm.": {"ssl.": {"force": True}}}}
    )
    out = render_base(spec)
    assert "npm.ssl.force=true" in out


def test_render_base_volumes_and_configs_sections():
    spec = load_service_spec(
        {
            "image": "wordpress",
            "configs": [{"name": "uploads", "target": "/etc/uploads.ini", "content": "a=1\n"}],
            "volumes": {"data": {"path": "/var/www/html"}},
        }
    )
    out = render_base(spec)
    assert "source: wordpress_uploads" in out
    assert "target: /etc/uploads.ini" in out
    assert "source: wordpress_data" in out
    assert "target: /var/www/html" in out
    assert "wordpress_uploads:\n    content: |\n      a=1\n" in out
    assert "pro.asemo.service=wordpress" in out


def test_render_backup_no_owner_has_no_chown_script():
    spec = load_service_spec(
        {"image": "redis", "volumes": {"data": {"path": "/data", "backup": True}}}
    )
    out = render_backup(spec)
    assert "include:\n  - ../../custom/backup/docker-compose.yml\n" in out
    assert "x-volumes: &redis-volumes" in out
    assert "redis_data:/mnt/redis" in out
    assert "chown" not in out


def test_render_backup_with_owner_writes_chown_chmod_script():
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
    out = render_backup(spec)
    assert "chown -R 999:999 /mnt/mariadb" in out
    assert "find /mnt/mariadb -type d -exec chmod 700 {} +" in out
    assert "find /mnt/mariadb -type f -exec chmod 600 {} +" in out
    assert "mariadb_restore_00_owner" in out


def test_render_extension_grouped_variant():
    spec = load_service_spec({"image": "gitea/gitea", "service": {"name": "gitea"}})
    group = ExtensionGroup(key="database", grouped=True)
    variant = Extension(
        key="mariadb",
        environment={
            "GITEA__database__DB_TYPE": "mariadb",
            "GITEA__database__HOST": "-host:mariadb:3306",
        },
        secrets=[],
    )
    out = render_extension(spec, group, variant)
    assert "services:\n  gitea:\n" in out
    assert "GITEA__database__DB_TYPE: mariadb" in out
    assert "GITEA__database__HOST: ${MARIADB_HOST:-mariadb:3306}" in out
