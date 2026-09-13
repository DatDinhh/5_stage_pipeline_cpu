# Five-Stage MIPS-Like CPU

This repository contains a 32-bit, single-issue, in-order IF/ID/EX/MEM/WB CPU and a reproducible verification environment. The design implements the explicitly documented MIPS-like subset; it is not a full MIPS implementation and has no architectural delay slot.

Validation status: **PASS** for the declared signoff suite, with **Verilator lint PASS and Yosys synthesis PASS**. The suite covers unit tests, directed and constrained-random RTL execution, nine full-core warm-reset scenarios, an expected missing-phase rejection, and two functionally detected RTL mutations. The checked-in [signoff evidence snapshot](docs/assets/signoff_summary.json) records the results and source hashes; the [validation report](docs/validation_report.md) describes the acceptance criteria and coverage.

## Architecture at a glance

![CPU core, separate instruction/data memory interfaces, and verification observation outputs](docs/assets/architecture_overview.svg)

The CPU fetches instructions on `ibus`, carries each instruction through five stages, and completes loads/stores on `dbus`. Ready results can forward to dependent instructions; memory waits hold younger work, and EX redirects discard wrong-path instructions.

Read the [illustrated architecture guide](docs/architecture.md) for the [pipeline and dependency paths](docs/assets/architecture_pipeline.svg), the [verification flow](docs/assets/architecture_verification.svg), a load/redirect walkthrough, and links from each block to its RTL or checker source.

## Design case studies

The [case studies](docs/showcase.md) cover forwarding priority, byte-lane selection, and warm reset, with two measured VCD comparisons of isolated injected faults against the corrected RTL. The [synthesis report](docs/synthesis.md) documents the generated netlist and resource counts.

```text
python scripts/build_showcase.py
```

This command regenerates the two waveform SVGs and their hashed evidence from fresh simulations. The demonstration checks three positive configurations and two deliberately injected mutations. The checked-in [showcase metadata](docs/assets/showcase_evidence.json) records their results and artifact hashes. Full signoff runs separately with the command below.

## Requirements

- Python 3.10 or newer; the verification Python uses only the standard library.
- Icarus Verilog 11.x with `iverilog` and matching `vvp` available on `PATH`, or explicit `--sim`/`--vvp` paths.
- PowerShell is optional; `scripts/run_tests.py` is the single implementation of runner logic and works from Windows or Linux. The PowerShell wrapper finds `python` or `py` on `PATH`; `-PythonExe` or `CPU_VERIFY_PYTHON` selects a specific interpreter.

Signoff also checks for Verilator and Yosys, including available WSL tools on Windows. An available tool's lint/synthesis failure fails signoff; a missing tool is explicitly recorded as `BLOCKED_TOOLING`, never as a passed check. GTKWave, commercial simulators, and an external MIPS toolchain are not required. VCD generation is opt-in with `--waves`.

## Quick start

Windows PowerShell:

```powershell
git clone https://github.com/DatDinhh/5_stage_pipeline_cpu.git
Set-Location 5_stage_pipeline_cpu
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_tests.ps1 -Suite unit
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_tests.ps1 -Suite directed
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_tests.ps1 -Suite signoff
```

Linux, macOS, or direct Python on Windows:

```bash
git clone https://github.com/DatDinhh/5_stage_pipeline_cpu.git
cd 5_stage_pipeline_cpu
python3 scripts/run_tests.py --suite unit
python3 scripts/run_tests.py --suite directed
```

The runner resolves paths from the repository location, independently of the caller's current directory, and supports paths containing spaces.

## Verification commands

```text
python scripts/run_tests.py --list
python scripts/run_tests.py --suite unit
python scripts/run_tests.py --suite directed --sim iverilog
python scripts/run_tests.py --suite random --seed 12345
python scripts/run_tests.py --suite signoff --sim iverilog
python scripts/run_tests.py --suite directed --test raw_load_store --seed 12345 --waves
```

- `unit` runs Python encoder/ISS/checker/runner/toolchain/synthesis tests, HDL forwarding/hazard tests, the memory-model protocol test, and an expected-failure range guard. These form five result gates.
- `directed` runs 17 assembly-backed test IDs in 23 timing configurations, plus the full-core warm-reset protocol gate and its expected missing-phase rejection check.
- `random` creates a bounded legal program from each requested master seed and runs four timing profiles.
- `signoff` runs unit, directed, exactly 32 master seeds across all four random profiles, required coverage gates, two isolated mutation checks, and independent toolchain checks. Partial `--test` or `--seed` selection is intentionally rejected for signoff. It re-hashes the complete verification input set before accepting the result.

Every invocation builds from an explicit source list into a unique `build/runs/<run-id>` directory. A positive RTL run requires simulator success, complete artifacts, exact completion/event count, active online monitor and scoreboard success, independent ISS comparison of every event, full 32-GPR/4-KiB-RAM comparison, and required nonzero coverage bins. Timeout, Icarus compile warnings, missing files, empty selection, mismatch, or stale/incomplete signoff returns nonzero. Independent toolchain diagnostics and synthesis memory-to-register conversion warnings are preserved in their reports.

The three adversarial full-core configurations use legal bus shims with four request-stall cycles configured on each bus and initial instruction/data response holds of two/twelve cycles at the memory-facing interface. The existing ISS, scoreboard, and core-facing protocol monitors remain active; shim assertions also check held payloads and transaction ownership. Required coverage includes both request backpressure bins, both memory-facing response hold bins, and a redirect occurring while an instruction request is blocked. Core-facing and memory-facing response hold counts remain separate; the core-facing data response hold bin is zero because the core accepts an owned data response immediately.

The warm-reset gate resets the core and memory-model protocol state during instruction, load, and store transactions at three phases each: request blocked, accepted request awaiting a response, and response offered. It checks reset quiescence, cleared ownership/registers, no ghost retirement or store, persistent RAM, and complete restart from PC zero in all nine scenarios. A separate negative run omits a required phase and must be rejected for missing coverage.

## Supported architecture

The implemented instructions are:

- R-type: ADD/ADDU, SUB/SUBU, AND/OR/XOR/NOR, SLT, SLL/SRL/SRA.
- Immediate: ADDI, ANDI/ORI/XORI, LUI.
- Memory: LW/SW, LB/LBU, LH/LHU, SB/SH.
- Control: BEQ/BNE, J/JAL, and custom exact encoding `0x60000000` for ERET.

Memory is byte-addressed little-endian. `r0` is hardwired to zero. Reset PC is `0`, trap vector is `0x80`, JAL links `PC+4`, and precise traps cover illegal, misaligned, unmapped, and invalid CSR operations. The exact encodings, memory map, causes, CSR policy, and design rationale are in [isa_subset.md](docs/isa_subset.md) and [design_decisions.md](docs/design_decisions.md).

## Repository layout

```text
rtl/core/               synthesizable pipeline, predictor, ALU, GPR, CSR
sim/mem/                ready/valid memory models and adversarial bus shim
sim/score/              online external transaction/retirement correlator
sim/assertions/         active Icarus-compatible safety monitors
sim/tb/                 configurable core and block testbenches
verification/           assembler, independent ISS, strict offline checker
verification/tests/     positive and negative unit tests
tests/programs/         directed assembly sources
tests/testplan.json     stable test/requirement/profile/coverage mapping
scripts/                verification runner, wrapper, showcase and evidence export
docs/                   ISA, architecture, ledger, showcase, synthesis, evidence
docs/assets/            measured waveform SVGs and checked-in evidence snapshots
```

Further details are in [microarchitecture.md](docs/microarchitecture.md), [verification_plan.md](docs/verification_plan.md), [bug_ledger.md](docs/bug_ledger.md), and [validation_report.md](docs/validation_report.md). Full generated executables, VCDs, and netlists remain in ignored build directories. Checked-in SVGs and JSON snapshots record their provenance; signoff always performs fresh builds from the declared source inputs.

## Validation scope

The recorded toolchain uses Icarus/vvp 11.0, Python 3.12.14, Verilator 5.020, and Yosys 0.33. Generic synthesis of `cpu_core` and its RTL dependencies produces 22,382 leaf cells: 5,882 flip-flop cells and 16,500 combinational cells. This includes trace/debug logic and excludes external instruction/data RAM. Technology-specific utilization, timing, power, formal proof, and RTL/netlist equivalence remain outside the measured scope. Warm-reset verification uses a shared reset for the core and memory-model transaction state; RAM contents persist.

Signoff verifies that all declared source/configuration inputs retain their pre-run SHA-256 values. The [checked-in signoff snapshot](docs/assets/signoff_summary.json) preserves the tested hashes and tool results. The regression covers the declared tests and coverage gates without claiming exhaustive CPU correctness.
