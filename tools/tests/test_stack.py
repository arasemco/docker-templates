from docker_templates_tools.scaffold import (
    render_stack,
    resolve_extra_service,
    resolve_stack_spec,
)

# --------------------------------------------------------------------------
# resolve_extra_service
# --------------------------------------------------------------------------


def test_resolve_extra_service_plain_string_untouched():
    assert resolve_extra_service("gitea-cli") == "gitea-cli"


def test_resolve_extra_service_extends_shorthand_expands_and_leads():
    svc = resolve_extra_service(
        {"name": "wordpress-init", "extra": {"configs": ["x"]}, "extends": "wordpress-cli"}
    )
    assert list(svc["extra"].keys()) == ["extends", "configs"]
    assert svc["extra"]["extends"] == {"service": "wordpress-cli"}
    assert svc["extra"]["configs"] == ["x"]


def test_resolve_extra_service_depends_on_string_shorthand():
    svc = resolve_extra_service({"name": "act-runner", "depends_on": "gitea"})
    assert svc["depends_on"] == {"gitea": {"condition": "service_healthy", "restart": True}}


def test_resolve_extra_service_depends_on_full_shape_untouched():
    full = {"gitea": {"condition": "service_healthy", "restart": False}}
    svc = resolve_extra_service({"name": "act-runner", "depends_on": full})
    assert svc["depends_on"] == full


# --------------------------------------------------------------------------
# resolve_stack_spec
# --------------------------------------------------------------------------


def test_resolve_stack_spec_single_dep_derives_includes():
    resolved = resolve_stack_spec(
        {"app": "npm", "dep": "mariadb", "app_slug": "nginx-proxy-manager"}
    )
    assert resolved["app_includes"] == ["../services/base/nginx-proxy-manager/docker-compose.yml"]
    assert resolved["deps"] == ["mariadb"]
    assert resolved["dep_includes"] == ["../services/base/mariadb/docker-compose.yml"]


def test_resolve_stack_spec_list_dep():
    resolved = resolve_stack_spec({"app": "wordpress", "dep": ["mariadb", "redis"]})
    assert resolved["deps"] == ["mariadb", "redis"]
    assert resolved["dep_includes"] == [
        "../services/base/mariadb/docker-compose.yml",
        "../services/base/redis/docker-compose.yml",
    ]


def test_resolve_stack_spec_extensions_and_custom_derive_extra_includes():
    resolved = resolve_stack_spec(
        {
            "app": "gitea",
            "dep": "mariadb",
            "extensions": {"database": "mariadb"},
            "custom": True,
        }
    )
    assert resolved["app_includes"] == [
        "../services/base/gitea/docker-compose.yml",
        "../services/base/gitea/database/docker-compose.mariadb.yml",
        "../services/custom/gitea/docker-compose.custom.yml",
    ]


def test_resolve_stack_spec_custom_false_omits_custom_include():
    resolved = resolve_stack_spec({"app": "gitea", "dep": "mariadb", "custom": False})
    assert resolved["app_includes"] == ["../services/base/gitea/docker-compose.yml"]


def test_resolve_stack_spec_passes_through_extra_fields():
    resolved = resolve_stack_spec(
        {
            "app": "wordpress",
            "dep": ["mariadb", "redis"],
            "app_extra": {"secrets": ["redis_password"]},
            "dep_extra": {"profiles": ["backend"]},
            "extra": {"configs": {}},
            "header_comment": "hi",
        }
    )
    assert resolved["app_extra"] == {"secrets": ["redis_password"]}
    assert resolved["dep_extra"] == {"profiles": ["backend"]}
    assert resolved["extra"] == {"configs": {}}
    assert resolved["header_comment"] == "hi"


# --------------------------------------------------------------------------
# render_stack
# --------------------------------------------------------------------------


def _base_stack_kwargs(**overrides):
    kwargs = {
        "app": "gitea",
        "app_includes": ["../services/base/gitea/docker-compose.yml"],
        "deps": ["mariadb"],
        "dep_includes": ["../services/base/mariadb/docker-compose.yml"],
    }
    kwargs.update(overrides)
    return kwargs


def test_render_stack_single_dep_depends_on_and_networks():
    out = render_stack(**_base_stack_kwargs())
    assert (
        "include:\n  - ../services/base/gitea/docker-compose.yml\n  - ../services/base/mariadb/docker-compose.yml\n"
        in out
    )
    assert "  gitea:\n    networks:\n      - backend\n" in out
    assert (
        "depends_on:\n      mariadb:\n        condition: service_healthy\n        restart: true\n"
        in out
    )
    assert "  mariadb:\n    networks:\n      - backend\n" in out
    assert "networks:\n  backend:\n    internal: true\n" in out


def test_render_stack_multiple_deps_all_depended_on():
    out = render_stack(
        **_base_stack_kwargs(
            deps=["mariadb", "redis"],
            dep_includes=[
                "../services/base/mariadb/docker-compose.yml",
                "../services/base/redis/docker-compose.yml",
            ],
        )
    )
    assert "mariadb:\n        condition: service_healthy" in out
    assert "redis:\n        condition: service_healthy" in out
    assert "  redis:\n    networks:\n      - backend\n" in out


def test_render_stack_app_networks_always_include_backend_plus_extras():
    out = render_stack(**_base_stack_kwargs(app_networks=["runner"]))
    assert "  gitea:\n    networks:\n      - backend\n      - runner\n" in out
    assert "networks:\n  backend:\n    internal: true\n  runner:\n" in out


def test_render_stack_multiple_app_includes_use_path_list():
    out = render_stack(
        **_base_stack_kwargs(
            app_includes=[
                "../services/base/gitea/docker-compose.yml",
                "../services/base/gitea/database/docker-compose.mariadb.yml",
            ]
        )
    )
    assert (
        "include:\n  - path:\n      - ../services/base/gitea/docker-compose.yml\n      - ../services/base/gitea/database/docker-compose.mariadb.yml\n"
        in out
    )


def test_render_stack_app_extra_merged_before_networks():
    out = render_stack(**_base_stack_kwargs(app_extra={"secrets": ["redis_password"]}))
    idx_extra = out.index("secrets:\n      - redis_password")
    idx_networks = out.index("networks:\n      - backend")
    assert idx_extra < idx_networks


def test_render_stack_app_extra_multiline_string_becomes_literal():
    out = render_stack(**_base_stack_kwargs(app_extra={"environment": {"X": "a\nb\n"}}))
    assert "X: |\n" in out


def test_render_stack_dep_extra_merged_before_networks_on_every_dep():
    out = render_stack(
        **_base_stack_kwargs(
            deps=["mariadb", "redis"],
            dep_includes=[
                "../services/base/mariadb/docker-compose.yml",
                "../services/base/redis/docker-compose.yml",
            ],
            dep_extra={"profiles": ["backend"]},
        )
    )
    assert (
        "  mariadb:\n    profiles:\n      - backend\n    networks:\n      - backend\n" in out
    )
    assert "  redis:\n    profiles:\n      - backend\n    networks:\n      - backend\n" in out


def test_render_stack_dep_extra_multiline_string_becomes_literal():
    out = render_stack(**_base_stack_kwargs(dep_extra={"environment": {"X": "a\nb\n"}}))
    assert "X: |\n" in out


def test_render_stack_extra_services_string_rides_backend():
    out = render_stack(**_base_stack_kwargs(extra_services=["gitea-cli"]))
    assert "  gitea-cli:\n    networks:\n      - backend\n" in out


def test_render_stack_extra_services_networks_empty_list_opts_out():
    out = render_stack(
        **_base_stack_kwargs(
            extra_services=[
                {
                    "name": "act-runner",
                    "networks": ["runner"],
                    "depends_on": {"gitea": {"condition": "service_healthy", "restart": True}},
                }
            ]
        )
    )
    assert "  act-runner:\n    networks:\n      - runner\n    depends_on:" in out
    assert "backend" not in out.split("act-runner:")[1].split("networks:")[1].split("\n")[1]


def test_render_stack_extra_services_include_added_to_top_level_include():
    out = render_stack(
        **_base_stack_kwargs(
            extra_services=[
                {"name": "act-runner", "include": "../services/base/act-runner/docker-compose.yml"}
            ]
        )
    )
    assert "  - ../services/base/act-runner/docker-compose.yml\n" in out


def test_render_stack_top_level_extra_rendered_before_networks():
    out = render_stack(**_base_stack_kwargs(extra={"configs": {"x": {"content": "y"}}}))
    assert "configs:\n  x:\n    content: y\n" in out
    # unindented (flush-left) "networks:" is the top-level section; every
    # per-service "networks:" is indented, so this pattern only matches
    # that one heading.
    assert out.index("configs:") < out.index("\nnetworks:\n")


def test_render_stack_header_comment_rendered_first():
    out = render_stack(**_base_stack_kwargs(header_comment="Composed template: Gitea + MariaDB."))
    assert out.startswith("# Composed template: Gitea + MariaDB.\n\ninclude:\n")


def test_render_stack_extra_network_names_declared_at_top_level():
    out = render_stack(**_base_stack_kwargs(app_networks=["runner"]))
    assert "  runner:\n" in out.split("networks:\n")[-1]
