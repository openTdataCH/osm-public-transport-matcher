"""Validate ownership, documentation references, and source import boundaries."""
from __future__ import annotations

import ast
from fnmatch import fnmatchcase
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CORE_FORBIDDEN = (
    "public_transport_matcher.acquisition", "public_transport_matcher.adapters",
    "public_transport_matcher.integrations", "public_transport_matcher.profiles",
    "public_transport_matcher.results", "public_transport_matcher.swiss", "public_transport_matcher.cli",
)
APP_FORBIDDEN = ("backend", "stop_sync_osm_atlas")
CODE_SUFFIXES = {".py", ".js", ".cjs", ".mjs", ".css", ".html", ".sql", ".sh", ".yml", ".yaml", ".json", ".toml"}
GENERATED_DIRS = {".git", ".venv", "__pycache__", "build", "dist", "mutants", ".ruff_cache", ".pytest_cache", ".hypothesis"}


def source_files(root: Path, catalog):
    """Discover production code independently of the catalog's ownership globs."""
    if (root / ".git").exists():
        names = subprocess.check_output(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root,
        ).decode().split("\0")
        candidates = {root / name for name in names if name}
    else:
        # Source archives and the supported container omit Git metadata.
        candidates = set()
        for directory, directories, files in os.walk(root):
            directories[:] = [name for name in directories if name not in GENERATED_DIRS and not name.endswith(".egg-info")]
            candidates.update(Path(directory) / name for name in files)
    explicit = {root / name for name in catalog.get("source_files", [])}
    return {
        path for path in candidates | explicit
        if path.is_file() and (path.suffix in CODE_SUFFIXES or path in explicit)
        and not any(fnmatchcase(path.relative_to(root).as_posix(), entry["pattern"]) for entry in catalog["exclusions"])
    }


def imports(path: Path, root: Path = ROOT):
    """Resolve absolute and relative static imports without importing source code."""
    relative = path.relative_to(root / "src").with_suffix("")
    package = list(relative.parts[:-1])
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            parts = package[:len(package) - node.level + 1] if node.level else []
            if node.module:
                parts.extend(node.module.split("."))
            base = ".".join(parts)
            yield base, node.lineno
            for alias in node.names:
                yield f"{base}.{alias.name}", node.lineno


def architecture_errors(root: Path = ROOT):
    errors = []
    for path in sorted((root / "src").rglob("*.py")):
        in_core = "core" in path.relative_to(root / "src").parts
        forbidden = APP_FORBIDDEN + (CORE_FORBIDDEN if in_core else ())
        for imported, line in imports(path, root):
            if any(imported == name or imported.startswith(name + ".") for name in forbidden):
                errors.append(f"{path.relative_to(root)}:{line}: forbidden dependency {imported}")
    return sorted(set(errors))


def catalog_errors(root: Path = ROOT):
    from jsonschema import Draft202012Validator

    catalog = json.loads((root / "quality/modules.yml").read_text(encoding="utf-8"))
    schema = json.loads((root / "quality/modules.schema.json").read_text(encoding="utf-8"))
    errors = [error.message for error in Draft202012Validator(schema).iter_errors(catalog)]
    if errors:
        return errors
    ids = [module["id"] for module in catalog["modules"]]
    if len(ids) != len(set(ids)):
        errors.append("Module IDs must be unique")
    owners = {}
    for module in catalog["modules"]:
        for pattern in module["paths"]["source"]:
            matches = [path for path in root.glob(pattern) if path.is_file()]
            if not matches:
                errors.append(f"{module['id']}: source pattern has no files: {pattern}")
            for path in matches:
                owners.setdefault(path, set()).add(module["id"])
        for pattern in module["paths"]["tests"]:
            if not any(root.glob(pattern)):
                errors.append(f"{module['id']}: missing test {pattern}")
        for paths in module["docs"].values():
            for path in paths:
                if not (root / path).is_file():
                    errors.append(f"{module['id']}: missing document {path}")
    for path in sorted(source_files(root, catalog)):
        if len(owners.get(path, set())) != 1:
            errors.append(f"{path.relative_to(root)}: expected exactly one owner, got {sorted(owners.get(path, set()))}")
    return errors


def main():
    if sys.version_info < (3, 13):
        raise SystemExit("Quality checks require Python 3.13+. See quality/README.md.")
    errors = catalog_errors() + architecture_errors()
    if errors:
        raise SystemExit("\n".join(errors))
    print("Engine catalog, documentation, and architecture checks passed.")


if __name__ == "__main__":
    main()
