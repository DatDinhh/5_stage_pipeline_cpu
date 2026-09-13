import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.export_evidence import build_summary
from scripts.run_tests import (
    AGGREGATE_SIGNOFF_BINS, RunnerError, SIGNOFF_MASTER_SEEDS,
    SIGNOFF_RANDOM_PROFILES, sha256_file,
)


class EvidenceExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.run = self.root / "build/runs/run-fixture"
        self.run.mkdir(parents=True)
        self.inputs = [self.write("rtl/core/cpu_core.v", "module cpu_core; endmodule\n"),
                       self.write("tests/testplan.json", '{"version": 1, "tests": []}\n')]
        self.write("build/runs/run-fixture/unit/python/run.log", "Ran 12 tests in 0.02s\n\nOK\n")
        artifact_dir = "build/runs/run-fixture/rtl/program"
        program = self.write(artifact_dir + "/program.hex", "00000000\n")
        data = self.write(artifact_dir + "/data.hex", "00000001\n")
        results = [{"kind": "unit", "name": name, "status": "pass"} for name in (
            "python_unit", "forwarding_unit", "hazard_unit", "memory_model", "memory_range_guard")]
        results += [{"kind": "rtl", "name": f"random_{seed}__{profile}", "status": "pass",
                     "master_seed": seed, "profile": profile, "cycles": 3,
                     "artifacts": artifact_dir, "program_sha256": sha256_file(program),
                     "data_sha256": sha256_file(data), "command": "private command"}
                    for seed in SIGNOFF_MASTER_SEEDS for profile in SIGNOFF_RANDOM_PROFILES]
        results += [{"kind": "mutation", "name": f"mutation_{index}", "status": "pass"}
                    for index in range(2)]
        self.summary = {"status": "PASS", "top": "cpu_core", "leaf_cells": 7,
                        "creator": "Yosys 0.33 (git sha1 abcdef)", "sequential_cells": 2,
                        "combinational_cells": 5}
        synthesis_dir = "build/runs/run-fixture/toolchain/yosys/"
        script = self.write(synthesis_dir + "synthesis.ys", "synth -top cpu_core\n")
        netlist = self.write(synthesis_dir + "netlist.json", '{"fixture": "netlist"}\n')
        artifacts = {}
        for name, contents in (
                ("statistics.json", json.dumps({"design": {"num_cells": 7}})),
                ("netlist_summary.json", json.dumps(self.summary)),
                ("synthesized.v", "module cpu_core; endmodule\n")):
            path = self.write(synthesis_dir + name, contents)
            artifacts[name] = {"path": str(path), "sha256": sha256_file(path)}
        sources = {path.relative_to(self.root).as_posix(): sha256_file(path) for path in self.inputs}
        versions = {"iverilog": "Icarus Verilog version 11.0 (stable)\nCopyright 1998-2020",
                    "vvp": "Icarus Verilog runtime version 11.0 (stable)\nprivate runtime path",
                    "verilator": "Verilator 5.020 2024-01-01 rev (Debian 5.020-1)",
                    "yosys": "Yosys 0.33 (git sha1 abcdef)"}
        checks = [{"name": "verilator", "kind": "lint", "status": "PASS",
                   "version": versions["verilator"], "warning_count": 0, "warnings": [],
                   "warning_policy": "default fatal warnings; no warning suppression",
                   "executable": "C:\\Users\\private_user\\verilator.exe"},
                  {"name": "yosys", "kind": "synthesis", "status": "PASS",
                   "version": versions["yosys"], "warning_count": 1,
                   "warnings": [f"Warning: sample. See {self.root.as_posix()}/rtl/core/cpu_core.v:1"],
                   "script": str(script), "script_sha256": sha256_file(script),
                   "netlist": str(netlist), "netlist_sha256": sha256_file(netlist),
                   "netlist_summary": copy.deepcopy(self.summary), "artifacts": artifacts,
                   "discovery": {"searched": ["C:\\Users\\private_user\\yosys.exe"]}}]
        self.manifest = {
            "schema_version": 1, "status": "pass", "suite": "signoff", "results": results,
            "input_hashes": sources, "final_input_hashes": dict(sources),
            "source_input_rehash": {"status": "pass", "checked_inputs": len(sources),
                                    "added": [], "removed": [], "changed": []},
            "mutation_original_tree_rehash": "pass", "testplan_sha256": sources["tests/testplan.json"],
            "aggregate_coverage": {name: 1 for name in AGGREGATE_SIGNOFF_BINS},
            "duration_seconds": 1.5, "python": "3.12.14 (main, Aug 25 2026, 14:01:42)",
            "tools": versions, "repository": str(self.root), "started_utc": "2026-09-13T20:09:01Z",
            "independent_toolchain": {
                "top": "cpu_core", "status": "PASS", "scope": "RTL core only",
                "sources": {"rtl/core/cpu_core.v": sources["rtl/core/cpu_core.v"]},
                "checks": checks, "synthesis_scope": {"target": "generic cells", "parameters": {},
                    "instrumentation": "trace/debug included", "external_memory": "excluded",
                    "physical_results": "not measured"}},
        }
        self.manifest_path = self.run / "manifest.json"
        self.addCleanup(mock.patch.stopall)
        mock.patch("scripts.export_evidence.verification_inputs", side_effect=lambda: self.inputs).start()
        mock.patch("scripts.export_evidence.inspect_netlist",
                   side_effect=lambda path: copy.deepcopy(self.summary)).start()

    def write(self, name, contents):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        return path

    def export(self, manifest=None):
        self.manifest_path.write_text(json.dumps(manifest or self.manifest), encoding="utf-8")
        return build_summary(self.manifest_path, root=self.root)

    def test_success_is_portable_and_preserves_real_hashes_and_counts(self):
        snapshot = self.export()
        payload = json.dumps(snapshot)
        for private in (str(self.root), self.root.as_posix(), "private_user", "2026-09-13",
                        "2024-01-01", "started_utc", "repository", '"commands"', '"discovery"', '"executable"'):
            self.assertNotIn(private, payload)
        self.assertEqual(snapshot["schema_version"], 2)
        self.assertEqual(snapshot["manifest_sha256"], sha256_file(self.manifest_path))
        self.assertEqual(snapshot["source_hashes"], self.manifest["input_hashes"])
        self.assertEqual(snapshot["python_unit_tests"], 12)
        self.assertEqual(snapshot["result_counts"], {"unit": 5, "rtl": 128, "mutation": 2})
        self.assertEqual(snapshot["rtl_cycles"], 384)
        yosys = snapshot["toolchain"]["checks"][1]
        self.assertEqual(yosys["warnings"], ["Warning: sample. See rtl/core/cpu_core.v:1"])
        self.assertEqual(yosys["netlist"]["sha256"], sha256_file(Path(self.manifest["independent_toolchain"]["checks"][1]["netlist"])))

    def test_rejects_changed_added_and_removed_sources(self):
        original = list(self.inputs)
        for change in ("changed", "added", "removed"):
            with self.subTest(change=change):
                self.inputs = list(original)
                if change == "changed":
                    self.inputs[0].write_text("changed source", encoding="utf-8")
                elif change == "added":
                    self.inputs.append(self.write("scripts/new_input.py", "new input\n"))
                else:
                    self.inputs.pop()
                with self.assertRaisesRegex(ValueError, "source inputs changed"):
                    self.export()
                original[0].write_text("module cpu_core; endmodule\n", encoding="utf-8")

    def test_rejects_failed_partial_incomplete_and_blocked_signoff(self):
        for change in ("failed", "partial", "missing_random", "missing_unit", "blocked", "missing_rehash"):
            with self.subTest(change=change):
                manifest = copy.deepcopy(self.manifest)
                if change == "failed":
                    manifest["results"][0]["status"] = "fail"
                elif change == "partial":
                    manifest["suite"] = "directed"
                elif change == "missing_random":
                    manifest["results"].pop(5)
                elif change == "missing_unit":
                    manifest["results"].pop(0)
                elif change == "blocked":
                    manifest["independent_toolchain"]["checks"][1]["status"] = "BLOCKED_TOOLING"
                else:
                    del manifest["mutation_original_tree_rehash"]
                with self.assertRaises((ValueError, RunnerError)):
                    self.export(manifest)

    def test_rejects_corrupted_synthesis_and_program_artifacts(self):
        yosys = self.manifest["independent_toolchain"]["checks"][1]
        paths = [Path(yosys["script"]), Path(yosys["netlist"])]
        paths += [Path(item["path"]) for item in yosys["artifacts"].values()]
        paths += [self.run / "rtl/program" / name for name in ("program.hex", "data.hex")]
        for path in paths:
            with self.subTest(artifact=path.name):
                contents = path.read_bytes()
                path.write_bytes(contents + b"corruption")
                with self.assertRaisesRegex(ValueError, "artifact hash mismatch"):
                    self.export()
                path.write_bytes(contents)

    def test_rejects_summary_disagreement_even_with_matching_artifact_hash(self):
        yosys = self.manifest["independent_toolchain"]["checks"][1]
        stats = yosys["artifacts"]["statistics.json"]
        path = Path(stats["path"])
        path.write_text('{"design": {"num_cells": 8}}', encoding="utf-8")
        stats["sha256"] = sha256_file(path)
        with self.assertRaisesRegex(ValueError, "summary/statistics disagree"):
            self.export()


if __name__ == "__main__":
    unittest.main()
