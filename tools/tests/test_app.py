import io
import re
import shutil

import pytest
import yaml

from docker_templates_tools import app, scaffold
from docker_templates_tools.app import (
    collect,
    find_stack,
    format_env_value,
    group_variables,
    load_services,
    new_stack_spec,
    render_stack_spec,
    run_app,
    update_env_text,
)
from docker_templates_tools.interpolation import VarRef
from docker_templates_tools.prompts import Aborted, Prompter


class Script:
    """Scripted answers for Prompter. Each step is (text the prompt must
    contain, answer); an answer can be a callable that gets everything
    printed so far (used to pick a menu entry by its label)."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.out = io.StringIO()

    def __call__(self, prompt):
        if not self.steps:
            raise EOFError
        expected, answer = self.steps.pop(0)
        assert expected in prompt, f"expected {expected!r} in prompt {prompt!r}"
        return answer(self.out.getvalue()) if callable(answer) else answer

    def prompter(self):
        return Prompter(self, out=self.out)


def pick(label):
    """Answer a menu prompt with the number of the entry containing `label`."""

    def answer(printed):
        for line in reversed(printed.splitlines()):
            m = re.match(r"\s*(\d+)\) (.*)$", line)
            if m and label in m.group(2):
                return m.group(1)
        raise AssertionError(f"{label!r} not in menu:\n{printed}")

    return answer


@pytest.fixture
def roots(tmp_path):
    stacks = tmp_path / "stacks"
    secrets = tmp_path / "secrets"
    (secrets / "env" / "000-generic").mkdir(parents=True)
    stacks.mkdir()
    (stacks / "common.mk").write_text("")
    (secrets / "env" / "000-generic" / ".env").write_text(
        f"SECRETS_DIR={secrets}\nSTACK_DOMAIN=example.com\n"
    )
    return stacks, secrets


@pytest.fixture
def repo_copy(tmp_path, monkeypatch):
    """A scratch copy of templates/ so generating a new stack never
    touches the real repo."""
    root = tmp_path / "repo"
    shutil.copytree(scaffold.TEMPLATES_DIR, root / "templates")
    monkeypatch.setattr(scaffold, "REPO_ROOT", root)
    monkeypatch.setattr(scaffold, "TEMPLATES_DIR", root / "templates")
    monkeypatch.setattr(scaffold, "SERVICES_BASE", root / "templates" / "services" / "base")
    monkeypatch.setattr(scaffold, "STACKS_DIR", root / "templates" / "stacks")
    monkeypatch.setattr(scaffold, "SERVICE_SPECS_DIR", root / "templates" / "specs" / "services")
    monkeypatch.setattr(scaffold, "STACK_SPECS_DIR", root / "templates" / "specs" / "stacks")
    return root


# --------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------


def test_collect_metabase_stack_vars_and_secrets():
    variables, secrets = collect([scaffold.STACKS_DIR / "docker-compose.metabase-postgres.yml"])
    assert variables["METABASE_TAG"].required
    assert variables["POSTGRES_NAME"].required
    assert variables["POSTGRES_HOST"].default == "postgres"
    assert variables["STACK_DOMAIN"].required
    assert "COMPOSE_PROJECT_NAME" not in variables
    assert "BACKUP_FILE" not in variables
    assert secrets == {
        "postgres_password": "${SECRETS_DIR:?SECRETS_DIR is required}"
        "/env/${COMPOSE_PROJECT_NAME}/postgres_password"
    }


def test_collect_ignores_references_in_comments():
    variables, _ = collect([scaffold.STACKS_DIR / "docker-compose.gitlab.yml"])
    assert "VAR" not in variables


def test_group_variables_shared_first_and_longest_prefix_wins():
    names = ["ADGUARD_HOME_TZ", "STACK_DOMAIN", "POSTGRES_NAME", "RESTART"]
    groups = group_variables(dict.fromkeys(names), {"ADGUARD", "ADGUARD_HOME", "POSTGRES"})
    assert groups == {
        "shared": ["STACK_DOMAIN", "RESTART"],
        "adguard-home": ["ADGUARD_HOME_TZ"],
        "postgres": ["POSTGRES_NAME"],
    }


def test_find_stack_matches_app_deps_and_extensions():
    stacks = [{"app": "gitea", "dep": "mariadb", "extensions": {"database": "mariadb"}}]
    assert find_stack(stacks, "gitea", ["mariadb"], {"database": "mariadb"})
    assert find_stack(stacks, "gitea", ["mariadb"], {}) is None
    assert find_stack(stacks, "gitea", [], {"database": "mariadb"}) is None


def test_update_env_text_replaces_in_place_and_appends():
    text = "# comment\nA=1\nB=2\n"
    assert update_env_text(text, {"B": "3", "C": "4"}) == "# comment\nA=1\nB=3\nC=4\n"


def test_format_env_value_quotes_only_when_needed():
    assert format_env_value("abc/def:1.0") == "abc/def:1.0"
    assert format_env_value("https://${STACK_DOMAIN}") == "https://${STACK_DOMAIN}"
    assert format_env_value("two words") == '"two words"'
    assert format_env_value('a"b') == '"a\\"b"'


def test_prompter_choose_none_and_retry():
    script = Script([("Select", "9"), ("Select", "0")])
    assert script.prompter().choose("t", ["a", "b"], none_label="none") is None
    assert "enter a number from 0 to 2" in script.out.getvalue()


def test_prompter_ask_required_repeats():
    script = Script([("X", ""), ("X", "v")])
    assert script.prompter().ask("X", required=True) == "v"


def test_prompter_eof_aborts():
    with pytest.raises(Aborted):
        Script([]).prompter().confirm("q")


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------

METABASE_ANSWERS = [
    ("App name", ""),
    ("External network", ""),
    ("Customize advanced", ""),
    ("STACK_DOMAIN (required) = 'example.com'", ""),
    ("SECRETS_DIR (required) =", ""),
    ("METABASE_TAG (required)", "v0.63.18.1"),
    ("POSTGRES_HOST (optional, default postgres", ""),
    ("POSTGRES_PORT (optional, default 5432", ""),
    ("POSTGRES_NAME (required)", "metabase"),
    ("POSTGRES_USER (required)", "metabase"),
    ("POSTGRES_TAG (required)", "16.4"),
    ("use generated value", ""),
    ("Write these files?", ""),
]


def test_app_from_stack_writes_compose_env_and_secret(roots):
    stacks, secrets = roots
    script = Script([("Select", pick("stack    metabase + postgres")), *METABASE_ANSWERS])

    assert run_app(script.prompter(), stacks_root=stacks, secrets_root=secrets) == 0
    assert not script.steps

    compose = yaml.safe_load((stacks / "metabase" / "docker-compose.yml").read_text())
    include = compose["include"][0]
    assert include["path"].endswith("templates/stacks/docker-compose.metabase-postgres.yml")
    assert (stacks / "metabase" / include["path"]).resolve().exists()
    assert include["env_file"] == [
        f"{secrets}/env/000-generic/.env",
        f"{secrets}/env/${{COMPOSE_PROJECT_NAME}}/.env",
    ]
    assert compose["services"]["metabase"]["networks"] == ["frontend"]
    assert compose["networks"]["frontend"] == {"name": "domain_reverse-proxy", "external": True}
    assert (stacks / "metabase" / "Makefile").read_text() == "include ../common.mk\n"

    env = (secrets / "env" / "metabase" / ".env").read_text()
    assert "METABASE_TAG=v0.63.18.1\n" in env
    assert "POSTGRES_TAG=16.4\n" in env
    assert "STACK_DOMAIN" not in env  # already provided by 000-generic/.env
    assert "POSTGRES_HOST" not in env  # default kept

    secret = secrets / "env" / "metabase" / "postgres_password"
    assert re.fullmatch(r"[A-Za-z0-9]{32}", secret.read_text())
    assert secret.stat().st_mode & 0o777 == 0o600


def test_app_confirms_existing_values_and_secrets(roots):
    stacks, secrets = roots
    app_env = secrets / "env" / "metabase" / ".env"
    app_env.parent.mkdir(parents=True)
    app_env.write_text("# keep me\nMETABASE_TAG=v0.60.0\nPOSTGRES_NAME=metabase\n")
    secret = secrets / "env" / "metabase" / "postgres_password"
    secret.write_text("old-secret")

    script = Script(
        [
            ("Select", pick("stack    metabase + postgres")),
            ("App name", ""),
            ("External network", "-"),
            ("Customize advanced", ""),
            ("STACK_DOMAIN (required) = 'example.com'", ""),
            ("SECRETS_DIR (required) =", ""),
            ("METABASE_TAG (required) = 'v0.60.0'", "n"),
            ("new value for METABASE_TAG", "v0.63.18.1"),
            ("POSTGRES_HOST", ""),
            ("POSTGRES_PORT", ""),
            ("POSTGRES_NAME (required) = 'metabase'", "y"),
            ("POSTGRES_USER (required)", "metabase"),
            ("POSTGRES_TAG (required)", "16.4"),
            ("Secret postgres_password exists", "y"),
            ("Write these files?", "y"),
        ]
    )
    assert run_app(script.prompter(), stacks_root=stacks, secrets_root=secrets) == 0
    assert not script.steps

    assert app_env.read_text() == (
        "# keep me\nMETABASE_TAG=v0.63.18.1\nPOSTGRES_NAME=metabase\n"
        "POSTGRES_USER=metabase\nPOSTGRES_TAG=16.4\n"
    )
    assert secret.read_text() == "old-secret"
    assert "services" not in yaml.safe_load(
        (stacks / "metabase" / "docker-compose.yml").read_text()
    )


def test_app_existing_compose_kept_when_not_replaced(roots):
    stacks, secrets = roots
    compose = stacks / "metabase" / "docker-compose.yml"
    compose.parent.mkdir()
    compose.write_text("include:\n  - custom.yml\n")
    answers = [a for a in METABASE_ANSWERS if a[0] != "External network"]
    script = Script(
        [
            ("Select", pick("stack    metabase + postgres")),
            answers[0],
            ("Replace it?", ""),
            *answers[1:],
        ]
    )
    assert run_app(script.prompter(), stacks_root=stacks, secrets_root=secrets) == 0
    assert compose.read_text() == "include:\n  - custom.yml\n"


def test_app_declined_writes_nothing(roots):
    stacks, secrets = roots
    answers = METABASE_ANSWERS[:-1] + [("Write these files?", "n")]
    script = Script([("Select", pick("stack    metabase + postgres")), *answers])
    assert run_app(script.prompter(), stacks_root=stacks, secrets_root=secrets) == 1
    assert not (stacks / "metabase").exists()
    assert not (secrets / "env" / "metabase").exists()


def test_app_service_with_variant_reuses_matching_stack(roots):
    stacks, secrets = roots
    script = Script(
        [
            ("Select", pick("service  metabase")),
            ("Select", pick("postgres (service)")),
            *METABASE_ANSWERS,
        ]
    )
    assert run_app(script.prompter(), stacks_root=stacks, secrets_root=secrets) == 0
    assert "Using existing stack metabase + postgres" in script.out.getvalue()


def test_app_service_without_deps_includes_service_files(roots):
    stacks, secrets = roots
    script = Script(
        [
            ("Select", pick("service  trilium")),
            ("App name", "notes"),
            ("External network", ""),
            ("Customize advanced", ""),
            ("STACK_DOMAIN", ""),
            ("TRILIUM_TAG", "v0.99.0"),
            ("TRILIUM_TRUSTED_REVERSE_PROXY", ""),
            ("TRILIUM_HOMEPAGE_KEY", ""),
            ("Write these files?", ""),
        ]
    )
    assert run_app(script.prompter(), stacks_root=stacks, secrets_root=secrets) == 0
    compose = yaml.safe_load((stacks / "notes" / "docker-compose.yml").read_text())
    path = compose["include"][0]["path"]
    assert path.endswith("templates/services/base/trilium/docker-compose.yml")
    assert "TRILIUM_TAG=v0.99.0" in (secrets / "env" / "notes" / ".env").read_text()


def test_app_generates_stack_for_uncovered_dependency(roots, repo_copy):
    stacks, secrets = roots
    script = Script(
        [
            ("Select", pick("service  metabase")),
            ("Select", pick("mariadb (service)")),
            ("App name", ""),
            ("External network", ""),
            ("Customize advanced", ""),
            ("STACK_DOMAIN", ""),
            ("SECRETS_DIR", ""),
            ("METABASE_TAG", "v0.63.18.1"),
            ("MARIADB_HOST", ""),
            ("MARIADB_PORT", ""),
            ("MARIADB_NAME", "metabase"),
            ("MARIADB_USER", "metabase"),
            ("MARIADB_TAG", "11.8"),
            ("MARIADB_BUFFER_POOL", ""),
            ("MARIADB_MAX_CONNECTIONS", ""),
            ("use generated value", ""),
            ("use generated value", "n"),
            ("value for mariadb_root_password", "my-root-pw"),
            ("Write these files?", ""),
        ]
    )
    assert run_app(script.prompter(), stacks_root=stacks, secrets_root=secrets) == 0
    assert not script.steps

    spec = yaml.safe_load(
        (repo_copy / "templates" / "specs" / "stacks" / "metabase-mariadb.yaml").read_text()
    )
    assert spec["app"] == "metabase"
    assert spec["dep"] == "mariadb"
    assert spec["extensions"] == {"database": "mariadb"}
    stack_file = repo_copy / "templates" / "stacks" / "docker-compose.metabase-mariadb.yml"
    assert stack_file.read_text().startswith(
        "# Auto-generated from templates/specs/stacks/metabase-mariadb.yaml"
    )
    assert "../services/base/metabase/database/docker-compose.mariadb.yml" in stack_file.read_text()
    root_pw = secrets / "env" / "metabase" / "mariadb_root_password"
    assert root_pw.read_text() == "my-root-pw"


def test_app_dry_run_writes_nothing(roots, repo_copy):
    stacks, secrets = roots
    answers = [a for a in METABASE_ANSWERS if a[0] != "Write these files?"]
    script = Script([("Select", pick("stack    metabase + postgres")), *answers])
    assert run_app(script.prompter(), stacks_root=stacks, secrets_root=secrets, dry_run=True) == 0
    assert not (stacks / "metabase").exists()
    assert "METABASE_TAG=v0.63.18.1" in script.out.getvalue()
    assert "postgres_password" in script.out.getvalue()


def test_cli_app_aborts_cleanly_on_eof(roots, monkeypatch, capsys):
    stacks, secrets = roots
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError))
    rc = scaffold.main(["app", "--stacks-dir", str(stacks), "--secrets-dir", str(secrets)])
    assert rc == 1
    assert "aborted, nothing written" in capsys.readouterr().err


def test_generate_secret_is_32_alphanumerics():
    assert re.fullmatch(r"[A-Za-z0-9]{32}", app.generate_secret())


def test_varref_merge_required_wins(tmp_path):
    a = tmp_path / "a.yml"
    b = tmp_path / "b.yml"
    a.write_text("include:\n  - b.yml\nservices:\n  x:\n    image: ${X:-1}\n")
    b.write_text("services:\n  y:\n    image: ${X:?X is required}\n")
    variables, _ = collect([a])
    assert variables["X"] == VarRef("X", required=True, default="1")


def test_render_stack_spec_matches_hand_written_layout():
    services = load_services()
    d = new_stack_spec(services["npm"], [services["mariadb"]], {"database": "mariadb"})
    text = render_stack_spec(d)
    assert text.startswith(
        "app: npm\napp_slug: nginx-proxy-manager\ndep: mariadb\n\n"
        "extensions:\n  database: mariadb\n\n"
        "app_extra:\n  profiles:\n    - frontend\n\n"
        "dep_extra:\n  profiles:\n    - backend\n\n"
        "header_comment: |\n  Composed template: Nginx Proxy Manager + MariaDB.\n"
    )
    assert yaml.safe_load(text) == d


PASSBOLT_ANSWERS = [
    ("App name", ""),
    ("External network", ""),
    ("Customize advanced", ""),
    ("SECRETS_DIR (required)", ""),
    ("STACK_DOMAIN (required) = 'example.com'", ""),
    ("PASSBOLT_TAG", "latest"),
    ("PASSBOLT_SSL_FORCE", ""),
    ("PASSBOLT_REGISTRATION_PUBLIC", ""),
    ("PASSBOLT_GPG_FINGERPRINT", "ABCD"),
    ("MARIADB_HOST", ""),
    ("MARIADB_NAME", "passbolt"),
    ("MARIADB_USER", "passbolt"),
    ("MARIADB_TAG", "11.8"),
    ("MARIADB_BUFFER_POOL", ""),
    ("MARIADB_MAX_CONNECTIONS", ""),
    ("SMTP_HOST", "mail-relay"),
    ("SMTP_PORT", ""),
    ("SMTP_TLS", ""),
    ("SMTP_USER", "noreply"),
    ("SMTP_FROM", "noreply@example.com"),
    ("use generated value", ""),
    ("use generated value", ""),
    ("use generated value", ""),
    ("Write these files?", ""),
]


def test_app_routes_shared_and_generic_values_to_their_files(tmp_path, monkeypatch):
    stacks, secrets = tmp_path / "stacks", tmp_path / "secrets"
    stacks.mkdir()
    (secrets / "env" / "000-generic").mkdir(parents=True)
    (secrets / "env" / "000-generic" / ".env").write_text("STACK_DOMAIN=example.com\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    script = Script([("Select", pick("stack    passbolt + mariadb")), *PASSBOLT_ANSWERS])

    assert run_app(script.prompter(), stacks_root=stacks, secrets_root=secrets) == 0
    assert not script.steps
    assert "-- smtp (shared: " in script.out.getvalue()

    generic = secrets / "env" / "000-generic"
    assert (generic / ".env").read_text() == (f"STACK_DOMAIN=example.com\nSECRETS_DIR={secrets}\n")
    assert (generic / "smtp" / ".env").read_text() == (
        "SMTP_HOST=mail-relay\nSMTP_USER=noreply\nSMTP_FROM=noreply@example.com\n"
    )
    own = (secrets / "env" / "passbolt" / ".env").read_text()
    assert "SMTP_" not in own and "SECRETS_DIR" not in own
    assert "PASSBOLT_GPG_FINGERPRINT=ABCD\n" in own
    assert (generic / "smtp" / "password").exists()

    text = (stacks / "passbolt" / "docker-compose.yml").read_text()
    assert (
        "    env_file:\n"
        "      - ${HOME}/secrets/env/000-generic/.env              # shared by every app\n"
        "      - ${HOME}/secrets/env/000-generic/smtp/.env         # shared, because passbolt uses smtp\n"
        "      - ${HOME}/secrets/env/${COMPOSE_PROJECT_NAME}/.env  # passbolt's own values, last so they win\n"
    ) in text


def test_app_creates_every_listed_env_file(roots):
    stacks, secrets = roots
    script = Script([("Select", pick("stack    metabase + postgres")), *METABASE_ANSWERS])
    assert run_app(script.prompter(), stacks_root=stacks, secrets_root=secrets) == 0
    assert (secrets / "env" / "000-generic" / ".env").exists()
    assert (secrets / "env" / "metabase" / ".env").exists()
    assert not (secrets / "env" / "000-generic" / "smtp").exists()  # metabase sends no mail


def test_shared_groups_follow_chosen_variants():
    services = load_services()
    assert app.shared_groups(services, "passbolt", ["mariadb"], {"mailer": "smtp"}) == ["smtp"]
    assert app.shared_groups(services, "passbolt", ["mariadb"], {}) == []
