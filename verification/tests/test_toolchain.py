import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from verification.toolchain import discover_tool, run_toolchain_checks
from verification.synthesis import CPU_CORE_PORTS


class IndependentToolchainTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "repository with spaces"
        (self.root / "rtl/core").mkdir(parents=True)
        (self.root / "rtl/core/cpu_core.v").write_text("module cpu_core; endmodule\n")
        self.output = self.root / "build/toolchain"

    @staticmethod
    def available(name, **kwargs):
        return {"status": "FOUND", "backend": "native", "executable": name,
                "prefix": [], "searched": [f"PATH:{name}"], "commands": []}

    @staticmethod
    def missing(name, **kwargs):
        return {"status": "BLOCKED_TOOLING", "reason": f"{name} absent",
                "searched": [f"PATH:{name}"], "commands": []}

    def run_report(self, discovery, process):
        with mock.patch("verification.toolchain.discover_tool", side_effect=discovery), \
                mock.patch("verification.toolchain.subprocess.run", side_effect=process):
            report = run_toolchain_checks(self.root, self.output, timeout=3)
        self.assertEqual(report, json.loads((self.output / "report.json").read_text()))
        return report

    def test_missing_tools_are_blocked_and_never_count_as_pass(self):
        process = mock.Mock(side_effect=AssertionError("missing tools must not execute"))
        report = self.run_report(self.missing, process)
        self.assertEqual(report["status"], "BLOCKED_TOOLING")
        self.assertEqual([check["status"] for check in report["checks"]],
                         ["BLOCKED_TOOLING", "BLOCKED_TOOLING"])
        process.assert_not_called()

    def test_lint_warning_failure_is_fatal_and_preserved(self):
        def discovery(name, **kwargs):
            return self.available(name) if name == "verilator" else self.missing(name)

        def process(command, **kwargs):
            if "--version" in command:
                return subprocess.CompletedProcess(command, 0, "Verilator test version", "")
            self.assertIn("--lint-only", command)
            self.assertNotIn("-Wno-fatal", command)
            self.assertFalse(any(arg.startswith("-Wno-") for arg in command))
            return subprocess.CompletedProcess(command, 1, "", "%Warning-WIDTH: bad width")

        report = self.run_report(discovery, process)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["checks"][0]["status"], "FAIL")
        self.assertIn("%Warning-WIDTH", (self.output / "verilator/run.log").read_text())

    def test_timeout_after_successful_version_fails_with_partial_log(self):
        def process(command, **kwargs):
            if "--version" in command or "-V" in command:
                return subprocess.CompletedProcess(command, 0, "test version", "")
            raise subprocess.TimeoutExpired(command, 3, output=b"partial output", stderr=b"stuck")

        report = self.run_report(self.available, process)
        self.assertEqual([check["status"] for check in report["checks"]], ["FAIL", "FAIL"])
        log = (self.output / "yosys/run.log").read_text()
        self.assertIn("TIMEOUT after 3s", log)
        self.assertIn("partial output", log)

    def test_discovered_but_unexecutable_tools_fail_instead_of_blocking(self):
        report = self.run_report(self.available, mock.Mock(side_effect=OSError("invalid executable")))
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual([check["status"] for check in report["checks"]], ["FAIL", "FAIL"])
        self.assertIn("EXECUTION_ERROR", (self.output / "verilator/version.log").read_text())

    def test_zero_exit_without_synthesized_netlist_fails(self):
        def process(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, "test version", "")

        report = self.run_report(self.available, process)
        self.assertEqual(report["checks"][0]["status"], "PASS")
        self.assertEqual(report["checks"][1]["status"], "FAIL")
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("nonempty netlist", report["checks"][1]["reason"])

    def test_both_successful_checks_capture_hashed_sources_and_synthesis(self):
        def process(command, **kwargs):
            if "-s" in command:
                script = Path(command[command.index("-s") + 1]).read_text()
                self.assertEqual(kwargs["cwd"], self.output / "yosys")
                self.assertIn("tee -o statistics.json stat -json", script)
                self.assertIn("hierarchy -check -top cpu_core", script)
                self.assertIn("synth -top cpu_core", script)
                self.assertIn("check -assert", script)
                self.assertIn('"' + self.root.as_posix() + "/rtl/core/cpu_core.v\"", script)
                (self.output / "yosys/netlist.json").write_text(json.dumps(self.valid_netlist()))
                (self.output / "yosys/statistics.json").write_text('{"design":{"num_cells":1}}')
                (self.output / "yosys/synthesized.v").write_text('module cpu_core; endmodule\n')
            return subprocess.CompletedProcess(command, 0, "test version", "")

        report = self.run_report(self.available, process)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(len(report["sources"]["rtl/core/cpu_core.v"]), 64)
        self.assertEqual(len(report["checks"][1]["netlist_sha256"]), 64)
        self.assertEqual(report["checks"][1]["netlist_summary"]["leaf_cells"], 1)
        self.assertIn("netlist_summary.json", report["checks"][1]["artifacts"])

    @staticmethod
    def valid_netlist():
        return {"modules": {"cpu_core": {
            "attributes": {"top": "1"},
            "ports": {name: {"direction": direction, "bits": list(range(width))}
                      for name, (direction, width) in CPU_CORE_PORTS.items()},
            "cells": {"register": {"type": "$_DFF_P_", "parameters": {},
                       "port_directions": {"C": "input", "D": "input", "Q": "output"},
                       "connections": {"C": [2], "D": [3], "Q": [4]}}},
        }}}

    def test_nonempty_but_unrelated_netlist_is_not_a_synthesis_pass(self):
        def process(command, **kwargs):
            if "-s" in command:
                (self.output / "yosys/netlist.json").write_text('{"modules":{"unrelated":{}}}')
            return subprocess.CompletedProcess(command, 0, "test version", "")

        report = self.run_report(self.available, process)
        self.assertEqual(report["checks"][1]["status"], "FAIL")
        self.assertIn("artifact validation failed", report["checks"][1]["reason"])

    def test_synthesis_statistics_must_match_netlist(self):
        def process(command, **kwargs):
            if "-s" in command:
                (self.output / "yosys/netlist.json").write_text(json.dumps(self.valid_netlist()))
                (self.output / "yosys/statistics.json").write_text('{"design":{"num_cells":999}}')
                (self.output / "yosys/synthesized.v").write_text('module cpu_core; endmodule\n')
            return subprocess.CompletedProcess(command, 0, "test version", "")

        report = self.run_report(self.available, process)
        self.assertEqual(report["checks"][1]["status"], "FAIL")
        self.assertIn("cell counts differ", report["checks"][1]["reason"])

    def test_invalid_explicit_executable_is_configuration_failure(self):
        with mock.patch.dict("os.environ", {"CPU_VERIFY_YOSYS": "nonexistent-yosys"}), \
                mock.patch("verification.toolchain.shutil.which", return_value=None):
            report = discover_tool("yosys", root=self.root, output_dir=self.output)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("CPU_VERIFY_YOSYS", report["reason"])

    def test_existing_output_is_rejected_before_stale_netlist_can_be_reused(self):
        (self.output / "yosys").mkdir(parents=True)
        netlist = self.output / "yosys/netlist.json"
        netlist.write_text('{"old_netlist":true}')
        with mock.patch("verification.toolchain.subprocess.run") as process, \
                self.assertRaises(FileExistsError):
            run_toolchain_checks(self.root, self.output)
        process.assert_not_called()
        self.assertEqual(netlist.read_text(), '{"old_netlist":true}')

    def test_wsl_mapping_failure_is_fatal_for_discovered_tool(self):
        def discovery(name, **kwargs):
            result = self.available(name)
            result.update(backend="wsl", executable=f"/usr/bin/{name}", prefix=["wsl.exe", "--exec"])
            return result

        def process(command, **kwargs):
            if "wslpath" in command:
                return subprocess.CompletedProcess(command, 1, "", "bad path")
            return subprocess.CompletedProcess(command, 0, "test version", "")

        report = self.run_report(discovery, process)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(all("path conversion failed" in check["reason"] for check in report["checks"]))


if __name__ == "__main__":
    unittest.main()
