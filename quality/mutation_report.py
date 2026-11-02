"""Keep selected mutation results separate from mutations outside the sample."""
from collections import Counter
from datetime import datetime, timezone
from fnmatch import fnmatchcase
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "src/osm_public_transport_matcher/results/validation.py"
TESTS = ("tests/test_results.py", "tests/test_quality_properties.py")


def fingerprints():
    return {
        "source_sha256": hashlib.sha256((ROOT / SOURCE).read_bytes()).hexdigest(),
        "tests_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in TESTS},
    }


def main():
    starting = sys.argv[1] == "--start"
    target, output_dir = sys.argv[2:] if starting else sys.argv[1:]
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    provenance_path = output / "engine-mutation-provenance.json"
    if starting:
        provenance = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "source_path": SOURCE,
            "scope": target,
            **fingerprints(),
        }
        provenance_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        return
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    if provenance["scope"] != target or any(provenance[key] != value for key, value in fingerprints().items()):
        raise SystemExit("Mutation source or tests changed during execution; rerun the sample.")
    raw = subprocess.check_output([sys.executable, "-m", "mutmut", "results", "--all", "true"], text=True)
    selected = {}
    for line in raw.splitlines():
        name, separator, status = line.strip().partition(": ")
        if separator and fnmatchcase(name, target):
            selected[name] = status
    if not selected:
        raise SystemExit(f"Mutation target matched no generated mutants: {target}")
    counts = dict(Counter(selected.values()))
    report = {
        "schema_version": 1,
        **provenance,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool": {"name": "mutmut", "version": version("mutmut")},
        "scope": target,
        "policy": "advisory; inspect surviving or untested mutants",
        "total_mutants": len(selected),
        "counts": counts,
        "mutants": selected,
    }
    (output / "engine-mutation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    text = f"Scope: {target}\nSelected mutants: {len(selected)}\nPolicy: {report['policy']}\n"
    text += "\n".join(f"{name}: {status}" for name, status in sorted(selected.items())) + "\n"
    (output / "engine-mutation.txt").write_text(text, encoding="utf-8")
    print(f"Mutation sample: {counts} ({len(selected)} selected mutants)")


if __name__ == "__main__":
    main()
