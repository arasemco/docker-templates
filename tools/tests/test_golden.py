"""Regression net for the scaffold.py refactor: every real spec under
templates/specs/ must still produce byte-for-byte the same files that are
already committed under templates/services/ and templates/stacks/. This is
the guarantee that a refactor "reproduces the same result" — a change that
alters output for any real spec fails here."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOLS_DIR.parent
TEMPLATES_DIR = REPO_ROOT / "templates"

SERVICE_SPECS = sorted((TEMPLATES_DIR / "specs" / "services").glob("*.yaml"))
STACK_SPECS = sorted((TEMPLATES_DIR / "specs" / "stacks").glob("*.yaml"))

_HEADER_RE = re.compile(r"^----- (.+) -----$")


def _dry_run_files(command: str, spec_path: Path) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "docker_templates_tools", command, str(spec_path), "--dry-run"],
        cwd=TOOLS_DIR,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    files = {}
    path = None
    buf = []
    for line in result.stdout.splitlines(keepends=True):
        m = _HEADER_RE.match(line.rstrip("\n"))
        if m:
            if path is not None:
                files[path] = "".join(buf)
            path = m.group(1)
            buf = []
        else:
            buf.append(line)
    if path is not None:
        files[path] = "".join(buf)
    return files


@pytest.mark.parametrize("spec_path", SERVICE_SPECS, ids=lambda p: p.stem)
def test_service_spec_output_matches_committed_files(spec_path):
    files = _dry_run_files("service", spec_path)
    assert files, f"no files generated for {spec_path}"
    for rel_path, content in files.items():
        on_disk = REPO_ROOT / rel_path
        assert on_disk.exists(), f"{rel_path} generated but not on disk"
        assert content == on_disk.read_text(), f"{rel_path} differs from generator output"


@pytest.mark.parametrize("spec_path", STACK_SPECS, ids=lambda p: p.stem)
def test_stack_spec_output_matches_committed_files(spec_path):
    files = _dry_run_files("stack", spec_path)
    assert files, f"no files generated for {spec_path}"
    for rel_path, content in files.items():
        on_disk = REPO_ROOT / rel_path
        assert on_disk.exists(), f"{rel_path} generated but not on disk"
        assert content == on_disk.read_text(), f"{rel_path} differs from generator output"


def test_every_committed_service_file_is_reproduced():
    """Catch the opposite drift too: a committed file no generator run
    produces (e.g. an orphaned docker-compose.homepage.yml left behind by a
    schema change)."""
    generated = set()
    for spec_path in SERVICE_SPECS:
        generated.update(_dry_run_files("service", spec_path).keys())

    # templates/services/base/backup/ is shared, hand-written infrastructure
    # (the backup/restore service every other service's
    # docker-compose.backup.yml includes) — no service spec generates it,
    # by design.
    services_base = TEMPLATES_DIR / "services" / "base"
    committed = {
        str(p.relative_to(REPO_ROOT))
        for p in services_base.rglob("docker-compose*.yml")
        if p.relative_to(services_base).parts[0] != "backup"
    }
    orphaned = committed - generated
    assert not orphaned, f"committed files no spec regenerates: {sorted(orphaned)}"
