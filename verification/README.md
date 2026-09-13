# Verification environment

This package verifies the CPU's architectural behavior with an independently executing instruction-set simulator (ISS), a strict retirement-trace checker, and complete register/memory comparison. The RTL testbench adds online safety monitors, a bus-to-retirement scoreboard, completion checks, and event coverage.

The [verification plan](../docs/verification_plan.md) defines the test inventory and coverage requirements. The [validation report](../docs/validation_report.md) and [signoff summary](../docs/assets/signoff_summary.json) record measured results and the source hashes associated with them.

## Verification flow

![Shared program inputs, RTL simulation, independent ISS execution, online monitors, and offline architectural comparison](../docs/assets/architecture_verification.svg)

Assembly and initial RAM contents feed both execution paths. The assembler is shared; the ISS owns its own PC, registers, memory, and instruction semantics. Observed RTL results do not select its expected operands or control-flow path. During comparison, the checker steps a fresh ISS; saved expected traces are diagnostic artifacts.

## Components

| Component | Responsibility |
|---|---|
| [encoding.py](encoding.py) | Two-pass assembler, instruction encoding, labels, directives, and field-range validation. |
| [iss.py](iss.py) | Architectural execution of the documented ISA, memory operations, CSR behavior, traps, and ERET. |
| [checker.py](checker.py) | Strict CSV/state parsing, event-by-event ISS comparison, completion validation, and final GPR/RAM comparison. |
| [toolchain.py](toolchain.py) | Native/WSL Verilator and Yosys discovery, execution, diagnostics, and synthesis artifact collection. |
| [synthesis.py](synthesis.py) | Validation of the synthesized CPU interface and reachable hierarchy, with generic cell counts and structural checks. |
| [tests/](tests/) | Python unit and negative tests for the assembler, ISS, checker, runner, synthesis, and evidence exporter. |
| [run_tests.py](../scripts/run_tests.py) | Test selection, fresh builds, timing/seed configuration, acceptance gates, mutation isolation, and run manifests. |
| [testplan.json](../tests/testplan.json) | Directed programs, completion labels, initial memory, timing profiles, requirement mappings, and coverage gates. |

The simulation side lives under `sim/`: [tb_core.sv](../sim/tb/tb_core.sv) drives ordinary program tests, [assertions.sv](../sim/assertions/assertions.sv) checks safety/protocol rules, and [scoreboard.v](../sim/score/scoreboard.v) correlates accepted data-bus transactions with retirement. The scoreboard checks transaction ownership; architectural expectations come from the ISS.

## Running tests

All commands below run from the **repository root**. Python 3.10+ is required; the Python code uses only the standard library. RTL tests also require Icarus `iverilog` and the matching `vvp` on `PATH`, or executable overrides through `--sim` and `--vvp`. Full lint/synthesis evidence requires Verilator and Yosys; Windows installations can use tools available through WSL.

```powershell
python scripts/run_tests.py --list
python scripts/run_tests.py --suite unit
python scripts/run_tests.py --suite directed
python scripts/run_tests.py --suite signoff
```

| Suite | Coverage |
|---|---|
| `unit` | Python tests, forwarding/hazard HDL tests, memory-model protocol checks, and the expected out-of-range rejection. |
| `directed` | Assembly-driven configurations, all nine full-core warm-reset scenarios, and the missing-reset-phase negative test. |
| `random` | A bounded generated program per master seed, exercised with four memory-timing profiles. |
| `signoff` | Unit and directed suites, exactly 32 master seeds across four profiles, coverage/matrix gates, two RTL mutations, and independent toolchain checks. |

The Python tests can run without an HDL simulator:

```powershell
python -m unittest discover -s verification/tests -t . -v
```

The PowerShell wrapper exposes the same runner:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_tests.ps1 -Suite signoff
```

`--test` and `--seed` select focused runs; neither is accepted for full signoff. The runner returns nonzero for configuration or verification failures. Missing optional lint/synthesis tools are recorded as `BLOCKED_TOOLING`; they do not count as successful tool checks. Local regression signoff can complete with blocked tools, so lint/synthesis status is checked separately in the manifest. The CI full-signoff job requires both tools to pass.

## Focused debugging

```powershell
# Forwarding priority with a VCD waveform
python scripts/run_tests.py --suite directed --test raw_forwarding_priority --waves

# A reproducible random program under all four random timing profiles
python scripts/run_tests.py --suite random --seed 12345 --waves

# Shared-reset recovery, including the expected missing-phase rejection
python scripts/run_tests.py --suite directed --test core_warm_reset
```

Directed test IDs come from `--list`; each selected ID runs the profiles declared in the test plan. The standard profiles are `fast`, `dslow`, `islow`, and `stress`. Three directed bus tests use `adversarial` to introduce request backpressure and held responses. Master/program/instruction-bus/data-bus seeds are deterministically separated so a failing configuration can be reproduced.

## What PASS means

An ordinary positive RTL result requires all of the following:

1. A fresh successful compile and simulator run, with the expected completion event and a drained CPU/scoreboard.
2. Zero online monitor/scoreboard errors and a complete, well-formed trace with valid event ordering.
3. Agreement with the ISS for every architectural event, including control flow, register results, memory effects, and traps.
4. Agreement for all 32 GPRs and the complete 4 KiB RAM, plus every coverage bin required by that test.

RTL cycle numbers and dynamic instruction UIDs have their own integrity checks. They need not match the ISS's instruction-count timing or speculative-fetch identity. Timing-dependent CSR reads require an independent value provider; the ISS raises `CSRValueUnavailable` when none is supplied. The [ISA contract](../docs/isa_subset.md) defines architectural semantics; the [microarchitecture](../docs/microarchitecture.md) defines pipeline timing and ownership.

Warm-reset tests use explicit HDL protocol/recovery expectations across reset epochs. Mutation tests pass only when an isolated injected fault causes an architectural mismatch; a compile failure or simulator crash is insufficient. The missing-phase reset test must fail for its specifically declared coverage error. These outcomes remain distinct in the manifest.

## Results and artifacts

Each invocation creates a fresh `build/runs/<run-id>/` directory, using a random identifier without a calendar date. `build/` is excluded from Git.

| Artifact | Contents |
|---|---|
| `manifest.json` | Suite status, result records, seeds/profiles, commands, coverage, tool results, and source hashes. |
| `compiled/` | Fresh executables and compile logs, separated by configuration. |
| `unit/`, `protocol/`, `protocol_negative/` | Block/Python test logs, reset results, and expected missing-phase rejection results. |
| `rtl/<case>/source.asm`, `program.hex`, `data.hex` | Program source and exact starting images for an ordinary RTL case. |
| `rtl/<case>/trace.csv`, `state.txt` | Observed retirement events and final register/RAM state. |
| `rtl/<case>/expected_trace.csv`, `expected_state.txt` | Architectural reference artifacts for diagnosis. |
| `rtl/<case>/coverage.txt`, `sim.log` | Observed coverage and simulator command/output. |
| `rtl/<case>/waves.vcd` | Waveform generated when `--waves` is selected. |
| `mutations/`, `mutation_sources/` | Fault-injection results, isolated RTL copies, and mutation hashes. |
| `toolchain/` | Independent lint/synthesis reports, logs, netlists, and statistics. |

The manifest points to the exact artifact directory for each case. Failure categories narrow the first inspection:

| Category | Diagnostic focus |
|---|---|
| `TRACE_FORMAT` / `TRACE_INTEGRITY` | Missing/unknown fields, malformed records, event order, cycle progression, or UID reuse. |
| `TRACE_MISMATCH` | First differing architectural event; compare its PC, expected value, observed value, and surrounding waveform. |
| `STATE_MISMATCH` | Final GPR/RAM differences after event checking. |
| `COMPLETION` / `COVERAGE` | Completion/event count and the named missing or zero coverage bins. |
| `SIMULATOR` / `INFRASTRUCTURE` | Simulator diagnostics, required files, or runtime/tool configuration. |

## Extending the suite and publishing evidence

New directed programs live in [tests/programs/](../tests/programs/). Their test-plan entries declare a unique ID, program path, completion label, suite/profile membership, requirement mapping, and required coverage bins; initial RAM values are optional. The selected test exercises the full normal acceptance path before inclusion in fixed signoff. Checker/reference-model changes also need negative tests showing that incorrect execution is rejected.

The [showcase builder](../scripts/build_showcase.py) regenerates measured fault-versus-corrected waveforms. After full signoff, the [evidence exporter](../scripts/export_evidence.py) validates source/artifact hashes and writes the portable summary:

```powershell
python scripts/export_evidence.py --manifest build/runs/<run-id>/manifest.json
```

The [GitHub workflow](../.github/workflows/ci.yml) runs `unit` and `directed` on pushes and pull requests. Manual dispatch with `full_signoff` enabled adds the complete matrix and requires both Verilator and Yosys to pass. Workflow artifacts retain the generated logs and manifests. Finite simulation, lint, and generic synthesis each establish their documented scope; physical timing and formal equivalence require separate verification flows.
