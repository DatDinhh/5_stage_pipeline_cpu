#!/usr/bin/env python3
"""Export a verified full signoff as a portable, repository-facing summary."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_tests import (  # noqa: E402
    AGGREGATE_SIGNOFF_BINS, RunnerError, VerificationRunner, load_plan,
    sha256_file, verification_inputs,
)
from verification.synthesis import inspect_netlist  # noqa: E402


def relative_path(value: str | Path, root: Path) -> tuple[Path, str]:
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    try:
        name = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("evidence path is outside the repository") from exc
    return path, name


def verified_artifact(value: str | Path, expected: str, root: Path) -> dict[str, str]:
    path, name = relative_path(value, root)
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or sha256_file(path) != expected:
        raise ValueError(f"artifact hash mismatch: {name}")
    return {"path": name, "sha256": expected}


def concise_version(name: str, value: str) -> str:
    labels = {"python": "Python", "iverilog": "Icarus Verilog",
              "vvp": "Icarus Verilog runtime", "verilator": "Verilator", "yosys": "Yosys"}
    prefixes = {"python": r"^", "iverilog": r"Icarus Verilog version\s+",
                "vvp": r"Icarus Verilog runtime version\s+", "verilator": r"Verilator\s+",
                "yosys": r"Yosys\s+"}
    match = re.search(prefixes[name] + r"(\d+(?:\.\d+)+(?:[a-zA-Z][a-zA-Z0-9]*)?)", value)
    if not match:
        raise ValueError(f"missing recognizable {name} version")
    return f"{labels[name]} {match.group(1)}"


def portable_text(value: str, root: Path) -> str:
    roots = {str(root), root.as_posix()}
    if root.drive:
        roots.add(f"/mnt/{root.drive[0].lower()}/{root.as_posix()[3:]}")
    for prefix in sorted(roots, key=len, reverse=True):
        value = value.replace(prefix + "/", "").replace(prefix + "\\", "")
    if re.search(r"[A-Za-z]:[\\/]|/(?:mnt|Users|home|root|tmp|usr|opt|var)/", value):
        raise ValueError("public evidence contains an absolute machine path")
    if re.search(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b|\b(?:19|20)\d{6}-\d{6}\b", value):
        raise ValueError("public evidence contains a calendar date")
    return value


def portable_tree(value: Any, root: Path) -> Any:
    if isinstance(value, str):
        return portable_text(value, root)
    if isinstance(value, list):
        return [portable_tree(item, root) for item in value]
    if isinstance(value, dict):
        return {portable_text(key, root): portable_tree(item, root) for key, item in value.items()}
    return value


def build_summary(manifest_path: Path, *, root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    manifest_path, manifest_name = relative_path(manifest_path, root)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "pass" or manifest.get("suite") != "signoff":
        raise ValueError("publication requires a passed full signoff")
    results = manifest["results"]
    if not results or any(record.get("status") != "pass" for record in results):
        raise ValueError("publication requires all result records to pass")
    if any(record.get("kind") not in {"unit", "rtl", "protocol", "protocol_negative", "mutation"}
           for record in results):
        raise ValueError("unknown signoff result kind")
    current = {path.resolve().relative_to(root).as_posix(): sha256_file(path)
               for path in verification_inputs()}
    if (not current or manifest.get("input_hashes") != current or
            manifest.get("final_input_hashes") != current):
        raise ValueError("source inputs changed, were added, or were removed since signoff")
    rehash = {"status": "pass", "checked_inputs": len(current),
              "added": [], "removed": [], "changed": []}
    if manifest.get("source_input_rehash") != rehash:
        raise ValueError("source input rehash evidence is incomplete")
    if manifest.get("mutation_original_tree_rehash") != "pass":
        raise ValueError("mutation source integrity evidence is incomplete")
    if current.get("tests/testplan.json") != manifest.get("testplan_sha256"):
        raise ValueError("test plan hash does not match the signoff")

    runner = object.__new__(VerificationRunner)
    runner.results = results
    runner.tests = load_plan(root / "tests/testplan.json")
    runner.aggregate_coverage = manifest["aggregate_coverage"]
    runner.enforce_signoff_matrix()
    runner.enforce_signoff_coverage()
    units = sorted(record["name"] for record in results if record["kind"] == "unit")
    if units != sorted(("python_unit", "forwarding_unit", "hazard_unit",
                        "memory_model", "memory_range_guard")):
        raise ValueError("signoff unit matrix is incomplete or duplicated")
    for record in results:
        if record["kind"] == "rtl":
            for filename, key in (("program.hex", "program_sha256"), ("data.hex", "data_sha256")):
                verified_artifact(Path(record["artifacts"]) / filename, record[key], root)

    unit_log = manifest_path.parent / "unit/python/run.log"
    log_text = unit_log.read_text(encoding="utf-8")
    counts = re.findall(r"^Ran (\d+) tests? in [0-9.]+s$", log_text, re.MULTILINE)
    if len(counts) != 1 or int(counts[0]) <= 0 or not re.search(r"^OK\s*$", log_text, re.MULTILINE):
        raise ValueError("Python unit log lacks an unambiguous passing test count")

    toolchain = manifest["independent_toolchain"]
    checks = toolchain["checks"]
    if (toolchain.get("status") != "PASS" or len(checks) != 2 or
            {check.get("name") for check in checks} != {"verilator", "yosys"} or
            any(check.get("status") != "PASS" for check in checks)):
        raise ValueError("publication requires Verilator and Yosys to pass")
    core_hashes = {name: digest for name, digest in current.items()
                   if name.startswith("rtl/core/") and name.endswith(".v")}
    if not core_hashes or toolchain.get("sources") != core_hashes:
        raise ValueError("toolchain source hashes do not match signoff")
    public_checks = []
    for check in checks:
        name = check["name"]
        public = {"name": name, "kind": "lint" if name == "verilator" else "synthesis",
                  "status": "PASS", "version": concise_version(name, check["version"]),
                  "warning_count": check["warning_count"], "warnings": check["warnings"]}
        if name == "verilator":
            public["warning_policy"] = check["warning_policy"]
        else:
            public["script"] = verified_artifact(check["script"], check["script_sha256"], root)
            public["netlist"] = verified_artifact(check["netlist"], check["netlist_sha256"], root)
            artifacts = {key: verified_artifact(item["path"], item["sha256"], root)
                         for key, item in check["artifacts"].items()}
            required = {"statistics.json", "synthesized.v", "netlist_summary.json"}
            if not required.issubset(artifacts):
                raise ValueError("synthesis artifacts are incomplete")
            public["artifacts"] = {key: artifacts[key] for key in sorted(required)}
            netlist_path, _ = relative_path(check["netlist"], root)
            summary = inspect_netlist(netlist_path)
            stored_path = root / artifacts["netlist_summary.json"]["path"]
            stats_path = root / artifacts["statistics.json"]["path"]
            if (summary != check["netlist_summary"] or
                    summary != json.loads(stored_path.read_text(encoding="utf-8")) or
                    summary["leaf_cells"] != json.loads(stats_path.read_text(encoding="utf-8"))["design"]["num_cells"]):
                raise ValueError("synthesis summary/statistics disagree with the netlist")
            summary["creator"] = concise_version("yosys", summary["creator"])
            public["netlist_summary"] = summary
        public_checks.append(public)

    scope = toolchain["synthesis_scope"]
    public_toolchain = {
        "top": toolchain["top"], "status": "PASS", "scope": toolchain["scope"],
        "synthesis_scope": {key: scope[key] for key in (
            "target", "parameters", "instrumentation", "external_memory", "physical_results")},
        "sources": core_hashes, "checks": public_checks,
    }
    snapshot = {
        "schema_version": 2, "status": "pass",
        "scope": "Portable summary of a local finite signoff; raw logs and manifests remain in build. No remote CI or physical implementation claim.",
        "reproduce": "python scripts/run_tests.py --suite signoff",
        "manifest": manifest_name, "manifest_sha256": sha256_file(manifest_path),
        "source_hashes": current, "source_input_rehash": rehash,
        "mutation_original_tree_rehash": "pass", "total_results": len(results),
        "result_counts": dict(sorted(Counter(record["kind"] for record in results).items())),
        "python_unit_tests": int(counts[0]),
        "python_unit_log": {"path": unit_log.relative_to(root).as_posix(), "sha256": sha256_file(unit_log)},
        "rtl_cycles": sum(record["cycles"] for record in results if record["kind"] == "rtl"),
        "duration_seconds": manifest["duration_seconds"],
        "aggregate_coverage": manifest["aggregate_coverage"],
        "required_core_coverage": list(AGGREGATE_SIGNOFF_BINS),
        "tools": {name: concise_version(name, value) for name, value in {
            "python": manifest["python"],
            **{name: manifest["tools"][name] for name in ("iverilog", "vvp", "verilator", "yosys")},
        }.items()},
        "toolchain": public_toolchain,
        "showcase_evidence": "docs/assets/showcase_evidence.json",
    }
    return portable_tree(snapshot, root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="passed full-signoff manifest")
    args = parser.parse_args(argv)
    try:
        summary = build_summary(args.manifest)
        output = ROOT / "docs/assets/signoff_summary.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    except (OSError, ValueError, KeyError, TypeError, RunnerError) as exc:
        print(f"EVIDENCE EXPORT FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"EVIDENCE EXPORT PASS: {output.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
