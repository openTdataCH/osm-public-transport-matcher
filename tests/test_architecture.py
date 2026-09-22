"""Guard real boundaries and prove that relative imports cannot bypass the guard."""
import importlib.util
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("engine_quality_check", ROOT / "quality/check.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def test_engine_dependency_boundaries():
    assert CHECK.architecture_errors() == []


@pytest.mark.parametrize("statement", [
    "import transport_matcher.adapters.gtfs",
    "from transport_matcher import profiles",
    "from ..results import write_bundle",
    "from .. import acquisition",
    "from backend.models import Stop",
])
def test_dependency_guard_rejects_absolute_and_relative_imports(tmp_path, statement):
    source = tmp_path / "src/transport_matcher/core/bad.py"
    source.parent.mkdir(parents=True)
    source.write_text(statement)
    assert CHECK.architecture_errors(tmp_path)


def test_generic_policy_preserves_public_import_identity():
    from transport_matcher import MatchingProfile, ReferenceRule
    from transport_matcher.core import profile
    from transport_matcher.profiles import GroupingPolicy, SWITZERLAND

    assert MatchingProfile is profile.MatchingProfile
    assert ReferenceRule is profile.ReferenceRule
    assert GroupingPolicy is profile.GroupingPolicy
    assert isinstance(SWITZERLAND, MatchingProfile)


@pytest.mark.parametrize("git_checkout", [True, False])
def test_ownership_discovery_includes_unregistered_source_outside_declared_roots(tmp_path, git_checkout):
    if git_checkout:
        subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    files = ["accidental.py", "new_directory/script.js", "Makefile", "tests/fixture.json", "quality/raw/report.json"]
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    catalog = {
        "source_roots": ["src"],
        "source_files": ["Makefile"],
        "exclusions": [{"pattern": "tests/**"}, {"pattern": "quality/raw/**"}],
    }
    discovered = {path.relative_to(tmp_path).as_posix() for path in CHECK.source_files(tmp_path, catalog)}
    assert discovered == {"accidental.py", "new_directory/script.js", "Makefile"}
