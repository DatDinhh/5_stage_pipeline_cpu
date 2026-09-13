#!/usr/bin/env python3
"""Reproducible verification runner for the five-stage CPU.

Every RTL result is produced by an explicit fresh Icarus build.  A run passes
only after the simulator, the strict trace parser, the independent ISS, the
full architectural-state comparison, and the requested coverage gates pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Iterable, Mapping
import uuid


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.checker import (  # noqa: E402
    StateMismatch,
    TraceMismatch,
    TraceRecord,
    VerificationError,
    state_text,
    trace_csv,
    verify_run,
)
from verification.encoding import EncodingError, ProgramImage, assemble  # noqa: E402
from verification.iss import ISS, ISSExecutionError  # noqa: E402
from verification.toolchain import run_toolchain_checks  # noqa: E402


MEM_BYTES = 4096
MEM_WORDS = MEM_BYTES // 4
CORE_SOURCES = (
    "rtl/core/alu.v",
    "rtl/core/control_unit.v",
    "rtl/core/reg_file.v",
    "rtl/core/forwarding_unit.v",
    "rtl/core/hazard_unit.v",
    "rtl/core/bpu.v",
    "rtl/core/csr_perf.v",
    "rtl/core/cpu_core.v",
    "sim/mem/rv_rom.v",
    "sim/mem/rv_mem.v",
    "sim/mem/rv_bus_shim.sv",
    "sim/score/scoreboard.v",
    "sim/assertions/assertions.sv",
    "sim/tb/tb_core.sv",
)


def verification_inputs() -> list[Path]:
    """Return every source/configuration input that can affect a result."""

    patterns = (
        "rtl/**/*.v", "sim/**/*.v", "sim/**/*.sv", "verification/**/*.py",
        "scripts/*.py", "scripts/*.ps1", "tests/**/*.asm", "tests/**/*.json",
    )
    paths: set[Path] = set()
    for pattern in patterns:
        paths.update(path for path in ROOT.glob(pattern)
                     if path.is_file() and "__pycache__" not in path.parts)
    return sorted(paths, key=lambda path: path.relative_to(ROOT).as_posix())

PROFILES: dict[str, dict[str, int]] = {
    "fast": {
        "I_RANDOM_WAIT": 0, "I_WAIT_MAX": 0, "I_FIXED_WAIT": 0,
        "D_RANDOM_WAIT": 0, "D_WAIT_MAX": 0, "D_FIXED_WAIT": 0,
    },
    "dslow": {
        "I_RANDOM_WAIT": 0, "I_WAIT_MAX": 0, "I_FIXED_WAIT": 0,
        "D_RANDOM_WAIT": 0, "D_WAIT_MAX": 0, "D_FIXED_WAIT": 5,
    },
    "islow": {
        "I_RANDOM_WAIT": 1, "I_WAIT_MAX": 5, "I_FIXED_WAIT": 0,
        "D_RANDOM_WAIT": 0, "D_WAIT_MAX": 0, "D_FIXED_WAIT": 0,
    },
    "stress": {
        "I_RANDOM_WAIT": 1, "I_WAIT_MAX": 5, "I_FIXED_WAIT": 0,
        "D_RANDOM_WAIT": 1, "D_WAIT_MAX": 9, "D_FIXED_WAIT": 0,
    },
    "adversarial": {
        "I_RANDOM_WAIT": 0, "I_WAIT_MAX": 0, "I_FIXED_WAIT": 0,
        "D_RANDOM_WAIT": 0, "D_WAIT_MAX": 0, "D_FIXED_WAIT": 0,
        "BUS_ADVERSARIAL": 1,
        "I_REQ_STALL_CYCLES": 4, "D_REQ_STALL_CYCLES": 4,
        "I_RESP_HOLD_CYCLES": 2, "D_RESP_HOLD_CYCLES": 12,
    },
}

SIGNOFF_RANDOM_PROFILES = ("fast", "dslow", "islow", "stress")
SIGNOFF_MASTER_SEEDS = tuple(range(32))
AGGREGATE_SIGNOFF_BINS = (
    "fwd_ex_a", "fwd_ex_b", "fwd_double_a", "fwd_double_b",
    "load_hazard", "exmem_wait", "store_retire", "load_retire",
    "branch_taken", "branch_not_taken", "redirect", "fetch_discard",
    "trap", "write_r0", "ibus_resp_hold",
    "ibus_backpressure", "dbus_backpressure",
    "ibus_mem_resp_hold", "dbus_mem_resp_hold",
    "redirect_ibus_blocked",
)


class RunnerError(RuntimeError):
    """Configuration, tool, or verification failure with a concise message."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def derived_seed(master: int, stream: str) -> int:
    """Stable 32-bit seed; independent of Python's randomized hash()."""

    payload = f"cpu5|{master}|{stream}".encode("utf-8")
    value = int.from_bytes(hashlib.sha256(payload).digest()[:4], "big")
    return value or 1


def command_text(command: Iterable[object]) -> str:
    def quote(item: object) -> str:
        value = str(item)
        return f'"{value}"' if any(char.isspace() for char in value) else value
    return " ".join(quote(item) for item in command)


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def run_process(command: list[str], *, cwd: Path, timeout: int,
                log_path: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, check=False, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        if log_path:
            log_path.write_text(
                f"COMMAND: {command_text(command)}\nTIMEOUT after {timeout}s\n"
                f"STDOUT:\n{exc.stdout or ''}\nSTDERR:\n{exc.stderr or ''}\n",
                encoding="utf-8")
        raise RunnerError(f"command timed out after {timeout}s: {command_text(command)}") from exc
    except OSError as exc:
        raise RunnerError(f"cannot execute {command_text(command)}: {exc}") from exc
    if log_path:
        log_path.write_text(
            f"COMMAND: {command_text(command)}\nRETURN_CODE: {result.returncode}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}\n",
            encoding="utf-8")
    return result


def parse_word_map(raw: Mapping[str, str] | None) -> dict[int, int]:
    result: dict[int, int] = {}
    for address, value in (raw or {}).items():
        addr_int, value_int = int(address, 0), int(value, 0)
        if addr_int & 3 or not 0 <= addr_int < MEM_BYTES:
            raise RunnerError(f"initial-memory address {address!r} is invalid")
        if not 0 <= value_int <= 0xFFFF_FFFF:
            raise RunnerError(f"initial-memory value {value!r} is invalid")
        result[addr_int] = value_int
    return result


def load_plan(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerError(f"cannot load test plan {path}: {exc}") from exc
    if data.get("version") != 1 or not isinstance(data.get("tests"), list):
        raise RunnerError("test plan must have version 1 and a tests array")
    seen: set[str] = set()
    negative_ids: set[str] = set()
    for test in data["tests"]:
        test_id = test.get("id")
        if not isinstance(test_id, str) or not test_id or test_id in seen:
            raise RunnerError(f"invalid or duplicate test id {test_id!r}")
        seen.add(test_id)
        bins = test.get("required_bins", [])
        if (not isinstance(bins, list) or
                any(not isinstance(item, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", item)
                    for item in bins) or len(set(bins)) != len(bins)):
            raise RunnerError(f"{test_id}: invalid required coverage bins")
        kind = test.get("kind", "rtl")
        if kind == "protocol":
            if not isinstance(test.get("top"), str) or not test["top"]:
                raise RunnerError(f"{test_id}: protocol top is required")
            sources = test.get("sources")
            if (not isinstance(sources, list) or not sources or
                    any(not isinstance(src, str) or not (ROOT / src).is_file()
                        for src in sources)):
                raise RunnerError(f"{test_id}: protocol source list is missing/invalid")
            if (not isinstance(test.get("pass_sentinel"), str) or
                    not test["pass_sentinel"] or not bins):
                raise RunnerError(f"{test_id}: protocol sentinel and coverage gates are required")
            negatives = test.get("negative_cases", [])
            if not isinstance(negatives, list):
                raise RunnerError(f"{test_id}: protocol negative_cases must be a list")
            for negative in negatives:
                if not isinstance(negative, dict):
                    raise RunnerError(f"{test_id}: invalid protocol negative entry")
                negative_id = negative.get("id")
                sentinel = negative.get("failure_sentinel")
                plusargs = negative.get("plusargs")
                if (not isinstance(negative_id, str) or
                        not re.fullmatch(r"[a-z][a-z0-9_]*", negative_id) or
                        negative_id in negative_ids or not isinstance(sentinel, str) or
                        not sentinel.strip() or not isinstance(plusargs, list) or not plusargs or
                        any(not isinstance(arg, str) or not arg.startswith("+") for arg in plusargs) or
                        negative.get("missing_bin") not in bins):
                    raise RunnerError(f"{test_id}: invalid protocol negative evidence contract")
                negative_ids.add(negative_id)
            continue
        if kind != "rtl":
            raise RunnerError(f"{test_id}: unknown test kind {kind!r}")
        program = ROOT / str(test.get("program", ""))
        if not program.is_file():
            raise RunnerError(f"{test_id}: program does not exist: {program}")
        profiles = test.get("profiles", [])
        if not profiles or any(profile not in PROFILES for profile in profiles):
            raise RunnerError(f"{test_id}: missing or unknown timing profile")
        if not isinstance(test.get("completion"), str):
            raise RunnerError(f"{test_id}: completion label is required")
        parse_word_map(test.get("initial_memory"))
    if negative_ids & seen:
        raise RunnerError("protocol negative ids must differ from positive test ids")
    return data["tests"]


def write_hex_images(run_dir: Path, image: ProgramImage,
                     initial_memory: Mapping[int, int]) -> tuple[Path, Path]:
    # Full-size images avoid simulator-dependent handling of short $readmemh files.
    program_path = run_dir / "program.hex"
    data_path = run_dir / "data.hex"
    program_path.write_text(image.hex_lines(start=0, end=MEM_BYTES), encoding="ascii")
    memory_words = [0] * MEM_WORDS
    for address, value in initial_memory.items():
        memory_words[address // 4] = value & 0xFFFF_FFFF
    data_path.write_text("".join(f"{word:08x}\n" for word in memory_words), encoding="ascii")
    return program_path, data_path


def expected_until_completion(image: ProgramImage, completion_pc: int,
                              initial_memory: Mapping[int, int], max_events: int = 2000) -> int:
    model = ISS(image, entry=image.entry, initial_memory=initial_memory)
    for count in range(1, max_events + 1):
        event = model.step()
        if event.pc == completion_pc:
            return count
    raise RunnerError(
        f"ISS did not retire completion PC 0x{completion_pc:08x} in {max_events} events")


def parse_coverage(path: Path) -> dict[str, int]:
    if not path.is_file():
        raise RunnerError(f"coverage artifact is missing: {path}")
    result: dict[str, int] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        match = re.fullmatch(r"([a-z][a-z0-9_]*)=(\d+)", raw.strip())
        if not match or match.group(1) in result:
            raise RunnerError(f"malformed coverage line {line_number}: {raw!r}")
        result[match.group(1)] = int(match.group(2))
    if not result:
        raise RunnerError("coverage artifact is empty")
    return result


def random_program(master_seed: int) -> tuple[str, dict[int, int]]:
    rng = random.Random(derived_seed(master_seed, "program"))
    lines = [
        "# Deterministic constrained-random program with directed coverage spine.",
        "addi r20, r0, 0x100",
        "addi r1, r0, 1",
        "addi r1, r1, 2",
        "add r2, r1, r1",
        "sub r3, r2, r1",
        "sw r2, 0(r20)",
        "lw r4, 0(r20)",
        "add r5, r4, r2",
        "beq r5, r5, spine_taken",
        "sw r1, 4(r20)",
        "spine_taken:",
        "bne r5, r0, spine_after_bne",
        "addi r6, r0, 0x55",
        "spine_after_bne:",
        "addi r15, r0, 3",
        "spine_loop:",
        "addi r15, r15, -1",
        "bne r15, r0, spine_loop",
    ]
    registers = list(range(1, 15))
    binary_ops = ("add", "addu", "sub", "subu", "and", "or", "xor", "nor", "slt")
    imm_ops = ("addi", "andi", "ori", "xori")
    load_ops = (("lw", 4), ("lb", 1), ("lbu", 1), ("lh", 2), ("lhu", 2))
    store_ops = (("sw", 4), ("sb", 1), ("sh", 2))
    for _ in range(48):
        choice = rng.randrange(100)
        rd, rs, rt = rng.choice(registers), rng.choice(registers), rng.choice(registers)
        if choice < 32:
            lines.append(f"{rng.choice(binary_ops)} r{rd}, r{rs}, r{rt}")
        elif choice < 53:
            op = rng.choice(imm_ops)
            immediate = rng.randrange(-128, 128) if op == "addi" else rng.randrange(0, 0x10000)
            lines.append(f"{op} r{rd}, r{rs}, {immediate}")
        elif choice < 64:
            lines.append(f"{rng.choice(('sll', 'srl', 'sra'))} r{rd}, r{rt}, {rng.randrange(32)}")
        elif choice < 70:
            lines.append(f"lui r{rd}, {rng.randrange(0x10000)}")
        elif choice < 86:
            op, size = rng.choice(load_ops)
            offset = rng.randrange(0, 64)
            offset -= offset % size
            lines.append(f"{op} r{rd}, {offset}(r20)")
        else:
            op, size = rng.choice(store_ops)
            offset = rng.randrange(0, 64)
            offset -= offset % size
            lines.append(f"{op} r{rt}, {offset}(r20)")
    lines.extend(("sw r5, 60(r20)", "lw r0, 60(r20)", "done:", "nop"))
    initial_memory = {
        0x100 + 4 * index: rng.getrandbits(32) for index in range(16)
    }
    return "\n".join(lines) + "\n", initial_memory


class VerificationRunner:
    def __init__(self, args: argparse.Namespace, tests: list[dict[str, Any]]) -> None:
        self.args = args
        self.tests = tests
        self.session_dir = ROOT / "build" / "runs" / f"run-{uuid.uuid4().hex}"
        self.session_dir.mkdir(parents=True, exist_ok=False)
        self.iverilog = shutil.which(args.sim)
        self.vvp = shutil.which(args.vvp) if args.vvp else None
        if not args.vvp and self.iverilog and not self.vvp:
            compiler_path = Path(self.iverilog)
            sibling_names = ("vvp.exe", "vvp") if os.name == "nt" else ("vvp", "vvp.exe")
            self.vvp = next((str(compiler_path.with_name(name)) for name in sibling_names
                             if compiler_path.with_name(name).is_file()), None)
        if not args.vvp and not self.vvp:
            self.vvp = shutil.which("vvp")
        self.compiled: dict[tuple[str, str], Path] = {}
        self.results: list[dict[str, Any]] = []
        self.aggregate_coverage: dict[str, int] = {}
        self.started = time.time()
        input_paths = verification_inputs()
        self.manifest: dict[str, Any] = {
            "schema_version": 1,
            "status": "running",
            "suite": args.suite,
            "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "repository": str(ROOT),
            "git_commit": self._git_commit(),
            "python": sys.version,
            "tools": {
                "iverilog": (self._tool_version([self.iverilog, "-V"])
                              if self.iverilog else "UNAVAILABLE"),
                "vvp": (self._tool_version([self.vvp, "-V"])
                        if self.vvp else "UNAVAILABLE"),
            },
            "input_hashes": {rel(path): sha256_file(path) for path in input_paths},
            "testplan_sha256": sha256_file(ROOT / "tests/testplan.json"),
            "profiles": PROFILES,
            "results": self.results,
        }

    def _git_commit(self) -> str | None:
        git = shutil.which("git")
        if not git:
            return None
        result = run_process([git, "rev-parse", "HEAD"], cwd=ROOT, timeout=10)
        return result.stdout.strip() if result.returncode == 0 else None

    def _require_hdl_tools(self) -> tuple[str, str]:
        missing = []
        if not self.iverilog:
            missing.append(f"compiler {self.args.sim!r}")
        if not self.vvp:
            missing.append(f"runtime {self.args.vvp or 'vvp'!r}")
        if missing:
            raise RunnerError("BLOCKED_TOOLING: unavailable " + ", ".join(missing))
        return self.iverilog, self.vvp

    @staticmethod
    def _tool_version(command: list[str]) -> str:
        result = run_process(command, cwd=ROOT, timeout=20)
        text = (result.stdout + "\n" + result.stderr).strip()
        return "\n".join(text.splitlines()[:12])

    def _record(self, *, name: str, kind: str, status: str,
                duration: float, **details: Any) -> None:
        record = {"name": name, "kind": kind, "status": status,
                  "duration_seconds": round(duration, 3)}
        record.update(details)
        self.results.append(record)
        marker = "PASS" if status == "pass" else "FAIL"
        print(f"[{marker}] {name} ({duration:.2f}s)", flush=True)

    def finalize(self, status: str, error: str | None = None) -> Path:
        self.manifest["status"] = status
        self.manifest["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.manifest["duration_seconds"] = round(time.time() - self.started, 3)
        self.manifest["aggregate_coverage"] = self.aggregate_coverage
        if error:
            self.manifest["error"] = error
        manifest_path = self.session_dir / "manifest.json"
        manifest_path.write_text(json.dumps(self.manifest, indent=2, sort_keys=True) + "\n",
                                 encoding="utf-8")
        return manifest_path

    def enforce_input_hashes(self) -> None:
        """Re-read the complete input set; additions/deletions also invalidate a run."""
        before = self.manifest["input_hashes"]
        after = {rel(path): sha256_file(path) for path in verification_inputs()}
        added = sorted(after.keys() - before.keys())
        removed = sorted(before.keys() - after.keys())
        changed = sorted(name for name in before.keys() & after.keys()
                         if before[name] != after[name])
        self.manifest["final_input_hashes"] = after
        self.manifest["source_input_rehash"] = {
            "status": "fail" if added or removed or changed else "pass",
            "checked_inputs": len(after), "added": added,
            "removed": removed, "changed": changed,
        }
        if added or removed or changed:
            raise RunnerError(f"source inputs changed during verification: "
                              f"added={added}, removed={removed}, changed={changed}")

    def run_independent_toolchain(self) -> None:
        report = run_toolchain_checks(ROOT, self.session_dir / "toolchain")
        self.manifest["independent_toolchain"] = report
        for check in report["checks"]:
            self.manifest["tools"][check["name"]] = check.get("version", check["status"])
            print(f"[{check['status']}] {check['name']} {check['kind']}", flush=True)
        if report["status"] == "FAIL":
            raise RunnerError("independent lint/synthesis failed; inspect toolchain/report.json")

    def _compile(self, profile: str, *, ibus_seed: int, dbus_seed: int,
                 source_root: Path = ROOT, tag: str = "base") -> Path:
        iverilog, _ = self._require_hdl_tools()
        key = (profile, f"{ibus_seed:08x}-{dbus_seed:08x}-{source_root.resolve()}")
        if key in self.compiled:
            return self.compiled[key]
        compile_dir = (self.session_dir / "compiled" / tag / profile /
                       f"i-{ibus_seed:08x}_d-{dbus_seed:08x}")
        compile_dir.mkdir(parents=True, exist_ok=True)
        output = compile_dir / "tb_core.vvp"
        parameters = dict(PROFILES[profile])
        parameters["I_WAIT_SEED"] = ibus_seed
        parameters["D_WAIT_SEED"] = dbus_seed
        command = [iverilog, "-g2012", "-Wall", "-s", "tb_core", "-o", str(output)]
        command.extend(f"-Ptb_core.{name}={value}" for name, value in parameters.items())
        command.extend(str(source_root / source) for source in CORE_SOURCES)
        result = run_process(command, cwd=ROOT, timeout=120, log_path=compile_dir / "compile.log")
        warnings = [line for line in (result.stdout + result.stderr).splitlines()
                    if re.search(r"\bwarning\b", line, re.IGNORECASE)]
        if result.returncode != 0 or warnings or not output.is_file():
            reason = f"return code {result.returncode}"
            if warnings:
                reason += f", warnings: {' | '.join(warnings[:5])}"
            raise RunnerError(f"fresh compile failed for {profile}/{tag}: {reason}")
        self.compiled[key] = output
        return output

    def run_unit(self) -> None:
        start = time.time()
        unit_dir = self.session_dir / "unit" / "python"
        unit_dir.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "unittest", "discover", "-s",
                   "verification/tests", "-t", ".", "-v"]
        result = run_process(command, cwd=ROOT, timeout=120, log_path=unit_dir / "run.log")
        if result.returncode != 0:
            self._record(name="python_unit", kind="unit", status="fail",
                         duration=time.time() - start, command=command_text(command))
            raise RunnerError("Python unit/negative checker tests failed")
        self._record(name="python_unit", kind="unit", status="pass",
                     duration=time.time() - start, command=command_text(command))

        iverilog, vvp = self._require_hdl_tools()
        units = (
            ("forwarding_unit", "tb_forwarding", ("rtl/core/forwarding_unit.v", "sim/tb/tb_forwarding.sv"), False,
             "PASS tb_forwarding:"),
            ("hazard_unit", "tb_hazard", ("rtl/core/hazard_unit.v", "sim/tb/tb_hazard.sv"), False,
             "PASS tb_hazard:"),
            ("memory_model", "tb_mem_model", ("sim/mem/rv_mem.v", "sim/mem/rv_rom.v", "sim/tb/tb_mem_model.sv"), False,
             "TB_MEM_MODEL PASS:"),
            ("memory_range_guard", "tb_mem_model", ("sim/mem/rv_mem.v", "sim/mem/rv_rom.v", "sim/tb/tb_mem_model.sv"), True,
             None),
        )
        compiled_units: dict[str, Path] = {}
        for name, top, sources, expect_negative, pass_sentinel in units:
            start = time.time()
            unit_dir = self.session_dir / "unit" / name
            unit_dir.mkdir(parents=True, exist_ok=True)
            if top not in compiled_units:
                output = unit_dir / f"{top}.vvp"
                command = [iverilog, "-g2012", "-Wall", "-s", top,
                           "-o", str(output), *(str(ROOT / src) for src in sources)]
                compile_result = run_process(
                    command, cwd=ROOT, timeout=120, log_path=unit_dir / "compile.log")
                warning = re.search(r"\bwarning\b", compile_result.stdout + compile_result.stderr,
                                    re.IGNORECASE)
                if compile_result.returncode != 0 or warning:
                    self._record(name=name, kind="unit", status="fail",
                                 duration=time.time() - start, phase="compile")
                    raise RunnerError(f"unit compile failed or warned: {name}")
                compiled_units[top] = output
            output = compiled_units[top]
            command = [vvp, str(output)] + (["+OUT_OF_RANGE"] if expect_negative else [])
            sim_result = run_process(command, cwd=ROOT, timeout=120,
                                     log_path=unit_dir / "run.log")
            if expect_negative:
                passed = (sim_result.returncode != 0 and
                          "request address out of range" in
                          (sim_result.stdout + sim_result.stderr))
            else:
                passed = (sim_result.returncode == 0 and pass_sentinel is not None and
                          any(line.startswith(pass_sentinel)
                              for line in sim_result.stdout.splitlines()))
            self._record(name=name, kind="unit", status="pass" if passed else "fail",
                         duration=time.time() - start,
                         expected_failure=expect_negative, command=command_text(command))
            if not passed:
                raise RunnerError(f"unit simulation failed: {name}")

    def _run_image(self, *, name: str, image: ProgramImage, completion_label: str,
                   initial_memory: Mapping[int, int], profile: str, master_seed: int,
                   required_bins: Iterable[str], source_text: str,
                   source_root: Path = ROOT,
                   compile_tag: str = "base", expect_failure: bool = False) -> dict[str, Any]:
        start = time.time()
        run_name = f"{name}__{profile}__seed-{master_seed}"
        run_dir = self.session_dir / ("mutations" if expect_failure else "rtl") / run_name
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "source.asm").write_text(source_text, encoding="utf-8")
        if completion_label not in image.labels:
            raise RunnerError(f"{name}: completion label {completion_label!r} not found")
        completion_pc = image.labels[completion_label]
        expected_events = expected_until_completion(image, completion_pc, initial_memory)
        golden_model = ISS(image, entry=image.entry, initial_memory=initial_memory)
        golden_events = golden_model.run(expected_events)
        golden_records = [
            TraceRecord.from_event(event, cycle=index + 1, uid=event.uid)
            for index, event in enumerate(golden_events)
        ]
        (run_dir / "expected_trace.csv").write_text(
            trace_csv(golden_records), encoding="utf-8")
        (run_dir / "expected_state.txt").write_text(
            state_text(golden_model, cycle_count=0), encoding="utf-8")
        program_path, data_path = write_hex_images(run_dir, image, initial_memory)
        trace_path = run_dir / "trace.csv"
        state_path = run_dir / "state.txt"
        coverage_path = run_dir / "coverage.txt"
        i_seed = derived_seed(master_seed, f"{profile}|ibus")
        d_seed = derived_seed(master_seed, f"{profile}|dbus")
        _, vvp = self._require_hdl_tools()
        simv = self._compile(
            profile, ibus_seed=i_seed, dbus_seed=d_seed,
            source_root=source_root, tag=compile_tag)
        command = [
            vvp, str(simv),
            f"+PROGRAM={program_path.as_posix()}", f"+DATA={data_path.as_posix()}",
            f"+TRACE={trace_path.as_posix()}", f"+STATE={state_path.as_posix()}",
            f"+COVERAGE={coverage_path.as_posix()}",
            f"+COMPLETION_PC={completion_pc:08x}", f"+EXPECTED_EVENTS={expected_events}",
            f"+MAX_CYCLES={self.args.max_cycles}",
        ]
        if self.args.waves:
            command.append(f"+WAVE={(run_dir / 'waves.vcd').as_posix()}")
        sim_result = run_process(command, cwd=ROOT, timeout=self.args.timeout,
                                 log_path=run_dir / "sim.log")
        failure_reason: str | None = None
        failure_category: str | None = None
        coverage: dict[str, int] = {}
        cycles: int | None = None
        if sim_result.returncode != 0:
            failure_reason = f"simulator exit {sim_result.returncode}"
            failure_category = "SIMULATOR"
        else:
            try:
                verify_result = verify_run(
                    ISS(image, entry=image.entry, initial_memory=initial_memory),
                    trace_path, state_path, expected_event_count=expected_events,
                    completion_pc=completion_pc, require_full_ram=True)
                cycles = verify_result.cycle_count
                coverage = parse_coverage(coverage_path)
                missing = [item for item in required_bins if coverage.get(item, 0) <= 0]
                if missing:
                    failure_reason = f"required coverage bins are zero/missing: {', '.join(missing)}"
                    failure_category = "COVERAGE"
            except VerificationError as exc:
                failure_reason = str(exc)
                failure_category = exc.category
            except (OSError, RunnerError, ISSExecutionError) as exc:
                failure_reason = str(exc)
                failure_category = "INFRASTRUCTURE"

        if expect_failure:
            passed = (sim_result.returncode == 0 and
                      failure_category in {"TRACE_MISMATCH", "STATE_MISMATCH"})
            status = "pass" if passed else "fail"
            self._record(
                name=run_name, kind="mutation", status=status,
                duration=time.time() - start, expected_failure=True,
                observed_failure=failure_reason, failure_category=failure_category,
                expected_events=expected_events,
                completion_pc=f"0x{completion_pc:08x}", command=command_text(command),
                artifacts=rel(run_dir))
            if not passed:
                raise RunnerError(f"mutation survived targeted test: {name}")
            return {"coverage": coverage, "failure": failure_reason}

        passed = failure_reason is None
        self._record(
            name=run_name, kind="rtl", status="pass" if passed else "fail",
            duration=time.time() - start, profile=profile, master_seed=master_seed,
            ibus_seed=i_seed, dbus_seed=d_seed, expected_events=expected_events,
            cycles=cycles, completion_pc=f"0x{completion_pc:08x}",
            required_bins=list(required_bins), coverage=coverage,
            program_sha256=sha256_file(program_path), data_sha256=sha256_file(data_path),
            command=command_text(command), artifacts=rel(run_dir), error=failure_reason)
        if not passed:
            raise RunnerError(f"{run_name}: {failure_reason}")
        for bin_name, count in coverage.items():
            self.aggregate_coverage[bin_name] = self.aggregate_coverage.get(bin_name, 0) + count
        return {"coverage": coverage, "cycles": cycles}

    def run_protocol(self, test: dict[str, Any]) -> None:
        """Full-core temporal checks use explicit HDL expectations across reset epochs."""
        start = time.time()
        run_dir = self.session_dir / "protocol" / test["id"]
        run_dir.mkdir(parents=True, exist_ok=False)
        iverilog, vvp = self._require_hdl_tools()
        output = run_dir / f"{test['top']}.vvp"
        command = [iverilog, "-g2012", "-Wall", "-s", test["top"],
                   "-o", str(output), *(str(ROOT / src) for src in test["sources"])]
        coverage: dict[str, int] = {}
        try:
            compiled = run_process(command, cwd=ROOT, timeout=120,
                                   log_path=run_dir / "compile.log")
            if (compiled.returncode != 0 or not output.is_file() or
                    re.search(r"\bwarning\b", compiled.stdout + compiled.stderr, re.IGNORECASE)):
                raise RunnerError(f"protocol compile failed or warned: {test['id']}")
            coverage_path = run_dir / "coverage.txt"
            command = [vvp, str(output), f"+COVERAGE={coverage_path.as_posix()}"]
            simulated = run_process(command, cwd=ROOT, timeout=self.args.timeout,
                                    log_path=run_dir / "sim.log")
            if simulated.returncode != 0:
                raise RunnerError(f"protocol simulator exit {simulated.returncode}: {test['id']}")
            if not any(line.startswith(test["pass_sentinel"])
                       for line in simulated.stdout.splitlines()):
                raise RunnerError(f"protocol completion sentinel missing: {test['id']}")
            coverage = parse_coverage(coverage_path)
            missing = [name for name in test["required_bins"] if coverage.get(name, 0) <= 0]
            if missing:
                raise RunnerError("protocol required coverage bins are zero/missing: " +
                                  ", ".join(missing))
        except (RunnerError, OSError) as exc:
            self._record(name=test["id"], kind="protocol", status="fail",
                         duration=time.time() - start, error=str(exc),
                         command=command_text(command), artifacts=rel(run_dir),
                         required_bins=test["required_bins"], coverage=coverage)
            raise
        self._record(name=test["id"], kind="protocol", status="pass",
                     duration=time.time() - start, command=command_text(command),
                     artifacts=rel(run_dir), required_bins=test["required_bins"],
                     coverage=coverage)
        for name, count in coverage.items():
            self.aggregate_coverage[name] = self.aggregate_coverage.get(name, 0) + count
        for negative in test.get("negative_cases", []):
            self.run_protocol_negative(test, negative, output, vvp)

    def run_protocol_negative(self, test: dict[str, Any], negative: dict[str, Any],
                              simv: Path, vvp: str) -> None:
        start = time.time()
        run_dir = self.session_dir / "protocol_negative" / negative["id"]
        run_dir.mkdir(parents=True, exist_ok=False)
        coverage_path = run_dir / "coverage.txt"
        command = [vvp, str(simv), f"+COVERAGE={coverage_path.as_posix()}",
                   *negative["plusargs"]]
        coverage: dict[str, int] = {}
        try:
            result = run_process(command, cwd=ROOT, timeout=self.args.timeout,
                                 log_path=run_dir / "sim.log")
            coverage = parse_coverage(coverage_path)
            missing_bin = negative["missing_bin"]
            passed = (result.returncode != 0 and
                      negative["failure_sentinel"] in result.stdout and
                      test["pass_sentinel"] not in result.stdout and
                      coverage.get(missing_bin, -1) == 0 and
                      all(coverage.get(name, 0) > 0 for name in test["required_bins"]
                          if name != missing_bin))
            if not passed:
                raise RunnerError("protocol negative did not fail solely for the omitted scenario")
        except (RunnerError, OSError) as exc:
            self._record(name=negative["id"], kind="protocol_negative", status="fail",
                         duration=time.time() - start, error=str(exc),
                         command=command_text(command), artifacts=rel(run_dir))
            raise
        self._record(name=negative["id"], kind="protocol_negative", status="pass",
                     duration=time.time() - start, expected_failure=True,
                     failure_category="COVERAGE", missing_bin=missing_bin,
                     coverage=coverage, command=command_text(command), artifacts=rel(run_dir))

    def run_directed(self, selected: list[dict[str, Any]]) -> None:
        seeds = self.args.seed or (0,)
        for test in selected:
            if test.get("kind") == "protocol":
                self.run_protocol(test)
                continue
            source = (ROOT / test["program"]).read_text(encoding="utf-8")
            try:
                image = assemble(source)
            except EncodingError as exc:
                raise RunnerError(f"{test['id']}: assembly failed: {exc}") from exc
            initial_memory = parse_word_map(test.get("initial_memory"))
            for profile in test["profiles"]:
                for master_seed in seeds:
                    self._run_image(
                        name=test["id"], image=image,
                        completion_label=test["completion"], initial_memory=initial_memory,
                        profile=profile, master_seed=master_seed,
                        required_bins=test.get("required_bins", ()), source_text=source)

    def run_random(self, seeds: Iterable[int]) -> None:
        for master_seed in seeds:
            source, initial_memory = random_program(master_seed)
            try:
                image = assemble(source)
            except EncodingError as exc:
                raise RunnerError(f"random seed {master_seed}: assembly failed: {exc}") from exc
            for profile in SIGNOFF_RANDOM_PROFILES:
                self._run_image(
                    name=f"random_{master_seed:08x}", image=image,
                    completion_label="done", initial_memory=initial_memory,
                    profile=profile, master_seed=master_seed, required_bins=(),
                    source_text=source)

    def _mutated_source(self, mutation: str, relative_file: str,
                        old: str, new: str) -> Path:
        source_root = self.session_dir / "mutation_sources" / mutation
        for source in CORE_SOURCES:
            destination = source_root / source
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / source, destination)
        target = source_root / relative_file
        text = target.read_text(encoding="utf-8")
        count = text.count(old)
        if count != 1:
            raise RunnerError(
                f"mutation {mutation}: patch anchor count is {count}, expected exactly one")
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        if sha256_file(target) == sha256_file(ROOT / relative_file):
            raise RunnerError(f"mutation {mutation}: source hash did not change")
        (source_root / "mutation.json").write_text(json.dumps({
            "name": mutation,
            "file": relative_file,
            "original_sha256": sha256_file(ROOT / relative_file),
            "mutated_sha256": sha256_file(target),
            "replacement_count": count,
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return source_root

    def run_mutations(self) -> None:
        by_id = {test["id"]: test for test in self.tests}
        original_hashes = {source: sha256_file(ROOT / source) for source in CORE_SOURCES}
        mutations = (
            (
                "wb_over_ex_forwarding", "rtl/core/forwarding_unit.v",
                "if (ex_match_a) begin\n            if (EX_MEM_resultReady) forwardA = 2'b10;\n            else                    blockedA = 1'b1;\n        end else if (wb_match_a) begin\n            forwardA = 2'b01;\n        end",
                "if (wb_match_a) begin\n            forwardA = 2'b01;\n        end else if (ex_match_a) begin\n            if (EX_MEM_resultReady) forwardA = 2'b10;\n            else                    blockedA = 1'b1;\n        end",
                "raw_forwarding_priority",
            ),
            (
                "wrong_byte_lane", "rtl/core/cpu_core.v",
                "2'd2: byte_value = raw[23:16];",
                "2'd2: byte_value = raw[15:8];",
                "memory_lanes",
            ),
        )
        for mutation, filename, old, new, test_id in mutations:
            source_root = self._mutated_source(mutation, filename, old, new)
            test = by_id[test_id]
            image = assemble((ROOT / test["program"]).read_text(encoding="utf-8"))
            self._run_image(
                name=mutation, image=image, completion_label=test["completion"],
                initial_memory=parse_word_map(test.get("initial_memory")),
                profile="fast", master_seed=0, required_bins=(),
                source_text=(ROOT / test["program"]).read_text(encoding="utf-8"),
                source_root=source_root, compile_tag=mutation, expect_failure=True)
        changed = [source for source, digest in original_hashes.items()
                   if sha256_file(ROOT / source) != digest]
        if changed:
            raise RunnerError(
                "mutation isolation failed; original source changed: " + ", ".join(changed))
        self.manifest["mutation_original_tree_rehash"] = "pass"

    def enforce_signoff_coverage(self) -> None:
        missing = [name for name in AGGREGATE_SIGNOFF_BINS
                   if self.aggregate_coverage.get(name, 0) <= 0]
        if missing:
            raise RunnerError(
                "aggregate signoff coverage bins are zero/missing: " + ", ".join(missing))

    def enforce_signoff_matrix(self) -> None:
        if any(record["status"] != "pass" for record in self.results):
            raise RunnerError("signoff matrix contains non-passing results")
        expected = {(seed, profile) for seed in SIGNOFF_MASTER_SEEDS
                    for profile in SIGNOFF_RANDOM_PROFILES}
        random_records = [(record.get("master_seed"), record.get("profile"))
                          for record in self.results if record["kind"] == "rtl" and
                          record["name"].startswith("random_")]
        observed = set(random_records)
        if observed != expected or len(random_records) != len(expected):
            missing = sorted(expected - observed)
            extra = sorted(observed - expected, key=str)
            raise RunnerError(
                f"signoff random matrix mismatch: count={len(random_records)}, "
                f"missing={missing[:8]}, extra={extra[:8]}")
        mutation_count = sum(record["kind"] == "mutation" and record["status"] == "pass"
                             for record in self.results)
        if mutation_count != 2:
            raise RunnerError(f"signoff requires exactly 2 killed mutations, got {mutation_count}")
        expected_directed = []
        for test in self.tests:
            if "signoff" not in test.get("suites", []):
                continue
            if test.get("kind") == "protocol":
                expected_directed.append(("protocol", test["id"]))
            else:
                expected_directed.extend(("rtl", f"{test['id']}__{profile}__seed-0")
                                         for profile in test["profiles"])
        observed_directed = [(record["kind"], record["name"]) for record in self.results
                             if record["kind"] in ("rtl", "protocol") and
                             not record["name"].startswith("random_") and
                             record["status"] == "pass"]
        if sorted(observed_directed) != sorted(expected_directed):
            raise RunnerError("signoff directed/protocol matrix is incomplete or duplicated")
        expected_negatives = sorted(negative["id"] for test in self.tests
                                    if "signoff" in test.get("suites", [])
                                    for negative in test.get("negative_cases", []))
        observed_negatives = sorted(record["name"] for record in self.results
                                    if record["kind"] == "protocol_negative" and
                                    record["status"] == "pass")
        if observed_negatives != expected_negatives:
            raise RunnerError("signoff protocol negative matrix is incomplete or duplicated")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("unit", "directed", "random", "signoff"),
                        default="directed")
    parser.add_argument("--test", action="append", default=[],
                        help="exact test-plan id; repeat to select several")
    parser.add_argument("--seed", action="append", type=lambda value: int(value, 0), default=[],
                        help="master seed; repeat for several (random default: 0)")
    parser.add_argument("--sim", default="iverilog", help="Icarus compiler executable")
    parser.add_argument("--vvp", default=None,
                        help="matching Icarus runtime (default: compiler sibling, then PATH)")
    parser.add_argument("--waves", action="store_true", help="write one VCD per RTL run")
    parser.add_argument("--max-cycles", type=int, default=4000)
    parser.add_argument("--timeout", type=int, default=120,
                        help="wall-clock timeout in seconds for each simulation")
    parser.add_argument("--list", action="store_true", help="list tests/profiles and exit")
    args = parser.parse_args(argv)
    if args.max_cycles <= 0 or args.timeout <= 0:
        parser.error("--max-cycles and --timeout must be positive")
    if any(seed < 0 or seed > 0xFFFF_FFFF for seed in args.seed):
        parser.error("every --seed must be a 32-bit unsigned integer")
    if len(set(args.seed)) != len(args.seed):
        parser.error("duplicate --seed values are not allowed")
    return args


def select_tests(tests: list[dict[str, Any]], ids: list[str], suite: str) -> list[dict[str, Any]]:
    known = {test["id"] for test in tests}
    unknown = sorted(set(ids) - known)
    if unknown:
        raise RunnerError("unknown test id(s): " + ", ".join(unknown))
    selected = [test for test in tests
                if (not ids or test["id"] in ids) and suite in test.get("suites", [])]
    if not selected:
        raise RunnerError(f"selection is empty for suite {suite!r}")
    return selected


def validate_request(args: argparse.Namespace, tests: list[dict[str, Any]]) -> None:
    known = {test["id"] for test in tests}
    unknown = sorted(set(args.test) - known)
    if unknown:
        raise RunnerError("unknown test id(s): " + ", ".join(unknown))
    if args.suite == "signoff" and (args.test or args.seed):
        raise RunnerError(
            "full signoff has a fixed directed set and 32-seed matrix; "
            "--test/--seed are not allowed")
    if args.suite == "unit" and (args.test or args.seed):
        raise RunnerError("unit suite does not accept --test or --seed")
    if args.suite == "random" and args.test:
        raise RunnerError("random suite does not accept --test")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        tests = load_plan(ROOT / "tests/testplan.json")
        validate_request(args, tests)
        if args.list:
            print("Profiles: " + ", ".join(PROFILES))
            for test in tests:
                print(f"{test['id']}: kind={test.get('kind', 'rtl')} "
                      f"profiles={','.join(test.get('profiles', []))} "
                      f"suites={','.join(test['suites'])}")
            return 0
        runner = VerificationRunner(args, tests)
        try:
            if args.suite in ("unit", "signoff"):
                runner.run_unit()
            if args.suite in ("directed", "signoff"):
                selected = select_tests(tests, args.test, "signoff" if args.suite == "signoff" else "directed")
                runner.run_directed(selected)
            if args.suite in ("random", "signoff") and not args.test:
                seeds = args.seed or (SIGNOFF_MASTER_SEEDS if args.suite == "signoff" else (0,))
                runner.run_random(seeds)
            if args.suite == "signoff" and not args.test:
                runner.run_mutations()
                runner.enforce_signoff_coverage()
                runner.enforce_signoff_matrix()
                runner.run_independent_toolchain()
            runner.enforce_input_hashes()
            manifest = runner.finalize("pass")
            label = "SIGNOFF PASS" if args.suite == "signoff" else f"{args.suite.upper()} PASS"
            print(f"{label}: manifest={manifest}", flush=True)
            return 0
        except Exception as exc:
            manifest = runner.finalize("fail", str(exc))
            print(f"VERIFICATION FAIL: {exc}", file=sys.stderr, flush=True)
            print(f"FAILED MANIFEST: {manifest}", file=sys.stderr, flush=True)
            return 1
    except (RunnerError, OSError, ValueError) as exc:
        print(f"CONFIGURATION FAIL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
