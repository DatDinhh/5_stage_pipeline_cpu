import contextlib
import json
from io import StringIO
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.run_tests import (
    PROFILES,
    ROOT,
    RunnerError,
    VerificationRunner,
    SIGNOFF_MASTER_SEEDS,
    SIGNOFF_RANDOM_PROFILES,
    derived_seed,
    load_plan,
    main,
    parse_args,
    parse_coverage,
    random_program,
    run_process,
    select_tests,
    sha256_file,
    validate_request,
)
from verification.encoding import assemble


class RunnerPureFunctionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tests = load_plan(ROOT / "tests/testplan.json")

    def test_paths_are_anchored_to_script_repository(self):
        self.assertTrue(ROOT.is_absolute())
        self.assertTrue((ROOT / "rtl/core/cpu_core.v").is_file())

    def test_run_directories_are_unique_without_calendar_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests").mkdir()
            (root / "tests/testplan.json").write_text(
                '{"version": 1, "tests": []}\n', encoding="utf-8")
            with mock.patch("scripts.run_tests.ROOT", root), \
                    mock.patch("scripts.run_tests.verification_inputs", return_value=[]), \
                    mock.patch("scripts.run_tests.shutil.which", return_value=None), \
                    mock.patch("scripts.run_tests.time.time", return_value=0):
                runners = [VerificationRunner(parse_args(["--suite", "unit"]), [])
                           for _ in range(2)]
            paths = [runner.session_dir for runner in runners]
            self.assertNotEqual(paths[0], paths[1])
            for path in paths:
                self.assertEqual(path.parent, root / "build/runs")
                self.assertRegex(path.name, r"^run-[0-9a-f]{32}$")
                self.assertTrue(path.is_dir())

    def test_seed_derivation_is_stable_and_stream_separated(self):
        self.assertEqual(derived_seed(123, "ibus"), derived_seed(123, "ibus"))
        self.assertNotEqual(derived_seed(123, "ibus"), derived_seed(123, "dbus"))
        self.assertNotEqual(derived_seed(123, "ibus"), derived_seed(124, "ibus"))

    def test_random_generation_is_deterministic_and_assemblable(self):
        source_a, memory_a = random_program(0x1234)
        source_b, memory_b = random_program(0x1234)
        self.assertEqual((source_a, memory_a), (source_b, memory_b))
        image = assemble(source_a)
        self.assertIn("done", image.labels)

    def test_unknown_and_empty_selection_fail_closed(self):
        with self.assertRaises(RunnerError):
            select_tests(self.tests, ["does_not_exist"], "directed")
        with self.assertRaises(RunnerError):
            select_tests(self.tests, [], "nonexistent-suite")

    def test_all_plan_profiles_are_defined(self):
        for test in self.tests:
            if test.get("kind") == "protocol":
                self.assertTrue(test["sources"])
                self.assertTrue(test["required_bins"])
                continue
            self.assertTrue(test["profiles"])
            self.assertTrue(set(test["profiles"]).issubset(PROFILES))

    def test_list_mode_does_not_require_simulator_or_create_build(self):
        stdout = StringIO()
        with contextlib.redirect_stdout(stdout):
            status = main(["--list"])
        self.assertEqual(status, 0)
        self.assertIn("pipe_compat_original", stdout.getvalue())

    def test_partial_signoff_requests_are_rejected(self):
        with self.assertRaises(RunnerError):
            validate_request(
                parse_args(["--suite", "signoff", "--test", "csr_read"]), self.tests)
        with self.assertRaises(RunnerError):
            validate_request(
                parse_args(["--suite", "signoff", "--seed", "7"]), self.tests)

    def test_random_and_unit_reject_inapplicable_filters(self):
        with self.assertRaises(RunnerError):
            validate_request(
                parse_args(["--suite", "random", "--test", "csr_read"]), self.tests)
        with self.assertRaises(RunnerError):
            validate_request(
                parse_args(["--suite", "unit", "--seed", "1"]), self.tests)

    def test_duplicate_seed_is_a_cli_error(self):
        with contextlib.redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parse_args(["--suite", "random", "--seed", "7", "--seed", "7"])

    def test_timeout_is_fail_closed_and_writes_diagnostic_log(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "timeout.log"
            expired = subprocess.TimeoutExpired(["fake-tool"], timeout=3,
                                                 output="partial", stderr="stuck")
            with mock.patch("scripts.run_tests.subprocess.run", side_effect=expired):
                with self.assertRaisesRegex(RunnerError, "timed out"):
                    run_process(["fake-tool"], cwd=ROOT, timeout=3, log_path=log_path)
            contents = log_path.read_text(encoding="utf-8")
            self.assertIn("TIMEOUT after 3s", contents)
            self.assertIn("partial", contents)

    def test_missing_and_empty_coverage_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coverage.txt"
            with self.assertRaises(RunnerError):
                parse_coverage(path)
            path.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(RunnerError, "empty"):
                parse_coverage(path)


class ProtocolAndIntegrityTests(unittest.TestCase):
    def make_runner(self, directory):
        runner = object.__new__(VerificationRunner)
        runner.args = parse_args(["--suite", "directed"])
        runner.session_dir = Path(directory)
        runner.results = []
        runner.aggregate_coverage = {}
        runner.manifest = {}
        return runner

    def protocol_case(self, *, stdout="CORE_RESET_PASS: complete\n",
                      coverage="reset_pending=1\n", returncode=0):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory)
            test = {"id": "reset", "top": "tb_reset", "sources": [],
                    "pass_sentinel": "CORE_RESET_PASS:",
                    "required_bins": ["reset_pending"]}

            def fake_process(command, **kwargs):
                if "-o" in command:
                    Path(command[command.index("-o") + 1]).write_text("fresh", encoding="utf-8")
                    return subprocess.CompletedProcess(command, 0, "", "")
                if coverage is not None:
                    path = next(arg.split("=", 1)[1] for arg in command
                                if arg.startswith("+COVERAGE="))
                    Path(path).write_text(coverage, encoding="utf-8")
                return subprocess.CompletedProcess(command, returncode, stdout, "")

            with mock.patch.object(runner, "_require_hdl_tools", return_value=("iverilog", "vvp")), \
                    mock.patch("scripts.run_tests.run_process", side_effect=fake_process), \
                    contextlib.redirect_stdout(StringIO()):
                runner.run_protocol(test)
            return runner

    def test_protocol_accepts_complete_evidence_and_aggregates_coverage(self):
        runner = self.protocol_case()
        self.assertEqual(runner.results[0]["status"], "pass")
        self.assertEqual(runner.aggregate_coverage, {"reset_pending": 1})

    def test_protocol_rejects_zero_exit_without_completion(self):
        with self.assertRaisesRegex(RunnerError, "sentinel missing"):
            self.protocol_case(stdout="")

    def test_protocol_rejects_fatal_even_with_pass_marker(self):
        with self.assertRaisesRegex(RunnerError, "simulator exit"):
            self.protocol_case(returncode=1)

    def test_protocol_rejects_missing_zero_and_wrong_coverage(self):
        for coverage in (None, "reset_pending=0\n", "another_bin=4\n", "reset_pending=x\n"):
            with self.subTest(coverage=coverage), self.assertRaises(RunnerError):
                self.protocol_case(coverage=coverage)

    def test_rehash_records_complete_matching_input_set(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.v"
            path.write_text("original", encoding="utf-8")
            runner = self.make_runner(directory)
            with mock.patch("scripts.run_tests.ROOT", Path(directory)), \
                    mock.patch("scripts.run_tests.verification_inputs", return_value=[path]):
                runner.manifest["input_hashes"] = {"source.v": sha256_file(path)}
                runner.enforce_input_hashes()
            self.assertEqual(runner.manifest["source_input_rehash"]["status"], "pass")
            self.assertEqual(runner.manifest["input_hashes"], runner.manifest["final_input_hashes"])

    def test_rehash_rejects_changed_added_and_removed_inputs(self):
        for mutation in ("changed", "added", "removed"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "source.v"
                path.write_text("original", encoding="utf-8")
                runner = self.make_runner(directory)
                runner.manifest["input_hashes"] = {"source.v": sha256_file(path)}
                inputs = [path]
                if mutation == "changed":
                    path.write_text("modified", encoding="utf-8")
                elif mutation == "added":
                    added = Path(directory) / "new.v"
                    added.write_text("new source", encoding="utf-8")
                    inputs.append(added)
                else:
                    inputs = []
                with mock.patch("scripts.run_tests.ROOT", Path(directory)), \
                        mock.patch("scripts.run_tests.verification_inputs", return_value=inputs), \
                        self.assertRaisesRegex(RunnerError, "source inputs changed"):
                    runner.enforce_input_hashes()
                self.assertEqual(runner.manifest["source_input_rehash"]["status"], "fail")
                self.assertTrue(runner.manifest["source_input_rehash"][mutation])

    def test_signoff_requires_protocol_case_exactly_once_and_passing(self):
        runner = self.make_runner("unused")
        runner.tests = [{"id": "reset", "kind": "protocol", "suites": ["signoff"]}]
        records = [{"kind": "rtl", "name": f"random_{seed}__{profile}",
                    "master_seed": seed, "profile": profile, "status": "pass"}
                   for seed in SIGNOFF_MASTER_SEEDS for profile in SIGNOFF_RANDOM_PROFILES]
        records += [{"kind": "mutation", "name": f"mutation_{index}", "status": "pass"}
                    for index in range(2)]
        reset = {"kind": "protocol", "name": "reset", "status": "pass"}
        runner.results = records + [reset]
        runner.enforce_signoff_matrix()
        for suffix in ([], [reset, reset], [dict(reset, status="fail")]):
            runner.results = records + suffix
            with self.subTest(suffix=suffix), self.assertRaises(RunnerError):
                runner.enforce_signoff_matrix()

    def test_signoff_rejects_failed_or_duplicate_random_result(self):
        runner = self.make_runner("unused")
        runner.tests = []
        records = [{"kind": "rtl", "name": f"random_{seed}__{profile}",
                    "master_seed": seed, "profile": profile, "status": "pass"}
                   for seed in SIGNOFF_MASTER_SEEDS for profile in SIGNOFF_RANDOM_PROFILES]
        records += [{"kind": "mutation", "name": f"mutation_{index}", "status": "pass"}
                    for index in range(2)]
        runner.results = records
        runner.enforce_signoff_matrix()
        runner.results = [dict(records[0], status="fail")] + records[1:]
        with self.assertRaisesRegex(RunnerError, "non-passing"):
            runner.enforce_signoff_matrix()
        runner.results = records + [dict(records[0])]
        with self.assertRaisesRegex(RunnerError, "random matrix mismatch"):
            runner.enforce_signoff_matrix()

    def test_protocol_negative_plan_rejects_empty_or_unrelated_evidence(self):
        base = {"id": "reset", "kind": "protocol", "top": "tb_reset",
                "sources": ["rtl/core/cpu_core.v"], "pass_sentinel": "CORE_RESET_PASS:",
                "required_bins": ["reset_pending"]}
        negative = {"id": "missing_phase", "failure_sentinel": "missing reset_pending",
                    "plusargs": ["+OMIT_SCENARIO=0"], "missing_bin": "reset_pending"}
        mutations = [{"failure_sentinel": ""}, {"missing_bin": "unrelated"},
                     {"plusargs": "invalid"}, {"id": ""}]
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                test = dict(base, negative_cases=[dict(negative, **mutation)])
                plan = Path(directory) / "plan.json"
                plan.write_text(json.dumps({"version": 1, "tests": [test]}), encoding="utf-8")
                with self.assertRaisesRegex(RunnerError, "negative evidence contract"):
                    load_plan(plan)

    def test_protocol_plan_cannot_omit_required_evidence(self):
        base = {"id": "reset", "kind": "protocol", "top": "tb_reset",
                "sources": ["rtl/core/cpu_core.v"], "pass_sentinel": "CORE_RESET_PASS:",
                "required_bins": ["reset_pending"]}
        for field in ("top", "sources", "pass_sentinel", "required_bins"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                test = dict(base)
                del test[field]
                plan = Path(directory) / "plan.json"
                plan.write_text(json.dumps({"version": 1, "tests": [test]}), encoding="utf-8")
                with self.assertRaises(RunnerError):
                    load_plan(plan)

    def test_protocol_negative_requires_exact_coverage_failure(self):
        cases = [
            (1, "missing phase first", "first=0\nsecond=1\n", True),
            (0, "missing phase first", "first=0\nsecond=1\n", False),
            (1, "unrelated failure", "first=0\nsecond=1\n", False),
            (1, "missing phase first", "first=0\nsecond=0\n", False),
            (1, "missing phase first", "second=1\n", False),
            (1, "CORE_RESET_PASS: missing phase first", "first=0\nsecond=1\n", False),
        ]
        for exit_code, stdout, coverage, should_pass in cases:
            with self.subTest(case=(exit_code, stdout, coverage)), \
                    tempfile.TemporaryDirectory() as directory:
                runner = self.make_runner(directory)
                test = {"required_bins": ["first", "second"], "pass_sentinel": "CORE_RESET_PASS:"}
                negative = {"id": "missing_phase", "plusargs": ["+OMIT_SCENARIO=0"],
                            "failure_sentinel": "missing phase first", "missing_bin": "first"}

                def fake_process(command, **kwargs):
                    path = next(arg.split("=", 1)[1] for arg in command
                                if arg.startswith("+COVERAGE="))
                    Path(path).write_text(coverage, encoding="utf-8")
                    return subprocess.CompletedProcess(command, exit_code, stdout, "")

                with mock.patch("scripts.run_tests.run_process", side_effect=fake_process), \
                        contextlib.redirect_stdout(StringIO()):
                    if should_pass:
                        runner.run_protocol_negative(test, negative, Path("fresh.vvp"), "vvp")
                        self.assertEqual(runner.results[-1]["status"], "pass")
                    else:
                        with self.assertRaises(RunnerError):
                            runner.run_protocol_negative(test, negative, Path("fresh.vvp"), "vvp")
                        self.assertEqual(runner.results[-1]["status"], "fail")
                self.assertEqual(runner.aggregate_coverage, {})

    def test_toolchain_blocked_stays_explicit_and_failure_fails_runner(self):
        for status in ("BLOCKED_TOOLING", "FAIL"):
            runner = self.make_runner("unused")
            runner.manifest["tools"] = {}
            report = {"status": status, "checks": [
                {"name": "yosys", "kind": "synthesis", "status": status}]}
            with mock.patch("scripts.run_tests.run_toolchain_checks", return_value=report), \
                    contextlib.redirect_stdout(StringIO()):
                if status == "FAIL":
                    with self.assertRaisesRegex(RunnerError, "independent lint/synthesis failed"):
                        runner.run_independent_toolchain()
                else:
                    runner.run_independent_toolchain()
            self.assertEqual(runner.manifest["independent_toolchain"]["status"], status)
            self.assertEqual(runner.results, [])


if __name__ == "__main__":
    unittest.main()
