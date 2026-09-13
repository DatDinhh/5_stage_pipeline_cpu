# Verification Plan

## Objective and qualification rule

The reference model owns its own program, PC, GPRs, CSRs, and byte memory. RTL retirement events never choose the model's control-flow path or expected operands. A run passes only when all applicable layers below pass; simulator exit zero or a log containing `PASS` is not sufficient.

Full signoff comprises 5 unit gates, 23 directed RTL configurations, 128 constrained-random RTL configurations, 1 full-core warm-reset protocol test, 1 expected-failure missing-reset-phase test, and 2 functionally detected mutations. Independent Verilator lint and Yosys generic synthesis are reported separately. The [validation report](validation_report.md) records measured results for the tested source.

The [portable signoff summary](assets/signoff_summary.json) identifies the tested source and results. See the [synthesis scope and reproduction guide](synthesis.md) for netlist interpretation and the [showcase walkthrough](showcase.md) for reproducible debugging examples.

## Checking layers

1. `core_monitors` checks reset/r0, held request and response stability, known trace controls, event/retirement order, strictly increasing committed UIDs, and absence of trap side effects.
2. `scoreboard.v` correlates one accepted external data request with its response and later memory retirement event. It retains full address, write data, strobes, response data, and transaction kind; internal CSR reads are deliberately excluded.
3. `tb_core.sv` writes a registered trace, all 32 GPRs, all 1,024 RAM words, and measured event coverage. It requires exact expected event count, completion, drained core/scoreboard, and zero monitor errors. Extra events and timeout are fatal.
4. `iss.py` independently executes the documented ISA, precise traps, ERET, CSR architectural values, and little-endian memory semantics.
5. `checker.py` rejects malformed, unknown, truncated, duplicate, missing, or extra trace data; compares every architectural field; checks completion; then compares full GPR and RAM state.
6. `encoding.py` is a checked two-pass assembler with labels, `.org`, `.word`, and field-range validation.
7. Python negative tests mutate retirement results, next PC, load/store payloads, order/UID/cycle, event count, completion, and final state. HDL block tests independently exercise forwarding, hazards, and memory protocol behavior.
8. `tb_core_reset.sv` keeps the shared monitors and scoreboard active across nine warm-reset scenarios. Its own exact recovery oracle checks every restart instruction, fetch address, retirement order/UID, register result, memory event, all 32 GPRs, and all 1,024 RAM words. This protocol test is separately identified in the manifest; it does not claim an offline ISS trace comparison.
9. `rv_bus_shim.sv` exercises accepted-request ownership and held response stability in full-core runs. Core-facing and memory-facing protocol signals have separate counters; adverse timing runs still require the unchanged trace/ISS/full-state comparison.
10. The runner rejects missing protocol phases, invalid negative results, incomplete fixed matrices, and source input changes. Independent-tool results are recorded separately, so an unavailable synthesis tool cannot be reported as synthesis PASS.

The online scoreboard is a protocol-to-retirement correlator rather than a second ISA model. The offline ISS checks `load_raw`, extended load value, computed address, store source and aligned bus payload independently.

## Timing profiles and seeds

| Profile | Instruction bus | Data bus |
|---|---|---|
| `fast` | fixed zero inserted waits | fixed zero inserted waits |
| `dslow` | fixed zero inserted waits | fixed five waits |
| `islow` | seeded random 0..5 waits | fixed zero inserted waits |
| `stress` | seeded random 0..5 waits | seeded random 0..9 waits |
| `adversarial` | zero slave waits; four request-stall cycles and two memory-response-hold cycles through the shim | zero slave waits; four request-stall cycles and twelve memory-response-hold cycles through the shim |

Responses are registered. Master, program, instruction-bus, and data-bus streams are deterministically separated with SHA-256 derivation. Full signoff fixes master seeds to 0..31 across four random profiles (`fast`, `dslow`, `islow`, `stress`): exactly 128 random configurations. The `adversarial` profile is used by three fixed directed tests. `--test` and `--seed` are rejected for `--suite signoff`, preventing a partial matrix from being labeled signoff.

## Directed inventory

Every RTL case in the table uses the online monitor/scoreboard and offline trace/ISS/full-state comparison. Listed coverage bins are additional nonzero gates. The standalone warm-reset protocol test and its negative companion are described below.

| Test ID | Profiles | Main mapping | Required bins |
|---|---|---|---|
| `pipe_compat_original` | fast, stress | CPU-01/05/07, original ROM | store/load retire, branch not taken |
| `pipe_replay_self_increment` | fast, stress | CPU-01, exactly-once store | store retire |
| `raw_forwarding_priority` | fast | CPU-02, newest writer/two sources | EX A/B, double-match A/B |
| `raw_load_store` | fast, dslow | CPU-02/03/05, load-use, store dependencies | load hazard, EX/MEM wait, store/load retire |
| `alu_decode_matrix` | fast | supported ALU/decode matrix | none |
| `memory_lanes` | fast, dslow | lane/signedness/preservation/load-r0 | store/load retire, load hazard |
| `control_branch_paths` | fast, stress | CPU-04/06, taken/not, backward, wrong path | taken/not, redirect, fetch discard |
| `control_jump_jal` | fast | CPU-02/04, J/JAL/link/wrong path | redirect |
| `trap_eret_load` | fast, stress | precise misaligned-load trap and retry | trap, redirect, load retire |
| `trap_eret_store` | fast | precise misaligned-store trap and retry | trap, store/load retire |
| `trap_illegal_wrong_path` | fast | illegal policy and wrong-path suppression | trap, redirect |
| `csr_read` | fast | instret/EPC/cause; no external CSR request | load retire |
| `trap_csr_write` | fast | CSR permission trap | trap, redirect |
| `trap_unmapped_recover` | fast | unmapped load and ERET retry | trap, load retire |
| `bus_load_store` | adversarial | load/store dependencies with bus stalls | I/D request backpressure, I/D memory-response hold, load/store retire |
| `bus_memory_lanes` | adversarial | byte lanes, held payloads, exactly-once stores | I/D request backpressure, I/D memory-response hold, load/store retire |
| `bus_control_redirect` | adversarial | redirect while instruction request is blocked; wrong-path suppression | I/D request backpressure, I/D memory-response hold, `redirect_ibus_blocked`, redirect, fetch discard, branch taken/not-taken |

There are 17 RTL IDs and 23 directed timing configurations. `core_warm_reset` adds one positive protocol result and `core_warm_reset_missing_phase` adds one expected-failure protocol result to both directed and fixed signoff suites. The block memory test additionally covers continuous request-valid, a held response, same-edge response-consume/request-accept turnover, deterministic long waits, byte strobes, warm reset during a pending request, and the expected out-of-range fatal.

## Full-core warm reset

Each scenario first retires at least two real instructions and establishes nonzero architectural state. Reset is then asserted at an observed bus phase, without forcing internal DUT signals. Instruction and data slaves use twelve inserted wait cycles; request gates apply valid/ready consistently on both sides. Pending request and waiting-response phases remain pending across three additional clock edges; an offered response is reset before the next edge can consume it.

| Target | Request blocked before acceptance | Accepted, waiting for response creation | Response offered before consumption |
|---|---|---|---|
| Instruction | `reset_ibus_request_pending=1` | `reset_ibus_response_wait=1` | `reset_ibus_response_offered=1` |
| Load | `reset_load_request_pending=1` | `reset_load_response_wait=1` | `reset_load_response_offered=1` |
| Store | `reset_store_request_pending=1` | `reset_store_response_wait=1` | `reset_store_response_offered=1` |

The reset contract is shared reset of the core, interconnect ownership, and memory slaves. The untagged bus requires the slaves to cancel old requests/responses at that reset; this test does not establish recovery from a slave that delivers a stale response afterward. Memory contents survive reset. An accepted store waiting for response creation is canceled without a memory effect; a store already visible when its response was created remains visible even if that response has not been consumed or retired. Reset must neither roll back that completed memory effect nor replay it.

Every scenario checks cleared pipeline, core/slave bus ownership, scoreboard state, and registers. With fetching disabled, it then observes twenty post-reset cycles, longer than the canceled transaction latency, for ghost retirement, writeback, bus traffic, and memory changes. A distinct five-instruction program must fetch from reset vector zero, retire exactly once in order, perform exactly one load and one store, drain, and preserve the expected full memory/register state. All nine scenarios complete, providing 45 exactly checked recovery retirements.

The additional eight required bins each equal 9: `reset_ownership_clear`, `reset_registers_clear`, `reset_no_ghost_retire`, `reset_no_ghost_store`, `reset_memory_persistence`, `reset_restart_vector`, `reset_restart_complete`, and `reset_warm_activity`. Together with the nine phase bins, these are 17/17 required reset gates. The testbench enforces the exact phase and summary counts before `CORE_RESET_PASS:`; the runner independently requires their nonzero coverage artifact values.

The fixed negative case runs the same freshly compiled testbench with `+OMIT_SCENARIO=0`. Qualification requires a nonzero simulator exit, the exact missing-phase failure marker, no positive completion marker, and `reset_ibus_request_pending=0` in the coverage artifact. The final run met those conditions; the other eight phase bins equal 1 and the summary counters equal 8. Negative coverage is excluded from positive aggregate coverage. An arbitrary crash, missing artifact, or silently skipped scenario cannot satisfy this gate.

## Coverage and mutation gates

Coverage counters increment only from observed and checked events. Signoff requires 20/20 core aggregate critical bins plus the 17/17 reset gates above. The core bins are EX forwarding A/B, EX-vs-WB double match A/B, load hazard, EX/MEM wait, store retire, load retire, branch taken/not-taken, redirect, fetch discard, trap, write-r0 attempt, held core instruction response, I/D request backpressure, I/D memory-facing response hold, and the `redirect_ibus_blocked` cross event. None is waived or converted to PASS when zero.

Final aggregate counts are:

| Group | Measured counts |
|---|---|
| Forwarding | EX A 894, EX B 328, WB A 170, WB B 235, double A 144, double B 82, blocked-youngest 605 |
| Hazards/memory | load hazard 147, EX/MEM wait 9,462, load retire 1,230, store retire 1,138 |
| Control | branch taken 521, not-taken 136, redirect 538, mispredict 532, fetch discard 554, `redirect_ibus_blocked` 1 |
| Safety | trap 6, write-r0 371 |
| Core-facing bus | I request backpressure 326, D request backpressure 108, held I response 5,221, held D response 0 |
| Memory-facing bus | held I response 5,351, held D response 324 |

The three adversarial full-core tests produce ready/valid request stalls and hold memory response-valid/data stable while withholding memory-side ready. They contribute 253 I memory-response-hold cycles and all 324 D memory-response-hold cycles; the I aggregate of 5,351 also includes naturally held responses in other profiles. The shim eventually transfers each owned response to the core. Its protocol checks verify payload stability and exclusive ownership, while the normal scoreboard and offline checker verify exactly-once architectural effects. `redirect_ibus_blocked=1` measures a redirect simultaneous with a blocked instruction request.

The core-facing `dbus_resp_hold` counter remains 0 because this core keeps ready asserted for its one owned data response. It is reported as measured and is not a required bin. The required `dbus_mem_resp_hold=324` observes the distinct slave-to-shim interface. Dedicated block memory tests retain independent held-response and capacity checks.

Signoff copies RTL into isolated mutation trees and applies exactly one textual change. Reversing EX-over-WB priority is detected as `TRACE_MISMATCH` at event 2 (`r2`: expected 6, actual 4). Selecting the wrong byte lane is detected as `TRACE_MISMATCH` at event 3 (expected `0xffffffff`, actual `0x0000007f`). Both mutations were killed. Compiler errors, runtime crashes, or missing artifacts do not count as a killed mutation. Re-hashing the original source tree after both checks passed.

## Commands and pass/fail behavior

```powershell
python scripts/run_tests.py --list
python scripts/run_tests.py --suite unit
python scripts/run_tests.py --suite directed --sim iverilog
python scripts/run_tests.py --suite random --seed 12345
python scripts/run_tests.py --suite signoff --sim iverilog
python scripts/run_tests.py --suite directed --test pipe_replay_self_increment --seed 12345 --waves
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_tests.ps1 -Suite directed
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_tests.ps1 -Suite signoff
```

Run unit, then directed, use selected random seeds for debug, and run fixed full signoff before qualification. Each run uses a unique directory under `build/runs`, explicit source lists, a fresh warning-checked compile, child-process timeouts, exact completion/event limits, and immutable artifacts. Manifests record all verification input hashes, tool versions, commands, configuration, program/data hashes, and separate bus seeds. Timeout, compile/runtime failure, incomplete artifact, mismatch, empty selection, missing bin, invalid negative outcome, surviving mutation, or incomplete signoff matrix returns nonzero.

Before returning PASS, the runner re-enumerates and hashes the entire declared source/configuration input set; added, removed, or changed inputs fail qualification. Each generated `build/runs/<run-id>/manifest.json` records these checks. Python results are retained in `unit/python/run.log`; the unit suite includes netlist-inspector and native-statistics consistency negatives.

Independent tool discovery checks native paths and WSL. An available tool must execute successfully; missing tools are explicitly `BLOCKED_TOOLING`. Verilator runs with `--lint-only --top-module cpu_core` and its default warning policy, without warning suppression. Yosys must complete synthesis, structural checks, and validated artifact inspection. These tool outcomes are separate from the 160 regression result records.

## Generic synthesis gate

Yosys reads the eight RTL core files, checks the hierarchy, synthesizes `cpu_core`, runs `check -assert`, and exports Verilog, a JSON netlist, and native JSON statistics. The inspector validates the expected top/interface, traverses reachable module instances, and cross-checks its hierarchy-expanded leaf-cell counts against Yosys statistics. It rejects unresolved references, blackboxes, latch-family cells, residual memories/processes, malformed results, and inconsistent counts. Fresh tool output directories and recorded artifact/source hashes prevent old or unrelated outputs from qualifying.

The default configuration is `DMEM_BYTES=4096`, `ENABLE_BPU=1`. Trace/debug outputs and live `fetch_enable`/`verification_halt` inputs are included; the external instruction/data memories and simulation environment are excluded. Register-file and predictor arrays map to registers and logic, with array-to-register warnings retained in the report. The [validation report](validation_report.md) records the measured generic primitive counts and structural checks.

Each run's `toolchain/yosys/` directory retains `synthesis.ys`, `run.log`, `netlist.json`, `synthesized.v`, `statistics.json`, and `netlist_summary.json`; `toolchain/report.json` records the status, configuration, diagnostics, and artifact hashes. Detailed acceptance and scope are documented in [synthesis.md](synthesis.md).

## Limitations

The verification flow covers finite simulation suites, lint, and generic synthesis. Post-synthesis equivalence, static timing, FPGA execution, formal functional proof, and physical area/power analysis require separate flows. Reset qualification assumes the shared-reset bus contract above; it does not cover asynchronous reset-domain crossings or late responses from independently reset slaves. The checked-in CI workflow is configuration only until an external service executes it. VCD provides supporting debug evidence alongside the signoff gates.
